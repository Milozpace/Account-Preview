//! Business ledger sync: pending preparation, two-slot publishing, mobile
//! fetch, minimal pairing, and status. Completely separate durable state
//! from the synthetic probe (own slots, files, cloud directory, identity).
//!
//! Protocol invariants carried over from the validated dual-slot probe:
//! write only the non-current slot, confirm by full readback, persist the
//! pending intent durably, retry with the exact same ciphertext, never
//! overwrite an unreadable or foreign cloud file, and never claim a local
//! cache is a published cloud version.
#![forbid(unsafe_code)]

use crate::ledger::{
    self, LEDGER_CACHE_FILE, LEDGER_FILES, LEDGER_INTENT_FILE, LEDGER_PENDING_FILE,
    LEDGER_REMOTE_DIRECTORY, LEDGER_STATE_FILE, LedgerIdentity,
};
use crate::probe_cache::SecretVault;
use crate::probe_sync::{Transaction, step_err};
use crate::sync_issue::SyncIssue;
use account_app_core::{
    ContentKey, EncryptedEnvelope, RevisionGuard, SnapshotMetadata, decrypt, decrypt_and_advance,
    encrypt,
};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde::Serialize;
use std::{fs, io::Write, path::Path, sync::Arc};
use tauri::Manager;
use tauri_plugin_platform::PlatformExt;

const STEP_CONFIG: &str = "配置/安全存储";
const STEP_KEY_CACHE: &str = "本机密钥与缓存读取";
const STEP_PAYLOAD: &str = "账单快照校验";
const STEP_PENDING: &str = "待发准备";
const STEP_DIRECTORY: &str = "云端目录建立/确认";
const STEP_OWNERSHIP: &str = "云端正式文件归属检查";
const STEP_OFFICIAL_READBACK: &str = "正式路径回读确认";
const STEP_LOCAL_SAVE: &str = "本地保存";
const STEP_CONTRACT: &str = "认证/契约/版本检查";

/// Serializes ledger network commands independently of the probe ones.
#[derive(Default)]
pub struct LedgerState {
    pub network: Arc<tauri::async_runtime::Mutex<()>>,
}

#[derive(Debug, Serialize, PartialEq, Eq, Clone)]
#[serde(rename_all = "camelCase")]
pub struct LedgerStatusSummary {
    pub initialized: bool,
    /// Real cloud publishing stays disabled until explicitly enabled in the
    /// native secure store; the UI must show this honestly.
    pub publish_enabled: bool,
    pub cached_revision: Option<u64>,
    pub pending_revision: Option<u64>,
    pub last_published_revision: Option<u64>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LedgerPrepareOutcome {
    pub revision: u64,
    pub reused: bool,
    /// True when the payload is identical to the last confirmed publish.
    pub already_published: bool,
    /// True when an outdated staged pending (plus its slot intent) was
    /// cancelled because the latest input equals the last published content.
    pub stale_pending_cancelled: bool,
    pub months: Vec<String>,
    pub exported_records: usize,
    pub payload_bytes: usize,
    pub dry_run: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LedgerPublishOutcome {
    pub revision: u64,
    pub method: &'static str,
    pub reused: bool,
}

#[derive(Debug, Serialize)]
#[serde(tag = "kind", rename_all = "kebab-case")]
pub enum LedgerFetchOutcome {
    Updated {
        revision: u64,
        snapshot: serde_json::Value,
        #[serde(skip_serializing_if = "Option::is_none")]
        warning: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        issue: Option<SyncIssue>,
    },
    Latest {
        revision: u64,
        snapshot: serde_json::Value,
        #[serde(skip_serializing_if = "Option::is_none")]
        warning: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        issue: Option<SyncIssue>,
    },
    CloudEmpty {
        has_cache: bool,
    },
}

// ---------------------------------------------------------------------------
// Local durable state (ledger-scoped files).
// ---------------------------------------------------------------------------

pub fn load_publish_state(dir: &Path) -> Result<u64, String> {
    let bytes = match fs::read(dir.join(LEDGER_STATE_FILE)) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(0),
        Err(_) => return Err("无法读取正式账本发布状态。".into()),
    };
    let value: serde_json::Value = serde_json::from_slice(&bytes)
        .map_err(|_| "正式账本发布状态损坏，请保留现场检查。".to_string())?;
    value
        .get("lastPublishedRevision")
        .and_then(|v| v.as_u64())
        .ok_or("正式账本发布状态损坏，请保留现场检查。".into())
}

pub fn save_publish_state(dir: &Path, revision: u64) -> Result<(), String> {
    let bytes = serde_json::json!({"lastPublishedRevision": revision})
        .to_string()
        .into_bytes();
    fs::create_dir_all(dir).map_err(|_| "无法建立正式账本状态目录。".to_string())?;
    let mut pending =
        tempfile::NamedTempFile::new_in(dir).map_err(|_| "无法准备发布状态文件。".to_string())?;
    pending
        .write_all(&bytes)
        .map_err(|_| "发布状态写入未完成。")?;
    pending
        .as_file()
        .sync_all()
        .map_err(|_| "发布状态写入未完成。")?;
    pending
        .persist(dir.join(LEDGER_STATE_FILE))
        .map_err(|_| "发布状态替换未完成。")?;
    Ok(())
}

const LOCK_FILE: &str = "ledger.lock";

/// Cross-process mutex for one ledger cache/pending mutation domain.
pub fn with_transaction<T>(
    directory: &Path,
    operation: impl FnOnce() -> Result<T, String>,
) -> Result<T, String> {
    fs::create_dir_all(directory).map_err(|_| "无法建立正式账本事务目录。".to_string())?;
    let guard = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(directory.join(LOCK_FILE))
        .map_err(|_| "无法打开正式账本事务锁，未修改密钥或缓存。".to_string())?;
    guard
        .lock()
        .map_err(|_| "无法锁定正式账本事务，未修改密钥或缓存。".to_string())?;
    operation()
}

pub struct LedgerAppTransaction<'a>(pub &'a tauri::AppHandle);
impl Transaction for LedgerAppTransaction<'_> {
    fn run<T: Send>(
        &self,
        operation: impl FnOnce() -> Result<T, String> + Send,
    ) -> Result<T, String> {
        let app = self.0;
        let directory = app
            .path()
            .app_data_dir()
            .map_err(|_| "无法定位应用数据目录。".to_string())?;
        with_transaction(&directory, operation)
    }
}

// ---------------------------------------------------------------------------
// Payload intake: read a validated export file and decide reuse vs new
// revision. Same content signature never manufactures a new publish version.
// ---------------------------------------------------------------------------

fn load_payload(path: &Path) -> Result<Vec<u8>, String> {
    let metadata = fs::metadata(path).map_err(|_| "找不到账单快照导出文件。".to_string())?;
    if metadata.len() > account_app_core::MAX_SNAPSHOT_BYTES as u64 {
        return Err("账单快照超过大小上限，已拒绝。".into());
    }
    fs::read(path).map_err(|_| "无法读取账单快照导出文件。".to_string())
}

/// Decide the pending envelope for `payload` without touching durable state
/// when `dry_run` is set.
/// Staging decision for one validated payload.
enum Decision {
    /// Stage this envelope (written to the pending file unless dry-run).
    Stage(EncryptedEnvelope, bool),
    /// The payload is byte-identical (modulo generatedAt) to the last
    /// confirmed publish at `revision`: no new version may be created.
    /// Any staged pending is stale in this case and gets cancelled.
    AlreadyPublished {
        revision: u64,
        cancelled_stale: bool,
    },
}

fn decide_pending(
    dir: &Path,
    key: &ContentKey,
    identity: &LedgerIdentity,
    payload: &[u8],
    dry_run: bool,
) -> Result<Decision, String> {
    let state_revision = load_publish_state(dir).map_err(|e| step_err(STEP_PENDING, e))?;
    let cache_revision = ledger::read_cache(&dir.join(LEDGER_CACHE_FILE), key, identity)
        .map_err(|e| step_err(STEP_KEY_CACHE, e))?
        .map(|envelope| envelope.metadata.revision)
        .unwrap_or(0);
    let existing = ledger::read_cache(&dir.join(LEDGER_PENDING_FILE), key, identity)
        .map_err(|e| step_err(STEP_PENDING, e))?;
    let new_signature =
        ledger::content_signature(payload).map_err(|error| step_err(STEP_PAYLOAD, error))?;
    // A replaced unpublished pending still consumed its revision number; the
    // replacement must be strictly newer so versions never collide.
    let pending_floor = existing
        .as_ref()
        .filter(|pending| pending.metadata.revision > state_revision)
        .map(|pending| pending.metadata.revision)
        .unwrap_or(0);
    // An attempted upload may have reached the server even though local
    // confirmation failed. Returning to cached content must then publish a
    // newer revision, rather than discard recovery evidence and claim that
    // the old confirmed cache is still the newest cloud content.
    let unconfirmed_upload = load_intent_unchecked(dir)
        .map_err(|e| step_err(STEP_PENDING, e))?
        .is_some_and(|intent| intent.envelope.metadata.revision > state_revision);
    if let Some(pending) =
        existing.filter(|pending| pending.metadata.revision > state_revision.max(cache_revision))
    {
        let pending_plain = ZeroizingVec(
            decrypt(&pending, key, identity.expected())
                .map_err(|error| step_err(STEP_PENDING, format!("待发内容无法认证：{error}")))?,
        );
        let pending_signature = ledger::content_signature(&pending_plain)
            .map_err(|error| step_err(STEP_PENDING, error))?;
        if pending_signature == new_signature {
            return Ok(Decision::Stage(pending, true));
        }
    }
    // Content identical to the last confirmed publish never manufactures a
    // new version: re-publishing it is an idempotent no-op confirmation.
    if let Some(cache) = ledger::read_cache(&dir.join(LEDGER_CACHE_FILE), key, identity)
        .map_err(|e| step_err(STEP_KEY_CACHE, e))?
        .filter(|cache| {
            !unconfirmed_upload && cache.metadata.revision <= state_revision && state_revision > 0
        })
    {
        let cache_plain =
            ZeroizingVec(decrypt(&cache, key, identity.expected()).map_err(|error| {
                step_err(STEP_KEY_CACHE, format!("已发布缓存无法认证：{error}"))
            })?);
        if ledger::content_signature(&cache_plain)
            .map_err(|error| step_err(STEP_KEY_CACHE, error))?
            == new_signature
        {
            // The latest input is the published content, so any staged
            // pending is by definition outdated: cancel it together with its
            // slot intent instead of leaving it to be published later.
            let mut cancelled_stale = false;
            if !dry_run {
                cancelled_stale = fs::remove_file(dir.join(LEDGER_PENDING_FILE)).is_ok();
                let _ = fs::remove_file(dir.join(LEDGER_INTENT_FILE));
            }
            return Ok(Decision::AlreadyPublished {
                revision: cache.metadata.revision,
                cancelled_stale,
            });
        }
    }
    let revision = state_revision
        .max(cache_revision)
        .max(pending_floor)
        .checked_add(1)
        .ok_or("发布版本已达上限。")?;
    let envelope = encrypt(
        payload,
        key,
        SnapshotMetadata {
            format_version: 1,
            ledger_id: identity.ledger_id.clone(),
            account_id: identity.account_id.clone(),
            key_generation: identity.key_generation,
            revision,
        },
    )
    .map_err(|_| "账单快照加密失败。".to_string())?;
    if !dry_run {
        ledger::persist_cache(&dir.join(LEDGER_PENDING_FILE), &envelope)
            .map_err(|e| step_err(STEP_PENDING, e))?;
        // Preserve slot-ownership evidence: an unconfirmed intent means an
        // earlier upload attempt may have partially written that cloud slot.
        // The replacement pending inherits the recovery responsibility (same
        // target slot), instead of silently forgetting it.
        match load_intent_unchecked(dir).map_err(|e| step_err(STEP_PENDING, e))? {
            Some(intent) if intent.envelope.metadata.revision > state_revision => {
                save_intent(dir, intent.target, &envelope, intent.bootstrap)
                    .map_err(|e| step_err(STEP_PENDING, e))?;
            }
            _ => {
                let _ = fs::remove_file(dir.join(LEDGER_INTENT_FILE));
            }
        }
    }
    Ok(Decision::Stage(envelope, false))
}

struct ZeroizingVec(Vec<u8>);
impl std::ops::Deref for ZeroizingVec {
    type Target = [u8];
    fn deref(&self) -> &[u8] {
        &self.0
    }
}
impl Drop for ZeroizingVec {
    fn drop(&mut self) {
        use zeroize::Zeroize;
        self.0.zeroize();
    }
}

// ---------------------------------------------------------------------------
// Slot protocol in the build-bound ledger directory; no old-directory fallback.
// ---------------------------------------------------------------------------

enum Read {
    Missing,
    Valid(EncryptedEnvelope),
    Invalid { malformed: bool, message: String },
    Unavailable(String),
}

fn read_slot(
    client: &crate::webdav::WebDavClient,
    file: &str,
    key: &ContentKey,
    identity: &LedgerIdentity,
) -> Read {
    let bytes = match client.get(file) {
        Ok(Some(bytes)) => bytes,
        Ok(None) => return Read::Missing,
        Err(error) => return Read::Unavailable(error.at_step("槽位下载", Some("GET")).into()),
    };
    let envelope = match EncryptedEnvelope::from_json(&bytes) {
        Ok(envelope) => envelope,
        Err(_) => {
            return Read::Invalid {
                malformed: true,
                message: "云端文件格式异常，已保留本地缓存。".into(),
            };
        }
    };
    let plaintext = match decrypt(&envelope, key, identity.expected()) {
        Ok(plaintext) => ZeroizingVec(plaintext),
        Err(_) => {
            return Read::Invalid {
                malformed: false,
                message: "云端文件不属于本配对或无法通过认证，已保留本地缓存。".into(),
            };
        }
    };
    if ledger::validate_publication_payload(&plaintext).is_err() {
        return Read::Invalid {
            malformed: false,
            message: "云端文件不属于正式账单契约，已保留本地缓存。".into(),
        };
    }
    Read::Valid(envelope)
}

