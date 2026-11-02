//! Nutstore WebDAV sync for the synthetic probe: configuration storage,
//! desktop publishing, and mobile fetch with cache-first acceptance rules.
//!
//! Cloud layout: two fixed ciphertext slots plus read-only legacy files. No
//! account email ever enters envelope identity; the ledger/account identity
//! is the locally paired synthetic probe identity.

mod slots;
mod test_round;

use crate::probe_cache::{
    PROBE_ACCOUNT, PROBE_LEDGER, SecretVault, SlotVault, Vault, ensure_key, identity, load_key,
    persist_cache, read_cache,
};
use crate::webdav::{PropfindOutcome, WebDavClient};
use account_app_core::{
    ContentKey, EncryptedEnvelope, RevisionGuard, SnapshotMetadata, decrypt, decrypt_and_advance,
    encrypt,
};
use serde::Serialize;
use std::{fs, path::Path, sync::Arc};
use tauri::Manager;
use tauri_plugin_platform::PlatformExt;
use zeroize::Zeroizing;

pub const DEFAULT_BASE_URL: &str = "https://dav.jianguoyun.com/dav/";
pub const REMOTE_DIRECTORY: &str = "Account/probe";
pub const REMOTE_FILE: &str = "latest.enc";
const REMOTE_PENDING_FILE: &str = "latest.enc.pending";

const CONFIG_SLOT: &str = "nutstore-dav-config-v1";
const PASSWORD_SLOT: &str = "nutstore-dav-password-v1";
pub const PROBE_KEY_SLOT: &str = "probe-content-key-v1";

const CACHE_FILE: &str = "synthetic-probe.enc";
const PENDING_FILE: &str = "publish-pending.enc";
const STATE_FILE: &str = "publish-state.json";

const PAYLOAD_SCHEMA: &str = "account-synthetic-probe-v1";
const RECORD_ONE: &str = "demo-001";
const RECORD_TWO: &str = "demo-002";
const PAYLOAD_ERROR: &str = "云端快照不属于本测试的虚构数据契约。";

// Stable pipeline-stage labels for the diagnostics contract. Every failure
// surfaced to the UI names its stage (and HTTP method/status for request
// errors), so a copied message pins down the exact failing call.
const STEP_CONFIG: &str = "配置/安全存储";
const STEP_KEY_CACHE: &str = "本机密钥与缓存读取";
const STEP_PENDING: &str = "待发准备";
const STEP_DIRECTORY: &str = "云端目录建立/确认";
const STEP_OWNERSHIP: &str = "云端正式文件归属检查";
const STEP_OFFICIAL_READBACK: &str = "正式路径回读确认";
const STEP_LOCAL_SAVE: &str = "本地保存";
const STEP_CONTRACT: &str = "认证/契约/版本检查";

/// Prefix a plain (non-HTTP) failure with its pipeline stage.
pub fn step_err(step: &'static str, error: String) -> String {
    format!("【{step}】{error}")
}

/// Fixed, copyable reason codes for a network command whose background task
/// never returned normally. Panic payloads are never included.
pub fn sync_join_failure(error: tauri::Error) -> String {
    if let tauri::Error::JoinError(join) = &error {
        if join.is_panic() {
            log::error!("sync task panicked (SYNC-PANIC)");
            return "同步任务异常终止（SYNC-PANIC）。本地缓存与待发状态已保留，可重试。".into();
        }
        if join.is_cancelled() {
            log::warn!("sync task cancelled (SYNC-CANCELLED)");
            return "同步任务被取消（SYNC-CANCELLED）。".into();
        }
    }
    log::error!("sync task failed to join (SYNC-FAILED)");
    "同步任务未能完成（SYNC-FAILED）。".into()
}

/// Serializes network commands so concurrent refreshes and publishing never
/// overlap; each command still re-validates against durable state.
#[derive(Default)]
pub struct SyncState {
    pub network: Arc<tauri::async_runtime::Mutex<()>>,
}

#[derive(Debug, Serialize, Clone, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct SyncRecord {
    pub id: String,
    pub label: String,
    pub amount_minor: u64,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct SyncStatusSummary {
    pub configured: bool,
    pub paired: bool,
    /// Revision of an unconfirmed pending publish, when one exists. Shown
    /// separately from the cached revision so a local cache at revision N is
    /// never mistaken for a published version N.
    pub pending_revision: Option<u64>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PublishOutcome {
    pub revision: u64,
    /// Fixed-file slot PUT with authenticated readback.
    pub method: &'static str,
}

#[derive(Debug, Serialize)]
#[serde(tag = "kind", rename_all = "kebab-case")]
pub enum FetchOutcome {
    Updated {
        revision: u64,
        records: Vec<SyncRecord>,
        #[serde(skip_serializing_if = "Option::is_none")]
        warning: Option<String>,
    },
    Latest {
        revision: u64,
        records: Vec<SyncRecord>,
        #[serde(skip_serializing_if = "Option::is_none")]
        warning: Option<String>,
    },
    CloudEmpty {
        has_cache: bool,
    },
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct SyncConfig {
    base_url: String,
    directory: String,
    file: String,
    username: String,
    ledger_id: String,
    account_id: String,
    key_generation: u64,
    #[serde(default, skip_serializing_if = "is_initial_round")]
    test_round: u32,
}

fn is_initial_round(round: &u32) -> bool {
    *round == 0
}

impl SyncConfig {
    fn matches_binding(&self) -> Result<(), String> {
        // The service base URL is written by sync_configure; HTTPS enforcement
        // itself belongs to the client (release builds reject plaintext). The
        // binding protects the test ledger identity and the fixed remote
        // layout from being silently re-pointed.
        if self.base_url.is_empty()
            || self.directory != REMOTE_DIRECTORY
            || self.file != REMOTE_FILE
            || self.ledger_id != PROBE_LEDGER
            || self.account_id != PROBE_ACCOUNT
            || self.key_generation != identity().key_generation
            || !matches!(self.test_round, 0 | 2)
        {
            return Err("同步配置与当前测试配对不匹配，请重新配置。".into());
        }
        Ok(())
    }

    fn resolved_directory(&self) -> String {
        if self.test_round == 2 {
            format!("{}/session-2", self.directory)
        } else {
            self.directory.clone()
        }
    }
}

/// The synthetic probe payload: two fixed fictional records whose first label
/// carries the publish count, so consecutive publishes visibly differ.
pub fn build_payload(revision: u64) -> Vec<u8> {
    serde_json::json!({
        "schema": PAYLOAD_SCHEMA,
        "records": [
            {"id": RECORD_ONE, "label": format!("第 {revision} 次发布"), "amountMinor": 1234},
            {"id": RECORD_TWO, "label": "虚构演示记录二", "amountMinor": 5678}
        ]
    })
    .to_string()
    .into_bytes()
}

/// Validate the fictional data contract. Errors never quote the payload.
pub fn parse_payload(plaintext: &[u8]) -> Result<Vec<SyncRecord>, String> {
    let value: serde_json::Value =
        serde_json::from_slice(plaintext).map_err(|_| PAYLOAD_ERROR.to_string())?;
    if value.get("schema").and_then(|v| v.as_str()) != Some(PAYLOAD_SCHEMA) {
        return Err(PAYLOAD_ERROR.into());
    }
    let records = value
        .get("records")
        .and_then(|v| v.as_array())
        .ok_or(PAYLOAD_ERROR)?;
    if records.len() != 2 {
        return Err(PAYLOAD_ERROR.into());
    }
    let mut parsed = Vec::with_capacity(2);
    for (record, expected) in records.iter().zip([RECORD_ONE, RECORD_TWO]) {
        if record.get("id").and_then(|v| v.as_str()) != Some(expected) {
            return Err(PAYLOAD_ERROR.into());
        }
        let label = record
            .get("label")
            .and_then(|v| v.as_str())
            .ok_or(PAYLOAD_ERROR)?;
        if label.trim().is_empty() || label.len() > 128 {
            return Err(PAYLOAD_ERROR.into());
        }
        let amount = record
            .get("amountMinor")
            .and_then(|v| v.as_u64())
            .ok_or(PAYLOAD_ERROR)?;
        if amount == 0 || amount > 1_000_000_000 {
            return Err(PAYLOAD_ERROR.into());
        }
        parsed.push(SyncRecord {
            id: expected.into(),
            label: label.into(),
            amount_minor: amount,
        });
    }
    Ok(parsed)
}

/// Display records of an authenticated local snapshot, accepting both the
/// legacy fixture and the current payload contract.
pub fn records_for_display(
    envelope: &EncryptedEnvelope,
    key: &ContentKey,
) -> Result<Vec<SyncRecord>, String> {
    let plaintext = Zeroizing::new(
        decrypt(envelope, key, identity()).map_err(|_| "缓存无法解密。".to_string())?,
    );
    if let Ok(records) = parse_payload(&plaintext) {
        return Ok(records);
    }
    if plaintext.as_slice() == crate::probe::FIXTURE {
        return Ok(vec![
            SyncRecord {
                id: RECORD_ONE.into(),
                label: "旧版离线验证缓存".into(),
                amount_minor: 1234,
            },
            SyncRecord {
                id: RECORD_TWO.into(),
                label: "旧版离线验证缓存".into(),
                amount_minor: 5678,
            },
        ]);
    }
    Err("缓存内容不属于本测试的虚构数据。".into())
}

/// Named-slot access over the native platform vault.
pub struct SyncVault<'a>(pub &'a tauri_plugin_platform::Platform<tauri::Wry>);
impl SecretVault for SyncVault<'_> {
    fn load_secret(&self, slot: &str) -> Result<Option<Vec<u8>>, String> {
        self.0
            .load_secret(slot)
            .map_err(|_| "原生安全存储读取失败，请保留现场检查。".into())
    }
    fn store_secret(&self, slot: &str, value: &[u8]) -> Result<(), String> {
        self.0
            .store_secret(slot, value)
            .map_err(|_| "原生安全存储写入失败，未保存变更。".into())
    }
    fn delete_secret(&self, slot: &str) -> Result<(), String> {
        self.0
            .delete_secret(slot)
            .map_err(|_| "原生安全存储清理失败，请保留现场检查。".into())
    }
}
impl Vault for SyncVault<'_> {
    fn load(&self) -> Result<Option<Vec<u8>>, String> {
        self.load_secret(PROBE_KEY_SLOT)
    }
    fn store(&self, key: &[u8]) -> Result<(), String> {
        self.store_secret(PROBE_KEY_SLOT, key)
    }
}

/// Runs a vault/cache mutation under the native probe transaction lock.
/// The test substitute executes inline without locking.
pub trait Transaction: Send + Sync {
    fn run<T: Send>(
        &self,
        operation: impl FnOnce() -> Result<T, String> + Send,
    ) -> Result<T, String>;
}

pub struct AppTransaction<'a> {
    pub app: &'a tauri::AppHandle,
}

impl Transaction for AppTransaction<'_> {
    fn run<T: Send>(
        &self,
        operation: impl FnOnce() -> Result<T, String> + Send,
    ) -> Result<T, String> {
        let state = self.app.state::<crate::probe::ProbeState>();
        let _guard = state
            .0
            .lock()
            .map_err(|_| "验证操作状态异常，请重启应用。".to_string())?;
        let directory = self
            .app
            .path()
            .app_data_dir()
            .map_err(|_| "无法定位应用数据目录。".to_string())?;
        crate::probe_transaction::with_transaction(&directory, operation)
    }
}

fn key_vault<'a, V: SecretVault>(vault: &'a V) -> SlotVault<'a, V> {
    SlotVault {
        vault,
        slot: PROBE_KEY_SLOT,
    }
}

fn load_content_key<V: SecretVault>(vault: &V) -> Result<Option<ContentKey>, String> {
    load_key(&key_vault(vault))
}

fn ensure_content_key<V: SecretVault>(vault: &V, cache_path: &Path) -> Result<ContentKey, String> {
    ensure_key(&key_vault(vault), cache_path)
}

fn build_client<V: SecretVault>(vault: &V) -> Result<WebDavClient, String> {
    build_bound_client(vault, false)
}

/// Native-layer reuse of the already-configured Nutstore credentials for the
/// business ledger publisher. Only the username/service URL and the app
/// password slot are read; nothing is written, logged, or exported. The
/// probe-specific binding is intentionally not enforced here: the business
/// ledger uses its own cloud directory and identity. Returns
/// `(base_url, username, password)`.
pub fn load_dav_credentials<V: SecretVault>(
    vault: &V,
) -> Result<(String, String, Zeroizing<String>), String> {
    let config_bytes = vault
        .load_secret(CONFIG_SLOT)?
        .ok_or("尚未配置坚果云同步，请先输入账号和应用密码。".to_string())?;
    let value: serde_json::Value = serde_json::from_slice(&config_bytes)
        .map_err(|_| "同步配置损坏，请重新配置。".to_string())?;
    let username = value
        .get("username")
        .and_then(|v| v.as_str())
        .filter(|username| !username.is_empty())
        .ok_or("同步配置缺少坚果云账号，请重新配置。".to_string())?
        .to_string();
    let base_url = value
        .get("base_url")
        .and_then(|v| v.as_str())
        .filter(|url| !url.is_empty())
        .unwrap_or(DEFAULT_BASE_URL)
        .to_string();
    let password_bytes = Zeroizing::new(
        vault
            .load_secret(PASSWORD_SLOT)?
            .ok_or("尚未配置应用密码，请重新配置。".to_string())?,
    );
    let password = Zeroizing::new(
        String::from_utf8(password_bytes.to_vec())
            .map_err(|_| "应用密码损坏，请重新配置。".to_string())?,
    );
    Ok((base_url, username, password))
}

