//! Explicitly approved second synthetic round. Never a general reset API.
use super::*;

pub(super) fn prepare<V: SecretVault>(
    vault: &V,
    dir: &Path,
    phone: bool,
) -> Result<String, String> {
    let previous_config = Zeroizing::new(
        vault
            .load_secret(CONFIG_SLOT)?
            .ok_or("请先保留已有同步配置。")?,
    );
    let mut config: SyncConfig =
        serde_json::from_slice(&previous_config).map_err(|_| "同步配置损坏，未修改。")?;
    config.matches_binding()?;
    if config.test_round == 2 {
        return Ok("第二轮已准备，原有备份与当前配对保持不变。".into());
    }
    if vault.load_secret(PASSWORD_SLOT)?.is_none() {
        return Err("同步密码缺失，未修改。".into());
    }
    let previous_key = Zeroizing::new(
        vault
            .load_secret(PROBE_KEY_SLOT)?
            .ok_or("测试密钥缺失，未修改。")?,
    );
    let key = load_content_key(vault)?.ok_or("测试密钥缺失，未修改。")?;
    let cache = read_cache(&dir.join(CACHE_FILE), &key)?.ok_or("测试缓存缺失，未修改。")?;
    records_for_display(&cache, &key)?;

    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|_| "无法准备备份。")?
        .as_nanos();
    let root = dir.join("test-round-backups");
    fs::create_dir_all(&root).map_err(|_| "无法建立测试备份。")?;
    let backup = root.join(format!("round-2-{stamp:x}"));
    fs::create_dir(&backup).map_err(|_| "无法建立独立测试备份。")?;
    let key_slot = format!("probe-backup-key-{stamp:x}");
    let config_slot = format!("probe-backup-config-{stamp:x}");
    for (slot, bytes) in [
        (&key_slot, previous_key.as_slice()),
        (&config_slot, previous_config.as_slice()),
    ] {
        vault.store_secret(slot, bytes)?;
        let saved = vault
            .load_secret(slot)?
            .map(Zeroizing::new)
            .ok_or("测试安全备份未保留，原状态未修改。")?;
        if saved.as_slice() != bytes {
            return Err("测试安全备份回验失败，原状态未修改。".into());
        }
    }
    // Fixed whitelist: only this synthetic probe's encrypted files and counters.
    let names = [
        CACHE_FILE,
        PENDING_FILE,
        STATE_FILE,
        "publish-slot-intent.json",
    ];
    let mut saved_names = Vec::new();
    for name in names {
        let source = dir.join(name);
        if !source
            .try_exists()
            .map_err(|_| "无法检查测试文件，原状态未修改。")?
        {
            continue;
        }
        if fs::metadata(&source)
            .map_err(|_| "无法检查测试文件。")?
            .len()
            > account_app_core::MAX_ENVELOPE_BYTES as u64
        {
            return Err("测试文件过大，原状态未修改。".into());
        }
        let bytes = fs::read(&source).map_err(|_| "无法读取测试备份。")?;
        let target = backup.join(name);
        use std::io::Write;
        let mut file = fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&target)
            .map_err(|_| "无法写入测试备份。")?;
        file.write_all(&bytes)
            .and_then(|_| file.sync_all())
            .map_err(|_| "测试备份写入未完成。")?;
        if fs::read(&target).map_err(|_| "无法回验测试备份。")? != bytes {
            return Err("测试备份回验失败。".into());
        }
        saved_names.push(name);
    }
    let manifest = serde_json::to_vec_pretty(&serde_json::json!({
        "round": 2, "role": if phone { "android-reader" } else { "windows-publisher" },
        "keySlot": key_slot, "configSlot": config_slot, "files": saved_names,
        "passwordUnchanged": true, "cloudOriginalsUnchanged": true,
    }))
    .map_err(|_| "无法生成测试备份记录。")?;
    fs::write(backup.join("manifest.json"), &manifest).map_err(|_| "无法保存测试备份记录。")?;
    fs::OpenOptions::new()
        .write(true)
        .open(backup.join("manifest.json"))
        .and_then(|file| file.sync_all())
        .map_err(|_| "备份记录未持久化。")?;

    let mut moved = Vec::new();
    let result: Result<(), String> = (|| {
        for name in &saved_names {
            if !phone && *name == CACHE_FILE {
                continue;
            }
            fs::rename(dir.join(name), backup.join(format!("active-{name}")))
                .map_err(|_| "测试状态归档未完成。")?;
            moved.push(*name);
        }
        if phone {
            vault.delete_secret(PROBE_KEY_SLOT)?;
            if vault.load_secret(PROBE_KEY_SLOT)?.is_some() {
                return Err("旧测试密钥未归档，已阻止新配对。".into());
            }
        }
        config.test_round = 2;
        let bytes =
            Zeroizing::new(serde_json::to_vec(&config).map_err(|_| "无法保存第二轮配置。")?);
        vault.store_secret(CONFIG_SLOT, &bytes)?;
        if vault.load_secret(CONFIG_SLOT)?.as_deref() != Some(bytes.as_slice()) {
            return Err("第二轮配置回验失败。".into());
        }
        Ok(())
    })();
    if result.is_err() {
        let rollback: Result<(), String> = (|| {
            vault.store_secret(CONFIG_SLOT, &previous_config)?;
            vault.store_secret(PROBE_KEY_SLOT, &previous_key)?;
            for name in moved.iter().rev() {
                fs::rename(backup.join(format!("active-{name}")), dir.join(name))
                    .map_err(|_| "恢复测试文件未完成。")?;
            }
            if vault.load_secret(CONFIG_SLOT)?.as_deref() != Some(previous_config.as_slice())
                || vault.load_secret(PROBE_KEY_SLOT)?.as_deref() != Some(previous_key.as_slice())
            {
                return Err("恢复回验失败。".into());
            }
            Ok(())
        })();
        return Err(if rollback.is_ok() {
            "第二轮准备失败，已恢复旧测试状态并保留备份。"
        } else {
            "第二轮准备失败，备份已保留；恢复未确认，请保留现场。"
        }
        .into());
    }
    Ok(if phone { "旧测试状态已安全备份。请扫描电脑二维码完成第二轮配对，无需重新输入坚果云密码。" }
       else { "第二轮测试已准备，旧状态已备份；电脑密钥与缓存保留，云端使用独立目录。请显示二维码让手机扫描。" }.into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{collections::BTreeMap, sync::Mutex};

    #[derive(Default)]
    struct Memory {
        slots: Mutex<BTreeMap<String, Vec<u8>>>,
        fail_backup: bool,
        fail_config_once: std::sync::atomic::AtomicBool,
    }
    impl SecretVault for Memory {
        fn load_secret(&self, slot: &str) -> Result<Option<Vec<u8>>, String> {
            Ok(self.slots.lock().unwrap().get(slot).cloned())
        }
        fn store_secret(&self, slot: &str, value: &[u8]) -> Result<(), String> {
            if (self.fail_backup && slot.starts_with("probe-backup-"))
                || (slot == CONFIG_SLOT
                    && self
                        .fail_config_once
                        .swap(false, std::sync::atomic::Ordering::SeqCst))
            {
                return Err("模拟存储失败。".into());
            }
            self.slots
                .lock()
                .unwrap()
                .insert(slot.into(), value.to_vec());
            Ok(())
        }
        fn delete_secret(&self, slot: &str) -> Result<(), String> {
            self.slots.lock().unwrap().remove(slot);
            Ok(())
        }
    }
    fn seed(vault: &Memory, dir: &Path) -> Vec<u8> {
        apply_configure(vault, "test@example.test", "test-password").unwrap();
        let key = ensure_content_key(vault, &dir.join(CACHE_FILE)).unwrap();
        let envelope = encrypt(
            &build_payload(7),
            &key,
            SnapshotMetadata {
                format_version: 1,
                ledger_id: PROBE_LEDGER.into(),
                account_id: PROBE_ACCOUNT.into(),
                key_generation: 1,
                revision: 7,
            },
        )
        .unwrap();
        persist_cache(&dir.join(CACHE_FILE), &envelope).unwrap();
        fs::write(dir.join(PENDING_FILE), b"unreadable-old-pending").unwrap();
        fs::write(dir.join(STATE_FILE), br#"{"revision":4}"#).unwrap();
        key.as_bytes().to_vec()
    }
    fn config(vault: &Memory) -> SyncConfig {
        serde_json::from_slice(&vault.load_secret(CONFIG_SLOT).unwrap().unwrap()).unwrap()
    }
    #[test]
    fn desktop_keeps_key_and_cache_and_archives_old_pending_without_password_change() {
        let dir = tempfile::tempdir().unwrap();
        let vault = Memory::default();
        let key = seed(&vault, dir.path());
        let cache = fs::read(dir.path().join(CACHE_FILE)).unwrap();
        prepare(&vault, dir.path(), false).unwrap();
        assert_eq!(vault.load_secret(PROBE_KEY_SLOT).unwrap().unwrap(), key);
        assert_eq!(fs::read(dir.path().join(CACHE_FILE)).unwrap(), cache);
        assert!(!dir.path().join(PENDING_FILE).exists());
        assert!(!dir.path().join(STATE_FILE).exists());
        assert_eq!(
            vault.load_secret(PASSWORD_SLOT).unwrap().unwrap(),
            b"test-password"
        );
        assert_eq!(
            config(&vault).resolved_directory(),
            "Account/probe/session-2"
        );
        let entries: Vec<_> = fs::read_dir(dir.path().join("test-round-backups"))
            .unwrap()
            .collect();
        assert_eq!(entries.len(), 1);
        let backup = entries[0].as_ref().unwrap().path();
        assert_eq!(
            fs::read(backup.join(PENDING_FILE)).unwrap(),
            b"unreadable-old-pending"
        );
        let manifest: serde_json::Value =
            serde_json::from_slice(&fs::read(backup.join("manifest.json")).unwrap()).unwrap();
        assert_eq!(
            vault
                .load_secret(manifest["keySlot"].as_str().unwrap())
                .unwrap()
                .unwrap(),
            key
        );
    }
    #[test]
    fn phone_backs_up_key_and_cache_then_allows_one_fresh_pairing() {
        let dir = tempfile::tempdir().unwrap();
        let vault = Memory::default();
        let old_key = seed(&vault, dir.path());
        prepare(&vault, dir.path(), true).unwrap();
        assert!(vault.load_secret(PROBE_KEY_SLOT).unwrap().is_none());
        assert!(!dir.path().join(CACHE_FILE).exists());
        assert!(
            vault
                .slots
                .lock()
                .unwrap()
                .iter()
                .any(|(slot, bytes)| slot.starts_with("probe-backup-key-") && bytes == &old_key)
        );
        // After the normal import saved a new key/cache, repeating setup must not erase it.
        let new_key = seed(&vault, dir.path());
        prepare(&vault, dir.path(), true).unwrap();
        assert_eq!(vault.load_secret(PROBE_KEY_SLOT).unwrap().unwrap(), new_key);
        assert!(dir.path().join(CACHE_FILE).exists());
    }
    #[test]
    fn backup_failure_leaves_active_state_untouched() {
        let dir = tempfile::tempdir().unwrap();
        let vault = Memory {
            fail_backup: true,
            ..Default::default()
        };
        let key = seed(&vault, dir.path());
        assert!(prepare(&vault, dir.path(), true).is_err());
        assert_eq!(vault.load_secret(PROBE_KEY_SLOT).unwrap().unwrap(), key);
        assert!(dir.path().join(CACHE_FILE).exists());
        assert!(dir.path().join(PENDING_FILE).exists());
        assert_eq!(config(&vault).test_round, 0);
    }
    #[test]
    fn config_write_failure_restores_active_key_and_files() {
        let dir = tempfile::tempdir().unwrap();
        let vault = Memory::default();
        let key = seed(&vault, dir.path());
        vault
            .fail_config_once
            .store(true, std::sync::atomic::Ordering::SeqCst);
        assert!(prepare(&vault, dir.path(), true).is_err());
        assert_eq!(vault.load_secret(PROBE_KEY_SLOT).unwrap().unwrap(), key);
        assert!(dir.path().join(CACHE_FILE).exists());
        assert!(dir.path().join(PENDING_FILE).exists());
        assert_eq!(config(&vault).test_round, 0);
    }
    #[test]
    fn legacy_config_remains_bound_and_unknown_round_is_rejected() {
        let dir = tempfile::tempdir().unwrap();
        let vault = Memory::default();
        seed(&vault, dir.path());
        let mut cfg = config(&vault);
        assert_eq!(cfg.resolved_directory(), REMOTE_DIRECTORY);
        assert!(!serde_json::to_string(&cfg).unwrap().contains("test_round"));
        cfg.test_round = 3;
        assert!(cfg.matches_binding().is_err());
    }
}