fn highest(reads: &[Read]) -> Result<Option<(usize, &EncryptedEnvelope)>, String> {
    let mut best: Option<(usize, &EncryptedEnvelope)> = None;
    for (index, read) in reads.iter().enumerate() {
        if let Read::Valid(envelope) = read {
            if let Some((_, previous)) = best {
                if reads.iter().any(|r| {
                    matches!(r, Read::Valid(other) if other.metadata.revision == envelope.metadata.revision && other != envelope)
                }) {
                    return Err(step_err(STEP_CONTRACT, "云端同版本内容不一致，已保留待发内容与本地缓存，未改动云端正式文件。".into()));
                }
                if previous.metadata.revision >= envelope.metadata.revision {
                    continue;
                }
            }
            best = Some((index, envelope));
        }
    }
    Ok(best)
}

#[derive(serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct Intent {
    target: usize,
    envelope: EncryptedEnvelope,
    bootstrap: bool,
}

fn load_intent_unchecked(dir: &Path) -> Result<Option<Intent>, String> {
    let bytes = match fs::read(dir.join(LEDGER_INTENT_FILE)) {
        Ok(bytes) => bytes,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err("无法读取待发槽位，请保留现场检查。".into()),
    };
    if bytes.len() > account_app_core::MAX_ENVELOPE_BYTES + 1024 {
        return Err("待发槽位状态损坏，请保留现场检查。".into());
    }
    let intent: Intent =
        serde_json::from_slice(&bytes).map_err(|_| "待发槽位状态损坏，请保留现场检查。")?;
    if intent.target >= LEDGER_FILES.len() {
        return Err("待发槽位状态损坏，请保留现场检查。".into());
    }
    Ok(Some(intent))
}

fn load_intent(dir: &Path, envelope: &EncryptedEnvelope) -> Result<Option<Intent>, String> {
    let Some(intent) = load_intent_unchecked(dir)? else {
        return Ok(None);
    };
    if intent.envelope == *envelope {
        return Ok(Some(intent));
    }
    if intent.envelope.metadata.revision <= load_publish_state(dir)? {
        return Ok(None);
    }
    Err("待发槽位与密文不一致，请保留现场检查。".into())
}

fn save_intent(
    dir: &Path,
    target: usize,
    envelope: &EncryptedEnvelope,
    bootstrap: bool,
) -> Result<(), String> {
    fs::create_dir_all(dir).map_err(|_| "无法建立发布状态目录。".to_string())?;
    let bytes = serde_json::to_vec(&Intent {
        target,
        envelope: envelope.clone(),
        bootstrap,
    })
    .map_err(|_| "无法编码待发槽位。".to_string())?;
    let mut temp =
        tempfile::NamedTempFile::new_in(dir).map_err(|_| "无法准备待发槽位。".to_string())?;
    temp.write_all(&bytes).map_err(|_| "待发槽位写入未完成。")?;
    temp.as_file()
        .sync_all()
        .map_err(|_| "待发槽位写入未完成。")?;
    temp.persist(dir.join(LEDGER_INTENT_FILE))
        .map_err(|_| "待发槽位替换未完成。")?;
    Ok(())
}

fn ensure_configured<V: SecretVault>(vault: &V) -> Result<(LedgerIdentity, ContentKey), String> {
    let identity = ledger::load_identity(vault)
        .map_err(|e| step_err(STEP_CONFIG, e))?
        .ok_or("正式账本尚未初始化。请先在电脑上运行 ledger 发布工具的初始化命令。".to_string())?;
    let key = ledger::load_key(vault)
        .map_err(|e| step_err(STEP_CONFIG, e))?
        .ok_or("正式账本密钥缺失，请保留现场检查。".to_string())?;
    Ok((identity, key))
}

fn build_client<V: SecretVault>(vault: &V) -> Result<crate::webdav::WebDavClient, String> {
    let (base_url, username, password) = crate::probe_sync::load_dav_credentials(vault)?;
    crate::webdav::WebDavClient::new(&base_url, LEDGER_REMOTE_DIRECTORY, &username, &password)
        .map_err(|e| step_err(STEP_CONFIG, e))
}

/// Validate and stage (or dry-run) one App V3 snapshot without publishing.
pub fn stage_pending<V: SecretVault + Sync, T: Transaction>(
    vault: &V,
    dir: &Path,
    tx: &T,
    payload_path: &Path,
    dry_run: bool,
) -> Result<LedgerPrepareOutcome, String> {
    let payload = load_payload(payload_path).map_err(|e| step_err(STEP_PAYLOAD, e))?;
    ledger::parse_payload(&payload).map_err(|error| step_err(STEP_PAYLOAD, error))?;
    let (identity, key) = ensure_configured(vault).map_err(|e| step_err(STEP_CONFIG, e))?;
    let (revision, reused, published_identical, cancelled_stale) = match tx
        .run(|| decide_pending(dir, &key, &identity, &payload, dry_run))
        .map_err(|e| step_err(STEP_PENDING, e))?
    {
        Decision::Stage(envelope, reused) => (envelope.metadata.revision, reused, false, false),
        Decision::AlreadyPublished {
            revision,
            cancelled_stale,
        } => (revision, true, true, cancelled_stale),
    };
    let snapshot =
        ledger::parse_payload(&payload).map_err(|error| step_err(STEP_PAYLOAD, error))?;
    Ok(LedgerPrepareOutcome {
        revision,
        reused,
        already_published: published_identical,
        stale_pending_cancelled: cancelled_stale,
        months: snapshot.coverage.months,
        exported_records: snapshot
            .months
            .iter()
            .map(|m| m.records.len() + m.structural_records.len() + m.income_records.len())
            .sum(),
        payload_bytes: payload.len(),
        dry_run,
    })
}

/// Publish the currently staged pending envelope (slot PUT + readback).
/// The publish gate is the mandatory check on this, the only real-upload
/// path: GUI, CLI, and post-processing all reach the network through here
/// and can never bypass it.
pub fn publish_staged<V: SecretVault + Sync, T: Transaction>(
    vault: &V,
    dir: &Path,
    tx: &T,
) -> Result<LedgerPublishOutcome, String> {
    publish_gate_open(vault).map_err(|e| step_err(STEP_CONFIG, e))?;
    let (identity, key) = ensure_configured(vault).map_err(|e| step_err(STEP_CONFIG, e))?;
    let client = build_client(vault)?;
    // One cross-process transaction covers the pending read, the network
    // exchange, and the final commit: no other entry can stage, publish, or
    // mutate durable state while this upload is in flight.
    tx.run(|| {
        let state_revision = load_publish_state(dir).map_err(|e| step_err(STEP_PENDING, e))?;
        let cache_revision = ledger::read_cache(&dir.join(LEDGER_CACHE_FILE), &key, &identity)
            .map_err(|e| step_err(STEP_KEY_CACHE, e))?
            .map(|cache| cache.metadata.revision)
            .unwrap_or(0);
        let envelope = ledger::read_cache(&dir.join(LEDGER_PENDING_FILE), &key, &identity)
            .map_err(|e| step_err(STEP_PENDING, e))?
            .filter(|pending| pending.metadata.revision > state_revision.max(cache_revision))
            .ok_or("没有待发内容。请先用准备命令暂存账单快照。".to_string())?;
        // Staged V2 history must be replaced by a validated V3 export before
        // publishing. Reject without network writes or changing recovery files.
        let plaintext = ZeroizingVec(
            decrypt(&envelope, &key, identity.expected())
                .map_err(|_| step_err(STEP_PENDING, "待发内容无法认证。".into()))?,
        );
        ledger::parse_payload(&plaintext).map_err(|e| step_err(STEP_PENDING, e))?;
        let intent = load_intent(dir, &envelope).map_err(|e| step_err(STEP_PENDING, e))?;
        client
            .ensure_collection()
            .map_err(|e| e.at_step(STEP_DIRECTORY, Some("MKCOL/PROPFIND")))?;
        let reads: Vec<_> = LEDGER_FILES
            .iter()
            .map(|file| read_slot(&client, file, &key, &identity))
            .collect();
        let bootstrap = highest(&reads)?.is_none();
        for read in &reads {
            if let Read::Unavailable(message) = read {
                return Err(message.clone());
            }
            if let Read::Invalid {
                malformed: false,
                message,
            } = read
            {
                return Err(step_err(STEP_OWNERSHIP, message.clone()));
            }
        }
        let best = highest(&reads)?;
        let intended_target = intent.as_ref().map(|intent| intent.target);
        let matching = reads
            .iter()
            .take(2)
            .position(|r| matches!(r, Read::Valid(e) if e == &envelope));
        if let Some((_, current)) = best {
            if current.metadata.revision > envelope.metadata.revision {
                return Err(step_err(
                    STEP_CONTRACT,
                    "云端已有更高版本，已保留待发内容，未改动云端正式文件。".into(),
                ));
            }
            if current.metadata.revision == envelope.metadata.revision && current != &envelope {
                return Err(step_err(
                    STEP_CONTRACT,
                    "云端同版本内容与待发内容不一致，已保留待发内容，未改动云端正式文件。".into(),
                ));
            }
        }
        let target = if let Some(matching) = matching {
            if intended_target.is_some_and(|target| target != matching) {
                return Err(step_err(
                    STEP_PENDING,
                    "云端待发槽位与本机意图冲突。".into(),
                ));
            }
            matching
        } else {
            // An intent target that has itself become the current valid slot
            // carries expired evidence: the older upload was fully confirmed
            // there, so there is no partial junk to recover. Publish to the
            // other (non-current) slot instead of refusing forever.
            let intended = intent
                .as_ref()
                .map(|intent| intent.target)
                .filter(|target| !best.is_some_and(|(index, _)| index == *target));
            match intended {
                Some(target) => target,
                None => match best {
                    Some((0, _)) => 1,
                    _ => 0,
                },
            }
        };
        for (index, read) in reads.iter().enumerate() {
            if let Read::Invalid {
                malformed: true,
                message,
            } = read
                && (intended_target != Some(index)
                    || index != target
                    || (best.is_none() && !bootstrap))
            {
                return Err(step_err(STEP_OWNERSHIP, message.clone()));
            }
        }
        save_intent(dir, target, &envelope, bootstrap).map_err(|e| step_err(STEP_PENDING, e))?;
        let bytes = envelope
            .to_json()
            .map_err(|_| "待发密文无效。".to_string())?;
        if matching.is_none() {
            client
                .put(LEDGER_FILES[target], &bytes)
                .map_err(|e| e.at_step("非最新槽位上传", Some("PUT")))?;
        }
        let confirmed = client
            .get(LEDGER_FILES[target])
            .map_err(|e| e.at_step(STEP_OFFICIAL_READBACK, Some("GET")))?;
        if confirmed.as_deref() != Some(bytes.as_slice()) {
            return Err(step_err(
                STEP_OFFICIAL_READBACK,
                "发布槽位回读不一致，已保留缓存与待发内容，可重试。".into(),
            ));
        }
        // Final commit: only clean up the pending/intent this publish
        // confirmed. A pending staged later (in case of any lock gap) with a
        // newer revision must survive an older confirmation.
        ledger::persist_cache(&dir.join(LEDGER_CACHE_FILE), &envelope)
            .map_err(|e| step_err(STEP_LOCAL_SAVE, e))?;
        save_publish_state(dir, envelope.metadata.revision)
            .map_err(|e| step_err(STEP_LOCAL_SAVE, e))?;
        let confirmed_envelope = envelope.clone();
        let current_pending = ledger::read_cache(&dir.join(LEDGER_PENDING_FILE), &key, &identity)
            .map_err(|e| step_err(STEP_LOCAL_SAVE, e))?;
        if current_pending.as_ref() == Some(&confirmed_envelope) {
            let _ = fs::remove_file(dir.join(LEDGER_PENDING_FILE));
        }
        let current_intent =
            load_intent_unchecked(dir).map_err(|e| step_err(STEP_LOCAL_SAVE, e))?;
        if current_intent
            .map(|intent| intent.envelope == confirmed_envelope)
            .unwrap_or(false)
        {
            let _ = fs::remove_file(dir.join(LEDGER_INTENT_FILE));
        }
        Ok(LedgerPublishOutcome {
            revision: envelope.metadata.revision,
            method: "put-slot",
            reused: matching.is_some(),
        })
    })
}

/// Validate, stage, then publish one App V3 snapshot in a single call.
/// The publish gate is checked before any local staging write.
pub fn prepare_and_publish<V: SecretVault + Sync, T: Transaction>(
    vault: &V,
    dir: &Path,
    tx: &T,
    payload_path: &Path,
    dry_run: bool,
) -> Result<LedgerPrepareOutcome, String> {
    publish_gate_open(vault).map_err(|e| step_err(STEP_CONFIG, e))?;
    let staged = stage_pending(vault, dir, tx, payload_path, dry_run)?;
    if dry_run || staged.already_published {
        // Content equal to the last confirmed publish is an idempotent
        // success; its stale pending (if any) was already cancelled.
        return Ok(staged);
    }
    let published = publish_staged(vault, dir, tx)?;
    Ok(LedgerPrepareOutcome {
        revision: published.revision,
        reused: published.reused,
        already_published: false,
        stale_pending_cancelled: staged.stale_pending_cancelled,
        months: staged.months,
        exported_records: staged.exported_records,
        payload_bytes: staged.payload_bytes,
        dry_run: false,
    })
}

/// Download and accept the newest valid slot, cache-first caller.
#[allow(dead_code)] // Keep the String interface for internal callers; Tauri uses diagnostics.
pub fn fetch_flow<V: SecretVault + Sync, T: Transaction>(
    vault: &V,
    dir: &Path,
    tx: &T,
) -> Result<LedgerFetchOutcome, String> {
    fetch_flow_diagnostic(vault, dir, tx).map_err(String::from)
}