fn build_bound_client<V: SecretVault>(
    vault: &V,
    original_layout: bool,
) -> Result<WebDavClient, String> {
    let config_bytes = vault
        .load_secret(CONFIG_SLOT)?
        .ok_or("尚未配置坚果云同步，请先输入账号和应用密码。".to_string())?;
    let config: SyncConfig = serde_json::from_slice(&config_bytes)
        .map_err(|_| "同步配置损坏，请重新配置。".to_string())?;
    config.matches_binding()?;
    let password_bytes = Zeroizing::new(
        vault
            .load_secret(PASSWORD_SLOT)?
            .ok_or("尚未配置应用密码，请重新配置。".to_string())?,
    );
    let password = Zeroizing::new(
        String::from_utf8(password_bytes.to_vec())
            .map_err(|_| "应用密码损坏，请重新配置。".to_string())?,
    );
    WebDavClient::new(
        &config.base_url,
        &if original_layout {
            config.directory.clone()
        } else {
            config.resolved_directory()
        },
        &config.username,
        &password,
    )
}

fn load_publish_state(dir: &Path) -> Result<u64, String> {
    let bytes = match fs::read(dir.join(STATE_FILE)) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(0),
        Err(_) => return Err("无法读取发布状态。".into()),
    };
    let value: serde_json::Value =
        serde_json::from_slice(&bytes).map_err(|_| "发布状态损坏，请保留现场检查。".to_string())?;
    value
        .get("lastPublishedRevision")
        .and_then(|v| v.as_u64())
        .ok_or("发布状态损坏，请保留现场检查。".into())
}

fn save_publish_state(dir: &Path, revision: u64) -> Result<(), String> {
    let bytes = serde_json::json!({"lastPublishedRevision": revision})
        .to_string()
        .into_bytes();
    let parent = dir;
    fs::create_dir_all(parent).map_err(|_| "无法建立同步状态目录。".to_string())?;
    let mut pending = tempfile::NamedTempFile::new_in(parent)
        .map_err(|_| "无法准备发布状态文件。".to_string())?;
    use std::io::Write;
    pending
        .write_all(&bytes)
        .map_err(|_| "发布状态写入未完成。".to_string())?;
    pending
        .as_file()
        .sync_all()
        .map_err(|_| "发布状态写入未完成。".to_string())?;
    pending
        .persist(dir.join(STATE_FILE))
        .map_err(|_| "发布状态替换未完成。".to_string())?;
    Ok(())
}

/// Prepare (or reuse) the pending ciphertext for publishing. A pending
/// envelope that is newer than the last durable publish state is reused
/// verbatim, so retries always carry the same ciphertext and revision; a
/// stale pending (already accounted for by the durable state, e.g. a torn
/// cleanup) is discarded and regenerated one revision above both counters.
fn prepare_pending(dir: &Path, key: &ContentKey) -> Result<EncryptedEnvelope, String> {
    let state_revision = load_publish_state(dir)?;
    let cache_revision = read_cache(&dir.join(CACHE_FILE), key)?
        .map(|envelope| envelope.metadata.revision)
        .unwrap_or(0);
    let pending_path = dir.join(PENDING_FILE);
    if let Some(pending) =
        read_cache(&pending_path, key)?.filter(|pending| pending.metadata.revision > state_revision)
    {
        return Ok(pending);
    }
    let revision = state_revision
        .max(cache_revision)
        .checked_add(1)
        .ok_or("发布版本已达上限。".to_string())?;
    let envelope = encrypt(
        &build_payload(revision),
        key,
        SnapshotMetadata {
            format_version: 1,
            ledger_id: PROBE_LEDGER.into(),
            account_id: PROBE_ACCOUNT.into(),
            key_generation: identity().key_generation,
            revision,
        },
    )
    .map_err(|_| "虚构数据加密失败。".to_string())?;
    persist_cache(&pending_path, &envelope)?;
    Ok(envelope)
}

/// Publish to the non-current fixed slot; legacy cloud files remain untouched.
pub fn publish_flow<V: SecretVault + Sync, T: Transaction>(
    vault: &V,
    dir: &Path,
    tx: &T,
) -> Result<PublishOutcome, String> {
    slots::publish(vault, dir, tx)
}

/// Fetch and acceptance: size-bounded download, authenticate, contract
/// validation, then atomic persistence. Cache-first is the caller's job:
/// this command never blocks on the network before the UI has rendered.
pub fn fetch_flow<V: SecretVault + Sync, T: Transaction>(
    vault: &V,
    dir: &Path,
    tx: &T,
) -> Result<FetchOutcome, String> {
    let (key, client, baseline) = tx.run(|| {
        let key = load_content_key(vault)?.ok_or("尚未配对，请先在电脑上扫码配对。".to_string())?;
        let client = build_client(vault).map_err(|error| step_err(STEP_CONFIG, error))?;
        let baseline = read_cache(&dir.join(CACHE_FILE), &key)
            .map_err(|error| step_err(STEP_KEY_CACHE, error))?
            .map(|envelope| envelope.metadata.revision)
            .unwrap_or(0);
        Ok((key, client, baseline))
    })?;
    let (incoming, warning) = slots::download(&client, &key)?;
    let Some(incoming) = incoming else {
        return Ok(FetchOutcome::CloudEmpty {
            has_cache: baseline > 0,
        });
    };
    tx.run(|| {
        let local = read_cache(&dir.join(CACHE_FILE), &key)
            .map_err(|error| step_err(STEP_KEY_CACHE, error))?;
        if let Some(local_envelope) = &local {
            if incoming.metadata.revision == local_envelope.metadata.revision {
                if &incoming == local_envelope {
                    let records = records_for_display(local_envelope, &key)?;
                    return Ok(FetchOutcome::Latest {
                        revision: local_envelope.metadata.revision,
                        records,
                        warning: warning.clone(),
                    });
                }
                return Err(step_err(
                    STEP_CONTRACT,
                    "云端同版本内容与本地缓存不一致，已保留本地缓存。".into(),
                ));
            }
            if incoming.metadata.revision < local_envelope.metadata.revision {
                return Err(step_err(
                    STEP_CONTRACT,
                    "云端版本低于本地缓存，已保留本地缓存。".into(),
                ));
            }
        }
        let local_revision = local
            .as_ref()
            .map(|envelope| envelope.metadata.revision)
            .unwrap_or(0);
        let mut guard = RevisionGuard::from_trusted_checkpoint(identity(), local_revision)
            .map_err(|_| step_err(STEP_CONTRACT, "本地版本状态异常，请保留现场检查。".into()))?;
        let plaintext = Zeroizing::new(
            decrypt_and_advance(&incoming, &key, identity(), &mut guard).map_err(|_| {
                step_err(
                    STEP_CONTRACT,
                    "云端文件无法通过本配对认证，已保留本地缓存。".into(),
                )
            })?,
        );
        let records = parse_payload(&plaintext)
            .map_err(|error| format!("【{STEP_CONTRACT}】{error}已保留本地缓存。"))?;
        persist_cache(&dir.join(CACHE_FILE), &incoming)
            .map_err(|error| step_err(STEP_LOCAL_SAVE, error))?;
        Ok(FetchOutcome::Updated {
            revision: incoming.metadata.revision,
            records,
            warning,
        })
    })
}

fn sync_summary<V: SecretVault>(vault: &V) -> Result<SyncStatusSummary, String> {
    let config = vault.load_secret(CONFIG_SLOT)?;
    let configured = match config {
        Some(bytes) => {
            serde_json::from_slice::<SyncConfig>(&bytes)
                .map_err(|_| "同步设置无法读取，请保留配置并重试。".to_string())?;
            vault.load_secret(PASSWORD_SLOT)?.is_some()
        }
        None => false,
    };
    let paired = load_content_key(vault)?.is_some();
    Ok(SyncStatusSummary {
        configured,
        paired,
        pending_revision: None,
    })
}

/// Best-effort read of an unconfirmed pending publish revision. Only the
/// public metadata is used; failures simply mean "not shown", never an error.
fn pending_revision<V: SecretVault>(dir: &Path, vault: &V) -> Option<u64> {
    let key = load_content_key(vault).ok()??;
    let state_revision = load_publish_state(dir).ok()?;
    let pending = read_cache(&dir.join(PENDING_FILE), &key).ok()??;
    (pending.metadata.revision > state_revision).then_some(pending.metadata.revision)
}

/// Validates and applies a sync configuration against the vault.
///
/// Account isolation: once a configuration exists, only the same account may
/// be re-entered (to replace the application password); a different account
/// is rejected before any write, so the content key, cache, and pending
/// publish state can never be silently adopted by another account.
///
/// Failure protection: a first-time configuration writes the config slot and
/// the password slot and verifies both by readback; if the write sequence
/// fails, the just-written slots are removed again so no half configuration
/// remains (both `sync_summary` and `build_client` also refuse either half).
/// A same-account password change rewrites only the password slot, so the
/// config and the content key are untouched.
fn apply_configure<V: SecretVault>(
    vault: &V,
    account: &str,
    password: &str,
) -> Result<SyncStatusSummary, String> {
    if account.is_empty() || account.len() > 256 {
        return Err("请输入有效的坚果云账号。".into());
    }
    if password.is_empty() || password.len() > 256 {
        return Err("请输入有效的坚果云应用密码。".into());
    }
    let existing = vault.load_secret(CONFIG_SLOT)?;
    if let Some(bytes) = &existing {
        let config: SyncConfig = serde_json::from_slice(bytes)
            .map_err(|_| "同步配置损坏，请保留现场检查。".to_string())?;
        if config.username != account {
            return Err(
                "已配置为另一坚果云账号。为避免另一账号沿用本机内容密钥与缓存，本轮不切换账号；如需更换请先在主对话确认重置方案。".into(),
            );
        }
        config.matches_binding()?;
    }
    let previous_password = vault.load_secret(PASSWORD_SLOT)?.map(Zeroizing::new);
    let fresh = existing.is_none();
    let config = SyncConfig {
        base_url: DEFAULT_BASE_URL.into(),
        directory: REMOTE_DIRECTORY.into(),
        file: REMOTE_FILE.into(),
        username: account.to_string(),
        ledger_id: PROBE_LEDGER.into(),
        account_id: PROBE_ACCOUNT.into(),
        key_generation: identity().key_generation,
        test_round: 0,
    };
    let config_bytes =
        serde_json::to_vec(&config).map_err(|_| "无法序列化同步配置。".to_string())?;
    let password_bytes = Zeroizing::new(password.as_bytes().to_vec());
    let result: Result<(), String> = (|| {
        if fresh {
            vault.store_secret(CONFIG_SLOT, &config_bytes)?;
        }
        vault.store_secret(PASSWORD_SLOT, &password_bytes)?;
        // Read back both slots so a silent storage failure surfaces now.
        if fresh {
            let stored_config = vault
                .load_secret(CONFIG_SLOT)?
                .ok_or("安全存储未保留同步配置，请重试。".to_string())?;
            let restored: SyncConfig = serde_json::from_slice(&stored_config)
                .map_err(|_| "安全存储校验同步配置失败，请重试。".to_string())?;
            if restored.username != account {
                return Err("安全存储校验同步配置失败，请重试。".into());
            }
            restored.matches_binding()?;
        }
        let stored = vault
            .load_secret(PASSWORD_SLOT)?
            .ok_or("安全存储未保留应用密码，请重试。".to_string())?;
        if stored != password_bytes.as_slice() {
            return Err("安全存储校验应用密码失败，请重试。".into());
        }
        Ok(())
    })();
    if let Err(error) = result {
        let rollback: Result<(), String> = (|| {
            if fresh {
                vault.delete_secret(CONFIG_SLOT)?;
            }
            let current = vault.load_secret(PASSWORD_SLOT)?.map(Zeroizing::new);
            if current.as_deref() != previous_password.as_deref() {
                match &previous_password {
                    Some(previous) => vault.store_secret(PASSWORD_SLOT, previous)?,
                    None => vault.delete_secret(PASSWORD_SLOT)?,
                }
            }
            let restored = vault.load_secret(PASSWORD_SLOT)?.map(Zeroizing::new);
            if restored.as_deref() != previous_password.as_deref() {
                return Err("旧应用密码恢复校验失败。".into());
            }
            Ok(())
        })();
        if rollback.is_err() {
            return Err(
                "配置写入失败，旧配置恢复未能确认。密钥和缓存保留，请重新配置应用密码。".into(),
            );
        }
        return Err(error);
    }
    sync_summary(vault)
}

