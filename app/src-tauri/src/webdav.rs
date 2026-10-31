//! Native HTTPS WebDAV client for the Nutstore sync probe.
//!
//! Bounds and safety properties enforced here:
//! - TLS certificate verification is always on (rustls); plaintext HTTP base
//!   URLs are rejected.
//! - Redirects are disabled. A 3xx response is an error, so credentials can
//!   never be forwarded to another host.
//! - Connect and total timeouts are bounded; response bodies are bounded at
//!   `MAX_ENVELOPE_BYTES` (checked against `Content-Length` before reading and
//!   enforced again while reading).
//! - Error messages are fixed and never contain the password, the account
//!   name, the URL, or response bodies.

use base64::{Engine, engine::general_purpose::STANDARD};
use reqwest::{
    Method, StatusCode,
    blocking::{Client, Response},
    header::{AUTHORIZATION, CONTENT_LENGTH},
    redirect::Policy,
};
use std::{io::Read, time::Duration};

pub const MAX_RESPONSE_BYTES: usize = account_app_core::MAX_ENVELOPE_BYTES;

const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const TOTAL_TIMEOUT: Duration = Duration::from_secs(30);

/// Error with an optional HTTP status code so callers can distinguish
/// semantic rejections (e.g. MOVE unsupported) from transport failures.
/// `step` carries the stable pipeline-stage label and HTTP method used by
/// the diagnostics contract; it is embedded into the display message so a
/// copied error always names the exact failing call.
#[derive(Debug)]
pub struct DavError {
    pub status: Option<u16>,
    /// `(stage label, HTTP method)` attached by the calling flow.
    step: Option<(&'static str, Option<&'static str>)>,
    message: String,
}

impl DavError {
    pub fn message(&self) -> &str {
        &self.message
    }

    /// Attach the failing pipeline stage (and HTTP method when known). The
    /// prefix format is stable and copyable: 【阶段 · METHOD · 状态】.
    pub fn at_step(mut self, step: &'static str, method: Option<&'static str>) -> Self {
        let status_part = self
            .status
            .map(|status| format!(" · {status}"))
            .unwrap_or_default();
        let method_part = method
            .map(|method| format!(" · {method}"))
            .unwrap_or_default();
        self.message = format!("【{step}{method_part}{status_part}】{}", self.message);
        self.step = Some((step, method));
        self
    }
}

impl From<String> for DavError {
    fn from(message: String) -> Self {
        DavError {
            status: None,
            step: None,
            message,
        }
    }
}

impl From<DavError> for String {
    fn from(error: DavError) -> Self {
        error.message
    }
}

impl std::fmt::Display for DavError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.message)
    }
}

/// Charset/length rules for one file or directory segment. The same rules
/// guard the diagnostic sub-paths, so nothing outside the bound directory
/// tree can be addressed.
fn valid_file_name(file: &str) -> bool {
    !file.is_empty()
        && file.len() <= 128
        && file
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
}

fn status_error(status: StatusCode) -> DavError {
    let message = match status.as_u16() {
        401 | 403 => "云端拒绝访问（401/403），请检查坚果云应用密码是否有效。".into(),
        404 => "云端没有找到该文件（404）。".into(),
        429 => "云端请求过于频繁（429），请稍后再试。".into(),
        502..=504 => "云端服务暂时不可用（5xx），请稍后再试。".into(),
        code if (300..400).contains(&code) => "云端返回重定向，已拒绝执行以保护凭据。".into(),
        code => format!("云端服务返回异常状态（{code}）。"),
    };
    DavError {
        status: Some(status.as_u16()),
        step: None,
        message,
    }
}

fn transport_error(error: reqwest::Error) -> DavError {
    let message = if error.is_timeout() {
        "连接或读取云端超时，请检查网络后重试。"
    } else if error.is_redirect() {
        // Unreachable with Policy::none(), kept as a defensive mapping.
        "云端返回重定向，已拒绝执行以保护凭据。"
    } else if error.is_connect() {
        "无法连接云端服务，请检查网络。"
    } else {
        "云端请求未能完成，请检查网络后重试。"
    };
    DavError {
        status: None,
        step: None,
        message: message.into(),
    }
}

/// Blocking WebDAV client bound to one fixed directory.
pub struct WebDavClient {
    agent: Client,
    directory_url: String,
    /// Every collection URL from the service root down to the bound
    /// directory, in creation order (`…/Account`, `…/Account/probe`).
    collection_urls: Vec<String>,
    authorization: zeroize::Zeroizing<String>,
}

/// What a PROPFIND (Depth 0) told us about one entry.
pub(crate) enum PropfindOutcome {
    NotFound,
    Collection,
    NotCollection,
}

impl WebDavClient {
    /// `base_url` must be HTTPS and end with `/`; `directory` is a relative
    /// ASCII path without slashes at the edges (for example `Account/probe`).
    pub fn new(
        base_url: &str,
        directory: &str,
        username: &str,
        password: &str,
    ) -> Result<Self, String> {
        // Production only ever gets HTTPS. Test builds may point the client at
        // a loopback plain-HTTP mock; cfg!(test) is never true in release.
        let enforce_https = !cfg!(test);
        if enforce_https && !base_url.starts_with("https://") {
            return Err("同步服务地址必须是 HTTPS。".into());
        }
        if !base_url.starts_with("https://") && !base_url.starts_with("http://") {
            return Err("同步服务地址配置无效。".into());
        }
        if !base_url.ends_with('/') {
            return Err("同步服务地址配置无效。".into());
        }
        if directory.is_empty()
            || directory.len() > 256
            || directory.starts_with('/')
            || directory.ends_with('/')
            || directory.split('/').any(|segment| {
                segment.is_empty()
                    || segment == "."
                    || segment == ".."
                    || !segment.bytes().all(|byte| {
                        byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.')
                    })
            })
        {
            return Err("云端目录配置无效。".into());
        }
        if username.is_empty() || username.len() > 256 {
            return Err("坚果云账号配置无效。".into());
        }
        if password.is_empty() || password.len() > 256 {
            return Err("应用密码配置无效。".into());
        }
        // Android: the platform verifier needs the JVM/context before any
        // client can be built. A failure here is a diagnosable, fixed-code
        // error that stops the request path instead of a panic deep inside
        // the TLS stack.
        crate::tls::ensure_tls_initialized().map_err(|error| format!("【HTTPS 初始化】{error}"))?;
        let agent = Client::builder()
            .redirect(Policy::none())
            .connect_timeout(CONNECT_TIMEOUT)
            .timeout(TOTAL_TIMEOUT)
            .build()
            .map_err(|_| "无法初始化同步客户端。".to_string())?;
        let credentials = zeroize::Zeroizing::new(format!("{username}:{password}"));
        let authorization =
            zeroize::Zeroizing::new(format!("Basic {}", STANDARD.encode(credentials.as_bytes())));
        let mut collection_urls = Vec::new();
        let mut accumulated = String::new();
        for segment in directory.split('/') {
            accumulated = if accumulated.is_empty() {
                segment.to_string()
            } else {
                format!("{accumulated}/{segment}")
            };
            collection_urls.push(format!("{base_url}{}", percent_encode_path(&accumulated)));
        }
        Ok(Self {
            agent,
            directory_url: collection_urls
                .last()
                .cloned()
                .ok_or("云端目录配置无效。".to_string())?,
            collection_urls,
            authorization,
        })
    }

