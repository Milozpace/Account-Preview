use serde::Serialize;
#[cfg(any(windows, target_os = "macos", test))]
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};

const MAX_REPORT_BYTES: usize = 4 * 1024 * 1024;

fn validate_report(file_name: &str, content: &str) -> Result<(), String> {
    let month = file_name
        .strip_prefix("消费复盘_")
        .and_then(|name| name.strip_suffix(".md"))
        .ok_or("报告文件名无效")?;
    let bytes = month.as_bytes();
    if bytes.len() != 7
        || bytes[4] != b'-'
        || !bytes[..4].iter().all(u8::is_ascii_digit)
        || !bytes[5..].iter().all(u8::is_ascii_digit)
        || &month[..4] == "0000"
        || !("01"..="12").contains(&&month[5..])
    {
        return Err("报告文件名无效".into());
    }
    if content.len() > MAX_REPORT_BYTES {
        return Err("报告超过 4 MiB 限制".into());
    }
    Ok(())
}

#[cfg(any(windows, target_os = "macos", test))]
fn save_selection(selection: Option<PathBuf>, content: &str) -> Result<bool, String> {
    use std::io::Write;
    let Some(path) = selection else {
        return Ok(false);
    };
    if path
        .extension()
        .and_then(|value| value.to_str())
        .is_none_or(|value| !value.eq_ignore_ascii_case("md"))
    {
        return Err("请选择 Markdown 文件".into());
    }
    // The path comes only from the native chooser. Stage in the same directory
    // so a failed write/sync/atomic replacement preserves any existing report.
    let parent = path.parent().ok_or("报告保存失败")?;
    let mut pending =
        tempfile::NamedTempFile::new_in(parent).map_err(|_| "报告保存失败".to_string())?;
    pending
        .write_all(content.as_bytes())
        .map_err(|_| "报告保存失败".to_string())?;
    pending.flush().map_err(|_| "报告保存失败".to_string())?;
    pending
        .as_file()
        .sync_all()
        .map_err(|_| "报告保存失败".to_string())?;
    pending
        .persist(path)
        .map_err(|_| "报告保存失败".to_string())?;
    Ok(true)
}

struct SaveLease<'a>(&'a AtomicBool);
impl Drop for SaveLease<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}
fn acquire_save(active: &AtomicBool) -> Result<SaveLease<'_>, String> {
    active
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .map_err(|_| "已有保存操作正在进行".to_string())?;
    Ok(SaveLease(active))
}

static SAVE_ACTIVE: AtomicBool = AtomicBool::new(false);

#[derive(Serialize)]
pub struct SaveResult {
    saved: bool,
}

/// A narrow report-only interface: no path, URI, vault, or general filesystem API.
#[tauri::command]
pub async fn analysis_export_save(
    app: tauri::AppHandle,
    file_name: String,
    content: String,
) -> Result<SaveResult, String> {
    validate_report(&file_name, &content)?;
    let _lease = acquire_save(&SAVE_ACTIVE)?;
    #[cfg(windows)]
    {
        let _ = app;
        tauri::async_runtime::spawn_blocking(move || {
            let path = windows_selection(&file_name)?;
            save_selection(path, &content).map(|saved| SaveResult { saved })
        })
        .await
        .map_err(|_| "报告保存失败".to_string())?
    }
    #[cfg(target_os = "macos")]
    {
        let _ = app;
        tauri::async_runtime::spawn_blocking(move || {
            let path = macos_selection(&file_name)?;
            save_selection(path, &content).map(|saved| SaveResult { saved })
        })
        .await
        .map_err(|_| "报告保存失败".to_string())?
    }
    #[cfg(target_os = "android")]
    {
        use tauri_plugin_platform::PlatformExt;
        let saved = app
            .platform()
            .save_markdown_report(&file_name, &content)
            .await
            .map_err(|_| "报告保存失败".to_string())?;
        Ok(SaveResult { saved })
    }
    #[cfg(not(any(windows, target_os = "macos", target_os = "android")))]
    {
        let _ = app;
        Err("当前平台不支持保存报告".into())
    }
}

