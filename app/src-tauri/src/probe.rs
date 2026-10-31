use crate::probe_cache::{
    PROBE_ACCOUNT, PROBE_LEDGER, Vault, cache_exists, ensure_key, identity, load_key,
    persist_cache, persist_key, read_cache,
};
use crate::probe_sync::{
    PROBE_KEY_SLOT, SyncRecord, build_payload, parse_payload, records_for_display,
};
use account_app_core::{ContentKey, EncryptedEnvelope, SnapshotMetadata, decrypt, encrypt};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde::{Deserialize, Serialize};
use std::{path::Path, sync::Mutex};
use tauri::Manager;
use tauri_plugin_platform::PlatformExt;
use zeroize::{Zeroize, Zeroizing};

pub(crate) const FIXTURE: &[u8] = br#"{"synthetic":true,"label":"probe-only","records":[{"id":"demo-001","amountMinor":1234},{"id":"demo-002","amountMinor":5678}]}"#;
const MAX_PAIRING_BYTES: usize = 4096;

#[derive(Default)]
pub struct ProbeState(pub Mutex<()>);

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProbeStatus {
    platform: &'static str,
    cached: bool,
    revision: Option<u64>,
    synthetic_records: usize,
    storage: &'static str,
    records: Option<Vec<SyncRecord>>,
}

impl ProbeStatus {
    fn from_revision(revision: Option<u64>) -> Self {
        Self {
            platform: if cfg!(target_os = "android") {
                "Android"
            } else {
                "Windows"
            },
            cached: revision.is_some(),
            revision,
            synthetic_records: if revision.is_some() { 2 } else { 0 },
            storage: if cfg!(target_os = "android") {
                "Android Keystore"
            } else {
                "Windows DPAPI"
            },
            records: None,
        }
    }