    fn file_url(&self, file: &str) -> Result<String, String> {
        if !valid_file_name(file) {
            return Err("云端文件名配置无效。".into());
        }
        Ok(format!("{}/{}", self.directory_url, file))
    }

    fn request(
        &self,
        method: Method,
        file: &str,
    ) -> Result<reqwest::blocking::RequestBuilder, String> {
        let url = self.file_url(file)?;
        Ok(self
            .agent
            .request(method, url)
            .header(AUTHORIZATION, self.authorization.as_str()))
    }

    /// GET one file. `Ok(None)` means 404. Body is bounded before reading.
    pub fn get(&self, file: &str) -> Result<Option<Vec<u8>>, DavError> {
        let response = self
            .request(Method::GET, file)?
            .send()
            .map_err(transport_error)?;
        if response.status() == StatusCode::NOT_FOUND {
            return Ok(None);
        }
        if !response.status().is_success() {
            return Err(status_error(response.status()));
        }
        Ok(Some(read_bounded(response)?))
    }

    /// PUT one file, replacing whatever is at that path. Requires 2xx.
    pub fn put(&self, file: &str, body: &[u8]) -> Result<(), DavError> {
        let response = self
            .request(Method::PUT, file)?
            .header(CONTENT_LENGTH, body.len())
            .body(body.to_vec())
            .send()
            .map_err(transport_error)?;
        if !response.status().is_success() {
            return Err(status_error(response.status()));
        }
        Ok(())
    }

    /// MOVE `from` onto `to` with overwrite enabled. Requires 2xx.
    #[cfg(test)]
    pub fn move_file(&self, from: &str, to: &str) -> Result<(), DavError> {
        let destination = self.file_url(to)?;
        let method = Method::from_bytes(b"MOVE").map_err(|_| DavError {
            step: None,
            status: None,
            message: "无法构造移动请求。".into(),
        })?;
        let response = self
            .request(method, from)?
            .header("Destination", destination)
            .header("Overwrite", "T")
            .send()
            .map_err(transport_error)?;
        if !response.status().is_success() {
            return Err(status_error(response.status()));
        }
        Ok(())
    }

    /// DELETE one file. A missing file counts as deleted.
    #[cfg(test)]
    pub fn delete(&self, file: &str) -> Result<(), DavError> {
        let response = self
            .request(Method::DELETE, file)?
            .send()
            .map_err(transport_error)?;
        if response.status() == StatusCode::NOT_FOUND || response.status().is_success() {
            return Ok(());
        }
        Err(status_error(response.status()))
    }

    // ------------------------------------------------------------------
    // Diagnostic access. Same transport guarantees (HTTPS enforcement,
    // bounded bodies, no redirects, fixed errors) as the file methods
    // above, but addressing sub-paths inside the bound directory. Every
    // segment is validated with the same charset rules, so nothing outside
    // the bound directory tree is reachable. Used only by the confined
    // cloud-semantics diagnostic.
    // ------------------------------------------------------------------

    fn sub_url(&self, relative: &str) -> Result<String, String> {
        if relative.is_empty() || relative.len() > 256 || relative.starts_with('/') {
            return Err("诊断路径无效。".into());
        }
        let segments: Vec<&str> = relative.split('/').collect();
        if segments.is_empty()
            || segments
                .iter()
                .any(|segment| segment.is_empty() || !valid_file_name(segment))
        {
            return Err("诊断路径无效。".into());
        }
        Ok(format!(
            "{}/{}",
            self.directory_url,
            percent_encode_path(relative)
        ))
    }

    fn request_rel(
        &self,
        method: Method,
        relative: &str,
    ) -> Result<reqwest::blocking::RequestBuilder, DavError> {
        let url = self.sub_url(relative).map_err(DavError::from)?;
        Ok(self
            .agent
            .request(method, url)
            .header(AUTHORIZATION, self.authorization.as_str()))
    }

    pub(crate) fn get_rel(&self, relative: &str) -> Result<Option<Vec<u8>>, DavError> {
        let response = self
            .request_rel(Method::GET, relative)?
            .send()
            .map_err(transport_error)?;
        if response.status() == StatusCode::NOT_FOUND {
            return Ok(None);
        }
        if !response.status().is_success() {
            return Err(status_error(response.status()));
        }
        Ok(Some(read_bounded(response)?))
    }

    pub(crate) fn put_rel(&self, relative: &str, body: &[u8]) -> Result<(), DavError> {
        let response = self
            .request_rel(Method::PUT, relative)?
            .header(CONTENT_LENGTH, body.len())
            .body(body.to_vec())
            .send()
            .map_err(transport_error)?;
        if !response.status().is_success() {
            return Err(status_error(response.status()));
        }
        Ok(())
    }

    pub(crate) fn move_rel(&self, from: &str, to: &str) -> Result<(), DavError> {
        let destination = self.sub_url(to).map_err(DavError::from)?;
        let method = Method::from_bytes(b"MOVE").map_err(|_| DavError {
            step: None,
            status: None,
            message: "无法构造移动请求。".into(),
        })?;
        let response = self
            .request_rel(method, from)?
            .header("Destination", destination)
            .header("Overwrite", "T")
            .send()
            .map_err(transport_error)?;
        if !response.status().is_success() {
            return Err(status_error(response.status()));
        }
        Ok(())
    }