#[tauri::command]
pub async fn sync_configure(
    app: tauri::AppHandle,
    account: String,
    app_password: String,
) -> Result<SyncStatusSummary, String> {
    // Same serialization boundary as publish/fetch: a configuration change
    // can never land between another command's credential load and its
    // network requests, and old requests can never submit after a
    // reconfiguration as if they used the new configuration.
    let network = app.state::<SyncState>().network.clone();
    let _network = network.lock().await;
    let password = Zeroizing::new(app_password);
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<crate::probe::ProbeState>();
        let _guard = state
            .0
            .lock()
            .map_err(|_| "验证操作状态异常，请重启应用。".to_string())?;
        let directory = app
            .path()
            .app_data_dir()
            .map_err(|_| "无法定位应用数据目录。".to_string())?;
        crate::probe_transaction::with_transaction(&directory, || {
            let vault = SyncVault(app.platform());
            apply_configure(&vault, account.trim(), &password)
        })
    })
    .await
    .map_err(sync_join_failure)?
}

#[tauri::command]
pub async fn sync_status(app: tauri::AppHandle) -> Result<SyncStatusSummary, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<crate::probe::ProbeState>();
        let _guard = state
            .0
            .lock()
            .map_err(|_| "验证操作状态异常，请重启应用。".to_string())?;
        let mut summary = sync_summary(&SyncVault(app.platform()))?;
        if let Ok(directory) = app.path().app_data_dir() {
            summary.pending_revision = pending_revision(&directory, &SyncVault(app.platform()));
        }
        Ok(summary)
    })
    .await
    .map_err(sync_join_failure)?
}

#[tauri::command]
pub async fn publish_snapshot(app: tauri::AppHandle) -> Result<PublishOutcome, String> {
    if cfg!(target_os = "android") {
        return Err("手机端不负责发布快照。".into());
    }
    let network = app.state::<SyncState>().network.clone();
    let _network = network.lock().await;
    tauri::async_runtime::spawn_blocking(move || {
        let directory = app
            .path()
            .app_data_dir()
            .map_err(|_| "无法定位应用数据目录。".to_string())?;
        let tx = AppTransaction { app: &app };
        publish_flow(&SyncVault(app.platform()), &directory, &tx)
    })
    .await
    .map_err(sync_join_failure)?
}

#[tauri::command]
pub async fn sync_fetch(app: tauri::AppHandle) -> Result<FetchOutcome, String> {
    let network = app.state::<SyncState>().network.clone();
    let _network = network.lock().await;
    tauri::async_runtime::spawn_blocking(move || {
        let directory = app
            .path()
            .app_data_dir()
            .map_err(|_| "无法定位应用数据目录。".to_string())?;
        let tx = AppTransaction { app: &app };
        fetch_flow(&SyncVault(app.platform()), &directory, &tx)
    })
    .await
    .map_err(sync_join_failure)?
}

// ---------------------------------------------------------------------------
// Confined cloud-semantics diagnostic.
//
// Uses the user's already-configured credentials through the native client
// (no script ever touches the vault). Phase 1 is read-only over the real
// paths; phase 2 records what the service actually answers for MKCOL /
// PUT / MOVE over an isolated, freshly created `diag-…` directory filled
// only with non-envelope dummy bytes. The official file is byte-compared
// before and after; every diagnostic artifact is deleted at the end and the
// deletion is verified. Output is a copyable list of fixed-format lines:
// never URLs, credentials, ciphertext, or response bodies.
// ---------------------------------------------------------------------------

const DIAG_BODY: &[u8] = b"account-probe-diagnostic-bytes-not-an-envelope";

fn diag_directory_name() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or(0);
    let pid = u128::from(std::process::id());
    format!("diag-{:08x}", ((nanos ^ (pid << 64)) & 0xffff_ffff) as u32)
}

fn describe_status(status: Option<u16>) -> String {
    status
        .map(|status| status.to_string())
        .unwrap_or_else(|| "传输失败".into())
}

/// Diagnostic core: local public metadata (read-only), cloud read-only
/// checks, and the confined service-semantics probes. Shared by the app
/// command and the Windows CLI. Never mutates the official cloud file and
/// never prints credentials, ciphertext, or URLs.
pub fn run_diagnostic_core<V: SecretVault>(vault: &V, directory: &Path) -> Vec<String> {
    run_diagnostic(vault, directory, !cfg!(target_os = "android"))
}

/// Use the GUI host's actual Tauri paths and native vault, without cloud writes.
#[cfg(any(windows, target_os = "macos"))]
pub fn inspect_gui_host(app: &tauri::AppHandle) -> Result<Vec<String>, String> {
    let data = app
        .path()
        .app_data_dir()
        .map_err(|_| "无法定位本机缓存目录。")?;
    let config = app
        .path()
        .app_config_dir()
        .map_err(|_| "无法定位本机安全存储目录。")?;
    let mut lines = vec![
        format!("GUI data directory: {}", data.display()),
        format!("GUI config directory: {}", config.display()),
    ];
    lines.extend(run_diagnostic(&SyncVault(app.platform()), &data, false));
    Ok(lines)
}

#[cfg(any(windows, target_os = "macos"))]
pub fn prepare_gui_test_round(app: &tauri::AppHandle) -> Result<String, String> {
    let network = app.state::<SyncState>().network.clone();
    let _network = network.blocking_lock();
    let directory = app
        .path()
        .app_data_dir()
        .map_err(|_| "无法定位本机测试目录。")?;
    AppTransaction { app }
        .run(|| test_round::prepare(&SyncVault(app.platform()), &directory, false))
}

#[tauri::command]
pub async fn prepare_new_test_round(app: tauri::AppHandle) -> Result<String, String> {
    let network = app.state::<SyncState>().network.clone();
    let _network = network.lock().await;
    tauri::async_runtime::spawn_blocking(move || {
        let directory = app
            .path()
            .app_data_dir()
            .map_err(|_| "无法定位本机测试目录。")?;
        AppTransaction { app: &app }.run(|| {
            test_round::prepare(
                &SyncVault(app.platform()),
                &directory,
                cfg!(target_os = "android"),
            )
        })
    })
    .await
    .map_err(sync_join_failure)?
}

fn run_diagnostic<V: SecretVault>(
    vault: &V,
    directory: &Path,
    allow_semantic_writes: bool,
) -> Vec<String> {
    let mut lines = Vec::new();
    // Local public metadata first (read-only).
    let key = load_content_key(vault).ok().flatten();
    match &key {
        Some(key) => {
            let describe_cache = |file: &str| match read_cache(&directory.join(file), key) {
                Ok(Some(envelope)) => format!("第 {} 版", envelope.metadata.revision),
                Ok(None) => "无".into(),
                Err(_) => "无法认证或读取（文件保留，请检查配对）".into(),
            };
            let state_revision = load_publish_state(directory).ok();
            lines.push(format!(
                "【本机】缓存版本 {}；发布状态 {}；待发文件版本 {}（待发仅在高于发布状态时有效）。",
                describe_cache(CACHE_FILE),
                state_revision
                    .map(|revision| format!("第 {revision} 版"))
                    .unwrap_or_else(|| "无法读取".into()),
                describe_cache(PENDING_FILE),
            ));
        }
        None => lines.push("【本机】尚未配对：没有本机内容密钥（只读检查照常进行）。".into()),
    }
    // Configuration + HTTPS initialization happen inside the client build.
    let client = match build_client(vault) {
        Ok(client) => {
            lines.push("【配置/安全存储】已用本机配置构建客户端；HTTPS/TLS 初始化：完成。".into());
            client
        }
        Err(error) => {
            lines.push(format!("【配置/安全存储】失败：{error}"));
            lines.push("诊断到此中止；以上文本可直接复制反馈。".into());
            return lines;
        }
    };
    // Phase 1: read-only checks over the real paths.
    for url in client.ancestor_urls() {
        match client.propfind_url(url) {
            Ok(outcome) => lines.push(format!(
                "【云端检查】PROPFIND · {} · {}。",
                match outcome {
                    PropfindOutcome::Collection => "207 · 是可用目录",
                    PropfindOutcome::NotCollection => "207 · 不是目录",
                    PropfindOutcome::NotFound => "404 · 不存在",
                },
                match outcome {
                    PropfindOutcome::Collection => "目录确认通过",
                    _ => "目录确认未通过",
                }
            )),
            Err(error) => lines.push(format!(
                "【云端检查】PROPFIND · {} · {}",
                describe_status(error.status),
                error.message()
            )),
        }
    }
    let official_before = match client.get(REMOTE_FILE) {
        Ok(Some(bytes)) => {
            let revision = EncryptedEnvelope::from_json(&bytes)
                .ok()
                .map(|envelope| envelope.metadata.revision);
            lines.push(format!(
                "【云端检查】GET · 200 · 正式文件存在，版本 {}，长度 {} 字节。",
                revision
                    .map(|revision| format!("第 {revision} 版"))
                    .unwrap_or_else(|| "无法解析（保留原文未改动）".into()),
                bytes.len()
            ));
            Some(bytes)
        }
        Ok(None) => {
            lines.push("【云端检查】GET · 404 · 正式文件不存在。".into());
            None
        }
        Err(error) => {
            lines.push(format!(
                "【云端检查】GET · {} · {}",
                describe_status(error.status),
                error.message()
            ));
            lines.push("【守护】正式文件初始状态未确认，已停止诊断，不执行云端写入。".into());
            return lines;
        }
    };
    let remote_pending = match client.get(REMOTE_PENDING_FILE) {
        Ok(Some(bytes)) => {
            let revision = EncryptedEnvelope::from_json(&bytes)
                .ok()
                .map(|envelope| envelope.metadata.revision);
            lines.push(format!(
                "【云端检查】GET · 200 · 云端遗留临时文件存在，版本 {}，长度 {} 字节。",
                revision
                    .map(|revision| format!("第 {revision} 版"))
                    .unwrap_or_else(|| "无法解析（保留原文未改动）".into()),
                bytes.len()
            ));
            Some(bytes)
        }
        Ok(None) => {
            lines.push("【云端检查】GET · 404 · 云端无遗留临时文件。".into());
            None
        }
        Err(error) => {
            lines.push(format!(
                "【云端检查】GET · {} · {}",
                describe_status(error.status),
                error.message()
            ));
            None
        }
    };
    if let (Some(before), Some(pending)) = (&official_before, &remote_pending) {
        lines.push(format!(
            "【云端检查】云端正式文件与遗留临时文件内容一致：{}。",
            if before == pending { "是" } else { "否" }
        ));
    }
    for file in slots::FILES {
        match client.get(file) {
            Ok(Some(bytes)) => {
                let revision = EncryptedEnvelope::from_json(&bytes)
                    .ok()
                    .map(|e| e.metadata.revision);
                lines.push(format!("【双文件检查】{file} · GET · 200 · 公开版本 {}（此处仅看格式，获取流程另行认证）。",
                    revision.map(|r| r.to_string()).unwrap_or_else(|| "无法解析".into())));
            }
            Ok(None) => lines.push(format!("【双文件检查】{file} · GET · 404 · 不存在。")),
            Err(error) => lines.push(format!(
                "【双文件检查】{file} · GET · {} · {}",
                describe_status(error.status),
                error.message()
            )),
        }
    }
    if !allow_semantic_writes {
        lines.push("手机只读诊断完成；未创建、上传、移动或删除云端文件。".into());
        return lines;
    }
    // Phase 2: confined service-semantics probes inside our own diag directory.
    let diag_dir = diag_directory_name();
    lines.push(format!(
        "【语义测试】以下请求全部限制在 Account/probe/{diag_dir}/ 内，仅使用非密文的占位字节。"
    ));
    let pending_rel = format!("{diag_dir}/diag.enc.pending");
    let moved_rel = format!("{diag_dir}/diag.enc");
    let mut dir_ready = false;
    match client.mkcol_rel(&diag_dir) {
        Ok(()) => {
            lines.push("【语义测试】MKCOL · 2xx · 新目录创建成功。".into());
            dir_ready = true;
        }
        Err(error) => {
            lines.push(format!(
                "【语义测试】MKCOL · {} · {}",
                describe_status(error.status),
                error.message()
            ));
            if error.status == Some(405) || error.status == Some(409) {
                match client.propfind_rel(&diag_dir) {
                    Ok(PropfindOutcome::Collection) => {
                        lines.push(
                            "【语义测试】PROPFIND · 207 · 已有同名目录且为可用目录，将复用。"
                                .into(),
                        );
                        dir_ready = true;
                    }
                    Ok(_) => {
                        lines.push(
                            "【语义测试】PROPFIND · 207 · 同名条目不是目录，语义测试中止。".into(),
                        );
                    }
                    Err(error) => lines.push(format!(
                        "【语义测试】PROPFIND · {} · {}",
                        describe_status(error.status),
                        error.message()
                    )),
                }
            } else {
                lines.push("【语义测试】目录创建失败，语义测试中止（正式文件未改动）。".into());
            }
        }
    }
    let diagnostic_directory_created = dir_ready;
    if dir_ready {
        // A reused directory may hold leftovers; clear them first so the
        // recorded sequence always starts from a known state. Failure to
        // clear aborts rather than reporting misleading semantics.
        let cleared = client
            .delete_rel(&moved_rel)
            .and_then(|()| client.delete_rel(&pending_rel));
        match cleared {
            Ok(()) => {
                lines.push("【语义测试】DELETE · 2xx/404 · 复用目录内已清空旧占位文件。".into())
            }
            Err(error) => {
                dir_ready = false;
                lines.push(format!(
                    "【语义测试】DELETE · {} · 复用目录清理失败，语义测试中止：{}",
                    describe_status(error.status),
                    error.message()
                ));
            }
        }
    }
    if dir_ready {
        // 1. MKCOL on an already existing collection.
        match client.mkcol_rel(&diag_dir) {
            Ok(()) => lines.push(
                "【语义测试】MKCOL(重复) · 2xx · 服务对已存在目录返回成功（异常语义）。".into(),
            ),
            Err(error) => lines.push(format!(
                "【语义测试】MKCOL(重复) · {} · 服务对已存在目录的应答。",
                describe_status(error.status)
            )),
        }
        // 2. PUT the dummy file.
        match client.put_rel(&pending_rel, DIAG_BODY) {
            Ok(()) => lines.push("【语义测试】PUT(临时文件) · 2xx · 上传成功。".into()),
            Err(error) => lines.push(format!(
                "【语义测试】PUT(临时文件) · {} · {}",
                describe_status(error.status),
                error.message()
            )),
        }
        // 3. Read it back.
        match client.get_rel(&pending_rel) {
            Ok(Some(bytes)) => lines.push(format!(
                "【语义测试】GET(回读) · 200 · 内容一致：{}。",
                if bytes == DIAG_BODY { "是" } else { "否" }
            )),
            Ok(None) => lines.push("【语义测试】GET(回读) · 404 · 上传后立即回读失败。".into()),
            Err(error) => lines.push(format!(
                "【语义测试】GET(回读) · {} · {}",
                describe_status(error.status),
                error.message()
            )),
        }
        // 4. First MOVE: target does not exist yet.
        match client.move_rel(&pending_rel, &moved_rel) {
            Ok(()) => lines.push("【语义测试】MOVE(目标不存在) · 2xx · 移动成功。".into()),
            Err(error) => lines.push(format!(
                "【语义测试】MOVE(目标不存在) · {} · {}",
                describe_status(error.status),
                error.message()
            )),
        }
        // 5. PUT again, then MOVE onto the existing target (the exact
        //    replacement step used by publishing).
        if let Err(error) = client.put_rel(&pending_rel, DIAG_BODY) {
            lines.push(format!(
                "【语义测试】PUT(临时文件) · {} · {}",
                describe_status(error.status),
                error.message()
            ));
        }
        match client.move_rel(&pending_rel, &moved_rel) {
            Ok(()) => lines.push(
                "【语义测试】MOVE(覆盖已存在目标 · Overwrite:T) · 2xx · 服务接受覆盖替换。".into(),
            ),
            Err(error) => lines.push(format!(
                "【语义测试】MOVE(覆盖已存在目标 · Overwrite:T) · {} · 服务拒绝覆盖替换：{}",
                describe_status(error.status),
                error.message()
            )),
        }
        // 6. Confirm what actually sits at the moved path.
        match client.get_rel(&moved_rel) {
            Ok(Some(bytes)) => lines.push(format!(
                "【语义测试】GET(目标) · 200 · 目标内容一致：{}。",
                if bytes == DIAG_BODY { "是" } else { "否" }
            )),
            Ok(None) => lines.push("【语义测试】GET(目标) · 404 · 目标不存在。".into()),
            Err(error) => lines.push(format!(
                "【语义测试】GET(目标) · {} · {}",
                describe_status(error.status),
                error.message()
            )),
        }
        // 5. Candidate minimal replacement path: PUT directly over an
        // existing target, then read back to prove the overwrite took
        // effect byte-for-byte. Evidence only; the publish protocol change
        // itself needs the maintainer's approval.
        const DIAG_BODY_OVERRIDE: &[u8] =
            b"account-probe-diagnostic-overwrite-body-not-an-envelope";
        match client.put_rel(&moved_rel, DIAG_BODY_OVERRIDE) {
            Ok(()) => lines.push("【语义测试】PUT(覆盖已存在目标) · 2xx · 覆盖上传成功。".into()),
            Err(error) => lines.push(format!(
                "【语义测试】PUT(覆盖已存在目标) · {} · {}",
                describe_status(error.status),
                error.message()
            )),
        }
        match client.get_rel(&moved_rel) {
            Ok(Some(bytes)) => lines.push(format!(
                "【语义测试】GET(覆盖后回读) · 200 · 内容为覆盖后字节：{}。",
                if bytes == DIAG_BODY_OVERRIDE {
                    "是"
                } else {
                    "否"
                }
            )),
            Ok(None) => lines.push("【语义测试】GET(覆盖后回读) · 404 · 目标不存在。".into()),
            Err(error) => lines.push(format!(
                "【语义测试】GET(覆盖后回读) · {} · {}",
                describe_status(error.status),
                error.message()
            )),
        }
    }
    // Cleanup: only our own diagnostic artifacts, then verify.
    if diagnostic_directory_created {
        let moved_deleted = client.delete_rel(&moved_rel).is_ok();
        let pending_deleted = client.delete_rel(&pending_rel).is_ok();
        let directory_deleted = client.delete_rel(&diag_dir).is_ok();
        let moved_absent = matches!(client.get_rel(&moved_rel), Ok(None));
        let pending_absent = matches!(client.get_rel(&pending_rel), Ok(None));
        let directory_absent = matches!(
            client.propfind_rel(&diag_dir),
            Ok(PropfindOutcome::NotFound)
        );
        let gone = moved_deleted
            && pending_deleted
            && directory_deleted
            && moved_absent
            && pending_absent
            && directory_absent;
        lines.push(format!(
            "【清理】诊断目录与占位文件已删除并回读验证：{}。",
            if gone {
                "完成"
            } else {
                "未完全确认（请反馈）"
            }
        ));
    }
    // Official file guard: byte-compare against the phase-1 read.
    match client.get(REMOTE_FILE) {
        Ok(official_after) => lines.push(format!(
            "【守护】云端正式文件在诊断全程未被改动：{}。",
            if official_before == official_after {
                "是"
            } else {
                "否（请立即反馈）"
            }
        )),
        Err(error) => lines.push(format!(
            "【守护】正式文件最终状态未确认 · GET · {} · {}",
            describe_status(error.status),
            error.message()
        )),
    }
    lines.push("诊断结束：请将以上全部文本复制反馈。".into());
    lines
}