enum FetchRead {
    Missing,
    Valid(EncryptedEnvelope),
    Failed {
        revision: Option<u64>,
        issue: SyncIssue,
    },
}

fn fetch_read(
    client: &crate::webdav::WebDavClient,
    file: &str,
    key: &ContentKey,
    identity: &LedgerIdentity,
) -> FetchRead {
    let bytes = match client.get(file) {
        Ok(Some(bytes)) => bytes,
        Ok(None) => return FetchRead::Missing,
        Err(error) => {
            return FetchRead::Failed {
                revision: None,
                issue: SyncIssue::dav(error, "download"),
            };
        }
    };
    let invalid = || {
        SyncIssue::new(
            "snapshot-invalid",
            "snapshot",
            "云端快照损坏或未通过完整性校验，已保留本地缓存。",
            false,
        )
    };
    let envelope = match EncryptedEnvelope::from_json(&bytes) {
        Ok(envelope) => envelope,
        Err(_) => {
            return FetchRead::Failed {
                revision: None,
                issue: invalid(),
            };
        }
    };
    let plaintext = match decrypt(&envelope, key, identity.expected()) {
        Ok(bytes) => ZeroizingVec(bytes),
        Err(_) => {
            return FetchRead::Failed {
                revision: None,
                issue: SyncIssue::new(
                    "snapshot-authentication-failed",
                    "authenticate",
                    "云端文件不属于本配对或无法通过认证，已保留本地缓存。",
                    false,
                ),
            };
        }
    };
    let revision = Some(envelope.metadata.revision);
    let value: serde_json::Value = match serde_json::from_slice(&plaintext) {
        Ok(value) => value,
        Err(_) => {
            return FetchRead::Failed {
                revision,
                issue: invalid(),
            };
        }
    };
    let issue = if value["snapshotKind"] != ledger::SNAPSHOT_KIND {
        Some(invalid())
    } else if value["schemaVersion"]
        .as_u64()
        .is_some_and(|schema| schema > 3)
    {
        Some(SyncIssue::new(
            "snapshot-newer-schema",
            "snapshot",
            "云端快照需要更新手机 App 后读取，已保留本地缓存。",
            false,
        ))
    } else if value["schemaVersion"] == 2
        && ledger::validate_publication_payload(&plaintext).is_ok()
    {
        Some(SyncIssue::new(
            "snapshot-older-schema",
            "snapshot",
            "云端仍是旧版快照，请在电脑更新后重新发布，已保留本地缓存。",
            false,
        ))
    } else if ledger::parse_payload(&plaintext).is_err() {
        Some(invalid())
    } else {
        None
    };
    match issue {
        Some(issue) => FetchRead::Failed { revision, issue },
        None => FetchRead::Valid(envelope),
    }
}

pub fn fetch_flow_diagnostic<V: SecretVault + Sync, T: Transaction>(
    vault: &V,
    dir: &Path,
    tx: &T,
) -> Result<LedgerFetchOutcome, SyncIssue> {
    let (identity, key, baseline) = tx
        .run(|| {
            let (identity, key) = ensure_configured(vault).map_err(|e| step_err(STEP_CONFIG, e))?;
            let baseline = ledger::read_cache(&dir.join(LEDGER_CACHE_FILE), &key, &identity)
                .map_err(|error| step_err(STEP_KEY_CACHE, error))?
                .map(|envelope| envelope.metadata.revision)
                .unwrap_or(0);
            Ok((identity, key, baseline))
        })
        .map_err(|error| SyncIssue::new("local-state-unavailable", "local-cache", error, false))?;
    let client = build_client(vault)
        .map_err(|error| SyncIssue::new("sync-not-configured", "configure", error, false))?;
    let reads: Vec<_> = LEDGER_FILES
        .iter()
        .map(|file| fetch_read(&client, file, &key, &identity))
        .collect();
    let valid: Vec<_> = reads
        .iter()
        .filter_map(|read| match read {
            FetchRead::Valid(envelope) => Some(envelope),
            _ => None,
        })
        .collect();
    if valid.len() == 2
        && valid[0].metadata.revision == valid[1].metadata.revision
        && valid[0] != valid[1]
    {
        return Err(SyncIssue::new(
            "snapshot-revision-conflict",
            "snapshot",
            "云端同版本内容不一致，已保留本地缓存。",
            false,
        ));
    }
    let best = valid
        .into_iter()
        .max_by_key(|envelope| envelope.metadata.revision)
        .cloned();
    // An authenticated, newer incompatible payload is authoritative evidence;
    // never silently adopt an older readable slot in its place.
    if let Some(issue) = reads
        .iter()
        .filter_map(|read| match read {
            FetchRead::Failed {
                revision: Some(revision),
                issue,
            } if best
                .as_ref()
                .is_none_or(|best| *revision >= best.metadata.revision) =>
            {
                Some((*revision, issue))
            }
            _ => None,
        })
        .max_by_key(|(revision, _)| *revision)
        .map(|(_, issue)| issue)
    {
        return Err(issue.clone());
    }
    let issue = reads.iter().find_map(|read| match read {
        FetchRead::Failed { issue, .. } => Some(issue.clone()),
        _ => None,
    });
    let warning = issue
        .as_ref()
        .map(|issue| format!("部分云端文件检查未完成：{}", issue.message));
    let Some(incoming) = best else {
        if let Some(issue) = issue {
            return Err(issue);
        }
        return Ok(LedgerFetchOutcome::CloudEmpty {
            has_cache: baseline > 0,
        });
    };
    tx.run(move || {
        Ok((|| {
            let local = ledger::read_cache(&dir.join(LEDGER_CACHE_FILE), &key, &identity).map_err(
                |error| SyncIssue::new("local-state-unavailable", "local-cache", error, false),
            )?;
            if let Some(local_envelope) = &local {
                if incoming.metadata.revision == local_envelope.metadata.revision {
                    if &incoming == local_envelope {
                        let snapshot =
                            snapshot_value(&incoming, &key, &identity).map_err(|error| {
                                SyncIssue::new("snapshot-invalid", "snapshot", error, false)
                            })?;
                        return Ok(LedgerFetchOutcome::Latest {
                            revision: local_envelope.metadata.revision,
                            snapshot,
                            warning: warning.clone(),
                            issue: issue.clone(),
                        });
                    }
                    return Err(SyncIssue::new(
                        "snapshot-revision-conflict",
                        "accept",
                        "云端同版本内容与本地缓存不一致，已保留本地缓存。",
                        false,
                    ));
                }
                if incoming.metadata.revision < local_envelope.metadata.revision {
                    return Err(SyncIssue::new(
                        "snapshot-rollback",
                        "accept",
                        "云端版本低于本地缓存，已保留本地缓存。",
                        false,
                    ));
                }
            }
            let local_revision = local
                .as_ref()
                .map(|envelope| envelope.metadata.revision)
                .unwrap_or(0);
            let mut guard =
                RevisionGuard::from_trusted_checkpoint(identity.expected(), local_revision)
                    .map_err(|_| {
                        SyncIssue::new(
                            "local-state-unavailable",
                            "local-cache",
                            "本地版本状态异常，请保留现场检查。",
                            false,
                        )
                    })?;
            let plaintext = ZeroizingVec(
                decrypt_and_advance(&incoming, &key, identity.expected(), &mut guard).map_err(
                    |_| {
                        SyncIssue::new(
                            "snapshot-authentication-failed",
                            "authenticate",
                            "云端文件无法通过本配对认证，已保留本地缓存。",
                            false,
                        )
                    },
                )?,
            );
            ledger::parse_payload(&plaintext).map_err(|error| {
                SyncIssue::new(
                    "snapshot-invalid",
                    "snapshot",
                    format!("{error}已保留本地缓存。"),
                    false,
                )
            })?;
            ledger::persist_cache(&dir.join(LEDGER_CACHE_FILE), &incoming).map_err(|error| {
                SyncIssue::new("local-state-unavailable", "cache-save", error, false)
            })?;
            let snapshot = snapshot_value(&incoming, &key, &identity)
                .map_err(|error| SyncIssue::new("snapshot-invalid", "snapshot", error, false))?;
            Ok(LedgerFetchOutcome::Updated {
                revision: incoming.metadata.revision,
                snapshot,
                warning,
                issue,
            })
        })())
    })
    .map_err(|error| SyncIssue::new("local-state-unavailable", "transaction", error, false))?
}

/// Parse an authenticated envelope into the display JSON value.
pub fn snapshot_value(
    envelope: &EncryptedEnvelope,
    key: &ContentKey,
    identity: &LedgerIdentity,
) -> Result<serde_json::Value, String> {
    let plaintext = ZeroizingVec(
        decrypt(envelope, key, identity.expected()).map_err(|_| "缓存无法解密。".to_string())?,
    );
    ledger::parse_payload(&plaintext).map_err(|error| step_err(STEP_CONTRACT, error))?;
    serde_json::from_slice(&plaintext).map_err(|_| "缓存内容解析失败。".to_string())
}

// ---------------------------------------------------------------------------
// Minimal pairing: identity + content key + fixed authenticated sample.
// ---------------------------------------------------------------------------

pub fn create_pairing_payload<V: SecretVault>(vault: &V) -> Result<String, String> {
    let (identity, key) = ensure_configured(vault)?;
    let sample = encrypt(
        ledger::SAMPLE_PLAINTEXT,
        &key,
        SnapshotMetadata {
            format_version: 1,
            ledger_id: identity.ledger_id.clone(),
            account_id: identity.account_id.clone(),
            key_generation: identity.key_generation,
            revision: 1,
        },
    )
    .map_err(|_| "配对样例加密失败。".to_string())?;
    let payload = serde_json::json!({
        "kind": ledger::PAIRING_KIND,
        "ledgerId": identity.ledger_id,
        "accountId": identity.account_id,
        "keyGeneration": identity.key_generation,
        "key": STANDARD.encode(key.as_bytes()),
        "sample": sample,
    });
    let text = payload.to_string();
    if text.len() > ledger::MAX_PAIRING_BYTES {
        return Err("配对内容超出二维码上限。".into());
    }
    Ok(text)
}

#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PairingQr {
    kind: String,
    ledger_id: String,
    account_id: String,
    key_generation: u64,
    sample: EncryptedEnvelope,
    key: String,
}

#[allow(dead_code)] // String compatibility interface; native commands use diagnostics.
pub fn accept_pairing<V: SecretVault>(
    vault: &V,
    payload: &str,
) -> Result<LedgerStatusSummary, String> {
    accept_pairing_diagnostic(vault, payload).map_err(String::from)
}

pub fn accept_pairing_diagnostic<V: SecretVault>(
    vault: &V,
    payload: &str,
) -> Result<LedgerStatusSummary, SyncIssue> {
    let invalid = |message| SyncIssue::new("pairing-invalid", "pairing", message, false);
    let storage =
        |message| SyncIssue::new("local-state-unavailable", "pairing-storage", message, false);
    if payload.len() > ledger::MAX_PAIRING_BYTES {
        return Err(invalid("配对内容过大。".to_string()));
    }
    let kind: serde_json::Value = serde_json::from_str(payload)
        .map_err(|_| invalid("无法识别正式账本配对二维码。".into()))?;
    if kind["kind"] != ledger::PAIRING_KIND {
        return Err(SyncIssue::new(
            "pairing-unsupported",
            "pairing",
            "二维码不是当前支持的正式账本配对，请在电脑显示配对码后重试。",
            false,
        ));
    }
    let qr: PairingQr = serde_json::from_str(payload)
        .map_err(|_| invalid("无法识别正式账本配对二维码。".into()))?;
    if qr.kind != ledger::PAIRING_KIND {
        return Err(invalid("二维码不属于正式账本配对。".into()));
    }
    let identity = LedgerIdentity {
        ledger_id: qr.ledger_id,
        account_id: qr.account_id,
        key_generation: qr.key_generation,
    };
    identity.validate().map_err(invalid)?;
    let bytes = ZeroizingVec(
        STANDARD
            .decode(&qr.key)
            .map_err(|_| "配对密钥无效。".to_string())
            .and_then(|bytes| {
                (bytes.len() == 32)
                    .then_some(bytes)
                    .ok_or("配对密钥无效。".to_string())
            })
            .map_err(invalid)?,
    );
    let mut key_bytes = zeroize::Zeroizing::new([0u8; 32]);
    key_bytes.copy_from_slice(&bytes);
    let imported = ContentKey::from_bytes(*key_bytes);
    // Authenticate the fixed sample against the QR identity before any write.
    let sample_plain = ZeroizingVec(decrypt(&qr.sample, &imported, identity.expected()).map_err(
        |_| {
            SyncIssue::new(
                "pairing-authentication-failed",
                "pairing",
                "配对样例认证失败。",
                false,
            )
        },
    )?);
    if sample_plain.0.as_slice() != ledger::SAMPLE_PLAINTEXT {
        return Err(invalid("配对样例不属于正式账本配对。".into()));
    }
    // Existing local identity/key are never replaced or reset.
    if let Some(existing) = ledger::load_identity(vault).map_err(storage)?
        && existing != identity
    {
        return Err(SyncIssue::new(
            "pairing-foreign-identity",
            "pairing",
            "本机已有另一组正式账本配对，拒绝覆盖。",
            false,
        ));
    }
    match ledger::load_key(vault).map_err(storage)? {
        Some(existing) => {
            if existing.as_bytes() != imported.as_bytes() {
                return Err(SyncIssue::new(
                    "pairing-foreign-identity",
                    "pairing",
                    "本机已有另一组正式账本密钥，拒绝覆盖。",
                    false,
                ));
            }
        }
        None => ledger::persist_key(vault, &imported).map_err(storage)?,
    }
    if ledger::load_identity(vault).map_err(storage)?.is_none() {
        ledger::persist_identity(vault, &identity).map_err(storage)?;
    }
    summarize_ledger(vault, None).map_err(storage)
}