    /// DELETE a relative path. A missing file counts as deleted.
    pub(crate) fn delete_rel(&self, relative: &str) -> Result<(), DavError> {
        let response = self
            .request_rel(Method::DELETE, relative)?
            .send()
            .map_err(transport_error)?;
        if response.status() == StatusCode::NOT_FOUND || response.status().is_success() {
            return Ok(());
        }
        Err(status_error(response.status()))
    }

    /// MKCOL a relative collection path; requires 2xx on creation.
    pub(crate) fn mkcol_rel(&self, relative: &str) -> Result<(), DavError> {
        let method = Method::from_bytes(b"MKCOL").map_err(|_| DavError {
            step: None,
            status: None,
            message: "无法构造目录创建请求。".into(),
        })?;
        let response = self
            .request_rel(method, relative)?
            .send()
            .map_err(transport_error)?;
        if !response.status().is_success() {
            return Err(status_error(response.status()));
        }
        Ok(())
    }

    /// PROPFIND (Depth 0) a relative path and classify the outcome.
    /// 404 maps to `NotFound`; only a verified DAV collection body maps to
    /// `Collection`. Any other status is an error.
    pub(crate) fn propfind_rel(&self, relative: &str) -> Result<PropfindOutcome, DavError> {
        let url = self.sub_url(relative).map_err(DavError::from)?;
        self.propfind_url(&url)
    }

    /// Ensures the bound directory and every ancestor collection exist and
    /// are usable directories, creating missing ones top-down with MKCOL.
    ///
    /// A 405 ("entry already exists") or a 409 (some servers answer MKCOL on
    /// an existing collection this way) is only accepted after a PROPFIND
    /// confirms the entry really is a collection, so credential failures,
    /// quota limits, missing parents, and a same-name file at that path never
    /// pass as success. Only paths below the bound directory are touched;
    /// nothing is listed, deleted, or overwritten beyond creating the missing
    /// collections.
    pub fn ensure_collection(&self) -> Result<(), DavError> {
        for url in &self.collection_urls {
            match self.mkcol(url) {
                Ok(()) => continue,
                Err(error) if error.status == Some(405) || error.status == Some(409) => {
                    self.verify_existing_collection(url)?;
                }
                Err(error) => return Err(error),
            }
        }
        Ok(())
    }

    /// MKCOL one collection URL. 405 means an entry already exists there and
    /// is handed back to the caller for PROPFIND verification.
    fn mkcol(&self, url: &str) -> Result<(), DavError> {
        let method = Method::from_bytes(b"MKCOL").map_err(|_| DavError {
            step: None,
            status: None,
            message: "无法构造目录创建请求。".into(),
        })?;
        let response = self
            .agent
            .request(method, url)
            .header(AUTHORIZATION, self.authorization.as_str())
            .send()
            .map_err(transport_error)?;
        if !response.status().is_success() {
            return Err(status_error(response.status()));
        }
        Ok(())
    }

    /// PROPFIND (Depth 0) an existing entry and require a collection
    /// resourcetype. Accepts only a 207 Multi-Status body; every other
    /// status is an error, never a silent success.
    fn verify_existing_collection(&self, url: &str) -> Result<(), DavError> {
        match self.propfind_url(url)? {
            PropfindOutcome::Collection => Ok(()),
            _ => Err(DavError {
                status: None,
                step: None,
                message: "云端同名路径已存在但不是可用目录，拒绝在该路径发布。".into(),
            }),
        }
    }

    /// Read-only diagnostic access: every collection URL from the service
    /// root down to the bound directory.
    pub(crate) fn ancestor_urls(&self) -> &[String] {
        &self.collection_urls
    }

    /// PROPFIND (Depth 0) one URL and classify the outcome. 404 maps to
    /// `NotFound`; only a verified DAV collection body maps to `Collection`.
    /// Any other status is an error.
    pub(crate) fn propfind_url(&self, url: &str) -> Result<PropfindOutcome, DavError> {
        let method = Method::from_bytes(b"PROPFIND").map_err(|_| DavError {
            step: None,
            status: None,
            message: "无法构造目录检查请求。".into(),
        })?;
        let response = self
            .agent
            .request(method, url)
            .header(AUTHORIZATION, self.authorization.as_str())
            .header("Depth", "0")
            .send()
            .map_err(transport_error)?;
        if response.status() == StatusCode::NOT_FOUND {
            return Ok(PropfindOutcome::NotFound);
        }
        if response.status() != StatusCode::MULTI_STATUS {
            return Err(status_error(response.status()));
        }
        let body = read_bounded(response)?;
        if verified_dav_collection(&body) {
            Ok(PropfindOutcome::Collection)
        } else {
            Ok(PropfindOutcome::NotCollection)
        }
    }
}

fn verified_dav_collection(body: &[u8]) -> bool {
    use quick_xml::{events::Event, name::ResolveResult, reader::NsReader};
    let mut reader = NsReader::from_reader(body);
    let mut path: Vec<String> = Vec::new();
    let mut roots = 0;
    let mut collection = false;
    let mut status = String::new();
    let mut verified = false;
    loop {
        let Ok((namespace, event)) = reader.read_resolved_event() else {
            return false;
        };
        let dav = matches!(namespace, ResolveResult::Bound(ns) if ns.as_ref() == "DAV:");
        match event {
            Event::Start(ref tag) | Event::Empty(ref tag) => {
                if path.len() >= 64 {
                    return false;
                }
                if path.is_empty() {
                    roots += 1;
                    if roots > 1 {
                        return false;
                    }
                }
                let local = tag.local_name().as_ref().to_owned();
                path.push(if dav { local } else { "*".into() });
                if path
                    .iter()
                    .map(String::as_str)
                    .eq(["multistatus", "response", "propstat"])
                {
                    collection = false;
                    status.clear();
                }
                if path.iter().map(String::as_str).eq([
                    "multistatus",
                    "response",
                    "propstat",
                    "prop",
                    "resourcetype",
                    "collection",
                ]) {
                    collection = true;
                }
                if matches!(event, Event::Empty(_)) {
                    path.pop();
                }
            }
            Event::Text(text) => {
                if path.iter().map(String::as_str).eq([
                    "multistatus",
                    "response",
                    "propstat",
                    "status",
                ]) {
                    status.push_str(text.as_ref());
                }
            }
            Event::End(_) => {
                if path
                    .iter()
                    .map(String::as_str)
                    .eq(["multistatus", "response", "propstat"])
                {
                    let mut fields = status.split_whitespace();
                    let http = fields
                        .next()
                        .is_some_and(|version| version.starts_with("HTTP/"));
                    verified |= collection && http && fields.next() == Some("200");
                }
                if path.pop().is_none() {
                    return false;
                }
            }
            Event::DocType(_) => return false,
            Event::Eof => return verified && path.is_empty(),
            _ => {}
        }
    }
}