#[tauri::command]
pub async fn sync_diagnose(app: tauri::AppHandle) -> Result<Vec<String>, String> {
    let network = app.state::<SyncState>().network.clone();
    let _network = network.lock().await;
    tauri::async_runtime::spawn_blocking(move || {
        let directory = app
            .path()
            .app_data_dir()
            .map_err(|_| "无法定位应用数据目录。".to_string())?;
        Ok(run_diagnostic_core(&SyncVault(app.platform()), &directory))
    })
    .await
    .map_err(sync_join_failure)?
}

/// SecretVault adapter over the standalone native vault used by the CLI.
/// Strictly read-only: storing and deleting are refused by contract.
#[cfg(any(windows, target_os = "macos"))]
pub struct CliVault(pub tauri_plugin_platform::StandaloneVault);

#[cfg(any(windows, target_os = "macos"))]
impl SecretVault for CliVault {
    fn load_secret(&self, slot: &str) -> Result<Option<Vec<u8>>, String> {
        self.0
            .load_secret(slot)
            .map_err(|_| "原生安全存储读取失败，请保留现场检查。".into())
    }

    fn store_secret(&self, _slot: &str, _value: &[u8]) -> Result<(), String> {
        Err("命令行诊断只读，不写入安全存储。".into())
    }

    fn delete_secret(&self, _slot: &str) -> Result<(), String> {
        Err("命令行诊断只读，不删除安全存储。".into())
    }
}

/// desktop CLI entry for the confined sync diagnostic. Opens exactly the
/// same native vault namespace as the desktop app (never exporting or printing
/// them), runs [`run_diagnostic_core`], and prints the copyable lines.
/// Returns a process exit code: 0 on a completed run, 2 on a local setup
/// failure (cloud outcomes are reported inside the lines themselves).
#[cfg(any(windows, target_os = "macos"))]
pub fn run_cli_diagnostic() -> i32 {
    let data_dir = match crate::ledger_cli_support::data_directory() {
        Ok(directory) => directory,
        Err(error) => {
            eprintln!("{error}");
            return 2;
        }
    };
    let vault = match tauri_plugin_platform::StandaloneVault::open(data_dir.join("vault")) {
        Ok(vault) => CliVault(vault),
        Err(_) => {
            eprintln!("无法打开本机安全存储（与桌面应用相同的原生安全存储），诊断中止。");
            return 2;
        }
    };
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.first().is_some_and(|arg| {
        arg == "--fetch" || arg == "--publish-pending" || arg == "--publish-next"
    }) {
        struct CliTransaction<'a>(&'a Path);
        impl Transaction for CliTransaction<'_> {
            fn run<T: Send>(
                &self,
                operation: impl FnOnce() -> Result<T, String> + Send,
            ) -> Result<T, String> {
                crate::probe_transaction::with_transaction(self.0, operation)
            }
        }
        let tx = CliTransaction(&data_dir);
        let result = (|| -> Result<(), String> {
            if args[0] == "--fetch" && args.len() == 1 {
                let outcome = fetch_flow(&vault, &data_dir, &tx)?;
                match outcome {
                    FetchOutcome::Updated {
                        revision, warning, ..
                    }
                    | FetchOutcome::Latest {
                        revision, warning, ..
                    } => {
                        println!(
                            "获取并认证版本 {revision}；完整云端检查：{}。",
                            warning.is_none()
                        );
                        if let Some(warning) = warning {
                            println!("{warning}");
                        }
                    }
                    FetchOutcome::CloudEmpty { .. } => println!("云端尚无快照。"),
                }
                return Ok(());
            }
            if args[0] == "--publish-pending" && args.len() == 2 {
                let expected = args[1].parse::<u64>().map_err(|_| "待发版本参数无效。")?;
                tx.run(|| check_existing_pending(&vault, &data_dir, expected))?;
            } else if !(args[0] == "--publish-next" && args.len() == 1) {
                return Err("命令参数无效。".into());
            }
            let client = build_bound_client(&vault, true)?;
            let legacy_before = client.get(REMOTE_FILE).map_err(String::from)?;
            let pending_before = client.get(REMOTE_PENDING_FILE).map_err(String::from)?;
            let outcome = publish_flow(&vault, &data_dir, &tx)?;
            println!(
                "发布成功：版本 {}，{}，槽位回读一致。",
                outcome.revision, outcome.method
            );
            let legacy_after = client.get(REMOTE_FILE).map_err(String::from)?;
            let pending_after = client.get(REMOTE_PENDING_FILE).map_err(String::from)?;
            if legacy_before != legacy_after || pending_before != pending_after {
                return Err("旧云端文件前后比对不一致，请保留现场检查。".into());
            }
            println!("旧 latest.enc 与旧云端 pending 前后字节一致。凭据和密钥未导出。 ");
            Ok(())
        })();
        return match result {
            Ok(()) => 0,
            Err(error) => {
                eprintln!("{error}");
                2
            }
        };
    }
    if !args.is_empty() && args != ["--read-only"] {
        eprintln!("命令参数无效。");
        return 2;
    }
    for line in run_diagnostic(&vault, &data_dir, args.is_empty()) {
        println!("{line}");
    }
    0
}