pub fn summarize_ledger<V: SecretVault>(
    vault: &V,
    dir: Option<&Path>,
) -> Result<LedgerStatusSummary, String> {
    // Missing slots mean first use; unreadable slots must remain an error.
    let identity = ledger::load_identity(vault)?;
    let key = ledger::load_key(vault)?;
    let initialized = identity.is_some() && key.is_some();
    let publish_enabled = vault
        .load_secret(crate::ledger::LEDGER_PUBLISH_ENABLED_SLOT)?
        .map(|bytes| bytes == b"true")
        .unwrap_or(false);
    let mut summary = LedgerStatusSummary {
        initialized,
        publish_enabled,
        cached_revision: None,
        pending_revision: None,
        last_published_revision: None,
    };
    if let Some(dir) = dir
        && initialized
        && let (Some(identity), Some(key)) = (identity, key)
    {
        summary.cached_revision = ledger::read_cache(&dir.join(LEDGER_CACHE_FILE), &key, &identity)
            .ok()
            .flatten()
            .map(|envelope| envelope.metadata.revision);
        summary.last_published_revision = load_publish_state(dir).ok();
        summary.pending_revision =
            ledger::read_cache(&dir.join(LEDGER_PENDING_FILE), &key, &identity)
                .ok()
                .flatten()
                .filter(|envelope| {
                    envelope.metadata.revision
                        > summary
                            .last_published_revision
                            .unwrap_or(0)
                            .max(summary.cached_revision.unwrap_or(0))
                })
                .map(|envelope| envelope.metadata.revision);
    }
    Ok(summary)
}

/// Trusted local initialization: generate and persist the business identity
/// and content key. Refuses to replace an existing identity.
pub fn initialize<V: SecretVault>(vault: &V) -> Result<LedgerStatusSummary, String> {
    if ledger::load_identity(vault)?.is_some() || ledger::load_key(vault)?.is_some() {
        return Err("正式账本身份已存在，拒绝重新初始化。请保留现场检查。".into());
    }
    let identity = LedgerIdentity {
        ledger_id: format!("ledger-{}", random_suffix()),
        account_id: format!("account-{}", random_suffix()),
        key_generation: 1,
    };
    let key = ContentKey::generate().map_err(|_| "设备无法生成安全随机密钥。".to_string())?;
    ledger::persist_identity(vault, &identity)?;
    ledger::persist_key(vault, &key)?;
    summarize_ledger(vault, None)
}

fn random_suffix() -> String {
    let key = ContentKey::generate().expect("CSPRNG unavailable");
    key.as_bytes()[..8]
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

/// Native publish-enable gate. Never set by this stage's development.
pub fn enable_publish<V: SecretVault>(vault: &V) -> Result<(), String> {
    if ledger::load_identity(vault)?.is_none() {
        return Err("正式账本尚未初始化，不能启用发布。".into());
    }
    vault
        .store_secret(crate::ledger::LEDGER_PUBLISH_ENABLED_SLOT, b"true")
        .map_err(|_| "安全存储写入失败，发布开关未启用。".to_string())?;
    Ok(())
}

/// Native publish-enable gate check. Real uploads refuse without it.
pub fn publish_gate_open<V: SecretVault>(vault: &V) -> Result<(), String> {
    let enabled = vault
        .load_secret(crate::ledger::LEDGER_PUBLISH_ENABLED_SLOT)
        .map_err(|_| "无法读取发布开关。".to_string())?
        .map(|bytes| bytes == b"true")
        .unwrap_or(false);
    if enabled {
        Ok(())
    } else {
        Err("真实发布未启用。本阶段发布入口保持禁用，待主对话复审后统一安排。".into())
    }
}

// ---------------------------------------------------------------------------
// Tauri commands. Role restriction is native: publishing and preparing are
// refused on Android, not merely hidden in the UI.
// ---------------------------------------------------------------------------

use crate::probe_sync::SyncVault;

#[tauri::command]
pub async fn ledger_status_command(app: tauri::AppHandle) -> Result<LedgerStatusSummary, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let directory = app
            .path()
            .app_data_dir()
            .map_err(|_| "无法定位应用数据目录。".to_string())?;
        summarize_ledger(&SyncVault(app.platform()), Some(&directory))
    })
    .await
    .map_err(|_| "正式账本状态检查未能完成。".to_string())?
}

#[tauri::command]
pub async fn ledger_create_pairing(app: tauri::AppHandle) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let vault = SyncVault(app.platform());
        let payload = create_pairing_payload(&vault)?;
        let code =
            qrcode::QrCode::with_error_correction_level(payload.as_bytes(), qrcode::EcLevel::M)
                .map_err(|_| "无法生成正式账本配对二维码。".to_string())?;
        let svg = code
            .render::<qrcode::render::svg::Color>()
            .min_dimensions(360, 360)
            .build();
        Ok(format!(
            "data:image/svg+xml;base64,{}",
            STANDARD.encode(svg.as_bytes())
        ))
    })
    .await
    .map_err(|_| "正式账本配对生成未能完成。".to_string())?
}

#[tauri::command]
pub async fn ledger_import_pairing(
    app: tauri::AppHandle,
    payload: String,
) -> Result<LedgerStatusSummary, SyncIssue> {
    let payload = zeroize::Zeroizing::new(payload);
    tauri::async_runtime::spawn_blocking(move || {
        let vault = SyncVault(app.platform());
        accept_pairing_diagnostic(&vault, &payload)
    })
    .await
    .map_err(|_| {
        SyncIssue::new(
            "native-task-failed",
            "pairing",
            "正式账本配对导入未能完成。",
            true,
        )
    })?
}

#[tauri::command]
pub async fn ledger_fetch(app: tauri::AppHandle) -> Result<LedgerFetchOutcome, SyncIssue> {
    let network = app.state::<LedgerState>().network.clone();
    let _network = network.lock().await;
    tauri::async_runtime::spawn_blocking(move || {
        let directory = app.path().app_data_dir().map_err(|_| {
            SyncIssue::new(
                "local-state-unavailable",
                "local-cache",
                "无法定位应用数据目录。",
                false,
            )
        })?;
        let tx = LedgerAppTransaction(&app);
        fetch_flow_diagnostic(&SyncVault(app.platform()), &directory, &tx)
    })
    .await
    .map_err(|error| {
        SyncIssue::new(
            "native-task-failed",
            "download",
            crate::probe_sync::sync_join_failure(error),
            true,
        )
    })?
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LedgerCacheOutcome {
    pub revision: u64,
    pub snapshot: serde_json::Value,
}

/// Read the local authenticated cache for first paint. No network involved:
/// the App can always show the last fetched ledger even offline.
#[tauri::command]
pub async fn ledger_cache(app: tauri::AppHandle) -> Result<Option<LedgerCacheOutcome>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let vault = SyncVault(app.platform());
        let (identity, key) = ensure_configured(&vault).map_err(|e| step_err(STEP_CONFIG, e))?;
        let directory = app
            .path()
            .app_data_dir()
            .map_err(|_| "无法定位应用数据目录。".to_string())?;
        let envelope = ledger::read_cache(&directory.join(LEDGER_CACHE_FILE), &key, &identity)
            .map_err(|error| step_err(STEP_KEY_CACHE, error))?;
        match envelope {
            Some(envelope) => {
                let snapshot = snapshot_value(&envelope, &key, &identity)?;
                Ok(Some(LedgerCacheOutcome {
                    revision: envelope.metadata.revision,
                    snapshot,
                }))
            }
            None => Ok(None),
        }
    })
    .await
    .map_err(crate::probe_sync::sync_join_failure)?
}

/// Inspect one exported App V3 snapshot file through the native entry point:
/// full contract validation (same rules as the encrypted channel) without
/// touching the ledger identity, durable state, or the cloud. Used for the
/// Windows local real-data check before official configuration migration.
#[tauri::command]
pub async fn ledger_inspect_snapshot(path: String) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || inspect_snapshot_file(&path))
        .await
        .map_err(crate::probe_sync::sync_join_failure)?
}

fn inspect_snapshot_file(path: &str) -> Result<serde_json::Value, String> {
    let file = Path::new(path);
    let metadata =
        fs::metadata(file).map_err(|_| "找不到快照导出文件，请确认路径。".to_string())?;
    if metadata.len() > account_app_core::MAX_SNAPSHOT_BYTES as u64 {
        return Err("快照文件超过大小上限，已拒绝。".into());
    }
    let plaintext = fs::read(file).map_err(|_| "无法读取快照导出文件。".to_string())?;
    ledger::parse_payload(&plaintext)
        .map_err(|error| format!("快照未通过 App V3 契约校验：{error}"))?;
    serde_json::from_slice(&plaintext).map_err(|_| "快照内容解析失败。".to_string())
}

/// Prepare always only validates and stages (or dry-runs); it never uploads.
/// Publishing belongs to the explicit gated publish entry.
#[tauri::command]
pub async fn ledger_prepare(
    app: tauri::AppHandle,
    payload_path: String,
    dry_run: bool,
) -> Result<LedgerPrepareOutcome, String> {
    if cfg!(target_os = "android") {
        return Err("手机端不负责准备或发布账单快照。".into());
    }
    let network = app.state::<LedgerState>().network.clone();
    let _network = network.lock().await;
    tauri::async_runtime::spawn_blocking(move || {
        let directory = app
            .path()
            .app_data_dir()
            .map_err(|_| "无法定位应用数据目录。".to_string())?;
        let tx = LedgerAppTransaction(&app);
        stage_pending(
            &SyncVault(app.platform()),
            &directory,
            &tx,
            Path::new(&payload_path),
            dry_run,
        )
    })
    .await
    .map_err(crate::probe_sync::sync_join_failure)?
}