fn read_bounded(response: Response) -> Result<Vec<u8>, DavError> {
    if let Some(_length) = response
        .content_length()
        .filter(|length| *length as usize > MAX_RESPONSE_BYTES)
    {
        return Err(DavError {
            step: None,
            status: None,
            message: "云端响应过大，拒绝读取。".into(),
        });
    }
    let mut reader = response.take((MAX_RESPONSE_BYTES + 1) as u64);
    let mut bytes = Vec::new();
    reader.read_to_end(&mut bytes).map_err(|_| DavError {
        status: None,
        step: None,
        message: "云端响应读取未完成。".into(),
    })?;
    if bytes.len() > MAX_RESPONSE_BYTES {
        return Err(DavError {
            step: None,
            status: None,
            message: "云端响应过大，拒绝读取。".into(),
        });
    }
    Ok(bytes)
}

/// Percent-encode a validated ASCII path so reserved characters cannot alter
/// the URL structure. Input is already restricted by `WebDavClient::new`.
fn percent_encode_path(path: &str) -> String {
    let mut encoded = String::with_capacity(path.len());
    for byte in path.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'/') {
            encoded.push(byte as char);
        } else {
            encoded.push_str(&format!("%{byte:02X}"));
        }
    }
    encoded
}

#[cfg(test)]
pub mod tests_support {
    use std::{
        io::{BufRead, BufReader, Read as _, Write},
        net::TcpListener,
        sync::{Arc, Mutex},
    };

    pub struct MockRequest {
        pub method: String,
        pub path: String,
        pub headers: Vec<(String, String)>,
        pub body: Vec<u8>,
    }

    pub struct MockResponse {
        pub status: u16,
        pub headers: Vec<(String, String)>,
        pub body: Vec<u8>,
        /// When set, the socket is closed after this many body bytes.
        pub truncate_body_after: Option<usize>,
    }

    pub type Handler = Arc<dyn Fn(&MockRequest) -> MockResponse + Send + Sync>;