#[cfg(any(windows, target_os = "macos", test))]
fn check_existing_pending<V: SecretVault>(
    vault: &V,
    dir: &Path,
    expected: u64,
) -> Result<(), String> {
    let key = load_content_key(vault)?.ok_or("没有现有配对，已停止发布。")?;
    let pending =
        read_cache(&dir.join(PENDING_FILE), &key)?.ok_or("没有现有待发文件，已停止发布。")?;
    if pending.metadata.revision != expected || load_publish_state(dir)? >= expected {
        return Err("待发版本与指定现场不符，已停止发布。".into());
    }
    records_for_display(&pending, &key)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn setup_status_read_errors_are_not_unconfigured() {
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
        let status = serde_json::to_value(sync_summary(&Unreadable)).unwrap();
        assert!(
            status.get("Err").is_some(),
            "storage failure must not be unconfigured"
        );
    }
    use crate::probe::FIXTURE;
    use crate::probe_cache::persist_key;
    use crate::webdav::tests_support::{MockRequest, MockResponse, hierarchy_server, spawn_server};
    use std::{
        collections::BTreeMap,
        path::Path,
        sync::{Arc, Mutex as StdMutex},
    };

    const TEST_PASSWORD: &str = "test-app-password";

    fn diagnostic_failure_server(mode: &'static str) -> (String, SeenRequests) {
        let official_reads = StdMutex::new(0usize);
        spawn_server(Arc::new(move |request| {
            let is_diag = request.path.contains("/diag-");
            let mut response = MockResponse {
                status: 404,
                headers: vec![],
                body: vec![],
                truncate_body_after: None,
            };
            match request.method.as_str() {
                "GET" if !is_diag && request.path.ends_with(REMOTE_FILE) => {
                    let mut count = official_reads.lock().unwrap();
                    *count += 1;
                    if mode == "baseline" || (mode == "guard" && *count > 1) {
                        response.status = 500;
                    }
                }
                "GET" if is_diag && mode == "cleanup" => response.status = 500,
                "DELETE" if mode == "prepare" => response.status = 500,
                "DELETE" => response.status = 204,
                "MKCOL" if mode == "guard" => response.status = 403,
                "MKCOL" => response.status = 201,
                "PUT" | "MOVE" => response.status = 201,
                "PROPFIND" if !is_diag => {
                    response.status = 207;
                    response.body = br#"<D:multistatus xmlns:D="DAV:"><D:response><D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response></D:multistatus>"#.to_vec();
                }
                _ => {}
            }
            response
        }))
    }

    #[test]
    fn diagnostic_baseline_failure_aborts_cloud_writes() {
        let (base, seen) = diagnostic_failure_server("baseline");
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        let lines = run_diagnostic_core(&vault, dir.path());
        assert!(
            !seen
                .lock()
                .unwrap()
                .iter()
                .any(|r| { matches!(r.method.as_str(), "MKCOL" | "PUT" | "MOVE" | "DELETE") }),
            "cannot mutate diagnostic paths without a known official-file baseline: {lines:?}"
        );
    }

    #[test]
    fn diagnostic_unreadable_pending_is_not_reported_as_absent() {
        let (base, _) = diagnostic_failure_server("baseline");
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        pair_cache(&vault, dir.path(), 1);
        let stranger = ContentKey::generate().unwrap();
        fs::write(
            dir.path().join(PENDING_FILE),
            envelope_for(&stranger, &build_payload(5), 5),
        )
        .unwrap();
        let lines = run_diagnostic(&vault, dir.path(), false);
        assert!(!lines[0].contains("待发文件版本 无（"));
        assert!(lines[0].contains("待发文件版本 无法认证或读取"));
    }

    #[test]
    fn diagnostic_failed_cleanup_readback_never_claims_complete() {
        let (base, _) = diagnostic_failure_server("cleanup");
        let dir = tempfile::tempdir().unwrap();
        let lines = run_diagnostic_core(&seeded_vault(&base, None), dir.path());
        assert!(
            !lines
                .iter()
                .any(|l| l.starts_with("【清理】") && l.contains("：完成")),
            "{lines:?}"
        );
    }

    #[test]
    fn diagnostic_guard_failure_is_unconfirmed_not_unchanged() {
        let (base, _) = diagnostic_failure_server("guard");
        let dir = tempfile::tempdir().unwrap();
        let lines = run_diagnostic_core(&seeded_vault(&base, None), dir.path());
        assert!(
            !lines
                .iter()
                .any(|l| l.starts_with("【守护】") && l.contains("：是")),
            "{lines:?}"
        );
        assert!(
            lines
                .iter()
                .any(|l| l.starts_with("【守护】") && l.contains("未确认")),
            "{lines:?}"
        );
    }

    #[test]
    fn diagnostic_preparation_cleanup_failure_aborts_semantic_writes() {
        let (base, seen) = diagnostic_failure_server("prepare");
        let dir = tempfile::tempdir().unwrap();
        let lines = run_diagnostic_core(&seeded_vault(&base, None), dir.path());
        assert!(
            !seen
                .lock()
                .unwrap()
                .iter()
                .any(|r| { matches!(r.method.as_str(), "PUT" | "MOVE") }),
            "{lines:?}"
        );
    }

    type SlotMap = BTreeMap<String, Vec<u8>>;

    /// Multi-slot memory vault implementing SecretVault; Sync+Send so it can
    /// cross the Send bounds of Transaction::run like the platform vault.
    #[derive(Clone, Default)]
    struct MemorySlots {
        slots: Arc<StdMutex<SlotMap>>,
    }
    impl SecretVault for MemorySlots {
        fn load_secret(&self, slot: &str) -> Result<Option<Vec<u8>>, String> {
            Ok(self.slots.lock().unwrap().get(slot).cloned())
        }
        fn store_secret(&self, slot: &str, value: &[u8]) -> Result<(), String> {
            self.slots
                .lock()
                .unwrap()
                .insert(slot.to_string(), value.to_vec());
            Ok(())
        }
        fn delete_secret(&self, slot: &str) -> Result<(), String> {
            self.slots.lock().unwrap().remove(slot);
            Ok(())
        }
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

    /// Build a vault with sync configuration, optionally pre-installed with a
    /// content key, bound to a mock server base URL.
    fn seeded_vault(base_url: &str, key: Option<&ContentKey>) -> MemorySlots {
        let vault = MemorySlots::default();
        let config = SyncConfig {
            base_url: base_url.to_string(),
            directory: REMOTE_DIRECTORY.into(),
            file: REMOTE_FILE.into(),
            username: "tester@example.test".into(),
            ledger_id: PROBE_LEDGER.into(),
            account_id: PROBE_ACCOUNT.into(),
            key_generation: 1,
            test_round: 0,
        };
        vault
            .store_secret(CONFIG_SLOT, &serde_json::to_vec(&config).unwrap())
            .unwrap();
        vault
            .store_secret(PASSWORD_SLOT, TEST_PASSWORD.as_bytes())
            .unwrap();
        if let Some(key) = key {
            persist_key(
                &SlotVault {
                    vault: &vault,
                    slot: PROBE_KEY_SLOT,
                },
                key,
            )
            .unwrap();
        }
        vault
    }

    /// Install a content key and persist a local cache at `revision`.
    fn pair_cache(vault: &MemorySlots, dir: &Path, revision: u64) -> ContentKey {
        let key = ensure_content_key(vault, &dir.join(CACHE_FILE)).unwrap();
        let envelope = encrypt(
            &build_payload(revision),
            &key,
            SnapshotMetadata {
                format_version: 1,
                ledger_id: PROBE_LEDGER.into(),
                account_id: PROBE_ACCOUNT.into(),
                key_generation: 1,
                revision,
            },
        )
        .unwrap();
        persist_cache(&dir.join(CACHE_FILE), &envelope).unwrap();
        key
    }

    fn envelope_for(key: &ContentKey, payload: &[u8], revision: u64) -> Vec<u8> {
        encrypt(
            payload,
            key,
            SnapshotMetadata {
                format_version: 1,
                ledger_id: PROBE_LEDGER.into(),
                account_id: PROBE_ACCOUNT.into(),
                key_generation: 1,
                revision,
            },
        )
        .unwrap()
        .to_json()
        .unwrap()
    }

    fn key_content(key: &ContentKey, payload: &[u8], revision: u64) -> EncryptedEnvelope {
        encrypt(
            payload,
            key,
            SnapshotMetadata {
                format_version: 1,
                ledger_id: PROBE_LEDGER.into(),
                account_id: PROBE_ACCOUNT.into(),
                key_generation: 1,
                revision,
            },
        )
        .unwrap()
    }

    fn cached_revision(vault: &MemorySlots, dir: &Path) -> u64 {
        let key = load_content_key(vault).unwrap().unwrap();
        read_cache(&dir.join(CACHE_FILE), &key)
            .unwrap()
            .unwrap()
            .metadata
            .revision
    }

    fn destination_path(destination: &str) -> String {
        // Strip scheme and host from an absolute Destination URL.
        destination
            .split_once("://")
            .and_then(|(_, rest)| rest.split_once('/'))
            .map(|(_, path)| format!("/{path}"))
            .unwrap_or_else(|| destination.to_string())
    }

    fn static_handler(
        status: u16,
        body: Vec<u8>,
    ) -> Arc<dyn Fn(&MockRequest) -> MockResponse + Send + Sync> {
        Arc::new(move |_| MockResponse {
            status,
            headers: vec![],
            body: body.clone(),
            truncate_body_after: None,
        })
    }

    #[derive(Default)]
    struct SlotFault {
        put_failure: Option<&'static str>,
        get_failure: Option<String>,
        fail_next_readback: bool,
    }

    fn slot_server(
        fault: Arc<StdMutex<SlotFault>>,
    ) -> (Arc<StdMutex<SlotMap>>, SeenRequests, String) {
        let files: Arc<StdMutex<SlotMap>> = Arc::default();
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
                "GET" => {
                    let mut fault = fault.lock().unwrap();
                    if fault
                        .get_failure
                        .as_ref()
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
                    let bytes = if failure == Some("partial") {
                        request.body[..request.body.len() / 2].to_vec()
                    } else {
                        request.body.clone()
                    };
                    cloud.lock().unwrap().insert(request.path.clone(), bytes);
                    response.status = if failure == Some("partial") || failure == Some("lost") {
                        504
                    } else {
                        201
                    };
                    fault.fail_next_readback = failure == Some("readback");
                }
                // Actual service incompatibility: publication must never need this.
                "MOVE" => response.status = 409,
                "DELETE" => response.status = 405,
                _ => {}
            }
            response
        }));
        (files, seen, base)
    }

    #[test]
    fn slots_publish_three_versions_without_move_or_legacy_overwrite() {
        let (files, seen, base) = slot_server(Arc::default());
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        pair_cache(&vault, dir.path(), 1);
        for revision in 2..=4 {
            assert_eq!(
                publish_flow(&vault, dir.path(), &InlineTx)
                    .unwrap()
                    .revision,
                revision
            );
        }
        let files = files.lock().unwrap();
        assert_eq!(files.len(), 2);
        assert_eq!(
            EncryptedEnvelope::from_json(&files["/Account/probe/snapshot-a.enc"])
                .unwrap()
                .metadata
                .revision,
            4
        );
        assert_eq!(
            EncryptedEnvelope::from_json(&files["/Account/probe/snapshot-b.enc"])
                .unwrap()
                .metadata
                .revision,
            3
        );
        assert!(
            seen.lock()
                .unwrap()
                .iter()
                .all(|r| r.method != "MOVE" && r.method != "DELETE")
        );
    }

    #[test]
    fn slots_migrate_legacy_four_reusing_exact_pending_five() {
        let (files, seen, base) = slot_server(Arc::default());
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        let key = pair_cache(&vault, dir.path(), 4);
        save_publish_state(dir.path(), 4).unwrap();
        let old = envelope_for(&key, &build_payload(4), 4);
        files
            .lock()
            .unwrap()
            .insert("/Account/probe/latest.enc".into(), old.clone());
        let pending = prepare_pending(dir.path(), &key)
            .unwrap()
            .to_json()
            .unwrap();
        pair_cache(&vault, dir.path(), 7); // Historical local probe state must not invalidate pending 5.
        assert_eq!(
            publish_flow(&vault, dir.path(), &InlineTx)
                .unwrap()
                .revision,
            5
        );
        assert_eq!(
            files.lock().unwrap()["/Account/probe/snapshot-a.enc"],
            pending
        );
        assert_eq!(files.lock().unwrap()["/Account/probe/latest.enc"], old);
        assert!(
            seen.lock()
                .unwrap()
                .iter()
                .all(|r| r.method != "PUT" || r.path.ends_with("snapshot-a.enc"))
        );
    }

    #[test]
    fn slots_partial_put_preserves_readable_copy_and_retries_same_ciphertext() {
        let fault = Arc::new(StdMutex::new(SlotFault::default()));
        let (files, seen, base) = slot_server(fault.clone());
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        let key = pair_cache(&vault, dir.path(), 1);
        publish_flow(&vault, dir.path(), &InlineTx).unwrap();
        publish_flow(&vault, dir.path(), &InlineTx).unwrap();
        let stable = files.lock().unwrap()["/Account/probe/snapshot-b.enc"].clone();
        fault.lock().unwrap().put_failure = Some("partial");
        assert!(publish_flow(&vault, dir.path(), &InlineTx).is_err());
        let pending = fs::read(dir.path().join(PENDING_FILE)).unwrap();
        assert_eq!(cached_revision(&vault, dir.path()), 3);
        assert_eq!(
            files.lock().unwrap()["/Account/probe/snapshot-b.enc"],
            stable
        );
        let phone = seeded_vault(&base, Some(&key));
        let phone_dir = tempfile::tempdir().unwrap();
        let fetched = fetch_flow(&phone, phone_dir.path(), &InlineTx).unwrap();
        assert!(matches!(
            fetched,
            FetchOutcome::Updated { revision: 3, .. } | FetchOutcome::Latest { revision: 3, .. }
        ));
        assert_eq!(
            publish_flow(&vault, dir.path(), &InlineTx)
                .unwrap()
                .revision,
            4
        );
        assert_eq!(
            files.lock().unwrap()["/Account/probe/snapshot-a.enc"],
            pending
        );
        let writes: Vec<_> = seen
            .lock()
            .unwrap()
            .iter()
            .filter(|r| r.method == "PUT" && r.path.ends_with("snapshot-a.enc"))
            .map(|r| r.body.clone())
            .collect();
        assert_eq!(writes[1], writes[2]);
    }

    #[test]
    fn slots_lost_put_response_or_readback_confirms_without_second_upload() {
        for failure in ["lost", "readback"] {
            let fault = Arc::new(StdMutex::new(SlotFault {
                put_failure: Some(failure),
                ..Default::default()
            }));
            let (_, seen, base) = slot_server(fault);
            let vault = seeded_vault(&base, None);
            let dir = tempfile::tempdir().unwrap();
            pair_cache(&vault, dir.path(), 1);
            assert!(publish_flow(&vault, dir.path(), &InlineTx).is_err());
            assert_eq!(
                publish_flow(&vault, dir.path(), &InlineTx)
                    .unwrap()
                    .revision,
                2
            );
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
    fn slots_first_upload_partial_retries_from_durable_empty_baseline() {
        let fault = Arc::new(StdMutex::new(SlotFault {
            put_failure: Some("partial"),
            ..Default::default()
        }));
        let (_, seen, base) = slot_server(fault);
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        pair_cache(&vault, dir.path(), 1);
        assert!(publish_flow(&vault, dir.path(), &InlineTx).is_err());
        assert_eq!(
            publish_flow(&vault, dir.path(), &InlineTx)
                .unwrap()
                .revision,
            2
        );
        let writes: Vec<_> = seen
            .lock()
            .unwrap()
            .iter()
            .filter(|r| r.method == "PUT")
            .map(|r| r.body.clone())
            .collect();
        assert_eq!(writes[0], writes[1]);
    }

    #[test]
    fn slots_unknown_damaged_file_is_never_overwritten() {
        let (files, seen, base) = slot_server(Arc::default());
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        let key = pair_cache(&vault, dir.path(), 2);
        files.lock().unwrap().insert(
            "/Account/probe/snapshot-a.enc".into(),
            b"unknown broken bytes".to_vec(),
        );
        files.lock().unwrap().insert(
            "/Account/probe/snapshot-b.enc".into(),
            envelope_for(&key, &build_payload(2), 2),
        );
        assert!(publish_flow(&vault, dir.path(), &InlineTx).is_err());
        assert!(seen.lock().unwrap().iter().all(|r| r.method != "PUT"));
        assert_eq!(
            files.lock().unwrap()["/Account/probe/snapshot-a.enc"],
            b"unknown broken bytes"
        );
    }

    #[test]
    fn slots_network_failure_keeps_available_copy_without_claiming_latest() {
        let fault = Arc::new(StdMutex::new(SlotFault {
            get_failure: Some("snapshot-a.enc".into()),
            ..Default::default()
        }));
        let (files, seen, base) = slot_server(fault);
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        let key = pair_cache(&vault, dir.path(), 1);
        files.lock().unwrap().insert(
            "/Account/probe/snapshot-b.enc".into(),
            envelope_for(&key, &build_payload(3), 3),
        );
        let result = fetch_flow(&vault, dir.path(), &InlineTx).unwrap();
        assert!(matches!(
            result,
            FetchOutcome::Updated {
                revision: 3,
                warning: Some(_),
                ..
            }
        ));
        assert!(publish_flow(&vault, dir.path(), &InlineTx).is_err());
        assert!(seen.lock().unwrap().iter().all(|r| r.method != "PUT"));
    }

    #[test]
    fn slots_equal_revision_divergence_blocks_fetch_and_publish_without_writes() {
        let (files, seen, base) = slot_server(Arc::default());
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        let key = pair_cache(&vault, dir.path(), 1);
        for file in ["snapshot-a.enc", "snapshot-b.enc"] {
            files.lock().unwrap().insert(
                format!("/Account/probe/{file}"),
                envelope_for(&key, &build_payload(3), 3),
            );
        }
        assert!(
            fetch_flow(&vault, dir.path(), &InlineTx)
                .unwrap_err()
                .contains("同版本内容")
        );
        assert!(
            publish_flow(&vault, dir.path(), &InlineTx)
                .unwrap_err()
                .contains("同版本内容")
        );
        assert_eq!(cached_revision(&vault, dir.path()), 1);
        assert!(seen.lock().unwrap().iter().all(|r| r.method != "PUT"));
    }

    #[test]
    fn cli_pending_guard_never_generates_or_replaces_a_pending_version() {
        let vault = seeded_vault("http://127.0.0.1:9/", None);
        let dir = tempfile::tempdir().unwrap();
        let key = pair_cache(&vault, dir.path(), 1);
        assert!(check_existing_pending(&vault, dir.path(), 2).is_err());
        assert!(!dir.path().join(PENDING_FILE).exists());
        prepare_pending(dir.path(), &key).unwrap();
        let bytes = fs::read(dir.path().join(PENDING_FILE)).unwrap();
        assert!(check_existing_pending(&vault, dir.path(), 3).is_err());
        check_existing_pending(&vault, dir.path(), 2).unwrap();
        assert_eq!(fs::read(dir.path().join(PENDING_FILE)).unwrap(), bytes);
    }

    #[test]
    fn slots_foreign_copy_never_overwritten_even_beside_valid_copy() {
        let (files, seen, base) = slot_server(Arc::default());
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        pair_cache(&vault, dir.path(), 1);
        files.lock().unwrap().insert(
            "/Account/probe/snapshot-b.enc".into(),
            fs::read(dir.path().join(CACHE_FILE)).unwrap(),
        );
        let stranger = ContentKey::generate().unwrap();
        let foreign = envelope_for(&stranger, &build_payload(9), 9);
        files
            .lock()
            .unwrap()
            .insert("/Account/probe/snapshot-a.enc".into(), foreign.clone());
        let fetched = fetch_flow(&vault, dir.path(), &InlineTx).unwrap();
        assert!(matches!(
            fetched,
            FetchOutcome::Updated {
                revision: 1,
                warning: Some(_),
                ..
            } | FetchOutcome::Latest {
                revision: 1,
                warning: Some(_),
                ..
            }
        ));
        assert!(publish_flow(&vault, dir.path(), &InlineTx).is_err());
        assert_eq!(
            files.lock().unwrap()["/Account/probe/snapshot-a.enc"],
            foreign
        );
        assert!(seen.lock().unwrap().iter().all(|r| r.method != "PUT"));
    }

    #[test]
    fn android_diagnostic_mode_is_read_only() {
        let (_, seen, base) = slot_server(Arc::default());
        let dir = tempfile::tempdir().unwrap();
        let lines = run_diagnostic(&seeded_vault(&base, None), dir.path(), false);
        assert!(lines.iter().any(|l| l.contains("手机只读诊断完成")));
        assert!(
            seen.lock()
                .unwrap()
                .iter()
                .all(|r| matches!(r.method.as_str(), "GET" | "PROPFIND"))
        );
    }

    #[test]
    fn payload_contract_roundtrip_and_rejections() {
        let records = parse_payload(&build_payload(3)).unwrap();
        assert_eq!(records.len(), 2);
        assert_eq!(records[0].label, "第 3 次发布");
        assert_eq!(records[1].amount_minor, 5678);
        // Legacy fixture is not contract-conformant.
        assert!(parse_payload(FIXTURE).is_err());
        assert!(parse_payload(b"not json").is_err());
        for mutate in ["schema", "record-id", "record-count", "amount"] {
            let mut value: serde_json::Value = serde_json::from_slice(&build_payload(1)).unwrap();
            match mutate {
                "schema" => value["schema"] = "other-schema".into(),
                "record-id" => value["records"][0]["id"] = "other".into(),
                "record-count" => {
                    value["records"].as_array_mut().unwrap().pop();
                }
                "amount" => value["records"][0]["amountMinor"] = serde_json::json!(0),
                _ => unreachable!(),
            }
            assert!(
                parse_payload(value.to_string().as_bytes()).is_err(),
                "mutation {mutate} must be rejected"
            );
        }
    }

    type SeenRequests = Arc<StdMutex<Vec<MockRequest>>>;

    fn storage_handler() -> (Arc<StdMutex<SlotMap>>, SeenRequests, String) {
        let uploaded: Arc<StdMutex<SlotMap>> = Arc::default();
        let handler = {
            let uploaded = uploaded.clone();
            Arc::new(move |request: &MockRequest| {
                let path = request.path.clone();
                match request.method.as_str() {
                    "GET" => {
                        let map = uploaded.lock().unwrap();
                        match map.get(&path) {
                            Some(body) => MockResponse {
                                status: 200,
                                headers: vec![],
                                body: body.clone(),
                                truncate_body_after: None,
                            },
                            None => MockResponse {
                                status: 404,
                                headers: vec![],
                                body: vec![],
                                truncate_body_after: None,
                            },
                        }
                    }
                    // Flat storage: both collections pre-exist, so MKCOL is
                    // answered with 405 and verified through PROPFIND.
                    "MKCOL" => MockResponse {
                        status: 405,
                        headers: vec![],
                        body: vec![],
                        truncate_body_after: None,
                    },
                    "PROPFIND" => MockResponse {
                        status: 207,
                        headers: vec![],
                        body: br#"<D:multistatus xmlns:D="DAV:"><D:response><D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response></D:multistatus>"#.to_vec(),
                        truncate_body_after: None,
                    },
                    "PUT" => {
                        uploaded.lock().unwrap().insert(path, request.body.clone());
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
                        let body = uploaded.lock().unwrap().remove(&path).unwrap_or_default();
                        uploaded
                            .lock()
                            .unwrap()
                            .insert(destination_path(&destination), body);
                        MockResponse {
                            status: 201,
                            headers: vec![],
                            body: vec![],
                            truncate_body_after: None,
                        }
                    }
                    "DELETE" => MockResponse {
                        status: 204,
                        headers: vec![],
                        body: vec![],
                        truncate_body_after: None,
                    },
                    other => panic!("unexpected method {other}"),
                }
            })
        };
        let (base, seen) = spawn_server(handler);
        (uploaded, seen, base)
    }

    #[test]
    fn publish_succeeds_via_slot_put_and_finalizes_local_state() {
        let (uploaded, seen, base) = slot_server(Arc::default());
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        pair_cache(&vault, dir.path(), 1);
        let outcome = publish_flow(&vault, dir.path(), &InlineTx).unwrap();
        assert_eq!(outcome.revision, 2);
        assert_eq!(outcome.method, "put-slot");
        assert_eq!(uploaded.lock().unwrap().len(), 1);
        assert_eq!(cached_revision(&vault, dir.path()), 2);
        assert!(!dir.path().join(PENDING_FILE).exists());
        assert_eq!(load_publish_state(dir.path()).unwrap(), 2);
        assert!(
            seen.lock()
                .unwrap()
                .iter()
                .all(|r| r.method != "MOVE" && r.method != "DELETE")
        );
    }

    #[test]
    fn publish_reuses_pending_ciphertext_after_failure_and_retry() {
        let fault = Arc::new(StdMutex::new(SlotFault {
            put_failure: Some("partial"),
            ..Default::default()
        }));
        let (_, seen, base) = slot_server(fault);
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        pair_cache(&vault, dir.path(), 1);
        assert!(publish_flow(&vault, dir.path(), &InlineTx).is_err());
        let before = fs::read(dir.path().join(PENDING_FILE)).unwrap();
        assert_eq!(cached_revision(&vault, dir.path()), 1);
        assert_eq!(
            publish_flow(&vault, dir.path(), &InlineTx)
                .unwrap()
                .revision,
            2
        );
        let puts: Vec<_> = seen
            .lock()
            .unwrap()
            .iter()
            .filter(|r| r.method == "PUT")
            .map(|r| r.body.clone())
            .collect();
        assert_eq!(puts, vec![before.clone(), before]);
    }

    #[test]
    fn publish_refuses_to_overwrite_a_foreign_cloud_file() {
        let foreign_key = ContentKey::generate().unwrap();
        let foreign_bytes = envelope_for(&foreign_key, b"someone else", 1);
        let (base, _seen) = spawn_server(Arc::new(move |_| MockResponse {
            status: 200,
            headers: vec![],
            body: foreign_bytes.clone(),
            truncate_body_after: None,
        }));
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        pair_cache(&vault, dir.path(), 1);
        let error = publish_flow(&vault, dir.path(), &InlineTx).unwrap_err();
        assert!(error.contains("不属于本配对"), "unexpected error: {error}");
        assert!(
            dir.path().join(PENDING_FILE).exists(),
            "pending must be kept"
        );
        assert_eq!(load_publish_state(dir.path()).unwrap(), 0);
        assert_eq!(cached_revision(&vault, dir.path()), 1);
    }

    #[test]
    fn publish_reports_conflict_instead_of_bumping_when_cloud_is_newer() {
        let key = ContentKey::generate().unwrap();
        let cloud_bytes = envelope_for(&key, &build_payload(9), 9);
        let (base, seen) = spawn_server(Arc::new(move |request: &MockRequest| {
            match request.method.as_str() {
                "MKCOL" => MockResponse {
                    status: 405,
                    headers: vec![],
                    body: vec![],
                    truncate_body_after: None,
                },
                "PROPFIND" => MockResponse {
                    status: 207,
                    headers: vec![],
                    body: br#"<D:multistatus xmlns:D="DAV:"><D:response><D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response></D:multistatus>"#.to_vec(),
                    truncate_body_after: None,
                },
                "GET" if request.path.ends_with(REMOTE_FILE) => MockResponse {
                    status: 200,
                    headers: vec![],
                    body: cloud_bytes.clone(),
                    truncate_body_after: None,
                },
                "GET" => MockResponse { status: 404, headers: vec![], body: vec![], truncate_body_after: None },
                other => panic!("unexpected request {other}"),
            }
        }));
        let vault = seeded_vault(&base, Some(&key));
        let dir = tempfile::tempdir().unwrap();
        pair_cache(&vault, dir.path(), 1);
        let error = publish_flow(&vault, dir.path(), &InlineTx).unwrap_err();
        assert!(
            error.contains("更高版本") && error.contains("已保留待发内容"),
            "unexpected error: {error}"
        );
        // The pending ciphertext is kept verbatim; nothing was uploaded.
        let pending = read_cache(&dir.path().join(PENDING_FILE), &key)
            .unwrap()
            .unwrap();
        assert_eq!(pending.metadata.revision, 2);
        assert_eq!(load_publish_state(dir.path()).unwrap(), 0);
        assert_eq!(cached_revision(&vault, dir.path()), 1);
        assert!(
            !seen.lock().unwrap().iter().any(|r| r.method == "PUT"),
            "a conflict must never upload or overwrite anything"
        );
    }

    #[test]
    fn publish_reports_conflict_on_same_version_with_different_ciphertext() {
        let key = ContentKey::generate().unwrap();
        // Same revision as the prepared pending file but different bytes
        // (fresh random nonce), which is a genuine divergence: report it.
        let cloud_bytes = envelope_for(&key, &build_payload(2), 2);
        let (base, seen) = spawn_server(Arc::new(move |request: &MockRequest| {
            match request.method.as_str() {
                "MKCOL" => MockResponse {
                    status: 405,
                    headers: vec![],
                    body: vec![],
                    truncate_body_after: None,
                },
                "PROPFIND" => MockResponse {
                    status: 207,
                    headers: vec![],
                    body: br#"<D:multistatus xmlns:D="DAV:"><D:response><D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response></D:multistatus>"#.to_vec(),
                    truncate_body_after: None,
                },
                "GET" if request.path.ends_with(REMOTE_FILE) => MockResponse {
                    status: 200,
                    headers: vec![],
                    body: cloud_bytes.clone(),
                    truncate_body_after: None,
                },
                "GET" => MockResponse { status: 404, headers: vec![], body: vec![], truncate_body_after: None },
                other => panic!("unexpected request {other}"),
            }
        }));
        let vault = seeded_vault(&base, Some(&key));
        let dir = tempfile::tempdir().unwrap();
        pair_cache(&vault, dir.path(), 1);
        let error = publish_flow(&vault, dir.path(), &InlineTx).unwrap_err();
        assert!(
            error.contains("同版本内容") && error.contains("未改动云端正式文件"),
            "unexpected error: {error}"
        );
        let pending = read_cache(&dir.path().join(PENDING_FILE), &key)
            .unwrap()
            .unwrap();
        assert_eq!(pending.metadata.revision, 2);
        assert!(
            !seen.lock().unwrap().iter().any(|r| r.method == "PUT"),
            "same-version divergence must never be overwritten"
        );
    }

    #[test]
    fn retry_after_failed_slot_readback_confirms_the_published_version() {
        let fault = Arc::new(StdMutex::new(SlotFault {
            put_failure: Some("readback"),
            ..Default::default()
        }));
        let (_, seen, base) = slot_server(fault);
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        pair_cache(&vault, dir.path(), 1);
        let error = publish_flow(&vault, dir.path(), &InlineTx).unwrap_err();
        assert!(error.contains("回读"));
        assert_eq!(load_publish_state(dir.path()).unwrap(), 0);
        assert!(dir.path().join(PENDING_FILE).exists());
        assert_eq!(
            publish_flow(&vault, dir.path(), &InlineTx)
                .unwrap()
                .revision,
            2
        );
        assert_eq!(
            seen.lock()
                .unwrap()
                .iter()
                .filter(|r| r.method == "PUT")
                .count(),
            1
        );
    }

    #[test]
    fn retry_after_lost_put_response_confirms_without_regenerating() {
        let fault = Arc::new(StdMutex::new(SlotFault {
            put_failure: Some("lost"),
            ..Default::default()
        }));
        let (_, seen, base) = slot_server(fault);
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        pair_cache(&vault, dir.path(), 1);
        assert!(publish_flow(&vault, dir.path(), &InlineTx).is_err());
        assert_eq!(load_publish_state(dir.path()).unwrap(), 0);
        assert_eq!(
            publish_flow(&vault, dir.path(), &InlineTx)
                .unwrap()
                .revision,
            2
        );
        assert_eq!(
            seen.lock()
                .unwrap()
                .iter()
                .filter(|r| r.method == "PUT")
                .count(),
            1
        );
    }

    #[test]
    fn local_finalize_failure_keeps_the_same_version_for_retry() {
        // The local commit after a confirmed publish fails once; the retry
        // must finalize the same version and ciphertext, not create another.
        struct FlakyTx {
            runs: StdMutex<usize>,
            fail_runs: Vec<usize>,
        }
        impl Transaction for FlakyTx {
            fn run<T: Send>(
                &self,
                operation: impl FnOnce() -> Result<T, String> + Send,
            ) -> Result<T, String> {
                let mut runs = self.runs.lock().unwrap();
                *runs += 1;
                if self.fail_runs.contains(&*runs) {
                    return Err("本地事务提交失败（模拟）。".to_string());
                }
                operation()
            }
        }
        let (uploaded, _seen, base) = storage_handler();
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        pair_cache(&vault, dir.path(), 1);
        let tx = FlakyTx {
            runs: StdMutex::new(0),
            fail_runs: vec![3],
        };
        let first = publish_flow(&vault, dir.path(), &tx);
        assert!(first.is_err(), "the first finalize must fail");
        assert!(dir.path().join(PENDING_FILE).exists());
        assert_eq!(load_publish_state(dir.path()).unwrap(), 0);
        // Retry with a healthy transaction: same pending, same version.
        let outcome = publish_flow(&vault, dir.path(), &InlineTx).unwrap();
        assert_eq!(outcome.revision, 2);
        assert_eq!(load_publish_state(dir.path()).unwrap(), 2);
        assert!(!dir.path().join(PENDING_FILE).exists());
        drop(uploaded);
    }

    #[test]
    fn move_unsupported_does_not_block_publishing_or_modify_legacy() {
        let (files, seen, base) = slot_server(Arc::default());
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        let key = pair_cache(&vault, dir.path(), 1);
        let legacy = envelope_for(&key, &build_payload(1), 1);
        files
            .lock()
            .unwrap()
            .insert("/Account/probe/latest.enc".into(), legacy.clone());
        assert_eq!(
            publish_flow(&vault, dir.path(), &InlineTx)
                .unwrap()
                .revision,
            2
        );
        assert_eq!(files.lock().unwrap()["/Account/probe/latest.enc"], legacy);
        assert!(
            seen.lock()
                .unwrap()
                .iter()
                .all(|r| r.method != "MOVE" && r.method != "DELETE")
        );
    }

    #[test]
    fn service_refusing_move_can_publish_slots_preserving_legacy_and_pending() {
        let (files, seen, base) = slot_server(Arc::default());
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        let key = pair_cache(&vault, dir.path(), 1);
        let legacy = envelope_for(&key, &build_payload(1), 1);
        files
            .lock()
            .unwrap()
            .insert("/Account/probe/latest.enc".into(), legacy.clone());
        let pending = prepare_pending(dir.path(), &key)
            .unwrap()
            .to_json()
            .unwrap();
        files
            .lock()
            .unwrap()
            .insert("/Account/probe/latest.enc.pending".into(), pending.clone());
        assert_eq!(
            publish_flow(&vault, dir.path(), &InlineTx)
                .unwrap()
                .revision,
            2
        );
        assert_eq!(files.lock().unwrap()["/Account/probe/latest.enc"], legacy);
        assert_eq!(
            files.lock().unwrap()["/Account/probe/latest.enc.pending"],
            pending
        );
        assert!(
            seen.lock()
                .unwrap()
                .iter()
                .all(|r| r.method != "MOVE" && r.method != "DELETE")
        );
    }

    #[test]
    fn first_publish_on_a_strict_empty_server_creates_directories_and_confirms() {
        let entries: crate::webdav::tests_support::HierarchyEntries = Arc::default();
        let (base, seen) = hierarchy_server(entries.clone(), None, None);
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        pair_cache(&vault, dir.path(), 1);
        let outcome = publish_flow(&vault, dir.path(), &InlineTx).unwrap();
        assert_eq!(outcome.revision, 2);
        {
            let map = entries.lock().unwrap();
            let official = map.get("Account/probe/snapshot-a.enc").unwrap();
            assert_eq!(official.0, 'f');
        }
        // The pending file is gone locally and the state advanced.
        assert!(!dir.path().join(PENDING_FILE).exists());
        assert_eq!(load_publish_state(dir.path()).unwrap(), 2);
        // No PUT was ever sent to the official path.
        assert!(
            seen.lock()
                .unwrap()
                .iter()
                .all(|r| !(r.method == "PUT" && r.path.ends_with(REMOTE_FILE)))
        );
    }

    #[test]
    fn first_publish_surfaces_directory_creation_failures_without_uploading() {
        // MKCOL refused with 403 (e.g. quota): publish must stop before any
        // upload instead of reporting success.
        let (base, seen) = hierarchy_server(Arc::default(), Some(403), None);
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        pair_cache(&vault, dir.path(), 1);
        let error = publish_flow(&vault, dir.path(), &InlineTx).unwrap_err();
        assert_eq!(
            seen.lock()
                .unwrap()
                .iter()
                .filter(|r| r.method == "PUT")
                .count(),
            0,
            "nothing may be uploaded when the directory cannot be ensured"
        );
        assert!(
            error.contains("403") || error.contains("拒绝访问"),
            "unexpected error: {error}"
        );
    }

    #[test]
    fn fetch_accepts_new_version_persists_and_reports_records() {
        let key = ContentKey::generate().unwrap();
        let cloud_bytes = envelope_for(&key, &build_payload(5), 5);
        let (base, _) = spawn_server(Arc::new(move |request: &MockRequest| {
            assert_eq!(request.method, "GET");
            MockResponse {
                status: 200,
                headers: vec![],
                body: cloud_bytes.clone(),
                truncate_body_after: None,
            }
        }));
        let vault = seeded_vault(&base, Some(&key));
        let dir = tempfile::tempdir().unwrap();
        pair_cache(&vault, dir.path(), 1);
        let outcome = fetch_flow(&vault, dir.path(), &InlineTx).unwrap();
        match outcome {
            FetchOutcome::Updated {
                revision, records, ..
            } => {
                assert_eq!(revision, 5);
                assert_eq!(records[0].label, "第 5 次发布");
                assert_eq!(records[1].amount_minor, 5678);
            }
            other => panic!("expected Updated, got {other:?}"),
        }
        assert_eq!(cached_revision(&vault, dir.path()), 5);
        // A repeat fetch of the identical cloud file reports Latest.
        let outcome = fetch_flow(&vault, dir.path(), &InlineTx).unwrap();
        match outcome {
            FetchOutcome::Latest {
                revision, records, ..
            } => {
                assert_eq!(revision, 5);
                assert_eq!(records.len(), 2);
            }
            other => panic!("expected Latest, got {other:?}"),
        }
    }

    #[test]
    fn fetch_rejects_lower_version_same_version_conflict_and_wrong_key() {
        let key = ContentKey::generate().unwrap();
        let older_bytes = envelope_for(&key, &build_payload(2), 2);
        let conflicting_bytes = envelope_for(&key, &build_payload(3), 3);
        let stranger_key = ContentKey::generate().unwrap();
        let stranger_bytes = envelope_for(&stranger_key, &build_payload(9), 9);
        let cases: Vec<(Vec<u8>, &str)> = vec![
            (older_bytes, "低于本地缓存"),
            (conflicting_bytes, "同版本内容"),
            (stranger_bytes, "无法通过认证"),
        ];
        for (body, expected) in cases {
            let (base, _) = spawn_server(Arc::new(move |_| MockResponse {
                status: 200,
                headers: vec![],
                body: body.clone(),
                truncate_body_after: None,
            }));
            let vault = seeded_vault(&base, Some(&key));
            let dir = tempfile::tempdir().unwrap();
            pair_cache(&vault, dir.path(), 3);
            let before = fs::read(dir.path().join(CACHE_FILE)).unwrap();
            let error = fetch_flow(&vault, dir.path(), &InlineTx).unwrap_err();
            assert!(error.contains(expected), "unexpected error: {error}");
            assert_eq!(before, fs::read(dir.path().join(CACHE_FILE)).unwrap());
            assert_eq!(cached_revision(&vault, dir.path()), 3);
        }
    }

    #[test]
    fn corrupted_high_version_never_poisons_the_revision_checkpoint() {
        let key = ContentKey::generate().unwrap();
        // High revision but tampered ciphertext: fails authentication or
        // parsing; the durable checkpoint stays at the cached revision.
        let high = key_content(&key, &build_payload(8), 8);
        let text = String::from_utf8(high.to_json().unwrap()).unwrap();
        let marker = "\"ciphertext\":\"";
        let start = text.find(marker).unwrap() + marker.len();
        let mut tampered = text;
        let flipped = if tampered.as_bytes()[start] == b'A' {
            'B'
        } else {
            'A'
        };
        tampered.replace_range(start..start + 1, &flipped.to_string());
        let tampered_bytes = tampered.into_bytes();
        let (base, _) = spawn_server(Arc::new(move |_| MockResponse {
            status: 200,
            headers: vec![],
            body: tampered_bytes.clone(),
            truncate_body_after: None,
        }));
        let vault = seeded_vault(&base, Some(&key));
        let dir = tempfile::tempdir().unwrap();
        pair_cache(&vault, dir.path(), 2);
        assert!(fetch_flow(&vault, dir.path(), &InlineTx).is_err());
        assert_eq!(cached_revision(&vault, dir.path()), 2);
        // A subsequent legitimate update still works.
        let legitimate = envelope_for(&key, &build_payload(3), 3);
        let (base, _) = spawn_server(Arc::new(move |_| MockResponse {
            status: 200,
            headers: vec![],
            body: legitimate.clone(),
            truncate_body_after: None,
        }));
        let vault = seeded_vault(&base, Some(&key));
        let dir = tempfile::tempdir().unwrap();
        pair_cache(&vault, dir.path(), 2);
        let outcome = fetch_flow(&vault, dir.path(), &InlineTx).unwrap();
        assert!(matches!(outcome, FetchOutcome::Updated { revision: 3, .. }));
    }

    #[test]
    fn contract_violating_high_version_is_rejected_without_persisting() {
        let key = ContentKey::generate().unwrap();
        // Validly authenticated under our key but violating the fictional
        // data contract: must never be persisted.
        let bogus = envelope_for(
            &key,
            br#"{"schema":"account-synthetic-probe-v1","records":[]}"#,
            7,
        );
        let (base, _) = spawn_server(Arc::new(move |_| MockResponse {
            status: 200,
            headers: vec![],
            body: bogus.clone(),
            truncate_body_after: None,
        }));
        let vault = seeded_vault(&base, Some(&key));
        let dir = tempfile::tempdir().unwrap();
        pair_cache(&vault, dir.path(), 1);
        let error = fetch_flow(&vault, dir.path(), &InlineTx).unwrap_err();
        assert!(error.contains("虚构数据契约"), "unexpected error: {error}");
        assert_eq!(cached_revision(&vault, dir.path()), 1);
        // A legitimate version 2 is still accepted afterwards.
        let legitimate = envelope_for(&key, &build_payload(2), 2);
        let (base, _) = spawn_server(Arc::new(move |_| MockResponse {
            status: 200,
            headers: vec![],
            body: legitimate.clone(),
            truncate_body_after: None,
        }));
        let vault = seeded_vault(&base, Some(&key));
        let dir = tempfile::tempdir().unwrap();
        pair_cache(&vault, dir.path(), 1);
        let outcome = fetch_flow(&vault, dir.path(), &InlineTx).unwrap();
        assert!(matches!(outcome, FetchOutcome::Updated { revision: 2, .. }));
    }

    #[test]
    fn fetch_reports_cloud_empty_and_keeps_cache_visible() {
        let (base, _) = spawn_server(static_handler(404, vec![]));
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        pair_cache(&vault, dir.path(), 4);
        let outcome = fetch_flow(&vault, dir.path(), &InlineTx).unwrap();
        match outcome {
            FetchOutcome::CloudEmpty { has_cache } => assert!(has_cache),
            other => panic!("expected CloudEmpty, got {other:?}"),
        }
        assert_eq!(cached_revision(&vault, dir.path()), 4);
    }

    #[test]
    fn unconfigured_or_unpaired_states_are_reported_not_crashed() {
        let (base, _) = spawn_server(static_handler(404, vec![]));
        // Unpaired but configured: fetch fails with the pairing hint.
        let vault = seeded_vault(&base, None);
        let dir = tempfile::tempdir().unwrap();
        let error = fetch_flow(&vault, dir.path(), &InlineTx).unwrap_err();
        assert!(error.contains("尚未配对"), "unexpected error: {error}");
        // Paired but unconfigured: fetch fails with the configuration hint.
        let bare = MemorySlots::default();
        let dir = tempfile::tempdir().unwrap();
        ensure_content_key(&bare, &dir.path().join(CACHE_FILE)).unwrap();
        let error = fetch_flow(&bare, dir.path(), &InlineTx).unwrap_err();
        assert!(
            error.contains("尚未配置坚果云同步"),
            "unexpected error: {error}"
        );
    }

    /// Vault wrapper whose store fails for selected slots, simulating a
    /// platform secure-storage write failure.
    #[test]
    fn password_readback_failure_restores_previous_password() {
        struct FailReadOnce {
            inner: MemorySlots,
            written: std::sync::atomic::AtomicBool,
            failed: std::sync::atomic::AtomicBool,
        }
        impl SecretVault for FailReadOnce {
            fn load_secret(&self, slot: &str) -> Result<Option<Vec<u8>>, String> {
                use std::sync::atomic::Ordering;
                if slot == PASSWORD_SLOT
                    && self.written.load(Ordering::SeqCst)
                    && !self.failed.swap(true, Ordering::SeqCst)
                {
                    return Err("密码回读失败（模拟）。".into());
                }
                self.inner.load_secret(slot)
            }
            fn store_secret(&self, slot: &str, value: &[u8]) -> Result<(), String> {
                self.inner.store_secret(slot, value)?;
                if slot == PASSWORD_SLOT {
                    self.written
                        .store(true, std::sync::atomic::Ordering::SeqCst);
                }
                Ok(())
            }
            fn delete_secret(&self, slot: &str) -> Result<(), String> {
                self.inner.delete_secret(slot)
            }
        }
        let inner = MemorySlots::default();
        apply_configure(&inner, "tester@example.test", "old-password").unwrap();
        let config_before = inner.load_secret(CONFIG_SLOT).unwrap();
        let vault = FailReadOnce {
            inner,
            written: std::sync::atomic::AtomicBool::new(false),
            failed: std::sync::atomic::AtomicBool::new(false),
        };
        assert!(apply_configure(&vault, "tester@example.test", "new-password").is_err());
        assert_eq!(
            vault.inner.load_secret(PASSWORD_SLOT).unwrap().unwrap(),
            b"old-password"
        );
        assert_eq!(vault.inner.load_secret(CONFIG_SLOT).unwrap(), config_before);
    }

    struct FailOnStore {
        inner: MemorySlots,
        fail_slots: Vec<&'static str>,
    }
    impl SecretVault for FailOnStore {
        fn load_secret(&self, slot: &str) -> Result<Option<Vec<u8>>, String> {
            self.inner.load_secret(slot)
        }
        fn store_secret(&self, slot: &str, value: &[u8]) -> Result<(), String> {
            if self.fail_slots.contains(&slot) {
                return Err("安全存储写入失败（模拟）。".to_string());
            }
            self.inner.store_secret(slot, value)
        }
        fn delete_secret(&self, slot: &str) -> Result<(), String> {
            self.inner.delete_secret(slot)
        }
    }

    #[test]
    fn apply_configure_fresh_same_account_and_foreign_rejection() {
        let vault = MemorySlots::default();
        // Fresh configuration succeeds and reports unpaired.
        let summary = apply_configure(&vault, "tester@example.test", "pw-one").unwrap();
        assert_eq!(
            summary,
            SyncStatusSummary {
                configured: true,
                paired: false,
                pending_revision: None
            }
        );
        // Same account, new password: only the password slot changes.
        let config_before = vault.load_secret(CONFIG_SLOT).unwrap();
        let key = ContentKey::generate().unwrap();
        persist_key(
            &SlotVault {
                vault: &vault,
                slot: PROBE_KEY_SLOT,
            },
            &key,
        )
        .unwrap();
        let summary = apply_configure(&vault, "tester@example.test", "pw-two").unwrap();
        assert!(summary.configured);
        assert_eq!(config_before, vault.load_secret(CONFIG_SLOT).unwrap());
        assert_eq!(
            vault.load_secret(PASSWORD_SLOT).unwrap().unwrap(),
            b"pw-two"
        );
        assert_eq!(
            load_content_key(&vault).unwrap().unwrap().as_bytes(),
            key.as_bytes(),
            "a password change must keep the content key"
        );
        // A different account is rejected before any write.
        let error = apply_configure(&vault, "someone@else.test", "pw-three").unwrap_err();
        assert!(
            error.contains("另一坚果云账号"),
            "unexpected error: {error}"
        );
        assert_eq!(
            vault.load_secret(PASSWORD_SLOT).unwrap().unwrap(),
            b"pw-two",
            "the rejection must not touch stored credentials"
        );
        assert_eq!(config_before, vault.load_secret(CONFIG_SLOT).unwrap());
    }

    #[test]
    fn apply_configure_failure_rolls_back_the_fresh_configuration() {
        // The config write itself fails: nothing is stored at all.
        let vault = FailOnStore {
            inner: MemorySlots::default(),
            fail_slots: vec![CONFIG_SLOT],
        };
        let error = apply_configure(&vault, "tester@example.test", "pw").unwrap_err();
        assert!(error.contains("写入失败"), "unexpected error: {error}");
        assert!(vault.load_secret(CONFIG_SLOT).unwrap().is_none());
        assert!(vault.load_secret(PASSWORD_SLOT).unwrap().is_none());
        // The password write fails after the config was stored: the rollback
        // removes both slots, leaving no half configuration behind.
        let vault = FailOnStore {
            inner: MemorySlots::default(),
            fail_slots: vec![PASSWORD_SLOT],
        };
        let error = apply_configure(&vault, "tester@example.test", "pw").unwrap_err();
        assert!(error.contains("写入失败"), "unexpected error: {error}");
        assert!(vault.load_secret(CONFIG_SLOT).unwrap().is_none());
        assert!(vault.load_secret(PASSWORD_SLOT).unwrap().is_none());
        assert!(!sync_summary(&vault).unwrap().configured);
        // Once the storage works again, the same configuration succeeds.
        let healthy = vault.inner.clone();
        let summary = apply_configure(&healthy, "tester@example.test", "pw").unwrap();
        assert!(summary.configured);
    }

    #[test]
    fn apply_configure_password_failure_keeps_the_old_working_pair() {
        // A failed password change on an existing configuration must leave
        // the previous credentials usable (same account, same config).
        let inner = MemorySlots::default();
        apply_configure(&inner, "tester@example.test", "old-pw").unwrap();
        let config_before = inner.load_secret(CONFIG_SLOT).unwrap();
        let vault = FailOnStore {
            inner,
            fail_slots: vec![PASSWORD_SLOT],
        };
        let error = apply_configure(&vault, "tester@example.test", "new-pw").unwrap_err();
        assert!(error.contains("写入失败"), "unexpected error: {error}");
        // The failed attempt must not roll back the pre-existing config.
        assert_eq!(
            vault.load_secret(CONFIG_SLOT).unwrap(),
            config_before,
            "an existing configuration is never deleted by a failed update"
        );
    }

    #[test]
    fn apply_configure_rejects_corrupt_existing_config_before_writes() {
        let vault = MemorySlots::default();
        vault.store_secret(CONFIG_SLOT, b"not-json-at-all").unwrap();
        let error = apply_configure(&vault, "tester@example.test", "pw").unwrap_err();
        assert!(error.contains("同步配置损坏"), "unexpected error: {error}");
        assert!(vault.load_secret(PASSWORD_SLOT).unwrap().is_none());
    }

    #[test]
    fn config_binding_rejects_foreign_ledger_or_path() {
        let base = SyncConfig {
            base_url: DEFAULT_BASE_URL.into(),
            directory: REMOTE_DIRECTORY.into(),
            file: REMOTE_FILE.into(),
            username: "tester@example.test".into(),
            ledger_id: PROBE_LEDGER.into(),
            account_id: PROBE_ACCOUNT.into(),
            key_generation: 1,
            test_round: 0,
        };
        let mut config = base.clone();
        config.ledger_id = "another-ledger".into();
        assert!(config.matches_binding().is_err());
        config = base.clone();
        config.directory = "Other/path".into();
        assert!(config.matches_binding().is_err());
        config = base.clone();
        config.key_generation = 2;
        assert!(config.matches_binding().is_err());
        config = base.clone();
        config.base_url = "http://insecure.example.test/dav/".into();
        // The binding check itself accepts the value; the release client
        // rejects plaintext at connection time.
        assert!(config.matches_binding().is_ok());
        // A different HTTPS origin (e.g. mock server) is acceptable to the
        // binding check; the client still enforces HTTPS in release builds.
        config = base.clone();
        config.base_url = "https://mock.example.test/dav/".into();
        assert!(config.matches_binding().is_ok());
        assert!(base.matches_binding().is_ok());
    }

    #[test]
    fn display_records_cover_legacy_fixture() {
        let key = ContentKey::generate().unwrap();
        let legacy = key_content(&key, FIXTURE, 1);
        let records = records_for_display(&legacy, &key).unwrap();
        assert_eq!(records.len(), 2);
        assert_eq!(records[0].id, RECORD_ONE);
        let modern = key_content(&key, &build_payload(2), 2);
        let records = records_for_display(&modern, &key).unwrap();
        assert_eq!(records[0].label, "第 2 次发布");
    }

    #[test]
    fn identity_is_fixed_and_email_free() {
        let expected = identity();
        assert_eq!(expected.ledger_id, PROBE_LEDGER);
        assert_eq!(expected.account_id, PROBE_ACCOUNT);
        assert!(!PROBE_ACCOUNT.contains('@'));
        assert!(!PROBE_LEDGER.contains('@'));
    }
}
