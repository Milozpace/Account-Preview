use account_app_core::{
    ContentKey, EncryptedEnvelope, ExpectedIdentity, MAX_ENVELOPE_BYTES, decrypt,
};
use std::{fs, io::Write, path::Path};

pub const PROBE_LEDGER: &str = "synthetic-probe-ledger";
pub const PROBE_ACCOUNT: &str = "synthetic-probe-account";

pub fn identity() -> ExpectedIdentity<'static> {
    ExpectedIdentity {
        ledger_id: PROBE_LEDGER,
        account_id: PROBE_ACCOUNT,
        key_generation: 1,
    }
}

/// Minimal secret-slot abstraction so tests can substitute memory storage.
pub trait Vault {
    fn load(&self) -> Result<Option<Vec<u8>>, String>;
    fn store(&self, key: &[u8]) -> Result<(), String>;
}

/// Extended secret-slot access for named slots (content key, sync config,
/// sync password). Implemented by the native platform vault.
pub trait SecretVault {
    fn load_secret(&self, slot: &str) -> Result<Option<Vec<u8>>, String>;
    fn store_secret(&self, slot: &str, value: &[u8]) -> Result<(), String>;
    /// Removes one slot so a failed first-time configuration cannot leave a
    /// half-written credential set behind. Removing a missing slot is fine.
    fn delete_secret(&self, slot: &str) -> Result<(), String>;
}

/// Adapter implementing [`Vault`] for one fixed slot of a [`SecretVault`].
pub struct SlotVault<'a, S: SecretVault> {
    pub vault: &'a S,
    pub slot: &'a str,
}

impl<S: SecretVault> Vault for SlotVault<'_, S> {
    fn load(&self) -> Result<Option<Vec<u8>>, String> {
        self.vault.load_secret(self.slot)
    }
    fn store(&self, key: &[u8]) -> Result<(), String> {
        self.vault.store_secret(self.slot, key)
    }
}

pub fn cache_exists(path: &Path) -> Result<bool, String> {
    match fs::metadata(path) {
        Ok(_) => Ok(true),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(_) => Err("无法检查本机验证缓存。".into()),
    }
}

pub fn load_key(vault: &impl Vault) -> Result<Option<ContentKey>, String> {
    let Some(bytes) = vault.load()?.map(zeroize::Zeroizing::new) else {
        return Ok(None);
    };
    if bytes.len() != 32 {
        return Err("本机验证密钥损坏，请保留现场检查。".into());
    }
    let mut key = zeroize::Zeroizing::new([0u8; 32]);
    key.copy_from_slice(&bytes);
    Ok(Some(ContentKey::from_bytes(*key)))
}

pub fn persist_key(vault: &impl Vault, key: &ContentKey) -> Result<(), String> {
    vault.store(key.as_bytes())?;
    let saved = load_key(vault)?.ok_or("安全存储没有保留验证密钥，未保存缓存。")?;
    if saved.as_bytes() != key.as_bytes() {
        return Err("安全存储密钥校验失败，未保存缓存。".into());
    }
    Ok(())
}

pub fn ensure_key(vault: &impl Vault, path: &Path) -> Result<ContentKey, String> {
    if let Some(key) = load_key(vault)? {
        return Ok(key);
    }
    if cache_exists(path)? {
        return Err("缓存已存在但本机密钥缺失，拒绝重置。".into());
    }
    let key = ContentKey::generate().map_err(|_| "设备无法生成安全随机密钥。")?;
    persist_key(vault, &key)?;
    Ok(key)
}

pub fn read_cache(path: &Path, key: &ContentKey) -> Result<Option<EncryptedEnvelope>, String> {
    let metadata = match fs::metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err("无法读取本机验证缓存。".into()),
    };
    if metadata.len() > MAX_ENVELOPE_BYTES as u64 {
        return Err("验证缓存过大，拒绝读取。".into());
    }
    let bytes = fs::read(path).map_err(|_| "无法读取本机验证缓存。")?;
    let envelope = EncryptedEnvelope::from_json(&bytes).map_err(|_| "验证缓存格式损坏。")?;
    let _plaintext = zeroize::Zeroizing::new(
        decrypt(&envelope, key, identity()).map_err(|_| "缓存无法解密，请保留现场检查。")?,
    );
    Ok(Some(envelope))
}

pub fn persist_cache(path: &Path, envelope: &EncryptedEnvelope) -> Result<(), String> {
    let parent = path.parent().ok_or("缓存位置无效。")?;
    fs::create_dir_all(parent).map_err(|_| "无法建立缓存目录。")?;
    let bytes = envelope.to_json().map_err(|_| "拒绝保存无效缓存。")?;
    let mut pending = tempfile::NamedTempFile::new_in(parent).map_err(|_| "无法准备缓存文件。")?;
    pending.write_all(&bytes).map_err(|_| "缓存写入未完成。")?;
    pending
        .as_file()
        .sync_all()
        .map_err(|_| "缓存写入未完成。")?;
    pending
        .persist(path)
        .map_err(|_| "缓存替换未完成，旧数据保留。")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use account_app_core::{SnapshotMetadata, encrypt};
    use std::fs;

    fn fixture(key: &ContentKey, revision: u64) -> EncryptedEnvelope {
        encrypt(
            br#"{"synthetic":true,"label":"probe-only"}"#,
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

    #[test]
    fn cache_survives_a_new_session_and_replaces_atomically() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("probe.enc");
        let key = ContentKey::generate().unwrap();
        assert!(read_cache(&path, &key).unwrap().is_none());
        persist_cache(&path, &fixture(&key, 1)).unwrap();
        let fresh_key = ContentKey::from_bytes(*key.as_bytes());
        assert_eq!(
            read_cache(&path, &fresh_key)
                .unwrap()
                .unwrap()
                .metadata
                .revision,
            1
        );
        persist_cache(&path, &fixture(&key, 2)).unwrap();
        assert_eq!(
            read_cache(&path, &fresh_key)
                .unwrap()
                .unwrap()
                .metadata
                .revision,
            2
        );
        assert!(
            !String::from_utf8(fs::read(path).unwrap())
                .unwrap()
                .contains("probe-only")
        );
    }

    #[test]
    fn rejected_write_does_not_replace_good_cache() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("probe.enc");
        let key = ContentKey::generate().unwrap();
        persist_cache(&path, &fixture(&key, 1)).unwrap();
        let before = fs::read(&path).unwrap();
        let mut invalid = fixture(&key, 2);
        invalid.metadata.format_version = 999;
        assert!(persist_cache(&path, &invalid).is_err());
        assert_eq!(before, fs::read(path).unwrap());
    }

    #[test]
    fn corruption_and_wrong_key_fail_without_silent_reset() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("probe.enc");
        let key = ContentKey::generate().unwrap();
        persist_cache(&path, &fixture(&key, 1)).unwrap();
        assert!(read_cache(&path, &ContentKey::generate().unwrap()).is_err());
        fs::write(&path, b"damaged").unwrap();
        assert!(read_cache(&path, &key).is_err());
        assert_eq!(fs::read(path).unwrap(), b"damaged");
    }
}