    pub fn spawn_server(handler: Handler) -> (String, Arc<Mutex<Vec<MockRequest>>>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap().to_string();
        let seen = Arc::new(Mutex::new(Vec::<MockRequest>::new()));
        let seen_for_thread = seen.clone();
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else {
                    break;
                };
                let handler = handler.clone();
                let seen = seen_for_thread.clone();
                std::thread::spawn(move || {
                    serve_connection(&mut stream, &handler, &seen);
                });
            }
        });
        (format!("http://{address}/"), seen)
    }

    fn serve_connection(
        stream: &mut std::net::TcpStream,
        handler: &Handler,
        seen: &Mutex<Vec<MockRequest>>,
    ) {
        let mut reader = BufReader::new(stream.try_clone().unwrap());
        let mut line = String::new();
        if reader.read_line(&mut line).unwrap() == 0 {
            return;
        }
        let mut parts = line.split_whitespace();
        let method = parts.next().unwrap_or("").to_string();
        let path = parts.next().unwrap_or("").to_string();
        let mut headers = Vec::new();
        loop {
            let mut header = String::new();
            reader.read_line(&mut header).unwrap();
            let header = header.trim_end().to_string();
            if header.is_empty() {
                break;
            }
            if let Some((name, value)) = header.split_once(':') {
                headers.push((name.trim().to_ascii_lowercase(), value.trim().to_string()));
            }
        }
        let length: usize = headers
            .iter()
            .find(|(name, _)| name == "content-length")
            .and_then(|(_, value)| value.parse().ok())
            .unwrap_or(0);
        let mut body = vec![0u8; length];
        reader.read_exact(&mut body).unwrap();
        seen.lock().unwrap().push(MockRequest {
            method: method.clone(),
            path: path.clone(),
            headers: headers.clone(),
            body: body.clone(),
        });
        let MockResponse {
            status,
            headers,
            body,
            truncate_body_after,
        } = handler(&MockRequest {
            method,
            path,
            headers,
            body,
        });
        let reason = match status {
            200 => "OK",
            201 => "Created",
            204 => "No Content",
            207 => "Multi-Status",
            302 => "Found",
            401 => "Unauthorized",
            403 => "Forbidden",
            404 => "Not Found",
            405 => "Method Not Allowed",
            409 => "Conflict",
            500 => "Internal Server Error",
            501 => "Not Implemented",
            _ => "Status",
        };
        let mut response = format!("HTTP/1.1 {status} {reason}\r\n");
        for (name, value) in &headers {
            response.push_str(&format!("{name}: {value}\r\n"));
        }
        let chunked = headers
            .iter()
            .any(|(name, _)| name.eq_ignore_ascii_case("transfer-encoding"));
        if !chunked {
            response.push_str(&format!("Content-Length: {}\r\n", body.len()));
        }
        response.push_str("Connection: close\r\n\r\n");
        stream.write_all(response.as_bytes()).unwrap();
        if chunked {
            for chunk in body.chunks(64 * 1024) {
                stream
                    .write_all(format!("{:x}\r\n", chunk.len()).as_bytes())
                    .unwrap();
                stream.write_all(chunk).unwrap();
                stream.write_all(b"\r\n").unwrap();
            }
            stream.write_all(b"0\r\n\r\n").unwrap();
            return;
        }
        match truncate_body_after {
            Some(limit) => {
                stream.write_all(&body[..limit.min(body.len())]).unwrap();
            }
            None => stream.write_all(&body).unwrap(),
        }
    }

    /// A strict hierarchy mock: MKCOL only succeeds for a missing entry whose
    /// parent collection exists; existing collections answer MKCOL with 405
    /// and PROPFIND with 207 + collection resourcetype; files and strangers
    /// fail the collection check. Keys are hierarchy-relative ("Account",
    /// "Account/probe", "Account/probe/latest.enc"); the service mount
    /// "dav/" is the always-existing virtual root.
    /// Simulated strict cloud: path -> ('c'ollection | 'f'ile, body).
    pub type HierarchyEntries = Arc<Mutex<std::collections::BTreeMap<String, (char, Vec<u8>)>>>;

    pub fn hierarchy_server(
        entries: HierarchyEntries,
        mkcol_status: Option<u16>,
        propfind_status: Option<u16>,
    ) -> (String, Arc<Mutex<Vec<MockRequest>>>) {
        spawn_server(Arc::new(move |request| {
            let path = request.path.trim_end_matches('/').to_string();
            let hierarchy = path
                .strip_prefix("/dav/")
                .unwrap_or(path.strip_prefix('/').unwrap_or(&path))
                .to_string();
            let segments: Vec<&str> = hierarchy.split('/').filter(|s| !s.is_empty()).collect();
            // Every strict ancestor prefix must exist; for a top-level entry
            // there are no ancestors and the dav mount root always exists.
            let parents_exist = segments[..segments.len().saturating_sub(1)]
                .iter()
                .scan(String::new(), |prefix, segment| {
                    if prefix.is_empty() {
                        *prefix = (*segment).to_string();
                    } else {
                        *prefix = format!("{prefix}/{segment}");
                    }
                    Some(prefix.clone())
                })
                .all(|prefix| entries.lock().unwrap().contains_key(&prefix));
            match request.method.as_str() {
                "MKCOL" => {
                    if let Some(forced) = mkcol_status {
                        return MockResponse {
                            status: forced,
                            headers: vec![],
                            body: vec![],
                            truncate_body_after: None,
                        };
                    }
                    let mut map = entries.lock().unwrap();
                    if !parents_exist || map.contains_key(&hierarchy) {
                        MockResponse {
                            status: if parents_exist { 405 } else { 409 },
                            headers: vec![],
                            body: vec![],
                            truncate_body_after: None,
                        }
                    } else {
                        map.insert(hierarchy, ('c', Vec::new()));
                        MockResponse {
                            status: 201,
                            headers: vec![],
                            body: vec![],
                            truncate_body_after: None,
                        }
                    }
                }
                "PROPFIND" => {
                    if let Some(forced) = propfind_status {
                        return MockResponse {
                            status: forced,
                            headers: vec![],
                            body: vec![],
                            truncate_body_after: None,
                        };
                    }
                    match entries
                    .lock()
                    .unwrap()
                    .get(&hierarchy)
                    .map(|(kind, _)| *kind)
                {
                    Some('c') => MockResponse {
                        status: 207,
                        headers: vec![],
                        body: br#"<D:multistatus xmlns:D="DAV:"><D:response><D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response></D:multistatus>"#.to_vec(),
                        truncate_body_after: None,
                    },
                    Some('f') => MockResponse {
                        status: 207,
                        headers: vec![],
                        body: br#"<D:multistatus xmlns:D="DAV:"><D:response><D:propstat><D:prop><D:resourcetype/></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response></D:multistatus>"#.to_vec(),
                        truncate_body_after: None,
                    },
                    None => MockResponse {
                        status: 404,
                        headers: vec![],
                        body: vec![],
                        truncate_body_after: None,
                    },
                    _ => MockResponse {
                        status: 207,
                        headers: vec![],
                        body: br#"<D:multistatus xmlns:D="DAV:"><D:response><D:propstat><D:prop><D:resourcetype/></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response></D:multistatus>"#.to_vec(),
                        truncate_body_after: None,
                    },
                }
                }
                "GET" | "PUT" | "MOVE" | "DELETE" => {
                    if !parents_exist {
                        return MockResponse {
                            status: 409,
                            headers: vec![],
                            body: vec![],
                            truncate_body_after: None,
                        };
                    }
                    let mut map = entries.lock().unwrap();
                    match request.method.as_str() {
                        "GET" => match map.get(&hierarchy) {
                            Some(('f', body)) => MockResponse {
                                status: 200,
                                headers: vec![],
                                body: body.clone(),
                                truncate_body_after: None,
                            },
                            _ => MockResponse {
                                status: 404,
                                headers: vec![],
                                body: vec![],
                                truncate_body_after: None,
                            },
                        },
                        "PUT" => {
                            map.insert(hierarchy, ('f', request.body.clone()));
                            MockResponse {
                                status: 201,
                                headers: vec![],
                                body: vec![],
                                truncate_body_after: None,
                            }
                        }
                        "MOVE" => {
                            let destination = request
                                .headers
                                .iter()
                                .find(|(name, _)| name == "destination")
                                .unwrap()
                                .1
                                .clone();
                            let target = destination
                                .split_once("://")
                                .and_then(|(_, rest)| rest.split_once('/'))
                                .map(|(_, path)| {
                                    path.strip_prefix("dav/").unwrap_or(path).to_string()
                                })
                                .unwrap_or_default();
                            // Real-service semantics (jianguoyun, verified
                            // 2026-10-13): MOVE onto an existing target is
                            // refused with 409 even with Overwrite: T, and
                            // the target keeps its old body.
                            if map.contains_key(&target) {
                                return MockResponse {
                                    status: 409,
                                    headers: vec![],
                                    body: vec![],
                                    truncate_body_after: None,
                                };
                            }
                            let moved_entry = map.remove(&hierarchy);
                            map.insert(
                                target,
                                ('f', moved_entry.map(|(_, body)| body).unwrap_or_default()),
                            );
                            MockResponse {
                                status: 201,
                                headers: vec![],
                                body: vec![],
                                truncate_body_after: None,
                            }
                        }
                        _ => MockResponse {
                            status: 204,
                            headers: vec![],
                            body: vec![],
                            truncate_body_after: None,
                        },
                    }
                }
                other => panic!("unexpected method {other}"),
            }
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::tests_support::*;
    use super::*;
    use std::sync::{Arc, Mutex};

    #[test]
    fn collection_verification_requires_successful_dav_resourcetype() {
        for resource in [
            "<D:displayname>prefix:collection</D:displayname><D:resourcetype/>",
            "<D:resourcetype><X:collection xmlns:X='other:'/></D:resourcetype>",
        ] {
            let body = format!("<D:multistatus xmlns:D='DAV:'><D:response><D:propstat><D:prop>{resource}</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response></D:multistatus>").into_bytes();
            let (base, _) = spawn_server(Arc::new(move |_| MockResponse {
                status: 207,
                headers: vec![],
                body: body.clone(),
                truncate_body_after: None,
            }));
            assert!(
                client_for(&format!("{base}dav/"))
                    .verify_existing_collection(&format!("{base}dav/Account"))
                    .is_err()
            );
        }
        for status in ["HTTP/1.1 403 Forbidden", "HTTP/1.1 404 Not Found"] {
            let body = format!("<D:multistatus xmlns:D='DAV:'><D:response><D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop><D:status>{status}</D:status></D:propstat></D:response></D:multistatus>").into_bytes();
            let (base, _) = spawn_server(Arc::new(move |_| MockResponse {
                status: 207,
                headers: vec![],
                body: body.clone(),
                truncate_body_after: None,
            }));
            assert!(
                client_for(&format!("{base}dav/"))
                    .verify_existing_collection(&format!("{base}dav/Account"))
                    .is_err()
            );
        }
    }

    fn client_for(base_url: &str) -> WebDavClient {
        WebDavClient::new(
            base_url,
            "Account/probe",
            "user@example.test",
            "app-password",
        )
        .unwrap()
    }

    fn header<'a>(request: &'a MockRequest, name: &str) -> Option<&'a str> {
        request
            .headers
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value.as_str())
    }

    #[test]
    fn rejects_malformed_configuration() {
        // Plaintext rejection is enforced in release builds only; test builds
        // allow the loopback mock's plain HTTP base URL.
        if cfg!(test) {
            assert!(
                WebDavClient::new("http://dav.example.test/dav/", "Account/probe", "u", "p")
                    .is_ok()
            );
        }
        assert!(
            WebDavClient::new("ftp://dav.example.test/dav/", "Account/probe", "u", "p").is_err()
        );
        assert!(WebDavClient::new("https://dav.example.test/dav/", "../escape", "u", "p").is_err());
        assert!(WebDavClient::new("https://dav.example.test/dav/", "a//b", "u", "p").is_err());
        assert!(
            WebDavClient::new("https://dav.example.test/dav/", "Account/probe", "", "p").is_err()
        );
        assert!(
            WebDavClient::new("https://dav.example.test/dav/", "Account/probe", "u", "").is_err()
        );
    }

    #[test]
    fn get_roundtrips_body_and_maps_404_to_none() {
        let (base, seen) = spawn_server(Arc::new(|request| {
            assert_eq!(request.method, "GET");
            assert_eq!(request.path, "/dav/Account/probe/latest.enc");
            MockResponse {
                status: 200,
                headers: vec![],
                body: b"envelope-bytes".to_vec(),
                truncate_body_after: None,
            }
        }));
        let client = client_for(&format!("{base}dav/"));
        assert_eq!(
            client.get("latest.enc").unwrap().unwrap(),
            b"envelope-bytes"
        );
        assert_eq!(
            header(&seen.lock().unwrap()[0], "authorization"),
            Some("Basic dXNlckBleGFtcGxlLnRlc3Q6YXBwLXBhc3N3b3Jk")
        );

        let (base, _) = spawn_server(Arc::new(|_| MockResponse {
            status: 404,
            headers: vec![],
            body: vec![],
            truncate_body_after: None,
        }));
        let client = client_for(&format!("{base}dav/"));
        assert!(client.get("latest.enc").unwrap().is_none());
    }

    #[test]
    fn put_and_move_send_expected_method_headers_and_body() {
        let (base, seen) = spawn_server(Arc::new(|request| match request.method.as_str() {
            "PUT" | "MOVE" => MockResponse {
                status: 201,
                headers: vec![],
                body: vec![],
                truncate_body_after: None,
            },
            other => panic!("unexpected method {other}"),
        }));
        let client = client_for(&format!("{base}dav/"));
        client.put("latest.enc.pending", b"payload").unwrap();
        client
            .move_file("latest.enc.pending", "latest.enc")
            .unwrap();
        let requests = seen.lock().unwrap();
        assert_eq!(requests[0].body, b"payload");
        assert_eq!(requests[1].method, "MOVE");
        assert_eq!(
            header(&requests[1], "destination"),
            Some(&*format!("{base}dav/Account/probe/latest.enc"))
        );
        assert_eq!(header(&requests[1], "overwrite"), Some("T"));
    }

    #[test]
    fn redirects_are_refused_and_never_followed() {
        let (base, seen) = spawn_server(Arc::new(|request| {
            assert_eq!(request.path, "/dav/Account/probe/latest.enc");
            MockResponse {
                status: 302,
                headers: vec![("Location".into(), "/attacker/latest.enc".into())],
                body: vec![],
                truncate_body_after: None,
            }
        }));
        let client = client_for(&format!("{base}dav/"));
        let error = client.get("latest.enc").unwrap_err();
        assert!(
            error.message().contains("重定向"),
            "unexpected error: {error}"
        );
        // Only the original path was ever requested; the credential stayed on
        // the original host because no redirect was followed.
        let requests = seen.lock().unwrap();
        assert_eq!(requests.len(), 1);
        assert_eq!(requests[0].path, "/dav/Account/probe/latest.enc");
    }

    #[test]
    fn oversized_content_length_is_rejected_before_reading() {
        let (base, _) = spawn_server(Arc::new(|_| MockResponse {
            status: 200,
            headers: vec![],
            body: vec![0u8; MAX_RESPONSE_BYTES + 1],
            truncate_body_after: None,
        }));
        let client = client_for(&format!("{base}dav/"));
        let error = client.get("latest.enc").unwrap_err();
        assert!(
            error.message().contains("过大"),
            "unexpected error: {error}"
        );
    }

    #[test]
    fn unbounded_chunked_stream_is_cut_at_the_limit() {
        // No Content-Length: the body must be cut while reading, never read
        // into memory without a bound.
        let (base, _) = spawn_server(Arc::new(|_| MockResponse {
            status: 200,
            headers: vec![("Transfer-Encoding".into(), "chunked".into())],
            body: vec![0u8; MAX_RESPONSE_BYTES + 10],
            truncate_body_after: None,
        }));
        let client = client_for(&format!("{base}dav/"));
        let error = client.get("latest.enc").unwrap_err();
        assert!(
            error.message().contains("过大"),
            "unexpected error: {error}"
        );
    }

    #[test]
    fn truncated_body_is_reported_as_incomplete() {
        let (base, _) = spawn_server(Arc::new(|_| MockResponse {
            status: 200,
            headers: vec![],
            body: b"0123456789".to_vec(),
            truncate_body_after: Some(4),
        }));
        let client = client_for(&format!("{base}dav/"));
        let error = client.get("latest.enc").unwrap_err();
        assert!(
            error.message().contains("未完成"),
            "unexpected error: {error}"
        );
    }

    #[test]
    fn credential_errors_are_recognizable_without_leaking_details() {
        let (base, _) = spawn_server(Arc::new(|_| MockResponse {
            status: 401,
            headers: vec![],
            body: b"secret-details".to_vec(),
            truncate_body_after: None,
        }));
        let client = client_for(&format!("{base}dav/"));
        let error = client.get("latest.enc").unwrap_err();
        assert!(
            error.message().contains("应用密码"),
            "unexpected error: {error}"
        );
        let message = error.message();
        assert!(!message.contains("secret-details"));
        assert!(!message.contains("app-password"));
        assert!(!message.contains("user@example.test"));
        assert_eq!(error.status, Some(401));
    }

    #[test]
    fn delete_treats_missing_file_as_done() {
        let (base, seen) = spawn_server(Arc::new(|request| MockResponse {
            status: if request.path.ends_with("gone.enc") {
                404
            } else {
                204
            },
            headers: vec![],
            body: vec![],
            truncate_body_after: None,
        }));
        let client = client_for(&format!("{base}dav/"));
        client.delete("latest.enc.pending").unwrap();
        client.delete("gone.enc").unwrap();
        assert_eq!(seen.lock().unwrap().len(), 2);
    }

    #[test]
    fn ensure_collection_creates_missing_directories_top_down() {
        let entries = Arc::new(Mutex::new(std::collections::BTreeMap::new()));
        let (base, seen) = hierarchy_server(entries.clone(), None, None);
        let client = client_for(&format!("{base}dav/"));
        client.ensure_collection().unwrap();
        {
            let map = entries.lock().unwrap();
            assert!(map.contains_key("Account"));
            assert!(map.contains_key("Account/probe"));
        }
        let methods: Vec<String> = seen
            .lock()
            .unwrap()
            .iter()
            .map(|r| r.method.clone())
            .collect();
        assert_eq!(methods, vec!["MKCOL", "MKCOL"]);
        // A repeat run reuses the existing directories: 405 + verified 207.
        client.ensure_collection().unwrap();
        let methods: Vec<String> = seen
            .lock()
            .unwrap()
            .iter()
            .skip(2)
            .map(|r| r.method.clone())
            .collect();
        assert_eq!(methods, vec!["MKCOL", "PROPFIND", "MKCOL", "PROPFIND"]);
    }

    #[test]
    fn ensure_collection_never_mistakes_rejections_for_success() {
        // Credential failure on MKCOL must surface, not pass.
        let (base, _) = hierarchy_server(
            Arc::new(Mutex::new(std::collections::BTreeMap::new())),
            Some(401),
            None,
        );
        let client = client_for(&format!("{base}dav/"));
        let error = client.ensure_collection().unwrap_err();
        assert_eq!(error.status, Some(401));
        assert!(error.message().contains("应用密码"));

        // An entry that exists but is NOT a collection must be rejected.
        let entries = Arc::new(Mutex::new(std::collections::BTreeMap::from([(
            "Account".to_string(),
            ('f', Vec::new()),
        )])));
        let (base, _) = hierarchy_server(entries, None, None);
        let client = client_for(&format!("{base}dav/"));
        let error = client.ensure_collection().unwrap_err();
        assert!(error.message().contains("不是可用目录"));

        // PROPFIND refusing to describe the entry must not pass either.
        let entries = Arc::new(Mutex::new(std::collections::BTreeMap::from([(
            "Account".to_string(),
            ('c', Vec::new()),
        )])));
        let (base, _) = hierarchy_server(entries, None, Some(403));
        let client = client_for(&format!("{base}dav/"));
        let error = client.ensure_collection().unwrap_err();
        assert_eq!(error.status, Some(403));
    }
}