    /// Attach the display records of an authenticated local snapshot.
    fn with_records(
        mut self,
        envelope: &EncryptedEnvelope,
        key: &ContentKey,
    ) -> Result<Self, String> {
        if self.cached {
            self.records = Some(records_for_display(envelope, key)?);
        }
        Ok(self)
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Pairing {
    kind: String,
    key: String,
    snapshot: EncryptedEnvelope,
}
impl Drop for Pairing {
    fn drop(&mut self) {
        self.key.zeroize();
    }
}

/// Authenticate a local snapshot and confirm it belongs to this synthetic
/// probe: either the legacy fixture bytes or a valid probe payload contract.
fn verify_fixture(envelope: &EncryptedEnvelope, key: &ContentKey) -> Result<u64, String> {
    let plaintext =
        Zeroizing::new(decrypt(envelope, key, identity()).map_err(|_| "验证缓存认证失败。")?);
    if plaintext.as_slice() != FIXTURE && parse_payload(&plaintext).is_err() {
        return Err("缓存不属于本次虚构数据验证，拒绝继续。".into());
    }
    Ok(envelope.metadata.revision)
}

fn run_fixture(vault: &impl Vault, path: &Path) -> Result<u64, String> {
    let key = ensure_key(vault, path)?;
    let revision = match read_cache(path, &key)? {
        Some(previous) => verify_fixture(&previous, &key)?
            .checked_add(1)
            .ok_or("验证版本已达到上限。")?,
        None => 1,
    };
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
    .map_err(|_| "虚构数据加密失败。")?;
    verify_fixture(&envelope, &key)?;
    persist_cache(path, &envelope)?;
    Ok(revision)
}

fn load_fixture(vault: &impl Vault, path: &Path) -> Result<Option<u64>, String> {
    let Some(key) = load_key(vault)? else {
        if cache_exists(path)? {
            return Err("缓存已存在但本机密钥缺失，拒绝重置。".into());
        }
        return Ok(None);
    };
    read_cache(path, &key)?
        .map(|envelope| verify_fixture(&envelope, &key))
        .transpose()
}

/// Current authenticated display status, including snapshot records.
fn current_status(vault: &impl Vault, path: &Path) -> Result<ProbeStatus, String> {
    let Some(key) = load_key(vault)? else {
        if cache_exists(path)? {
            return Err("缓存已存在但本机密钥缺失，拒绝重置。".into());
        }
        return Ok(ProbeStatus::from_revision(None));
    };
    match read_cache(path, &key)? {
        None => Ok(ProbeStatus::from_revision(None)),
        Some(envelope) => {
            let revision = verify_fixture(&envelope, &key)?;
            ProbeStatus::from_revision(Some(revision)).with_records(&envelope, &key)
        }
    }
}

fn pairing_payload(vault: &impl Vault, path: &Path) -> Result<Zeroizing<String>, String> {
    if load_fixture(vault, path)?.is_none() {
        run_fixture(vault, path)?;
    }
    let key = ensure_key(vault, path)?;
    let snapshot = read_cache(path, &key)?.ok_or("没有可配对的验证缓存。")?;
    verify_fixture(&snapshot, &key)?;
    let pairing = Pairing {
        kind: "account-synthetic-pairing-v1".into(),
        key: STANDARD.encode(key.as_bytes()),
        snapshot,
    };
    Ok(Zeroizing::new(
        serde_json::to_string(&pairing).map_err(|_| "无法生成验证配对。")?,
    ))
}

fn accept_pairing(vault: &impl Vault, path: &Path, payload: &str) -> Result<u64, String> {
    if payload.len() > MAX_PAIRING_BYTES {
        return Err("验证配对内容过大。".into());
    }
    let pairing: Pairing = serde_json::from_str(payload).map_err(|_| "无法识别验证配对二维码。")?;
    if pairing.kind != "account-synthetic-pairing-v1" {
        return Err("二维码不属于虚构数据验证。".into());
    }
    let bytes = Zeroizing::new(
        STANDARD
            .decode(&pairing.key)
            .map_err(|_| "验证配对密钥无效。")?,
    );
    if bytes.len() != 32 {
        return Err("验证配对密钥无效。".into());
    }
    let mut imported_bytes = Zeroizing::new([0u8; 32]);
    imported_bytes.copy_from_slice(&bytes);
    let imported = ContentKey::from_bytes(*imported_bytes);
    let revision = verify_fixture(&pairing.snapshot, &imported)?;
    // Authenticate the incoming synthetic snapshot before touching the vault.
    // An existing key is never replaced, even if this device has no cache yet.
    match load_key(vault)? {
        Some(existing) => {
            if existing.as_bytes() != imported.as_bytes() {
                return Err("本机已有另一组验证配对，拒绝覆盖。".into());
            }
            if let Some(envelope) = read_cache(path, &existing)? {
                return verify_fixture(&envelope, &existing);
            }
        }
        None => {
            if cache_exists(path)? {
                return Err("缓存已存在但本机密钥缺失，拒绝覆盖。".into());
            }
            persist_key(vault, &imported)?;
        }
    }
    persist_cache(path, &pairing.snapshot)?;
    Ok(revision)
}

struct NativeVault<'a>(&'a tauri_plugin_platform::Platform<tauri::Wry>);
impl Vault for NativeVault<'_> {
    fn load(&self) -> Result<Option<Vec<u8>>, String> {
        self.0
            .load_secret(PROBE_KEY_SLOT)
            .map_err(|_| "原生安全存储读取失败，请保留现场检查。".into())
    }
    fn store(&self, key: &[u8]) -> Result<(), String> {
        self.0
            .store_secret(PROBE_KEY_SLOT, key)
            .map_err(|_| "原生安全存储写入失败，未保存验证缓存。".into())
    }
}