#[cfg(windows)]
fn windows_selection(file_name: &str) -> Result<Option<PathBuf>, String> {
    use base64::{Engine as _, engine::general_purpose::STANDARD};
    use std::{
        io::Write,
        os::windows::process::CommandExt,
        process::{Command, Stdio},
    };
    // Fixed script. All user data is JSON through stdin, never PowerShell code.
    const SCRIPT: &str = r#"
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = [Windows.Forms.SaveFileDialog]::new()
    try {
        $dialog.Title = '保存消费复盘'
        $dialog.Filter = 'Markdown (*.md)|*.md'
        $dialog.DefaultExt = 'md'
        $dialog.AddExtension = $true
        $dialog.OverwritePrompt = $true
        $dialog.CheckPathExists = $true
        $dialog.FileName = $request.fileName
        $path = $null
        if ($dialog.ShowDialog() -eq [Windows.Forms.DialogResult]::OK) { $path = $dialog.FileName }
        [Console]::Out.Write((@{ path = $path } | ConvertTo-Json -Compress))
    } finally { $dialog.Dispose() }
} catch { exit 1 }
"#;
    let encoded: Vec<u8> = SCRIPT.encode_utf16().flat_map(u16::to_le_bytes).collect();
    let powershell =
        PathBuf::from(std::env::var_os("SystemRoot").unwrap_or_else(|| "C:\\Windows".into()))
            .join("System32/WindowsPowerShell/v1.0/powershell.exe");
    let mut child = Command::new(powershell)
        .args([
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-STA",
            "-EncodedCommand",
            &STANDARD.encode(encoded),
        ])
        .creation_flags(0x0800_0000)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| "无法打开保存窗口".to_string())?;
    let request = serde_json::to_vec(&serde_json::json!({"fileName": file_name}))
        .map_err(|_| "无法打开保存窗口".to_string())?;
    if child
        .stdin
        .take()
        .ok_or("无法打开保存窗口")?
        .write_all(&request)
        .is_err()
    {
        let _ = child.kill();
        let _ = child.wait();
        return Err("无法打开保存窗口".into());
    }
    let output = child
        .wait_with_output()
        .map_err(|_| "保存窗口返回失败".to_string())?;
    if !output.status.success() {
        return Err("保存窗口返回失败".into());
    }
    parse_selection(&output.stdout)
}

#[cfg(target_os = "macos")]
fn macos_selection(file_name: &str) -> Result<Option<PathBuf>, String> {
    use std::{
        io::Write,
        process::{Command, Stdio},
    };
    // Fixed JXA code in argv; all user data arrives as JSON through stdin.
    // Report content never leaves Rust. Native cancellation (-128) is success.
    const SCRIPT: &str = r#"
ObjC.import('Foundation');
const input = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
const text = ObjC.unwrap($.NSString.alloc.initWithDataEncoding(input, $.NSUTF8StringEncoding));
const request = JSON.parse(text);
const app = Application.currentApplication();
app.includeStandardAdditions = true;
let result;
try {
    const chosen = app.chooseFileName({withPrompt: '保存消费复盘', defaultName: request.fileName});
    result = {path: chosen.toString()};
} catch (error) {
    if (error.errorNumber === -128) result = {path: null};
    else throw error;
}
JSON.stringify(result);
"#;
    let mut child = Command::new("/usr/bin/osascript")
        .args(["-l", "JavaScript", "-e", SCRIPT])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| "无法打开保存窗口".to_string())?;
    let request = serde_json::to_vec(&serde_json::json!({"fileName": file_name}))
        .map_err(|_| "无法打开保存窗口".to_string())?;
    if child
        .stdin
        .take()
        .ok_or("无法打开保存窗口")?
        .write_all(&request)
        .is_err()
    {
        let _ = child.kill();
        let _ = child.wait();
        return Err("无法打开保存窗口".into());
    }
    let output = child
        .wait_with_output()
        .map_err(|_| "保存窗口返回失败".to_string())?;
    if !output.status.success() {
        return Err("保存窗口返回失败".into());
    }
    parse_selection(&output.stdout)
}