#[cfg(test)]
mod inbox_list_tests {
    use super::*;
    fn xml(href: &str) -> Vec<u8> {
        format!("<d:multistatus xmlns:d=\"DAV:\"><d:response><d:href>{href}</d:href><d:propstat><d:prop/><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>").into_bytes()
    }
    #[test]
    fn immediate_scoped_names_only_and_namespace_verified() {
        let base = "https://example.test/dav/Account/inbox/scope";
        let name = format!("{}.bill.enc", "a".repeat(64));
        assert_eq!(
            parse_inbox_listing(&xml(&format!("/dav/Account/inbox/scope/{name}")), base).unwrap(),
            vec![name.clone()]
        );
        for href in [
            format!("https://evil.test/dav/Account/inbox/scope/{name}"),
            format!("/dav/Account/inbox/scope/../{name}"),
            format!("/dav/Account/inbox/scope/sub/{name}"),
            format!("/dav/Account/inbox/scope/%2e%2e/{name}"),
            format!("/dav/Account/inbox/foreign/{name}"),
        ] {
            assert!(parse_inbox_listing(&xml(&href), base).is_err(), "{href}");
        }
        assert!(
            parse_inbox_listing(
                &String::from_utf8(xml(&format!("/dav/Account/inbox/scope/{name}")))
                    .unwrap()
                    .replace("DAV:", "fake")
                    .into_bytes(),
                base
            )
            .is_err()
        );
    }
    #[test]
    fn limit_fails_instead_of_silent_truncation() {
        let base = "https://example.test/dav/Account/inbox/scope";
        let mut body = "<d:multistatus xmlns:d=\"DAV:\">".to_string();
        for i in 0..750 {
            body.push_str(&format!("<d:response><d:href>/dav/Account/inbox/scope/{i:064x}.bill.enc</d:href><d:status>HTTP/1.1 200 OK</d:status></d:response>"));
        }
        body.push_str("</d:multistatus>");
        assert!(parse_inbox_listing(body.as_bytes(), base).is_err());
    }
}