async fn native_operation<T: Send + 'static>(
    app: tauri::AppHandle,
    operation: impl FnOnce(&NativeVault<'_>, &Path) -> Result<T, String> + Send + 'static,
) -> Result<T, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<ProbeState>();
        let _guard = state
            .0
            .lock()
            .map_err(|_| "验证操作状态异常，请重启应用。")?;
        let directory = app
            .path()
            .app_data_dir()
            .map_err(|_| "无法定位验证缓存。")?;
        crate::probe_transaction::with_transaction(&directory, || {
            let path = directory.join("synthetic-probe.enc");
            operation(&NativeVault(app.platform()), &path)
        })
    })
    .await
    .map_err(|_| "原生验证未能完成。".to_string())?
}

#[tauri::command]
pub async fn run_probe(app: tauri::AppHandle) -> Result<ProbeStatus, String> {
    native_operation(app, |vault, path| {
        run_fixture(vault, path)?;
        current_status(vault, path)
    })
    .await
}

#[tauri::command]
pub async fn load_probe(app: tauri::AppHandle) -> Result<ProbeStatus, String> {
    native_operation(app, |vault, path| current_status(vault, path)).await
}

#[tauri::command]
pub async fn create_pairing(app: tauri::AppHandle) -> Result<String, String> {
    native_operation(app, |vault, path| {
        let payload = pairing_payload(vault, path)?;
        let code =
            qrcode::QrCode::with_error_correction_level(payload.as_bytes(), qrcode::EcLevel::M)
                .map_err(|_| "无法生成验证二维码。")?;
        let svg = Zeroizing::new(
            code.render::<qrcode::render::svg::Color>()
                .min_dimensions(360, 360)
                .build(),
        );
        Ok(format!(
            "data:image/svg+xml;base64,{}",
            STANDARD.encode(svg.as_bytes())
        ))
    })
    .await
}

#[tauri::command]
pub async fn import_pairing(app: tauri::AppHandle, payload: String) -> Result<ProbeStatus, String> {
    let payload = Zeroizing::new(payload);
    native_operation(app, move |vault, path| {
        accept_pairing(vault, path, &payload)?;
        current_status(vault, path)
    })
    .await
}