#[cfg(any(windows, target_os = "macos", test))]
fn parse_selection(bytes: &[u8]) -> Result<Option<PathBuf>, String> {
    #[derive(serde::Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Selection {
        path: Option<PathBuf>,
    }
    serde_json::from_slice::<Selection>(bytes)
        .map(|selection| selection.path)
        .map_err(|_| "保存窗口返回失败".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_selection_json_distinguishes_cancel_from_failure() {
        assert_eq!(parse_selection(br#"{"path":null}"#).unwrap(), None);
        assert_eq!(
            parse_selection(br#"{"path":"/tmp/report.md"}"#).unwrap(),
            Some(PathBuf::from("/tmp/report.md"))
        );
        assert!(parse_selection(b"not JSON").is_err());
        assert!(parse_selection(br#"{"path":false}"#).is_err());
    }

    #[test]
    fn accepts_only_month_report_names_and_utf8_size_boundary() {
        assert!(validate_report("消费复盘_2026-10.md", "# 消费复盘\n金额 ¥12.34").is_ok());
        assert!(validate_report("消费复盘_2026-01.md", &"a".repeat(MAX_REPORT_BYTES)).is_ok());
        assert!(
            validate_report(
                "消费复盘_2026-12.md",
                &"中".repeat(MAX_REPORT_BYTES / 3 + 1)
            )
            .is_err()
        );
        for name in [
            "",
            "report.md",
            "../消费复盘_2026-10.md",
            "C:\\消费复盘_2026-10.md",
            "消费复盘_2026-10.md.exe",
            "消费复盘_2026-00.md",
            "消费复盘_2026-13.md",
            "消费复盘_0000-01.md",
            "消费复盘_2026-10.MD",
            "消费复盘_2026-10.md\0",
        ] {
            assert!(validate_report(name, "text").is_err(), "accepted {name:?}");
        }
    }

    #[test]
    fn cancellation_writes_nothing_and_selected_markdown_preserves_utf8() {
        let directory = tempfile::tempdir().unwrap();
        assert!(!save_selection(None, "private content").unwrap());
        assert_eq!(std::fs::read_dir(directory.path()).unwrap().count(), 0);
        let selected = directory.path().join("复盘.md");
        let content = "# 复盘\n| 项目 | 金额 |\n| 中饭 | 1.00 |\n";
        assert!(save_selection(Some(selected.clone()), content).unwrap());
        assert_eq!(std::fs::read(selected).unwrap(), content.as_bytes());
    }

    #[test]
    fn rejects_non_markdown_selection_and_reports_write_failure() {
        let directory = tempfile::tempdir().unwrap();
        let protected = directory.path().join("account.sqlite3");
        std::fs::write(&protected, b"original").unwrap();
        assert!(save_selection(Some(protected.clone()), "replacement").is_err());
        assert_eq!(std::fs::read(protected).unwrap(), b"original");
        assert!(save_selection(Some(directory.path().join("missing/report.md")), "text").is_err());
    }

    #[test]
    fn replaces_existing_report_without_leaving_temporary_files() {
        let directory = tempfile::tempdir().unwrap();
        let selected = directory.path().join("report.md");
        std::fs::write(&selected, b"old report").unwrap();
        assert!(save_selection(Some(selected.clone()), "# 新复盘\n").unwrap());
        assert_eq!(std::fs::read(selected).unwrap(), "# 新复盘\n".as_bytes());
        assert_eq!(std::fs::read_dir(directory.path()).unwrap().count(), 1);
    }

    #[cfg(windows)]
    #[test]
    fn failed_atomic_replacement_preserves_existing_report_and_cleans_draft() {
        use std::os::windows::fs::OpenOptionsExt;
        let directory = tempfile::tempdir().unwrap();
        let selected = directory.path().join("report.md");
        std::fs::write(&selected, b"original report").unwrap();
        // Permit in-place writes, but deny deletion/renaming: a direct write
        // would truncate the original, while atomic replacement must fail.
        let reader = std::fs::OpenOptions::new()
            .read(true)
            .share_mode(3)
            .open(&selected)
            .unwrap();
        assert!(save_selection(Some(selected.clone()), "replacement report").is_err());
        assert_eq!(std::fs::read(&selected).unwrap(), b"original report");
        assert_eq!(std::fs::read_dir(directory.path()).unwrap().count(), 1);
        drop(reader);
    }

    #[test]
    fn concurrent_save_is_rejected_until_lease_is_released() {
        let active = AtomicBool::new(false);
        let lease = acquire_save(&active).unwrap();
        assert!(acquire_save(&active).is_err());
        drop(lease);
        assert!(acquire_save(&active).is_ok());
    }
}