impl WebDavClient {
    /// Only the bound inbox directory is listed. No href returned by the
    /// server is followed; validated basenames are later passed to GET.
    pub(crate) fn list_inbox(&self) -> Result<Vec<String>, DavError> {
        let method = Method::from_bytes(b"PROPFIND")
            .map_err(|_| DavError::from("收件箱目录检查失败。".to_string()))?;
        let response = self
            .agent
            .request(method, &self.directory_url)
            .header(AUTHORIZATION, self.authorization.as_str())
            .header("Depth", "1")
            .header("Content-Type", "application/xml; charset=utf-8")
            .body("<d:propfind xmlns:d=\"DAV:\"><d:prop><d:resourcetype/></d:prop></d:propfind>")
            .send()
            .map_err(transport_error)?;
        if response.status() == StatusCode::NOT_FOUND {
            return Ok(vec![]);
        }
        if response.status() != StatusCode::MULTI_STATUS {
            return Err(status_error(response.status()));
        }
        let body = read_bounded(response)?;
        parse_inbox_listing(&body, &self.directory_url).map_err(DavError::from)
    }
}
fn parse_inbox_listing(body: &[u8], directory_url: &str) -> Result<Vec<String>, String> {
    use quick_xml::{events::Event, name::ResolveResult, reader::NsReader};
    let invalid = || "收件箱目录响应不完整或包含不安全路径，已停止。".to_string();
    if body.len() > MAX_RESPONSE_BYTES {
        return Err(invalid());
    }
    let directory = reqwest::Url::parse(directory_url).map_err(|_| invalid())?;
    let prefix = format!("{}/", directory.path().trim_end_matches('/'));
    let mut reader = NsReader::from_reader(body);
    let mut path: Vec<String> = vec![];
    let mut roots = 0;
    let mut count = 0;
    let mut href = String::new();
    let mut status = String::new();
    let mut hrefs = 0;
    let mut good_status = false;
    let mut names = std::collections::BTreeSet::new();
    loop {
        let (namespace, event) = reader.read_resolved_event().map_err(|_| invalid())?;
        let dav = matches!(namespace, ResolveResult::Bound(ns) if ns.as_ref() == "DAV:");
        match event {
            Event::Start(ref tag) | Event::Empty(ref tag) => {
                if path.len() >= 32 {
                    return Err(invalid());
                }
                let local = if dav {
                    tag.local_name().as_ref().to_string()
                } else {
                    "*".into()
                };
                if path.is_empty() {
                    roots += 1;
                    if roots != 1 || local != "multistatus" {
                        return Err(invalid());
                    }
                }
                path.push(local);
                if path
                    .iter()
                    .map(String::as_str)
                    .eq(["multistatus", "response"])
                {
                    count += 1;
                    if count >= 750 {
                        return Err("云端收件箱已达到目录列举上限，请分批检查。".into());
                    }
                    href.clear();
                    hrefs = 0;
                    good_status = false;
                }
                if path
                    .iter()
                    .map(String::as_str)
                    .eq(["multistatus", "response", "href"])
                {
                    hrefs += 1;
                }
                if path.last().is_some_and(|p| p == "status") {
                    status.clear();
                }
                if matches!(event, Event::Empty(_)) {
                    if matches!(
                        path.last().map(String::as_str),
                        Some("response" | "href" | "status")
                    ) {
                        return Err(invalid());
                    }
                    path.pop();
                }
            }
            Event::Text(text) => {
                if path
                    .iter()
                    .map(String::as_str)
                    .eq(["multistatus", "response", "href"])
                {
                    href.push_str(text.as_ref());
                }
                if path.last().is_some_and(|p| p == "status") {
                    status.push_str(text.as_ref());
                }
            }
            Event::End(_) => {
                if path.iter().map(String::as_str).eq([
                    "multistatus",
                    "response",
                    "propstat",
                    "status",
                ]) || path
                    .iter()
                    .map(String::as_str)
                    .eq(["multistatus", "response", "status"])
                {
                    let mut fields = status.split_whitespace();
                    good_status |= fields.next().is_some_and(|s| s.starts_with("HTTP/"))
                        && fields.next() == Some("200");
                }
                if path
                    .iter()
                    .map(String::as_str)
                    .eq(["multistatus", "response"])
                {
                    if hrefs != 1 || !good_status || href.contains(['%', '?', '#', '\\']) {
                        return Err(invalid());
                    }
                    let url = if href.starts_with('/') && !href.starts_with("//") {
                        directory.join(&href).map_err(|_| invalid())?
                    } else {
                        reqwest::Url::parse(&href).map_err(|_| invalid())?
                    };
                    if url.origin() != directory.origin()
                        || !url.username().is_empty()
                        || url.password().is_some()
                        || url.query().is_some()
                        || url.fragment().is_some()
                        || href.split('/').any(|part| matches!(part, "." | ".."))
                    {
                        return Err(invalid());
                    }
                    if url.path().trim_end_matches('/') != directory.path().trim_end_matches('/') {
                        let name = url.path().strip_prefix(&prefix).ok_or_else(invalid)?;
                        if name.contains('/') || !valid_file_name(name) {
                            return Err(invalid());
                        }
                        let id = name
                            .strip_suffix(".bill.enc")
                            .or_else(|| name.strip_suffix(".receipt.enc"))
                            .ok_or_else(invalid)?;
                        if id.len() != 64
                            || !id
                                .bytes()
                                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
                            || !names.insert(name.to_string())
                        {
                            return Err(invalid());
                        }
                    }
                }
                if path.pop().is_none() {
                    return Err(invalid());
                }
            }
            Event::DocType(_) | Event::CData(_) | Event::GeneralRef(_) => return Err(invalid()),
            Event::Eof => {
                if roots != 1 || !path.is_empty() {
                    return Err(invalid());
                }
                return Ok(names.into_iter().collect());
            }
            _ => {}
        }
    }
}