#[tauri::command]
pub async fn ledger_publish(
    app: tauri::AppHandle,
    payload_path: String,
) -> Result<LedgerPrepareOutcome, String> {
    if cfg!(target_os = "android") {
        return Err("手机端不负责准备或发布账单快照。".into());
    }
    let network = app.state::<LedgerState>().network.clone();
    let _network = network.lock().await;
    tauri::async_runtime::spawn_blocking(move || {
        let vault = SyncVault(app.platform());
        publish_gate_open(&vault)?;
        let directory = app
            .path()
            .app_data_dir()
            .map_err(|_| "无法定位应用数据目录。".to_string())?;
        let tx = LedgerAppTransaction(&app);
        prepare_and_publish(&vault, &directory, &tx, Path::new(&payload_path), false)
    })
    .await
    .map_err(crate::probe_sync::sync_join_failure)?
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn setup_status_read_errors_are_not_unpaired() {
        struct Unreadable;
        impl SecretVault for Unreadable {
            fn load_secret(&self, _: &str) -> Result<Option<Vec<u8>>, String> {
                Err("storage unavailable".into())
            }
            fn store_secret(&self, _: &str, _: &[u8]) -> Result<(), String> {
                panic!("read only")
            }
            fn delete_secret(&self, _: &str) -> Result<(), String> {
                panic!("read only")
            }
        }
        let status = serde_json::to_value(summarize_ledger(&Unreadable, None)).unwrap();
        assert!(
            status.get("Err").is_some(),
            "storage failure must not be an unpaired status"
        );
    }
    use crate::ledger::tests::{MemorySlots, valid_payload};
    use crate::webdav::tests_support::{MockRequest, MockResponse, spawn_server};
    use std::sync::Mutex as StdMutex;

    type Seen = Arc<StdMutex<Vec<MockRequest>>>;
    type SlotMap = Arc<StdMutex<BTreeMap<String, Vec<u8>>>>;
    use std::collections::BTreeMap;

    #[derive(Default)]
    struct Fault {
        put_failure: Option<&'static str>,
        fail_next_readback: bool,
        get_failure_file: Option<&'static str>,
        put_hook: Option<Box<dyn FnOnce() + Send>>,
    }

    fn slot_server(fault: Arc<StdMutex<Fault>>) -> (SlotMap, Seen, String) {
        let files: SlotMap = Arc::default();
        let cloud = files.clone();
        let (base, seen) = spawn_server(Arc::new(move |request| {
            let mut response = MockResponse {
                status: 404,
                headers: vec![],
                body: vec![],
                truncate_body_after: None,
            };
            match request.method.as_str() {
                "MKCOL" => response.status = 201,
                "PROPFIND" => {
                    response.status = 207;
                    response.body = br#"<D:multistatus xmlns:D="DAV:"><D:response><D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response></D:multistatus>"#.to_vec();
                }
                "GET" => {
                    let mut fault = fault.lock().unwrap();
                    if fault
                        .get_failure_file
                        .is_some_and(|file| request.path.ends_with(file))
                    {
                        response.status = 500;
                    } else if fault.fail_next_readback {
                        fault.fail_next_readback = false;
                        response.status = 500;
                    } else if let Some(bytes) = cloud.lock().unwrap().get(&request.path) {
                        response.status = 200;
                        response.body = bytes.clone();
                    }
                }
                "PUT" => {
                    let mut fault = fault.lock().unwrap();
                    let failure = fault.put_failure.take();
                    let hook = fault.put_hook.take();
                    let bytes = if failure == Some("partial") {
                        request.body[..request.body.len() / 2].to_vec()
                    } else {
                        request.body.clone()
                    };
                    cloud.lock().unwrap().insert(request.path.clone(), bytes);
                    response.status = if matches!(failure, Some("partial") | Some("lost")) {
                        504
                    } else {
                        201
                    };
                    fault.fail_next_readback = failure == Some("readback");
                    drop(fault);
                    // Runs after the cloud file changed but before the
                    // publisher's readback — the interleaving window.
                    if let Some(hook) = hook {
                        hook();
                    }
                }
                "MOVE" => response.status = 409,
                "DELETE" => response.status = 405,
                _ => {}
            }
            response
        }));
        (files, seen, base)
    }

    struct InlineTx;
    impl Transaction for InlineTx {
        fn run<T: Send>(
            &self,
            operation: impl FnOnce() -> Result<T, String> + Send,
        ) -> Result<T, String> {
            operation()
        }
    }

    /// Real cross-process file-lock transaction (the production semantics).
    #[derive(Clone)]
    struct DiskTx(std::path::PathBuf);
    impl Transaction for DiskTx {
        fn run<T: Send>(
            &self,
            operation: impl FnOnce() -> Result<T, String> + Send,
        ) -> Result<T, String> {
            with_transaction(&self.0, operation)
        }
    }

    fn write_payload(dir: &Path, name: &str, payload: &[u8]) -> std::path::PathBuf {
        let path = dir.join(name);
        let mut file = std::fs::File::create(&path).unwrap();
        file.write_all(payload).unwrap();
        path
    }

    /// Vault pre-configured with Nutstore credentials and a business identity.
    fn seeded_vault(base_url: &str) -> MemorySlots {
        let vault = MemorySlots::default();
        let config = serde_json::json!({"base_url": base_url, "username": "tester@example.test"});
        vault
            .store_secret("nutstore-dav-config-v1", config.to_string().as_bytes())
            .unwrap();
        vault
            .store_secret("nutstore-dav-password-v1", b"test-app-password")
            .unwrap();
        vault
            .store_secret(crate::ledger::LEDGER_PUBLISH_ENABLED_SLOT, b"true")
            .unwrap();
        crate::ledger_sync::initialize(&vault).unwrap();
        vault
    }

    fn cloud_revision(files: &SlotMap, file: &str) -> u64 {
        let bytes = files
            .lock()
            .unwrap()
            .get(file)
            .expect("slot file exists")
            .clone();
        EncryptedEnvelope::from_json(&bytes)
            .unwrap()
            .metadata
            .revision
    }

    fn cached_revision(vault: &MemorySlots, dir: &Path) -> u64 {
        let identity = ledger::load_identity(vault).unwrap().unwrap();
        let key = ledger::load_key(vault).unwrap().unwrap();
        ledger::read_cache(&dir.join(LEDGER_CACHE_FILE), &key, &identity)
            .unwrap()
            .map(|envelope| envelope.metadata.revision)
            .unwrap_or(0)
    }

    fn payload_variant(base: &[u8], marker: &str) -> Vec<u8> {
        let mut value: serde_json::Value = serde_json::from_slice(base).unwrap();
        value["months"][0]["records"][0]["title"] =
            serde_json::json!(format!("合成商户 · {marker}"));
        value.to_string().into_bytes()
    }

    fn legacy_payload() -> Vec<u8> {
        let mut value: serde_json::Value = serde_json::from_slice(&valid_payload()).unwrap();
        value["schemaVersion"] = serde_json::json!(2);
        for month in value["months"].as_array_mut().unwrap() {
            month.as_object_mut().unwrap().remove("incomeRecords");
            month.as_object_mut().unwrap().remove("incomeCategories");
            month["summary"]
                .as_object_mut()
                .unwrap()
                .remove("incomeCents");
        }
        value.to_string().into_bytes()
    }

    fn seal(vault: &MemorySlots, payload: &[u8], revision: u64) -> EncryptedEnvelope {
        let identity = ledger::load_identity(vault).unwrap().unwrap();
        encrypt(
            payload,
            &ledger::load_key(vault).unwrap().unwrap(),
            SnapshotMetadata {
                format_version: 1,
                ledger_id: identity.ledger_id,
                account_id: identity.account_id,
                key_generation: identity.key_generation,
                revision,
            },
        )
        .unwrap()
    }

    #[test]
    fn schema_upgrade_direct_legacy_pending_is_preserved_without_upload() {
        let (_, seen, base) = slot_server(Arc::default());
        let vault = seeded_vault(&base);
        let dir = tempfile::tempdir().unwrap();
        let pending = seal(&vault, &legacy_payload(), 1);
        ledger::persist_cache(&dir.path().join(LEDGER_PENDING_FILE), &pending).unwrap();
        let before = fs::read(dir.path().join(LEDGER_PENDING_FILE)).unwrap();
        assert!(publish_staged(&vault, dir.path(), &InlineTx).is_err());
        assert_eq!(
            fs::read(dir.path().join(LEDGER_PENDING_FILE)).unwrap(),
            before
        );
        assert!(seen.lock().unwrap().is_empty());
    }

    #[test]
    fn schema_upgrade_signature_rejects_invalid_legacy_contracts() {
        let old = legacy_payload();
        assert_ne!(
            ledger::content_signature(&old).unwrap(),
            ledger::content_signature(&valid_payload()).unwrap()
        );
        for change in ["schema", "summary", "unknown", "income", "purpose"] {
            let mut v: serde_json::Value = serde_json::from_slice(&old).unwrap();
            match change {
                "schema" => v["schemaVersion"] = serde_json::json!(99),
                "summary" => v["months"][0]["summary"]["personalCents"] = serde_json::json!(999),
                "unknown" => v["months"][0]["secret"] = serde_json::json!("forbidden"),
                "income" => v["months"][0]["incomeRecords"] = serde_json::json!([]),
                _ => v["snapshotKind"] = serde_json::json!("another-purpose"),
            }
            assert!(
                ledger::content_signature(v.to_string().as_bytes()).is_err(),
                "{change}"
            );
        }
    }

    #[test]
    fn schema_upgrade_invalid_or_foreign_legacy_slot_is_never_overwritten() {
        for defect in [
            "schema", "purpose", "summary", "unknown", "income", "identity",
        ] {
            let (files, seen, base) = slot_server(Arc::default());
            let vault = seeded_vault(&base);
            let dir = tempfile::tempdir().unwrap();
            let mut value: serde_json::Value = serde_json::from_slice(&legacy_payload()).unwrap();
            match defect {
                "schema" => value["schemaVersion"] = serde_json::json!(99),
                "purpose" => value["snapshotKind"] = serde_json::json!("another-purpose"),
                "summary" => {
                    value["months"][0]["summary"]["personalCents"] = serde_json::json!(999)
                }
                "unknown" => value["months"][0]["unexpected"] = serde_json::json!(true),
                "income" => value["months"][0]["summary"]["incomeCents"] = serde_json::json!(0),
                _ => (),
            }
            let envelope = if defect == "identity" {
                let foreign = seeded_vault(&base);
                seal(&foreign, &value.to_string().into_bytes(), 1)
            } else {
                seal(&vault, &value.to_string().into_bytes(), 1)
            };
            let remote = format!("/{}/{}", ledger::LEDGER_REMOTE_DIRECTORY, LEDGER_FILES[0]);
            files
                .lock()
                .unwrap()
                .insert(remote, envelope.to_json().unwrap());
            let before = files.lock().unwrap().clone();
            let path = write_payload(dir.path(), "new.json", &valid_payload());
            assert!(
                prepare_and_publish(&vault, dir.path(), &InlineTx, &path, false).is_err(),
                "{defect}"
            );
            assert_eq!(*files.lock().unwrap(), before, "{defect}");
            assert!(
                !seen.lock().unwrap().iter().any(|r| r.method == "PUT"),
                "{defect}"
            );
            assert_eq!(cached_revision(&vault, dir.path()), 0);
        }
    }

    #[test]
    fn schema_upgrade_legacy_duplicate_fields_are_rejected() {
        let text = String::from_utf8(legacy_payload()).unwrap();
        let duplicate = text.replace(
            "\"schemaVersion\":2",
            "\"schemaVersion\":2,\"schemaVersion\":2",
        );
        assert_ne!(duplicate, text);
        assert!(ledger::content_signature(duplicate.as_bytes()).is_err());
        let value: serde_json::Value = serde_json::from_str(&text).unwrap();
        let amount = format!(
            "\"personalCents\":{}",
            value["months"][0]["summary"]["personalCents"]
        );
        let duplicate = text.replacen(&amount, &format!("{amount},{amount}"), 1);
        assert_ne!(duplicate, text);
        assert!(ledger::content_signature(duplicate.as_bytes()).is_err());
    }

    #[test]
    fn schema_upgrade_confirmed_and_pending_legacy_slots_publish_v3() {
        for pending in [false, true] {
            let (files, seen, base) = slot_server(Arc::default());
            let vault = seeded_vault(&base);
            let dir = tempfile::tempdir().unwrap();
            let secrets = vault.slots.lock().unwrap().clone();
            let old = legacy_payload();
            let previous = seal(&vault, &old, 4);
            let confirmed = seal(&vault, &old, 5);
            let slot_path = |file: &str| format!("/{}/{}", ledger::LEDGER_REMOTE_DIRECTORY, file);
            files
                .lock()
                .unwrap()
                .insert(slot_path(LEDGER_FILES[0]), confirmed.to_json().unwrap());
            files
                .lock()
                .unwrap()
                .insert(slot_path(LEDGER_FILES[1]), previous.to_json().unwrap());
            ledger::persist_cache(&dir.path().join(LEDGER_CACHE_FILE), &confirmed).unwrap();
            save_publish_state(dir.path(), 5).unwrap();
            if pending {
                let pending = seal(&vault, &old, 6);
                ledger::persist_cache(&dir.path().join(LEDGER_PENDING_FILE), &pending).unwrap();
                save_intent(dir.path(), 1, &pending, false).unwrap();
                let before = fs::read(dir.path().join(LEDGER_PENDING_FILE)).unwrap();
                assert!(publish_staged(&vault, dir.path(), &InlineTx).is_err());
                assert_eq!(
                    fs::read(dir.path().join(LEDGER_PENDING_FILE)).unwrap(),
                    before
                );
                assert!(!seen.lock().unwrap().iter().any(|r| r.method == "PUT"));
            }
            let path = write_payload(dir.path(), "new.json", &valid_payload());
            let outcome = prepare_and_publish(&vault, dir.path(), &InlineTx, &path, false).unwrap();
            assert_eq!(outcome.revision, if pending { 7 } else { 6 });
            assert!(!outcome.already_published);
            assert_eq!(cached_revision(&vault, dir.path()), outcome.revision);
            assert_eq!(cloud_revision(&files, &slot_path(LEDGER_FILES[0])), 5);
            assert_eq!(
                cloud_revision(&files, &slot_path(LEDGER_FILES[1])),
                outcome.revision
            );
            assert_eq!(*vault.slots.lock().unwrap(), secrets);
            assert!(
                seen.lock()
                    .unwrap()
                    .iter()
                    .all(|r| r.path.starts_with(&format!(
                        "/{}",
                        ledger::LEDGER_REMOTE_DIRECTORY.split('/').next().unwrap()
                    )))
            );
        }
    }

    #[test]
    fn schema_upgrade_old_cloud_and_offline_preserve_v3_cache() {
        let fault = Arc::new(StdMutex::new(Fault::default()));
        let (files, _, base) = slot_server(fault.clone());
        let vault = seeded_vault(&base);
        let dir = tempfile::tempdir().unwrap();
        let current = seal(&vault, &valid_payload(), 4);
        ledger::persist_cache(&dir.path().join(LEDGER_CACHE_FILE), &current).unwrap();
        let bytes = fs::read(dir.path().join(LEDGER_CACHE_FILE)).unwrap();
        let old = seal(&vault, &legacy_payload(), 5);
        files.lock().unwrap().insert(
            format!("/{}/{}", ledger::LEDGER_REMOTE_DIRECTORY, LEDGER_FILES[0]),
            old.to_json().unwrap(),
        );
        for offline in [false, true] {
            if offline {
                fault.lock().unwrap().get_failure_file = Some(LEDGER_FILES[0]);
            }
            assert!(fetch_flow(&vault, dir.path(), &InlineTx).is_err());
            assert_eq!(fs::read(dir.path().join(LEDGER_CACHE_FILE)).unwrap(), bytes);
            let identity = ledger::load_identity(&vault).unwrap().unwrap();
            let key = ledger::load_key(&vault).unwrap().unwrap();
            assert_eq!(
                snapshot_value(&current, &key, &identity).unwrap()["schemaVersion"],
                3
            );
            assert!(snapshot_value(&old, &key, &identity).is_err());
        }
        let path = write_payload(dir.path(), "old.json", &legacy_payload());
        let original = fs::read(&path).unwrap();
        assert!(stage_pending(&vault, dir.path(), &InlineTx, &path, false).is_err());
        assert_eq!(fs::read(path).unwrap(), original);
        assert_eq!(fs::read(dir.path().join(LEDGER_CACHE_FILE)).unwrap(), bytes);
    }

    #[test]
    fn preparation_receipt_counts_income_records() {
        let (_, seen, base) = slot_server(Arc::default());
        let vault = seeded_vault(&base);
        let dir = tempfile::tempdir().unwrap();
        let payload = include_bytes!("../../../tests/fixtures/app-v2-snapshot.json");
        let path = write_payload(dir.path(), "snapshot.json", payload);
        let prepared = stage_pending(&vault, dir.path(), &InlineTx, &path, false).unwrap();
        // Shared fixture: 9 consumption + 2 structural + 2 income records.
        assert_eq!(prepared.exported_records, 13);
        assert!(seen.lock().unwrap().is_empty());
    }

    #[test]
    fn cloud_namespace_preview_upgrade_preserves_cache_and_old_scope_evidence() {
        if crate::cloud_scope::IDENTIFIER != "com.account.preview" {
            return;
        }
        let (files, _, base) = slot_server(Arc::new(StdMutex::new(Fault::default())));
        let vault = seeded_vault(&base);
        let dir = tempfile::tempdir().unwrap();
        let identity = ledger::load_identity(&vault).unwrap().unwrap();
        let key = ledger::load_key(&vault).unwrap().unwrap();
        let payload = valid_payload();
        let make = |revision| {
            encrypt(
                &payload,
                &key,
                SnapshotMetadata {
                    format_version: 1,
                    ledger_id: identity.ledger_id.clone(),
                    account_id: identity.account_id.clone(),
                    key_generation: identity.key_generation,
                    revision,
                },
            )
            .unwrap()
        };
        ledger::persist_cache(&dir.path().join(LEDGER_CACHE_FILE), &make(5)).unwrap();
        ledger::persist_cache(&dir.path().join(LEDGER_PENDING_FILE), &make(2)).unwrap();
        let legacy_state = br#"{"lastPublishedRevision":5}"#;
        let legacy_intent = b"old recovery evidence stays untouched";
        fs::write(dir.path().join("ledger-publish-state.json"), legacy_state).unwrap();
        fs::write(dir.path().join("ledger-slot-intent.json"), legacy_intent).unwrap();
        assert!(publish_staged(&vault, dir.path(), &InlineTx).is_err());
        assert_eq!(cached_revision(&vault, dir.path()), 5);
        assert!(files.lock().unwrap().is_empty());
        let path = write_payload(dir.path(), "snapshot.json", &payload);
        let result = prepare_and_publish(&vault, dir.path(), &InlineTx, &path, false).unwrap();
        assert!(!result.already_published);
        assert_eq!(result.revision, 6);
        assert_eq!(
            cloud_revision(&files, "/Account-Preview/ledger/snapshot-a.enc"),
            6
        );
        assert_eq!(cached_revision(&vault, dir.path()), 6);
        assert_eq!(
            fs::read(dir.path().join("ledger-publish-state.json")).unwrap(),
            legacy_state
        );
        assert_eq!(
            fs::read(dir.path().join("ledger-slot-intent.json")).unwrap(),
            legacy_intent
        );
        assert_eq!(ledger::load_identity(&vault).unwrap().unwrap(), identity);
    }

    #[test]
    fn cloud_namespace_preview_publish_and_phone_fetch_never_touch_personal_paths() {
        if crate::cloud_scope::IDENTIFIER != "com.account.preview" {
            return;
        }
        let (files, seen, base) = slot_server(Arc::new(StdMutex::new(Fault::default())));
        let production = [
            (
                "/Account/ledger/snapshot-a.enc",
                b"personal-ledger".to_vec(),
            ),
            ("/Account/probe/snapshot-a.enc", b"personal-probe".to_vec()),
            (
                "/Account/inbox/private/existing.bill.enc",
                b"personal-inbox".to_vec(),
            ),
        ];
        for (path, bytes) in &production {
            files.lock().unwrap().insert((*path).into(), bytes.clone());
        }
        let vault = seeded_vault(&base);
        let dir = tempfile::tempdir().unwrap();
        let path = write_payload(dir.path(), "snapshot.json", &valid_payload());
        prepare_and_publish(&vault, dir.path(), &InlineTx, &path, false).unwrap();
        let phone = seeded_vault(&base);
        // A paired phone uses the desktop identity/key, not a new ledger.
        for slot in [ledger::LEDGER_IDENTITY_SLOT, ledger::LEDGER_KEY_SLOT] {
            phone
                .store_secret(slot, &vault.load_secret(slot).unwrap().unwrap())
                .unwrap();
        }
        let phone_dir = tempfile::tempdir().unwrap();
        assert!(matches!(
            fetch_flow(&phone, phone_dir.path(), &InlineTx).unwrap(),
            LedgerFetchOutcome::Updated { revision: 1, .. }
        ));
        assert_eq!(cached_revision(&phone, phone_dir.path()), 1);
        for (path, bytes) in &production {
            assert_eq!(files.lock().unwrap().get(*path), Some(bytes));
        }
        assert!(seen.lock().unwrap().iter().all(|request| request.path != "/Account" && !request.path.starts_with("/Account/")));
        assert!(
            files
                .lock()
                .unwrap()
                .contains_key("/Account-Preview/ledger/snapshot-a.enc")
        );
    }

    #[test]
    fn three_consecutive_publishes_reuse_slots_without_move() {
        let fault = Arc::new(StdMutex::new(Fault::default()));
        let (files, seen, base) = slot_server(fault);
        let vault = seeded_vault(&base);
        let dir = tempfile::tempdir().unwrap();
        for revision in 1..=3u64 {
            let payload = if revision == 1 {
                valid_payload()
            } else {
                payload_variant(&valid_payload(), &format!("r{revision}"))
            };
            let path = write_payload(dir.path(), "snapshot.json", &payload);
            let outcome = stage_pending(&vault, dir.path(), &InlineTx, &path, false).unwrap();
            assert_eq!(outcome.revision, revision);
            assert!(!outcome.reused);
            let published = publish_staged(&vault, dir.path(), &InlineTx).unwrap();
            assert_eq!(published.revision, revision);
        }
        assert_eq!(
            cloud_revision(
                &files,
                concat!("/", env!("ACCOUNT_REMOTE_ROOT"), "/ledger/snapshot-a.enc")
            ),
            3
        );
        assert_eq!(
            cloud_revision(
                &files,
                concat!("/", env!("ACCOUNT_REMOTE_ROOT"), "/ledger/snapshot-b.enc")
            ),
            2
        );
        assert!(
            seen.lock()
                .unwrap()
                .iter()
                .all(|r| r.method != "MOVE" && r.method != "DELETE")
        );
    }

    #[test]
    fn same_content_with_new_timestamp_reuses_revision() {
        let (files, _, base) = slot_server(Arc::default());
        let vault = seeded_vault(&base);
        let dir = tempfile::tempdir().unwrap();
        let path = write_payload(dir.path(), "snapshot.json", &valid_payload());
        prepare_and_publish(&vault, dir.path(), &InlineTx, &path, false).unwrap();
        // Re-export of the same content only changed generatedAt.
        let mut value: serde_json::Value = serde_json::from_slice(&valid_payload()).unwrap();
        value["generatedAt"] = serde_json::json!("2026-10-15T20:00:00+08:00");
        let same = write_payload(dir.path(), "same.json", value.to_string().as_bytes());
        let outcome = prepare_and_publish(&vault, dir.path(), &InlineTx, &same, false).unwrap();
        assert!(
            outcome.reused,
            "identical content must not manufacture a new version"
        );
        assert_eq!(outcome.revision, 1);
        assert_eq!(
            cloud_revision(
                &files,
                concat!("/", env!("ACCOUNT_REMOTE_ROOT"), "/ledger/snapshot-a.enc")
            ),
            1
        );
        assert!(!dir.path().join(LEDGER_PENDING_FILE).exists());
    }

    #[test]
    fn newest_content_wins_bounded_queue() {
        let (files, _, base) = slot_server(Arc::default());
        let vault = seeded_vault(&base);
        let dir = tempfile::tempdir().unwrap();
        let first = write_payload(dir.path(), "a.json", &valid_payload());
        let staged = stage_pending(&vault, dir.path(), &InlineTx, &first, false).unwrap();
        assert_eq!(staged.revision, 1);
        // New content arrives before the first publish succeeded.
        let newer = write_payload(
            dir.path(),
            "b.json",
            &payload_variant(&valid_payload(), "b"),
        );
        let outcome = stage_pending(&vault, dir.path(), &InlineTx, &newer, false).unwrap();
        assert!(!outcome.reused);
        assert_eq!(outcome.revision, 2);
        assert_eq!(cached_revision(&vault, dir.path()), 0);
        // Publishing now uploads only the newest content; version 1 never reaches the cloud.
        let published = publish_staged(&vault, dir.path(), &InlineTx).unwrap();
        assert_eq!(published.revision, 2);
        assert_eq!(
            cloud_revision(
                &files,
                concat!("/", env!("ACCOUNT_REMOTE_ROOT"), "/ledger/snapshot-a.enc")
            ),
            2
        );
        assert_eq!(cached_revision(&vault, dir.path()), 2);
        let bytes = files
            .lock()
            .unwrap()
            .get(concat!(
                "/",
                env!("ACCOUNT_REMOTE_ROOT"),
                "/ledger/snapshot-a.enc"
            ))
            .unwrap()
            .clone();
        let envelope = EncryptedEnvelope::from_json(&bytes).unwrap();
        let identity = ledger::load_identity(&vault).unwrap().unwrap();
        let key = ledger::load_key(&vault).unwrap().unwrap();
        let plaintext = decrypt(&envelope, &key, identity.expected()).unwrap();
        let value: serde_json::Value = serde_json::from_slice(&plaintext).unwrap();
        assert_eq!(value["months"][0]["records"][0]["title"], "合成商户 · b");
        assert!(
            !files
                .lock()
                .unwrap()
                .iter()
                .any(|(path, _)| path.ends_with("snapshot-b.enc"))
        );
    }

    #[test]
    fn retry_after_failed_put_keeps_same_ciphertext_and_revision() {
        let fault = Arc::new(StdMutex::new(Fault {
            put_failure: Some("partial"),
            ..Default::default()
        }));
        let (files, seen, base) = slot_server(fault);
        let vault = seeded_vault(&base);
        let dir = tempfile::tempdir().unwrap();
        let path = write_payload(dir.path(), "snapshot.json", &valid_payload());
        assert!(prepare_and_publish(&vault, dir.path(), &InlineTx, &path, false).is_err());
        let pending = std::fs::read(dir.path().join(LEDGER_PENDING_FILE)).unwrap();
        assert_eq!(cached_revision(&vault, dir.path()), 0);
        let outcome = prepare_and_publish(&vault, dir.path(), &InlineTx, &path, false).unwrap();
        assert_eq!(outcome.revision, 1);
        assert_eq!(cached_revision(&vault, dir.path()), 1);
        let puts: Vec<Vec<u8>> = seen
            .lock()
            .unwrap()
            .iter()
            .filter(|r| r.method == "PUT" && r.path.ends_with("snapshot-a.enc"))
            .map(|r| r.body.clone())
            .collect();
        assert_eq!(puts.len(), 2);
        assert_eq!(puts[0], puts[1]);
        assert_eq!(
            files
                .lock()
                .unwrap()
                .get(concat!(
                    "/",
                    env!("ACCOUNT_REMOTE_ROOT"),
                    "/ledger/snapshot-a.enc"
                ))
                .unwrap(),
            &pending
        );
    }

    #[test]
    fn lost_put_response_confirms_on_retry_without_second_upload() {
        for failure in ["lost", "readback"] {
            let fault = Arc::new(StdMutex::new(Fault {
                put_failure: Some(failure),
                ..Default::default()
            }));
            let (_, seen, base) = slot_server(fault);
            let vault = seeded_vault(&base);
            let dir = tempfile::tempdir().unwrap();
            let path = write_payload(dir.path(), "snapshot.json", &valid_payload());
            assert!(prepare_and_publish(&vault, dir.path(), &InlineTx, &path, false).is_err());
            let outcome = prepare_and_publish(&vault, dir.path(), &InlineTx, &path, false).unwrap();
            assert_eq!(outcome.revision, 1);
            assert_eq!(
                seen.lock()
                    .unwrap()
                    .iter()
                    .filter(|r| r.method == "PUT")
                    .count(),
                1
            );
        }
    }

    #[test]
    fn damaged_or_foreign_cloud_file_is_never_overwritten() {
        let (files, seen, base) = slot_server(Arc::default());
        let vault = seeded_vault(&base);
        let dir = tempfile::tempdir().unwrap();
        let stranger = {
            let other = MemorySlots::default();
            crate::ledger_sync::initialize(&other).unwrap();
            let identity = ledger::load_identity(&other).unwrap().unwrap();
            let key = ledger::load_key(&other).unwrap().unwrap();
            encrypt(
                &valid_payload(),
                &key,
                SnapshotMetadata {
                    format_version: 1,
                    ledger_id: identity.ledger_id.clone(),
                    account_id: identity.account_id.clone(),
                    key_generation: identity.key_generation,
                    revision: 9,
                },
            )
            .unwrap()
            .to_json()
            .unwrap()
        };
        files.lock().unwrap().insert(
            concat!("/", env!("ACCOUNT_REMOTE_ROOT"), "/ledger/snapshot-a.enc").into(),
            b"unknown broken bytes".to_vec(),
        );
        files.lock().unwrap().insert(
            concat!("/", env!("ACCOUNT_REMOTE_ROOT"), "/ledger/snapshot-b.enc").into(),
            stranger,
        );
        let path = write_payload(dir.path(), "snapshot.json", &valid_payload());
        assert!(prepare_and_publish(&vault, dir.path(), &InlineTx, &path, false).is_err());
        assert!(seen.lock().unwrap().iter().all(|r| r.method != "PUT"));
        assert_eq!(
            files
                .lock()
                .unwrap()
                .get(concat!(
                    "/",
                    env!("ACCOUNT_REMOTE_ROOT"),
                    "/ledger/snapshot-a.enc"
                ))
                .unwrap(),
            b"unknown broken bytes"
        );
    }

    #[test]
    fn higher_cloud_version_blocks_publish_and_lower_cloud_version_blocks_fetch() {
        let (files, _, base) = slot_server(Arc::default());
        let vault = seeded_vault(&base);
        let dir = tempfile::tempdir().unwrap();
        let identity = ledger::load_identity(&vault).unwrap().unwrap();
        let key = ledger::load_key(&vault).unwrap().unwrap();
        files.lock().unwrap().insert(
            concat!("/", env!("ACCOUNT_REMOTE_ROOT"), "/ledger/snapshot-a.enc").into(),
            encrypt(
                &payload_variant(&valid_payload(), "high"),
                &key,
                SnapshotMetadata {
                    format_version: 1,
                    ledger_id: identity.ledger_id.clone(),
                    account_id: identity.account_id.clone(),
                    key_generation: identity.key_generation,
                    revision: 5,
                },
            )
            .unwrap()
            .to_json()
            .unwrap(),
        );
        let path = write_payload(dir.path(), "snapshot.json", &valid_payload());
        assert!(prepare_and_publish(&vault, dir.path(), &InlineTx, &path, false).is_err());
        assert_eq!(cached_revision(&vault, dir.path()), 0);
        // Fetch accepts the higher version.
        let fetched = fetch_flow(&vault, dir.path(), &InlineTx).unwrap();
        assert!(matches!(
            fetched,
            LedgerFetchOutcome::Updated { revision: 5, .. }
        ));
        // A lower cloud version is rejected and keeps the cache.
        files.lock().unwrap().insert(
            concat!("/", env!("ACCOUNT_REMOTE_ROOT"), "/ledger/snapshot-b.enc").into(),
            encrypt(
                &valid_payload(),
                &key,
                SnapshotMetadata {
                    format_version: 1,
                    ledger_id: identity.ledger_id.clone(),
                    account_id: identity.account_id.clone(),
                    key_generation: identity.key_generation,
                    revision: 2,
                },
            )
            .unwrap()
            .to_json()
            .unwrap(),
        );
        let again = fetch_flow(&vault, dir.path(), &InlineTx).unwrap();
        assert!(matches!(
            again,
            LedgerFetchOutcome::Latest { revision: 5, .. }
        ));
    }

    #[test]
    fn fetch_rollback_has_its_own_diagnostic_and_preserves_cache() {
        let (files, _, base) = slot_server(Arc::default());
        let vault = seeded_vault(&base);
        let dir = tempfile::tempdir().unwrap();
        let cached = seal(&vault, &valid_payload(), 5);
        ledger::persist_cache(&dir.path().join(LEDGER_CACHE_FILE), &cached).unwrap();
        let before = fs::read(dir.path().join(LEDGER_CACHE_FILE)).unwrap();
        files.lock().unwrap().insert(
            format!("/{LEDGER_REMOTE_DIRECTORY}/{}", LEDGER_FILES[0]),
            seal(&vault, &valid_payload(), 4).to_json().unwrap(),
        );
        let issue = fetch_flow_diagnostic(&vault, dir.path(), &InlineTx).unwrap_err();
        assert_eq!(issue.code, "snapshot-rollback");
        assert_eq!(issue.stage, "accept");
        assert_eq!(
            before,
            fs::read(dir.path().join(LEDGER_CACHE_FILE)).unwrap()
        );
    }

    #[test]
    fn fetch_http_failures_keep_status_and_do_not_claim_cloud_empty() {
        for (status, code, retryable) in [
            (401, "auth-unauthorized", false),
            (403, "access-forbidden", false),
            (409, "cloud-path-conflict", false),
            (429, "rate-limited", true),
            (500, "service-unavailable", true),
            (503, "service-unavailable", true),
        ] {
            let (base, seen) = spawn_server(Arc::new(move |_| MockResponse {
                status,
                headers: vec![],
                body: vec![],
                truncate_body_after: None,
            }));
            let vault = seeded_vault(&base);
            let dir = tempfile::tempdir().unwrap();
            let key = ledger::load_key(&vault).unwrap().unwrap();
            let identity = ledger::load_identity(&vault).unwrap().unwrap();
            let cached = encrypt(
                &valid_payload(),
                &key,
                SnapshotMetadata {
                    format_version: 1,
                    ledger_id: identity.ledger_id.clone(),
                    account_id: identity.account_id.clone(),
                    key_generation: identity.key_generation,
                    revision: 7,
                },
            )
            .unwrap();
            ledger::persist_cache(&dir.path().join(LEDGER_CACHE_FILE), &cached).unwrap();
            let before = fs::read(dir.path().join(LEDGER_CACHE_FILE)).unwrap();
            let error = fetch_flow_diagnostic(&vault, dir.path(), &InlineTx).unwrap_err();
            assert_eq!(
                (error.code, error.http_status, error.retryable),
                (code, Some(status), retryable)
            );
            let serialized = serde_json::to_value(&error).unwrap();
            assert_eq!(serialized["httpStatus"], status);
            assert!(serialized.get("http_status").is_none());
            assert_eq!(
                before,
                fs::read(dir.path().join(LEDGER_CACHE_FILE)).unwrap()
            );
            assert!(seen.lock().unwrap().iter().all(|r| r.method == "GET"));
            if status == 403 || status == 409 {
                assert!(!error.message.contains("密码"));
                assert!(!error.message.contains("首次发布"));
            }
        }
    }

    #[test]
    fn fetch_both_404_are_cloud_empty_with_existing_cache_preserved() {
        let (_, seen, base) = slot_server(Arc::default());
        let vault = seeded_vault(&base);
        let dir = tempfile::tempdir().unwrap();
        let cached = seal(&vault, &valid_payload(), 3);
        ledger::persist_cache(&dir.path().join(LEDGER_CACHE_FILE), &cached).unwrap();
        let before = fs::read(dir.path().join(LEDGER_CACHE_FILE)).unwrap();
        assert!(matches!(
            fetch_flow_diagnostic(&vault, dir.path(), &InlineTx).unwrap(),
            LedgerFetchOutcome::CloudEmpty { has_cache: true }
        ));
        assert_eq!(
            before,
            fs::read(dir.path().join(LEDGER_CACHE_FILE)).unwrap()
        );
        assert_eq!(seen.lock().unwrap().len(), 2);
    }

    #[test]
    fn fetch_old_snapshot_requires_computer_publish_and_corruption_is_separate() {
        for (payload, code) in [
            (legacy_payload(), "snapshot-older-schema"),
            (
                br#"{"schemaVersion":3,"snapshotKind":"account-ledger-view"}"#.to_vec(),
                "snapshot-invalid",
            ),
        ] {
            let (files, _, base) = slot_server(Arc::default());
            let vault = seeded_vault(&base);
            let dir = tempfile::tempdir().unwrap();
            let good = seal(&vault, &valid_payload(), 4);
            ledger::persist_cache(&dir.path().join(LEDGER_CACHE_FILE), &good).unwrap();
            let before = fs::read(dir.path().join(LEDGER_CACHE_FILE)).unwrap();
            for (file, envelope) in [
                (LEDGER_FILES[0], good),
                (LEDGER_FILES[1], seal(&vault, &payload, 5)),
            ] {
                files.lock().unwrap().insert(
                    format!("/{LEDGER_REMOTE_DIRECTORY}/{file}"),
                    envelope.to_json().unwrap(),
                );
            }
            assert_eq!(
                fetch_flow_diagnostic(&vault, dir.path(), &InlineTx)
                    .unwrap_err()
                    .code,
                code
            );
            assert_eq!(
                before,
                fs::read(dir.path().join(LEDGER_CACHE_FILE)).unwrap()
            );
        }
    }

    #[test]
    fn pairing_diagnostics_reject_unsupported_and_foreign_without_writes() {
        let creator = MemorySlots::default();
        initialize(&creator).unwrap();
        let receiver = MemorySlots::default();
        initialize(&receiver).unwrap();
        let before = create_pairing_payload(&receiver).unwrap();
        let identity = ledger::load_identity(&receiver).unwrap();
        let key = ledger::load_key(&receiver).unwrap().unwrap();
        assert_eq!(
            accept_pairing_diagnostic(&receiver, r#"{"kind":"future-pairing-v9"}"#)
                .unwrap_err()
                .code,
            "pairing-unsupported"
        );
        assert_eq!(
            accept_pairing_diagnostic(&receiver, &create_pairing_payload(&creator).unwrap())
                .unwrap_err()
                .code,
            "pairing-foreign-identity"
        );
        assert_eq!(ledger::load_identity(&receiver).unwrap(), identity);
        assert_eq!(
            ledger::load_key(&receiver).unwrap().unwrap().as_bytes(),
            key.as_bytes()
        );
        assert!(accept_pairing_diagnostic(&receiver, &before).is_ok());
    }

    #[test]
    fn fetch_never_accepts_stale_slot_when_newer_authenticated_schema_is_unsupported() {
        let (files, _, base) = slot_server(Arc::default());
        let vault = seeded_vault(&base);
        let dir = tempfile::tempdir().unwrap();
        let identity = ledger::load_identity(&vault).unwrap().unwrap();
        let key = ledger::load_key(&vault).unwrap().unwrap();
        let mut newer: serde_json::Value = serde_json::from_slice(&valid_payload()).unwrap();
        newer["schemaVersion"] = serde_json::json!(4);
        for (file, revision, payload) in [
            (LEDGER_FILES[0], 4, valid_payload()),
            (LEDGER_FILES[1], 5, newer.to_string().into_bytes()),
        ] {
            let envelope = encrypt(
                &payload,
                &key,
                SnapshotMetadata {
                    format_version: 1,
                    ledger_id: identity.ledger_id.clone(),
                    account_id: identity.account_id.clone(),
                    key_generation: identity.key_generation,
                    revision,
                },
            )
            .unwrap();
            files.lock().unwrap().insert(
                format!("/{LEDGER_REMOTE_DIRECTORY}/{file}"),
                envelope.to_json().unwrap(),
            );
        }
        let error = fetch_flow(&vault, dir.path(), &InlineTx)
            .expect_err("newer schema must block stale-slot acceptance");
        assert!(error.contains("更新手机"), "{error}");
        assert_eq!(cached_revision(&vault, dir.path()), 0);
    }

    #[test]
    fn fetch_cloud_empty_then_partial_failure_warns_and_accepts_readable_copy() {
        let fault = Arc::new(StdMutex::new(Fault::default()));
        let (files, _, base) = slot_server(fault.clone());
        let vault = seeded_vault(&base);
        let dir = tempfile::tempdir().unwrap();
        let empty = fetch_flow(&vault, dir.path(), &InlineTx).unwrap();
        assert!(matches!(
            empty,
            LedgerFetchOutcome::CloudEmpty { has_cache: false }
        ));
        let identity = ledger::load_identity(&vault).unwrap().unwrap();
        let key = ledger::load_key(&vault).unwrap().unwrap();
        files.lock().unwrap().insert(
            concat!("/", env!("ACCOUNT_REMOTE_ROOT"), "/ledger/snapshot-b.enc").into(),
            encrypt(
                &valid_payload(),
                &key,
                SnapshotMetadata {
                    format_version: 1,
                    ledger_id: identity.ledger_id.clone(),
                    account_id: identity.account_id.clone(),
                    key_generation: identity.key_generation,
                    revision: 4,
                },
            )
            .unwrap()
            .to_json()
            .unwrap(),
        );
        // Slot A becomes unreachable: the readable copy is still accepted,
        // but the incomplete check must surface as a warning.
        fault.lock().unwrap().get_failure_file = Some("snapshot-a.enc");
        let outcome = fetch_flow(&vault, dir.path(), &InlineTx).unwrap();
        match outcome {
            LedgerFetchOutcome::Updated {
                revision: 4,
                warning: Some(_),
                ..
            } => {}
            other => panic!("expected updated-with-warning, got {other:?}"),
        }
        // With no readable copy at all, an unavailable slot must not be
        // reported as a confirmed empty cloud.
        files.lock().unwrap().clear();
        assert!(fetch_flow(&vault, dir.path(), &InlineTx).is_err());
    }

    #[test]
    fn pairing_import_is_repeatable_and_never_resets_state() {
        let creator = MemorySlots::default();
        crate::ledger_sync::initialize(&creator).unwrap();
        let qr = create_pairing_payload(&creator).unwrap();
        // Receiver: import twice; second import must keep cache and version.
        let receiver = MemorySlots::default();
        accept_pairing(&receiver, &qr).unwrap();
        assert!(crate::ledger::load_identity(&receiver).unwrap().is_some());
        // Simulate an accepted cache at revision 3.
        let identity = ledger::load_identity(&receiver).unwrap().unwrap();
        let key = ledger::load_key(&receiver).unwrap().unwrap();
        let envelope = encrypt(
            &valid_payload(),
            &key,
            SnapshotMetadata {
                format_version: 1,
                ledger_id: identity.ledger_id.clone(),
                account_id: identity.account_id.clone(),
                key_generation: identity.key_generation,
                revision: 3,
            },
        )
        .unwrap();
        let dir = tempfile::tempdir().unwrap();
        ledger::persist_cache(&dir.path().join(LEDGER_CACHE_FILE), &envelope).unwrap();
        accept_pairing(&receiver, &qr).unwrap();
        assert_eq!(cached_revision(&receiver, dir.path()), 3);
        // A different ledger identity is refused before any write.
        let other = MemorySlots::default();
        crate::ledger_sync::initialize(&other).unwrap();
        let foreign_qr = create_pairing_payload(&other).unwrap();
        assert!(accept_pairing(&receiver, &foreign_qr).is_err());
        assert_eq!(cached_revision(&receiver, dir.path()), 3);
        // Tampered sample (wrong key) never installs anything.
        let mut tampered: serde_json::Value = serde_json::from_str(&qr).unwrap();
        tampered["key"] =
            serde_json::json!(base64::engine::general_purpose::STANDARD.encode([7u8; 32]));
        assert!(accept_pairing(&receiver, &tampered.to_string()).is_err());
        assert_eq!(cached_revision(&receiver, dir.path()), 3);
    }

    #[test]
    fn publish_gate_blocks_real_uploads_until_enabled() {
        let vault = MemorySlots::default();
        crate::ledger_sync::initialize(&vault).unwrap();
        // Gate closed: publish must refuse before any network or state change.
        let dir = tempfile::tempdir().unwrap();
        let path = write_payload(dir.path(), "snapshot.json", &valid_payload());
        let closed = vault
            .slots
            .lock()
            .unwrap()
            .get(crate::ledger::LEDGER_PUBLISH_ENABLED_SLOT)
            .cloned();
        assert_ne!(closed, Some(b"true".to_vec()));
        // Gate open (as the reviewed enable command sets it) allows staging.
        crate::ledger_sync::enable_publish(&vault).unwrap();
        assert_eq!(
            vault
                .slots
                .lock()
                .unwrap()
                .get(crate::ledger::LEDGER_PUBLISH_ENABLED_SLOT)
                .cloned(),
            Some(b"true".to_vec())
        );
        let _ = path;
    }

    #[test]
    fn dry_run_never_touches_durable_state() {
        let (_, _, base) = slot_server(Arc::default());
        let vault = seeded_vault(&base);
        let dir = tempfile::tempdir().unwrap();
        let path = write_payload(dir.path(), "snapshot.json", &valid_payload());
        let outcome = prepare_and_publish(&vault, dir.path(), &InlineTx, &path, true).unwrap();
        assert!(outcome.dry_run);
        assert!(!dir.path().join(LEDGER_PENDING_FILE).exists());
        assert!(!dir.path().join(LEDGER_CACHE_FILE).exists());
        assert!(!dir.path().join(LEDGER_STATE_FILE).exists());
    }

    #[test]
    fn snapshot_file_inspection_validates_contract_without_ledger_state() {
        let dir = tempfile::tempdir().unwrap();
        let good = write_payload(dir.path(), "good.json", &valid_payload());
        let inspected = inspect_snapshot_file(good.to_str().unwrap()).unwrap();
        assert_eq!(inspected["schemaVersion"], 3);
        assert_eq!(inspected["snapshotKind"], "account-ledger-view");
        // No ledger durable state was created by inspection.
        assert!(!dir.path().join(LEDGER_CACHE_FILE).exists());
        assert!(!dir.path().join(LEDGER_PENDING_FILE).exists());

        let bad_path = write_payload(dir.path(), "bad.json", br#"{"schemaVersion":2}"#);
        let error = inspect_snapshot_file(bad_path.to_str().unwrap()).unwrap_err();
        assert!(error.contains("契约校验"));

        assert!(
            inspect_snapshot_file(dir.path().join("missing.json").to_str().unwrap())
                .unwrap_err()
                .contains("找不到")
        );
    }

    // -----------------------------------------------------------------------
    // Review R2/R3 regressions (converted from the independent reproductions;
    // each asserts the final cloud content, not just success/failure).
    // -----------------------------------------------------------------------

    fn put_count(seen: &Seen) -> usize {
        seen.lock()
            .unwrap()
            .iter()
            .filter(|r| r.method == "PUT")
            .count()
    }

    fn vault_with_gate(base_url: &str, enabled: bool) -> MemorySlots {
        let vault = seeded_vault(base_url);
        if !enabled {
            vault
                .delete_secret(crate::ledger::LEDGER_PUBLISH_ENABLED_SLOT)
                .unwrap();
        }
        vault
    }

    #[test]
    fn closed_gate_blocks_every_real_upload_path_with_zero_puts() {
        let fault = Arc::new(StdMutex::new(Fault::default()));
        let (files, seen, base) = slot_server(fault);
        let vault = vault_with_gate(&base, false);
        let dir = tempfile::tempdir().unwrap();
        let a = write_payload(dir.path(), "a.json", &valid_payload());

        // Staging is local-only and stays allowed.
        stage_pending(&vault, dir.path(), &InlineTx, &a, false).unwrap();
        assert!(publish_staged(&vault, dir.path(), &InlineTx).is_err());
        assert!(prepare_and_publish(&vault, dir.path(), &InlineTx, &a, false).is_err());
        // Both publish entries ran through the full chain and never uploaded.
        assert_eq!(put_count(&seen), 0);
        assert!(files.lock().unwrap().is_empty());
        // The staged pending survived the refused publish.
        assert!(dir.path().join(LEDGER_PENDING_FILE).exists());
    }

    #[test]
    fn partial_put_then_new_content_recovers_with_slot_evidence() {
        let fault = Arc::new(StdMutex::new(Fault::default()));
        let (files, seen, base) = slot_server(fault.clone());
        let vault = seeded_vault(&base);
        let dir = tempfile::tempdir().unwrap();
        let a = write_payload(dir.path(), "a.json", &valid_payload());
        let b = write_payload(
            dir.path(),
            "b.json",
            &payload_variant(&valid_payload(), "B"),
        );

        fault.lock().unwrap().put_failure = Some("partial");
        assert!(prepare_and_publish(&vault, dir.path(), &InlineTx, &a, false).is_err());
        assert!(dir.path().join(LEDGER_INTENT_FILE).exists());
        // A newer source replaces the pending; the intent evidence must
        // survive (previously it was deleted and the retry was stuck forever).
        let staged = stage_pending(&vault, dir.path(), &InlineTx, &b, false).unwrap();
        assert_eq!(staged.revision, 2);
        assert!(dir.path().join(LEDGER_INTENT_FILE).exists());
        let identity = ledger::load_identity(&vault).unwrap().unwrap();
        let key = ledger::load_key(&vault).unwrap().unwrap();
        let pending = ledger::read_cache(&dir.path().join(LEDGER_PENDING_FILE), &key, &identity)
            .unwrap()
            .unwrap();
        assert_eq!(pending.metadata.revision, 2);
        let published = publish_staged(&vault, dir.path(), &InlineTx).unwrap();
        assert_eq!(published.revision, 2);
        // Final cloud content is the complete newest envelope on the intended
        // slot; local pending and intent are cleaned up.
        let cloud = files.lock().unwrap();
        assert!(
            cloud
                .values()
                .any(|bytes| EncryptedEnvelope::from_json(bytes).unwrap() == pending)
        );
        assert!(cloud.values().all(|bytes| {
            EncryptedEnvelope::from_json(bytes)
                .unwrap()
                .metadata
                .revision
                == 2
        }));
        assert!(!dir.path().join(LEDGER_PENDING_FILE).exists());
        assert!(!dir.path().join(LEDGER_INTENT_FILE).exists());
        // Exactly the retried PUT landed (partial + retry of revision 2;
        // the broken revision 1 partial is overwritten, never re-sent).
        assert_eq!(put_count(&seen), 2);
    }

    #[test]
    fn reverting_to_published_content_cancels_stale_pending() {
        let fault = Arc::new(StdMutex::new(Fault::default()));
        let (files, seen, base) = slot_server(fault);
        let vault = seeded_vault(&base);
        let dir = tempfile::tempdir().unwrap();
        let a = write_payload(dir.path(), "a.json", &valid_payload());
        let b = write_payload(
            dir.path(),
            "b.json",
            &payload_variant(&valid_payload(), "B"),
        );

        assert!(prepare_and_publish(&vault, dir.path(), &InlineTx, &a, false).is_ok());
        stage_pending(&vault, dir.path(), &InlineTx, &b, false).unwrap();
        // Latest input returns to A: B is stale and must be cancelled, and
        // the A-input publish is an idempotent success, never a B upload.
        let outcome = prepare_and_publish(&vault, dir.path(), &InlineTx, &a, false).unwrap();
        assert!(outcome.already_published);
        assert!(outcome.stale_pending_cancelled);
        assert_eq!(outcome.revision, 1);
        assert!(!dir.path().join(LEDGER_PENDING_FILE).exists());
        assert_eq!(put_count(&seen), 1, "only the original A publish may PUT");
        assert_eq!(load_publish_state(dir.path()).unwrap(), 1);
        // Cloud still holds exactly revision 1.
        assert!(files.lock().unwrap().values().all(|bytes| {
            EncryptedEnvelope::from_json(bytes)
                .unwrap()
                .metadata
                .revision
                == 1
        }));
        // Repeating the same --publish stays idempotent instead of erroring
        // with "no pending".
        let again = prepare_and_publish(&vault, dir.path(), &InlineTx, &a, false).unwrap();
        assert!(again.already_published);
        assert!(!again.stale_pending_cancelled);
        assert_eq!(put_count(&seen), 1);
    }

    #[test]
    fn reverting_after_unconfirmed_upload_preserves_recovery_and_revision_floor() {
        for failure in ["partial", "lost"] {
            let fault = Arc::new(StdMutex::new(Fault::default()));
            let (files, seen, base) = slot_server(fault.clone());
            let vault = seeded_vault(&base);
            let dir = tempfile::tempdir().unwrap();
            let a = write_payload(dir.path(), "a.json", &valid_payload());
            let b = write_payload(
                dir.path(),
                "b.json",
                &payload_variant(&valid_payload(), "B"),
            );
            let c = write_payload(
                dir.path(),
                "c.json",
                &payload_variant(&valid_payload(), "C"),
            );
            prepare_and_publish(&vault, dir.path(), &InlineTx, &a, false).unwrap();
            fault.lock().unwrap().put_failure = Some(failure);
            assert!(prepare_and_publish(&vault, dir.path(), &InlineTx, &b, false).is_err());
            assert!(dir.path().join(LEDGER_INTENT_FILE).exists());

            let restored = prepare_and_publish(&vault, dir.path(), &InlineTx, &a, false).unwrap();
            assert!(
                !restored.already_published,
                "{failure}: B may already be on the server"
            );
            assert_eq!(restored.revision, 3);
            let latest = files
                .lock()
                .unwrap()
                .values()
                .map(|bytes| EncryptedEnvelope::from_json(bytes).unwrap())
                .max_by_key(|envelope| envelope.metadata.revision)
                .unwrap();
            assert_eq!(latest.metadata.revision, 3);
            let identity = ledger::load_identity(&vault).unwrap().unwrap();
            let key = ledger::load_key(&vault).unwrap().unwrap();
            let plaintext = decrypt(&latest, &key, identity.expected()).unwrap();
            assert_eq!(
                ledger::content_signature(&plaintext).unwrap(),
                ledger::content_signature(&valid_payload()).unwrap()
            );
            assert_eq!(put_count(&seen), 3);
            assert!(!dir.path().join(LEDGER_PENDING_FILE).exists());
            assert!(!dir.path().join(LEDGER_INTENT_FILE).exists());
            assert_eq!(
                prepare_and_publish(&vault, dir.path(), &InlineTx, &c, false)
                    .unwrap()
                    .revision,
                4
            );
        }
    }

    #[test]
    fn final_commit_never_deletes_a_newer_pending() {
        let fault = Arc::new(StdMutex::new(Fault::default()));
        let (files, _, base) = slot_server(fault.clone());
        let vault = seeded_vault(&base);
        let dir = tempfile::tempdir().unwrap();
        let a = write_payload(dir.path(), "a.json", &valid_payload());
        let b = write_payload(
            dir.path(),
            "b.json",
            &payload_variant(&valid_payload(), "B"),
        );

        // Simulate another entry staging revision 2 while revision 1 is in
        // flight (InlineTx has no file lock, so the PUT hook can interleave).
        stage_pending(&vault, dir.path(), &InlineTx, &a, false).unwrap();
        let hook_vault = vault.clone();
        let hook_dir = dir.path().to_owned();
        fault.lock().unwrap().put_hook = Some(Box::new(move || {
            stage_pending(&hook_vault, &hook_dir, &InlineTx, &b, false).unwrap();
        }));
        publish_staged(&vault, dir.path(), &InlineTx).unwrap();
        // The older confirmation must not delete the newer staged pending.
        assert_eq!(load_publish_state(dir.path()).unwrap(), 1);
        let identity = ledger::load_identity(&vault).unwrap().unwrap();
        let key = ledger::load_key(&vault).unwrap().unwrap();
        let pending = ledger::read_cache(&dir.path().join(LEDGER_PENDING_FILE), &key, &identity)
            .unwrap()
            .expect("revision 2 pending survives the older final commit");
        assert_eq!(pending.metadata.revision, 2);
        // Cloud holds exactly the confirmed revision 1 envelope.
        let cloud = files.lock().unwrap();
        assert!(cloud.values().all(|bytes| {
            EncryptedEnvelope::from_json(bytes)
                .unwrap()
                .metadata
                .revision
                == 1
        }));
        // And the newer pending still publishes afterwards.
        drop(cloud);
        let second = publish_staged(&vault, dir.path(), &InlineTx).unwrap();
        assert_eq!(second.revision, 2);
        assert!(!dir.path().join(LEDGER_PENDING_FILE).exists());
    }

    #[test]
    fn file_lock_serializes_publish_against_concurrent_stage() {
        use std::sync::mpsc;
        use std::thread;
        use std::time::Duration;
        let fault = Arc::new(StdMutex::new(Fault::default()));
        let (files, _, base) = slot_server(fault.clone());
        let vault = seeded_vault(&base);
        let dir = tempfile::tempdir().unwrap();
        let a = write_payload(dir.path(), "a.json", &valid_payload());
        let b = write_payload(
            dir.path(),
            "b.json",
            &payload_variant(&valid_payload(), "B"),
        );
        let tx = DiskTx(dir.path().to_owned());

        stage_pending(&vault, dir.path(), &tx, &a, false).unwrap();
        // The concurrent stage starts inside the publish transaction window
        // and blocks on the cross-process file lock until the publish
        // (network included) has fully committed.
        let (done_tx, done_rx) = mpsc::channel::<u64>();
        let hook_vault = vault.clone();
        let hook_dir = dir.path().to_owned();
        fault.lock().unwrap().put_hook = Some(Box::new(move || {
            thread::spawn(move || {
                let outcome =
                    stage_pending(&hook_vault, &hook_dir, &DiskTx(hook_dir.clone()), &b, false)
                        .unwrap();
                let _ = done_tx.send(outcome.revision);
            });
        }));
        let published = publish_staged(&vault, dir.path(), &tx).unwrap();
        assert_eq!(published.revision, 1);
        let staged_rev = done_rx
            .recv_timeout(Duration::from_secs(10))
            .expect("concurrent stage must complete after the publish commits");
        assert_eq!(staged_rev, 2);
        // Final state: cloud has revision 1, local pending holds revision 2.
        assert_eq!(load_publish_state(dir.path()).unwrap(), 1);
        assert!(files.lock().unwrap().values().all(|bytes| {
            EncryptedEnvelope::from_json(bytes)
                .unwrap()
                .metadata
                .revision
                == 1
        }));
        let identity = ledger::load_identity(&vault).unwrap().unwrap();
        let key = ledger::load_key(&vault).unwrap().unwrap();
        let pending = ledger::read_cache(&dir.path().join(LEDGER_PENDING_FILE), &key, &identity)
            .unwrap()
            .expect("later staged pending is intact");
        assert_eq!(pending.metadata.revision, 2);
    }
}
