//! Separate encrypted raw-bill inbox. Never writes ledger or snapshot state.
#![forbid(unsafe_code)]

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cloud_namespace_inbox_matches_the_installed_product() {
        let root = match crate::cloud_scope::IDENTIFIER {
            "com.account.preview" => "Account-Preview",
            "com.account.probe" => "Account",
            _ => panic!("Unsupported installation identity"),
        };
        let directory = Context::test().directory().unwrap();
        assert!(
            directory.starts_with(&format!("{root}/inbox/")),
            "{directory}"
        );
    }
    #[test]
    fn rejects_unsafe_names_and_accepts_bill_basenames() {
        for name in [
            "../a.csv",
            "a/b.csv",
            "a\\b.csv",
            "CON.csv",
            "a.csv:ads",
            "a.txt",
            "a\n.csv",
            "a..csv",
            "a.csv.",
        ] {
            assert!(safe_name(name).is_err(), "{name:?}");
        }
        assert!(safe_name("微信账单 2026.csv").is_ok());
        assert!(safe_name("账单.XLSX").is_ok());
    }
    #[test]
    fn bill_authentication_binds_identity_hash_and_purpose() {
        let context = Context::test();
        let bill = Bill::new("账单.csv", b"date,amount\n2026,1").unwrap();
        let encrypted = context.seal(&bill, Purpose::Bill).unwrap();
        assert_eq!(
            context.bill(&encrypted, &bill.id).unwrap().content,
            bill.content
        );
        assert!(!String::from_utf8_lossy(&encrypted).contains("账单.csv"));
        assert!(context.bill(&encrypted, &"0".repeat(64)).is_err());
        assert!(context.open::<Bill>(&encrypted, Purpose::Receipt).is_err());
        let mut wrong = Context::test();
        wrong.identity.account_id = "foreign".into();
        assert!(wrong.bill(&encrypted, &bill.id).is_err());
        wrong = Context::test();
        wrong.parent = ContentKey::from_bytes([8; 32]);
        assert!(wrong.bill(&encrypted, &bill.id).is_err());
        assert!(Bill::new("a.csv", &vec![0; MAX_BILL_BYTES + 1]).is_err());
    }
}

use crate::{
    ledger::{self, LedgerIdentity},
    probe_cache::SecretVault,
    webdav::WebDavClient,
};
use account_app_core::{ContentKey, EncryptedEnvelope, SnapshotMetadata, decrypt, encrypt};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{Read, Write},
    path::Path,
};