#[cfg(any(windows, target_os = "macos"))]
pub async fn self_test(app: tauri::AppHandle) -> Result<(), String> {
    native_operation(app, |vault, path| {
        let revision = run_fixture(vault, path)?;
        if load_fixture(vault, path)? != Some(revision) {
            return Err("原生缓存重读未通过。".into());
        }
        let payload = pairing_payload(vault, path)?;
        if accept_pairing(vault, path, &payload)? != revision {
            return Err("原生配对重试未通过。".into());
        }
        Ok(())
    })
    .await
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        cell::{Cell, RefCell},
        fs,
    };

    #[derive(Default)]
    struct MemoryVault {
        key: RefCell<Option<Vec<u8>>>,
        reject_writes: Cell<bool>,
        discard_writes: Cell<bool>,
    }
    impl Vault for MemoryVault {
        fn load(&self) -> Result<Option<Vec<u8>>, String> {
            Ok(self.key.borrow().clone())
        }
        fn store(&self, key: &[u8]) -> Result<(), String> {
            if self.reject_writes.get() {
                return Err("模拟安全存储不可用".into());
            }
            if self.discard_writes.get() {
                return Ok(());
            }
            self.key.replace(Some(key.to_vec()));
            Ok(())
        }
    }

    #[test]
    fn fixture_reopens_offline_with_persisted_key_and_advances_revision() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("probe.enc");
        let vault = MemoryVault::default();
        assert_eq!(load_fixture(&vault, &path).unwrap(), None);
        assert_eq!(run_fixture(&vault, &path).unwrap(), 1);
        let reopened = MemoryVault {
            key: RefCell::new(vault.load().unwrap()),
            ..Default::default()
        };
        assert_eq!(load_fixture(&reopened, &path).unwrap(), Some(1));
        assert_eq!(run_fixture(&reopened, &path).unwrap(), 2);
        assert!(
            !String::from_utf8(fs::read(path).unwrap())
                .unwrap()
                .contains("probe-only")
        );
    }

    #[test]
    fn lost_key_and_corruption_never_reset_cache_or_create_replacement_key() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("probe.enc");
        let vault = MemoryVault::default();
        run_fixture(&vault, &path).unwrap();
        let before = fs::read(&path).unwrap();
        vault.key.replace(None);
        assert!(run_fixture(&vault, &path).is_err());
        assert!(vault.load().unwrap().is_none());
        assert_eq!(fs::read(&path).unwrap(), before);
        fs::write(&path, b"damaged").unwrap();
        assert!(run_fixture(&vault, &path).is_err());
        assert_eq!(fs::read(path).unwrap(), b"damaged");
    }

    #[test]
    fn failed_secure_storage_does_not_publish_a_cache() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("probe.enc");
        let vault = MemoryVault::default();
        vault.reject_writes.set(true);
        assert!(run_fixture(&vault, &path).is_err());
        assert!(!path.exists());
    }

    #[test]
    fn acknowledged_but_missing_secure_write_does_not_publish_cache() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("probe.enc");
        let vault = MemoryVault::default();
        vault.discard_writes.set(true);
        assert!(run_fixture(&vault, &path).is_err());
        assert!(!path.exists());
    }

    #[test]
    fn tampered_pairing_never_installs_a_key_or_cache() {
        let directory = tempfile::tempdir().unwrap();
        let origin = directory.path().join("origin.enc");
        let target = directory.path().join("target.enc");
        let payload = pairing_payload(&MemoryVault::default(), &origin).unwrap();
        let original: serde_json::Value = serde_json::from_str(&payload).unwrap();
        let mut changed_revision = original.clone();
        changed_revision["snapshot"]["metadata"]["revision"] = 100.into();
        let mut unknown_field = original.clone();
        unknown_field["untrusted"] = true.into();
        let mut changed_key = original;
        changed_key["key"] = STANDARD.encode([1u8; 32]).into();
        let receiver = MemoryVault::default();
        for changed in [changed_revision, unknown_field, changed_key] {
            assert!(
                accept_pairing(
                    &receiver,
                    &target,
                    &serde_json::to_string(&changed).unwrap()
                )
                .is_err()
            );
            assert!(receiver.load().unwrap().is_none());
            assert!(!target.exists());
        }
    }

    #[test]
    fn pairing_import_authenticates_and_retries_without_overwriting_newer_cache() {
        let directory = tempfile::tempdir().unwrap();
        let desktop = directory.path().join("desktop.enc");
        let phone = directory.path().join("phone.enc");
        let origin = MemoryVault::default();
        let payload = pairing_payload(&origin, &desktop).unwrap();
        let receiver = MemoryVault::default();
        assert_eq!(accept_pairing(&receiver, &phone, &payload).unwrap(), 1);
        assert_eq!(load_fixture(&receiver, &phone).unwrap(), Some(1));
        assert_eq!(run_fixture(&receiver, &phone).unwrap(), 2);
        let before = fs::read(&phone).unwrap();
        assert_eq!(accept_pairing(&receiver, &phone, &payload).unwrap(), 2);
        assert_eq!(fs::read(phone).unwrap(), before);
    }

    #[test]
    fn different_pairing_and_malformed_input_preserve_existing_key_and_cache() {
        let directory = tempfile::tempdir().unwrap();
        let source = directory.path().join("source.enc");
        let target = directory.path().join("target.enc");
        let payload = pairing_payload(&MemoryVault::default(), &source).unwrap();
        let vault = MemoryVault::default();
        run_fixture(&vault, &target).unwrap();
        let old_key = vault.load().unwrap();
        let before = fs::read(&target).unwrap();
        assert!(accept_pairing(&vault, &target, &payload).is_err());
        for input in ["invalid".to_string(), "x".repeat(8192)] {
            assert!(accept_pairing(&vault, &target, &input).is_err());
        }
        assert_eq!(vault.load().unwrap(), old_key);
        assert_eq!(fs::read(target).unwrap(), before);
    }
}