pub const MAX_BILL_BYTES: usize = 4 * 1024 * 1024;
const MAX_LOCAL_ITEMS: usize = 128;
const ERROR: &str = "账单收件箱验证失败，请保留现场后重试。";
const LOCAL_ERROR: &str = "账单本地加密记录无法保存或读取，请保留现场。";
fn fail() -> String {
    ERROR.into()
}
fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn valid_id(id: &str) -> bool {
    id.len() == 64
        && id
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
fn safe_name(name: &str) -> Result<(), String> {
    let stem = name.split('.').next().unwrap_or("").to_ascii_uppercase();
    let reserved = ["CON", "PRN", "AUX", "NUL"].contains(&stem.as_str())
        || (stem.len() == 4
            && (stem.starts_with("COM") || stem.starts_with("LPT"))
            && matches!(stem.as_bytes()[3], b'1'..=b'9'));
    if name.is_empty()
        || name.len() > 200
        || name.trim() != name
        || name.ends_with('.')
        || name.contains("..")
        || name
            .chars()
            .any(|c| c.is_control() || "<>:\"/\\|?*".contains(c))
        || reserved
        || !(name.to_ascii_lowercase().ends_with(".csv")
            || name.to_ascii_lowercase().ends_with(".xlsx"))
    {
        return Err("请选择不超过 4 MiB 的 CSV 或 XLSX 账单文件。".into());
    }
    Ok(())
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct UploadSummary {
    pub id: String,
    pub file_name: String,
    pub byte_count: usize,
    pub status: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ReceivedBill {
    pub id: String,
    pub file_name: String,
    pub byte_count: usize,
    pub sha256: String,
    pub path: String,
}
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Bill {
    kind: String,
    id: String,
    sha256: String,
    file_name: String,
    byte_count: usize,
    content: String,
}
impl Bill {
    fn new(name: &str, bytes: &[u8]) -> Result<Self, String> {
        safe_name(name)?;
        if bytes.is_empty() || bytes.len() > MAX_BILL_BYTES {
            return Err("请选择不超过 4 MiB 的 CSV 或 XLSX 账单文件。".into());
        }
        let id = digest(bytes);
        Ok(Self {
            kind: "account-bill-v1".into(),
            id: id.clone(),
            sha256: id,
            file_name: name.into(),
            byte_count: bytes.len(),
            content: STANDARD.encode(bytes),
        })
    }
    fn bytes(&self, id: &str) -> Result<zeroize::Zeroizing<Vec<u8>>, String> {
        safe_name(&self.file_name)?;
        if self.kind != "account-bill-v1"
            || !valid_id(id)
            || self.id != id
            || self.sha256 != id
            || self.byte_count == 0
            || self.byte_count > MAX_BILL_BYTES
            || self.content.len() > MAX_BILL_BYTES.div_ceil(3) * 4
        {
            return Err(fail());
        }
        let bytes = zeroize::Zeroizing::new(STANDARD.decode(&self.content).map_err(|_| fail())?);
        if bytes.len() != self.byte_count || digest(&bytes) != id {
            return Err(fail());
        }
        Ok(bytes)
    }
    fn summary(&self, status: &str) -> UploadSummary {
        UploadSummary {
            id: self.id.clone(),
            file_name: self.file_name.clone(),
            byte_count: self.byte_count,
            status: status.into(),
        }
    }
}
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Receipt {
    kind: String,
    id: String,
    sha256: String,
    status: String,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct History {
    kind: String,
    summary: UploadSummary,
}
#[derive(Clone, Copy)]
enum Purpose {
    Bill,
    Receipt,
    History,
}
impl Purpose {
    fn domain(self) -> &'static [u8] {
        match self {
            Self::Bill => b"account.bill-inbox.upload.v1\0",
            Self::Receipt => b"account.bill-inbox.receipt.v1\0",
            Self::History => b"account.bill-inbox.local-history.v1\0",
        }
    }
}
struct Context {
    identity: LedgerIdentity,
    parent: ContentKey,
}
impl Context {
    fn load(vault: &impl SecretVault) -> Result<Self, String> {
        Ok(Self {
            identity: ledger::load_identity(vault)?.ok_or("请先完成正式账本配对。")?,
            parent: ledger::load_key(vault)?.ok_or("请先完成正式账本配对。")?,
        })
    }
    #[cfg(test)]
    fn test() -> Self {
        Self {
            identity: LedgerIdentity {
                ledger_id: "test-ledger".into(),
                account_id: "test-account".into(),
                key_generation: 1,
            },
            parent: ContentKey::from_bytes([7; 32]),
        }
    }
    fn directory(&self) -> Result<String, String> {
        let stable = serde_json::to_vec(&self.identity).map_err(|_| fail())?;
        Ok(format!(
            "{}/{}",
            crate::cloud_scope::INBOX_DIRECTORY,
            &digest(&stable)[..32]
        ))
    }
    fn key(&self, purpose: Purpose) -> ContentKey {
        let mut hasher = Sha256::new();
        hasher.update(purpose.domain());
        hasher.update(self.parent.as_bytes());
        ContentKey::from_bytes(hasher.finalize().into())
    }
    fn seal(&self, value: &impl Serialize, purpose: Purpose) -> Result<Vec<u8>, String> {
        let plain = zeroize::Zeroizing::new(serde_json::to_vec(value).map_err(|_| fail())?);
        encrypt(
            &plain,
            &self.key(purpose),
            SnapshotMetadata {
                format_version: 1,
                ledger_id: self.identity.ledger_id.clone(),
                account_id: self.identity.account_id.clone(),
                key_generation: self.identity.key_generation,
                revision: 1,
            },
        )
        .map_err(|_| fail())?
        .to_json()
        .map_err(|_| fail())
    }
    fn open<T: DeserializeOwned>(&self, bytes: &[u8], purpose: Purpose) -> Result<T, String> {
        let envelope = EncryptedEnvelope::from_json(bytes).map_err(|_| fail())?;
        if envelope.metadata.revision != 1 {
            return Err(fail());
        }
        let plain = zeroize::Zeroizing::new(
            decrypt(&envelope, &self.key(purpose), self.identity.expected()).map_err(|_| fail())?,
        );
        serde_json::from_slice(&plain).map_err(|_| fail())
    }
    fn bill(&self, bytes: &[u8], id: &str) -> Result<Bill, String> {
        let bill: Bill = self.open(bytes, Purpose::Bill)?;
        bill.bytes(id)?;
        Ok(bill)
    }
    fn receipt(&self, bytes: &[u8], id: &str) -> Result<Receipt, String> {
        let receipt: Receipt = self.open(bytes, Purpose::Receipt)?;
        if receipt.kind != "account-bill-receipt-v1"
            || receipt.id != id
            || receipt.sha256 != id
            || !matches!(receipt.status.as_str(), "received" | "recorded")
        {
            return Err(fail());
        }
        Ok(receipt)
    }
    fn client(&self, vault: &impl SecretVault) -> Result<WebDavClient, String> {
        let (base, user, password) = crate::probe_sync::load_dav_credentials(vault)?;
        WebDavClient::new(&base, &self.directory()?, &user, &password)
    }
}

#[cfg(test)]
mod transport_tests {
    use super::*;
    use std::{cell::RefCell, collections::BTreeMap};
    #[derive(Default)]
    struct MemoryDav {
        files: RefCell<BTreeMap<String, Vec<u8>>>,
        writes: RefCell<usize>,
        fail_put: RefCell<bool>,
    }
    impl Remote for MemoryDav {
        fn get(&self, name: &str) -> Result<Option<Vec<u8>>, String> {
            Ok(self.files.borrow().get(name).cloned())
        }
        fn put(&self, name: &str, body: &[u8]) -> Result<(), String> {
            if *self.fail_put.borrow() {
                return Err("offline".into());
            }
            *self.writes.borrow_mut() += 1;
            self.files.borrow_mut().insert(name.into(), body.into());
            Ok(())
        }
        fn ensure(&self) -> Result<(), String> {
            Ok(())
        }
        fn list(&self) -> Result<Vec<String>, String> {
            Ok(self.files.borrow().keys().cloned().collect())
        }
    }
    #[test]
    fn confirmed_recorded_history_requires_no_more_remote_checks_or_deletion() {
        let ctx = Context::test();
        let dir = tempfile::tempdir().unwrap();
        let initial = MemoryDav::default();
        let summary = upload_to(&ctx, &initial, dir.path(), "账单.csv", b"a,b\n1,2").unwrap();
        acknowledge_to(&ctx, &initial, &summary.id, "recorded").unwrap();
        assert_eq!(
            refresh_to(&ctx, &initial, dir.path()).unwrap()[0].status,
            "recorded"
        );
        struct Offline;
        impl Remote for Offline {
            fn get(&self, _: &str) -> Result<Option<Vec<u8>>, String> {
                Err("unexpected remote request".into())
            }
            fn put(&self, _: &str, _: &[u8]) -> Result<(), String> {
                Err("unexpected write".into())
            }
            fn ensure(&self) -> Result<(), String> {
                Err("unexpected collection".into())
            }
            fn list(&self) -> Result<Vec<String>, String> {
                Err("unexpected listing".into())
            }
        }
        assert_eq!(
            refresh_to(&ctx, &Offline, dir.path()).unwrap()[0].status,
            "recorded"
        );
        assert!(stage_path(dir.path(), &summary.id).is_file());
        assert!(history_path(dir.path(), &summary.id).is_file());
    }
    #[test]
    fn pending_survives_failure_and_duplicate_retry_reuses_ciphertext() {
        let ctx = Context::test();
        let remote = MemoryDav::default();
        let dir = tempfile::tempdir().unwrap();
        *remote.fail_put.borrow_mut() = true;
        assert!(upload_to(&ctx, &remote, dir.path(), "账单.csv", b"a,b\n1,2").is_err());
        let pending = local_status(&ctx, dir.path()).unwrap();
        assert_eq!(pending[0].status, "pending");
        let staged = fs::read(stage_path(dir.path(), &pending[0].id)).unwrap();
        assert!(!String::from_utf8_lossy(&staged).contains("a,b"));
        *remote.fail_put.borrow_mut() = false;
        assert_eq!(
            retry_to(&ctx, &remote, dir.path(), &pending[0].id)
                .unwrap()
                .status,
            "uploaded"
        );
        retry_to(&ctx, &remote, dir.path(), &pending[0].id).unwrap();
        assert_eq!(*remote.writes.borrow(), 1);
        assert_eq!(remote.files.borrow().values().next().unwrap(), &staged);
        fs::remove_file(history_path(dir.path(), &pending[0].id)).unwrap();
        assert_eq!(local_status(&ctx, dir.path()).unwrap()[0].status, "pending");
        assert_eq!(
            retry_to(&ctx, &remote, dir.path(), &pending[0].id)
                .unwrap()
                .status,
            "uploaded"
        );
    }
    #[test]
    fn receipts_authenticate_recorded_and_never_downgrade() {
        let ctx = Context::test();
        let remote = MemoryDav::default();
        let dir = tempfile::tempdir().unwrap();
        let summary = upload_to(&ctx, &remote, dir.path(), "a.csv", b"a,b").unwrap();
        let unconfirmed = tempfile::tempdir().unwrap();
        upload_to(&ctx, &remote, unconfirmed.path(), "a.csv", b"a,b").unwrap();
        acknowledge_to(&ctx, &remote, &summary.id, "recorded").unwrap();
        acknowledge_to(&ctx, &remote, &summary.id, "received").unwrap();
        assert_eq!(
            refresh_to(&ctx, &remote, dir.path()).unwrap()[0].status,
            "recorded"
        );
        assert!(receive_from(&ctx, &remote, dir.path()).unwrap().is_empty());
        remote
            .files
            .borrow_mut()
            .insert(format!("{}.receipt.enc", summary.id), b"garbage".to_vec());
        // Previously confirmed history remains valid offline, but a new or
        // unconfirmed local item must still authenticate its first completion.
        assert_eq!(
            refresh_to(&ctx, &remote, dir.path()).unwrap()[0].status,
            "recorded"
        );
        assert!(refresh_to(&ctx, &remote, unconfirmed.path()).is_err());
        assert_eq!(
            local_status(&ctx, unconfirmed.path()).unwrap()[0].status,
            "uploaded"
        );
    }
    #[test]
    fn corrupt_staged_remote_repaired_but_foreign_valid_identity_never_overwritten() {
        let ctx = Context::test();
        let remote = MemoryDav::default();
        let dir = tempfile::tempdir().unwrap();
        let summary = upload_to(&ctx, &remote, dir.path(), "a.csv", b"a,b").unwrap();
        let name = format!("{}.bill.enc", summary.id);
        remote
            .files
            .borrow_mut()
            .insert(name.clone(), b"broken".to_vec());
        retry_to(&ctx, &remote, dir.path(), &summary.id).unwrap();
        let mut foreign = Context::test();
        foreign.identity.account_id = "foreign".into();
        let bill = Bill::new("a.csv", b"a,b").unwrap();
        let encrypted = foreign.seal(&bill, Purpose::Bill).unwrap();
        remote
            .files
            .borrow_mut()
            .insert(name.clone(), encrypted.clone());
        assert!(retry_to(&ctx, &remote, dir.path(), &summary.id).is_err());
        assert_eq!(remote.files.borrow()[&name], encrypted);
    }
    #[test]
    fn receive_exclusive_output_and_reuse_without_overwriting() {
        let ctx = Context::test();
        let remote = MemoryDav::default();
        let stage = tempfile::tempdir().unwrap();
        let output = tempfile::tempdir().unwrap();
        let summary = upload_to(&ctx, &remote, stage.path(), "a.csv", b"a,b").unwrap();
        let files = receive_from(&ctx, &remote, output.path()).unwrap();
        assert_eq!(files.len(), 1);
        assert_eq!(fs::read(&files[0].path).unwrap(), b"a,b");
        assert_eq!(
            receive_from(&ctx, &remote, output.path()).unwrap()[0].path,
            files[0].path
        );
        fs::write(&files[0].path, b"protected").unwrap();
        assert!(receive_from(&ctx, &remote, output.path()).is_err());
        assert_eq!(fs::read(&files[0].path).unwrap(), b"protected");
        assert!(acknowledge_to(&ctx, &remote, &summary.id, "uploaded").is_err());
    }
}

trait Remote {
    fn get(&self, name: &str) -> Result<Option<Vec<u8>>, String>;
    fn put(&self, name: &str, bytes: &[u8]) -> Result<(), String>;
    fn ensure(&self) -> Result<(), String>;
    fn list(&self) -> Result<Vec<String>, String>;
}
impl Remote for WebDavClient {
    fn get(&self, n: &str) -> Result<Option<Vec<u8>>, String> {
        WebDavClient::get(self, n).map_err(String::from)
    }
    fn put(&self, n: &str, b: &[u8]) -> Result<(), String> {
        WebDavClient::put(self, n, b).map_err(String::from)
    }
    fn ensure(&self) -> Result<(), String> {
        self.ensure_collection().map_err(String::from)
    }
    fn list(&self) -> Result<Vec<String>, String> {
        self.list_inbox().map_err(String::from)
    }
}
fn stage_path(dir: &Path, id: &str) -> std::path::PathBuf {
    dir.join(format!("{id}.pending.enc"))
}
fn history_path(dir: &Path, id: &str) -> std::path::PathBuf {
    dir.join(format!("{id}.history.enc"))
}
fn check_dir(dir: &Path) -> Result<(), String> {
    fs::create_dir_all(dir).map_err(|_| LOCAL_ERROR)?;
    let metadata = fs::symlink_metadata(dir).map_err(|_| LOCAL_ERROR)?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return Err(LOCAL_ERROR.into());
    }
    Ok(())
}
fn read_local(path: &Path) -> Result<Vec<u8>, String> {
    let metadata = fs::symlink_metadata(path).map_err(|_| LOCAL_ERROR)?;
    if !metadata.is_file()
        || metadata.file_type().is_symlink()
        || metadata.len() > account_app_core::MAX_ENVELOPE_BYTES as u64
    {
        return Err(LOCAL_ERROR.into());
    }
    let mut bytes = Vec::new();
    fs::File::open(path)
        .map_err(|_| LOCAL_ERROR)?
        .take((account_app_core::MAX_ENVELOPE_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| LOCAL_ERROR)?;
    if bytes.len() > account_app_core::MAX_ENVELOPE_BYTES {
        return Err(LOCAL_ERROR.into());
    }
    Ok(bytes)
}
fn atomic_save(path: &Path, bytes: &[u8], exclusive: bool) -> Result<(), String> {
    let parent = path.parent().ok_or(LOCAL_ERROR)?;
    check_dir(parent)?;
    if let Ok(metadata) = fs::symlink_metadata(path)
        && (metadata.file_type().is_symlink() || !metadata.is_file())
    {
        return Err(LOCAL_ERROR.into());
    }
    let mut file = tempfile::NamedTempFile::new_in(parent).map_err(|_| LOCAL_ERROR)?;
    file.write_all(bytes)
        .and_then(|_| file.as_file().sync_all())
        .map_err(|_| LOCAL_ERROR)?;
    if exclusive {
        file.persist_noclobber(path).map_err(|_| LOCAL_ERROR)?;
    } else {
        file.persist(path).map_err(|_| LOCAL_ERROR)?;
    }
    Ok(())
}
fn save_history(ctx: &Context, dir: &Path, summary: &UploadSummary) -> Result<(), String> {
    let encrypted = ctx.seal(
        &History {
            kind: "account-bill-history-v1".into(),
            summary: summary.clone(),
        },
        Purpose::History,
    )?;
    atomic_save(&history_path(dir, &summary.id), &encrypted, false)
}
fn load_summary(ctx: &Context, dir: &Path, bill: &Bill) -> Result<UploadSummary, String> {
    let path = history_path(dir, &bill.id);
    if !path.try_exists().map_err(|_| LOCAL_ERROR)? {
        return Ok(bill.summary("pending"));
    }
    let history: History = ctx.open(&read_local(&path)?, Purpose::History)?;
    if history.kind != "account-bill-history-v1"
        || history.summary.id != bill.id
        || history.summary.file_name != bill.file_name
        || history.summary.byte_count != bill.byte_count
        || !matches!(
            history.summary.status.as_str(),
            "pending" | "uploaded" | "received" | "recorded"
        )
    {
        return Err(fail());
    }
    Ok(history.summary)
}
fn local_status(ctx: &Context, dir: &Path) -> Result<Vec<UploadSummary>, String> {
    if !dir.try_exists().map_err(|_| LOCAL_ERROR)? {
        return Ok(vec![]);
    }
    check_dir(dir)?;
    let mut result = Vec::new();
    let mut seen = 0;
    for entry in fs::read_dir(dir).map_err(|_| LOCAL_ERROR)? {
        let entry = entry.map_err(|_| LOCAL_ERROR)?;
        seen += 1;
        if seen > MAX_LOCAL_ITEMS * 3 + 16 {
            return Err("本地账单记录已达到上限，请保留现场检查。".into());
        }
        let name = entry.file_name();
        let name = name.to_str().ok_or(LOCAL_ERROR)?;
        let Some(id) = name.strip_suffix(".pending.enc") else {
            continue;
        };
        if !valid_id(id) {
            return Err(fail());
        }
        if result.len() >= MAX_LOCAL_ITEMS {
            return Err("本地账单记录已达到上限。".into());
        }
        let bill = ctx.bill(&read_local(&entry.path())?, id)?;
        result.push(load_summary(ctx, dir, &bill)?);
    }
    result.sort_by(|a, b| a.id.cmp(&b.id));
    Ok(result)
}
fn upload_to(
    ctx: &Context,
    remote: &impl Remote,
    dir: &Path,
    name: &str,
    bytes: &[u8],
) -> Result<UploadSummary, String> {
    let bill = Bill::new(name, bytes)?;
    check_dir(dir)?;
    let existing = local_status(ctx, dir)?;
    let path = stage_path(dir, &bill.id);
    if path.try_exists().map_err(|_| LOCAL_ERROR)? {
        let prior = ctx.bill(&read_local(&path)?, &bill.id)?;
        if prior.bytes(&bill.id)?.as_slice() != bytes {
            return Err(fail());
        }
    } else {
        if existing.len() >= MAX_LOCAL_ITEMS {
            return Err("本地账单记录已达到上限。".into());
        }
        atomic_save(&path, &ctx.seal(&bill, Purpose::Bill)?, true)?;
        save_history(ctx, dir, &bill.summary("pending"))?;
    }
    retry_to(ctx, remote, dir, &bill.id)
}
fn retry_to(
    ctx: &Context,
    remote: &impl Remote,
    dir: &Path,
    id: &str,
) -> Result<UploadSummary, String> {
    if !valid_id(id) {
        return Err(fail());
    }
    let staged = read_local(&stage_path(dir, id))?;
    let bill = ctx.bill(&staged, id)?;
    let mut summary = load_summary(ctx, dir, &bill)?;
    remote.ensure()?;
    let name = format!("{id}.bill.enc");
    let mut present = false;
    let existing = remote.get(&name)?;
    let parsed = existing.as_deref().map(EncryptedEnvelope::from_json);
    if summary.status == "pending" && parsed.as_ref().is_some_and(Result::is_err) {
        return Err(fail());
    }
    if let Some(existing) = existing
        && let Some(Ok(envelope)) = parsed
    {
        let m = &envelope.metadata;
        if m.ledger_id != ctx.identity.ledger_id
            || m.account_id != ctx.identity.account_id
            || m.key_generation != ctx.identity.key_generation
        {
            return Err(fail());
        }
        // A valid envelope from another channel is misplaced data, never
        // a damaged bill. Preserve it even at an already confirmed path.
        let other_purpose = [Purpose::Receipt, Purpose::History]
            .into_iter()
            .any(|purpose| {
                decrypt(&envelope, &ctx.key(purpose), ctx.identity.expected())
                    .map(zeroize::Zeroizing::new)
                    .is_ok()
            });
        if other_purpose
            || decrypt(&envelope, &ctx.parent, ctx.identity.expected())
                .map(zeroize::Zeroizing::new)
                .is_ok()
        {
            return Err(fail());
        }
        match ctx.open::<Bill>(&existing, Purpose::Bill) {
            Ok(prior) => {
                if prior.bytes(id)?.as_slice() != bill.bytes(id)?.as_slice() {
                    return Err(fail());
                }
                present = true;
            }
            Err(_) if summary.status == "pending" => return Err(fail()),
            Err(_) => {} // Previously confirmed own path: retry persisted bytes.
        }
    }
    if !present {
        remote.put(&name, &staged)?;
        let back = remote.get(&name)?.ok_or_else(fail)?;
        if back != staged {
            return Err(fail());
        }
        ctx.bill(&back, id)?;
    }
    if summary.status == "pending" {
        summary.status = "uploaded".into();
    }
    if let Some(receipt) = remote.get(&format!("{id}.receipt.enc"))? {
        let receipt = ctx.receipt(&receipt, id)?;
        if summary.status != "recorded" {
            summary.status = receipt.status;
        }
    }
    save_history(ctx, dir, &summary)?;
    Ok(summary)
}
fn refresh_to(
    ctx: &Context,
    remote: &impl Remote,
    dir: &Path,
) -> Result<Vec<UploadSummary>, String> {
    let mut summaries = local_status(ctx, dir)?;
    for summary in &mut summaries {
        // This state was already authenticated and durably saved. Keep it as
        // deduplication evidence without polling its receipt forever.
        if summary.status == "recorded" {
            continue;
        }
        if let Some(receipt) = remote.get(&format!("{}.receipt.enc", summary.id))? {
            let receipt = ctx.receipt(&receipt, &summary.id)?;
            if summary.status != "recorded" {
                summary.status = receipt.status;
            }
            save_history(ctx, dir, summary)?;
        }
    }
    Ok(summaries)
}
fn acknowledge_to(
    ctx: &Context,
    remote: &impl Remote,
    id: &str,
    status: &str,
) -> Result<(), String> {
    if !valid_id(id) || !matches!(status, "received" | "recorded") {
        return Err(fail());
    }
    let raw = remote.get(&format!("{id}.bill.enc"))?.ok_or_else(fail)?;
    ctx.bill(&raw, id)?;
    let name = format!("{id}.receipt.enc");
    if let Some(previous) = remote.get(&name)? {
        let receipt = ctx.receipt(&previous, id)?;
        if receipt.status == "recorded" || receipt.status == status {
            return Ok(());
        }
    }
    let encrypted = ctx.seal(
        &Receipt {
            kind: "account-bill-receipt-v1".into(),
            id: id.into(),
            sha256: id.into(),
            status: status.into(),
        },
        Purpose::Receipt,
    )?;
    remote.put(&name, &encrypted)?;
    let restored = remote.get(&name)?.ok_or_else(fail)?;
    let receipt = ctx.receipt(&restored, id)?;
    if receipt.status != status || restored != encrypted {
        return Err(fail());
    }
    Ok(())
}
fn receive_from(
    ctx: &Context,
    remote: &impl Remote,
    output: &Path,
) -> Result<Vec<ReceivedBill>, String> {
    receive_named(ctx, remote, output, remote.list()?)
}
fn receive_named(
    ctx: &Context,
    remote: &impl Remote,
    output: &Path,
    names: Vec<String>,
) -> Result<Vec<ReceivedBill>, String> {
    if !output.is_absolute() {
        return Err("收取目录必须是绝对路径。".into());
    }
    check_dir(output)?;
    if names.len() >= 750 {
        return Err("云端收件箱已达到目录列举上限，请分批检查。".into());
    }
    let mut files = Vec::new();
    for name in names {
        let Some(id) = name.strip_suffix(".bill.enc") else {
            continue;
        };
        if !valid_id(id) {
            return Err(fail());
        }
        if let Some(receipt) = remote.get(&format!("{id}.receipt.enc"))?
            && ctx.receipt(&receipt, id)?.status == "recorded"
        {
            continue;
        }
        let raw = remote.get(&name)?.ok_or_else(fail)?;
        let bill = ctx.bill(&raw, id)?;
        let bytes = bill.bytes(id)?;
        let ext = if bill.file_name.to_ascii_lowercase().ends_with(".csv") {
            "csv"
        } else {
            "xlsx"
        };
        let destination = output.join(format!("bill-{id}.{ext}"));
        if destination.try_exists().map_err(|_| LOCAL_ERROR)? {
            let previous = zeroize::Zeroizing::new(read_local(&destination)?);
            if previous.as_slice() != bytes.as_slice() {
                return Err("收取目录已有不同内容的同名文件，已停止并保留原文件。".into());
            }
        } else {
            atomic_save(&destination, &bytes, true)?;
        }
        files.push(ReceivedBill {
            id: id.into(),
            file_name: bill.file_name.clone(),
            byte_count: bill.byte_count,
            sha256: id.into(),
            path: destination.to_str().ok_or(LOCAL_ERROR)?.into(),
        });
    }
    Ok(files)
}
pub fn upload(
    vault: &impl SecretVault,
    local_dir: &Path,
    name: &str,
    bytes: &[u8],
) -> Result<UploadSummary, String> {
    let ctx = Context::load(vault)?;
    let remote = ctx.client(vault)?;
    upload_to(&ctx, &remote, &local_dir.join("bill-inbox"), name, bytes)
}
pub fn retry(
    vault: &impl SecretVault,
    local_dir: &Path,
    id: &str,
) -> Result<UploadSummary, String> {
    let ctx = Context::load(vault)?;
    let remote = ctx.client(vault)?;
    retry_to(&ctx, &remote, &local_dir.join("bill-inbox"), id)
}
pub fn status(
    vault: &impl SecretVault,
    local_dir: &Path,
    refresh: bool,
) -> Result<Vec<UploadSummary>, String> {
    let ctx = Context::load(vault)?;
    let dir = local_dir.join("bill-inbox");
    if refresh {
        let remote = ctx.client(vault)?;
        refresh_to(&ctx, &remote, &dir)
    } else {
        local_status(&ctx, &dir)
    }
}
pub fn receive(vault: &impl SecretVault, output_dir: &Path) -> Result<Vec<ReceivedBill>, String> {
    let ctx = Context::load(vault)?;
    let remote = ctx.client(vault)?;
    receive_from(&ctx, &remote, output_dir)
}
pub fn acknowledge(vault: &impl SecretVault, id: &str, status: &str) -> Result<(), String> {
    let ctx = Context::load(vault)?;
    let remote = ctx.client(vault)?;
    acknowledge_to(&ctx, &remote, id, status)
}

#[cfg(test)]
mod retry_safety_tests {
    use super::*;
    struct Existing(Vec<u8>, std::cell::Cell<bool>);
    impl Remote for Existing {
        fn get(&self, _: &str) -> Result<Option<Vec<u8>>, String> {
            Ok(Some(self.0.clone()))
        }
        fn put(&self, _: &str, _: &[u8]) -> Result<(), String> {
            self.1.set(true);
            Ok(())
        }
        fn ensure(&self) -> Result<(), String> {
            Ok(())
        }
        fn list(&self) -> Result<Vec<String>, String> {
            Ok(vec![])
        }
    }
    #[test]
    fn pending_wrong_key_cannot_overwrite_same_identity_envelope() {
        let ctx = Context::test();
        let mut foreign = Context::test();
        foreign.parent = ContentKey::from_bytes([42; 32]);
        let bill = Bill::new("a.csv", b"a,b").unwrap();
        let dir = tempfile::tempdir().unwrap();
        atomic_save(
            &stage_path(dir.path(), &bill.id),
            &ctx.seal(&bill, Purpose::Bill).unwrap(),
            true,
        )
        .unwrap();
        let remote = Existing(
            foreign.seal(&bill, Purpose::Bill).unwrap(),
            std::cell::Cell::new(false),
        );
        assert!(retry_to(&ctx, &remote, dir.path(), &bill.id).is_err());
        assert!(!remote.1.get());
    }
    #[test]
    fn authenticated_different_bill_at_path_is_not_corruption_to_repair() {
        let ctx = Context::test();
        let bill = Bill::new("a.csv", b"a,b").unwrap();
        let other = Bill::new("a.csv", b"different").unwrap();
        let dir = tempfile::tempdir().unwrap();
        atomic_save(
            &stage_path(dir.path(), &bill.id),
            &ctx.seal(&bill, Purpose::Bill).unwrap(),
            true,
        )
        .unwrap();
        save_history(&ctx, dir.path(), &bill.summary("uploaded")).unwrap();
        let remote = Existing(
            ctx.seal(&other, Purpose::Bill).unwrap(),
            std::cell::Cell::new(false),
        );
        assert!(retry_to(&ctx, &remote, dir.path(), &bill.id).is_err());
        assert!(!remote.1.get());
    }
}

#[cfg(test)]
mod memory_hygiene_tests {
    use super::*;
    #[test]
    fn decrypted_bill_base64_is_zeroized_on_drop() {
        let mut bill = Bill::new("a.csv", b"private raw bytes").unwrap();
        assert!(!bill.content.is_empty());
        bill.erase_content();
        assert!(bill.content.is_empty());
    }
}

impl Bill {
    fn erase_content(&mut self) {
        use zeroize::Zeroize;
        self.content.zeroize();
    }
}
impl Drop for Bill {
    fn drop(&mut self) {
        self.erase_content();
    }
}

#[cfg(test)]
mod cross_purpose_tests {
    use super::*;
    struct Dav {
        body: Vec<u8>,
        overwritten: std::cell::Cell<bool>,
    }
    impl Remote for Dav {
        fn get(&self, _: &str) -> Result<Option<Vec<u8>>, String> {
            Ok(Some(self.body.clone()))
        }
        fn put(&self, _: &str, _: &[u8]) -> Result<(), String> {
            self.overwritten.set(true);
            Ok(())
        }
        fn ensure(&self) -> Result<(), String> {
            Ok(())
        }
        fn list(&self) -> Result<Vec<String>, String> {
            Ok(vec![])
        }
    }
    #[test]
    fn valid_other_purpose_envelope_is_never_repaired_as_bill_corruption() {
        let ctx = Context::test();
        let bill = Bill::new("a.csv", b"a,b").unwrap();
        let dir = tempfile::tempdir().unwrap();
        atomic_save(
            &stage_path(dir.path(), &bill.id),
            &ctx.seal(&bill, Purpose::Bill).unwrap(),
            true,
        )
        .unwrap();
        save_history(&ctx, dir.path(), &bill.summary("uploaded")).unwrap();
        for purpose in [Purpose::Receipt, Purpose::History] {
            let remote = Dav {
                body: ctx
                    .seal(&serde_json::json!({"other":"purpose"}), purpose)
                    .unwrap(),
                overwritten: std::cell::Cell::new(false),
            };
            assert!(retry_to(&ctx, &remote, dir.path(), &bill.id).is_err());
            assert!(!remote.overwritten.get());
        }
    }
}

#[cfg(test)]
mod page_tests {
    use super::*;
    struct Dav {
        files: std::cell::RefCell<std::collections::BTreeMap<String, Vec<u8>>>,
        writes: std::cell::Cell<usize>,
    }
    impl Remote for Dav {
        fn get(&self, n: &str) -> Result<Option<Vec<u8>>, String> {
            Ok(self.files.borrow().get(n).cloned())
        }
        fn put(&self, n: &str, b: &[u8]) -> Result<(), String> {
            self.writes.set(self.writes.get() + 1);
            self.files.borrow_mut().insert(n.into(), b.into());
            Ok(())
        }
        fn ensure(&self) -> Result<(), String> {
            Ok(())
        }
        fn list(&self) -> Result<Vec<String>, String> {
            Ok(self.files.borrow().keys().cloned().collect())
        }
    }
    fn dav() -> Dav {
        Dav {
            files: Default::default(),
            writes: Default::default(),
        }
    }
    #[test]
    fn pending_cannot_repair_unknown_unparseable_cloud_body() {
        let ctx = Context::test();
        let dir = tempfile::tempdir().unwrap();
        let bill = Bill::new("a.csv", b"a,b").unwrap();
        atomic_save(
            &stage_path(dir.path(), &bill.id),
            &ctx.seal(&bill, Purpose::Bill).unwrap(),
            true,
        )
        .unwrap();
        let remote = dav();
        remote.files.borrow_mut().insert(
            format!("{}.bill.enc", bill.id),
            b"unknown damaged bytes".to_vec(),
        );
        assert!(retry_to(&ctx, &remote, dir.path(), &bill.id).is_err());
        assert_eq!(remote.writes.get(), 0);
        assert_eq!(local_status(&ctx, dir.path()).unwrap()[0].status, "pending");
    }
    #[test]
    fn paged_receive_advances_past_recorded_and_resumes_after_interruption() {
        let ctx = Context::test();
        let remote = dav();
        let output = tempfile::tempdir().unwrap();
        let mut bills = vec![
            Bill::new("a.csv", b"one").unwrap(),
            Bill::new("b.csv", b"two").unwrap(),
        ];
        bills.sort_by(|a, b| a.id.cmp(&b.id));
        for bill in &bills {
            remote.files.borrow_mut().insert(
                format!("{}.bill.enc", bill.id),
                ctx.seal(bill, Purpose::Bill).unwrap(),
            );
        }
        acknowledge_to(&ctx, &remote, &bills[0].id, "recorded").unwrap();
        let first = receive_page_from(&ctx, &remote, output.path(), None).unwrap();
        assert!(first.files.is_empty());
        assert_eq!(first.next_cursor.as_deref(), Some(bills[0].id.as_str()));
        let second =
            receive_page_from(&ctx, &remote, output.path(), first.next_cursor.as_deref()).unwrap();
        assert_eq!(second.files.len(), 1);
        assert_eq!(second.files[0].id, bills[1].id);
        let replay =
            receive_page_from(&ctx, &remote, output.path(), first.next_cursor.as_deref()).unwrap();
        assert_eq!(replay.files[0].path, second.files[0].path);
        assert!(
            receive_page_from(&ctx, &remote, output.path(), second.next_cursor.as_deref())
                .unwrap()
                .next_cursor
                .is_none()
        );
        assert!(receive_page_from(&ctx, &remote, output.path(), Some("../bad")).is_err());
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReceivePage {
    pub files: Vec<ReceivedBill>,
    pub next_cursor: Option<String>,
}
fn receive_page_from(
    ctx: &Context,
    remote: &impl Remote,
    output: &Path,
    after_id: Option<&str>,
) -> Result<ReceivePage, String> {
    if after_id.is_some_and(|id| !valid_id(id)) || !output.is_absolute() {
        return Err(fail());
    }
    let names = remote.list()?;
    if names.len() >= 750 {
        return Err("云端收件箱已达到目录列举上限，请分批检查。".into());
    }
    let mut candidates = Vec::new();
    for name in names {
        let Some(id) = name.strip_suffix(".bill.enc") else {
            continue;
        };
        if !valid_id(id) {
            return Err(fail());
        }
        if after_id.is_none_or(|after| id > after) {
            candidates.push((id.to_string(), name));
        }
    }
    candidates.sort_by(|a, b| a.0.cmp(&b.0));
    let Some((id, name)) = candidates.into_iter().next() else {
        return Ok(ReceivePage {
            files: vec![],
            next_cursor: None,
        });
    };
    Ok(ReceivePage {
        files: receive_named(ctx, remote, output, vec![name])?,
        next_cursor: Some(id),
    })
}
pub fn receive_page(
    vault: &impl SecretVault,
    output_dir: &Path,
    after_id: Option<&str>,
) -> Result<ReceivePage, String> {
    if after_id.is_some_and(|id| !valid_id(id)) || !output_dir.is_absolute() {
        return Err(fail());
    }
    let ctx = Context::load(vault)?;
    let remote = ctx.client(vault)?;
    receive_page_from(&ctx, &remote, output_dir, after_id)
}
