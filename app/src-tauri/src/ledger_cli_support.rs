//! Desktop CLI support for the business ledger publisher. Compiled into the
//! library so `bin/ledger_cli.rs` stays a thin shim. Secrets never leave the
//! native vault; output lines never contain credentials, ciphertext, or URLs.
#![forbid(unsafe_code)]

use crate::ledger_sync::{LedgerPrepareOutcome, publish_staged, stage_pending, with_transaction};
use crate::probe_cache::SecretVault;
use crate::probe_sync::Transaction;
use std::path::{Path, PathBuf};

pub enum LedgerOutcome {
    Ok(Vec<String>),
    Failed(Vec<String>),
}

/// Writable native vault over the same namespace as the desktop app.
pub struct LedgerCliVault {
    inner: tauri_plugin_platform::StandaloneVault,
}

pub fn open_vault(data_dir: &Path) -> Result<LedgerCliVault, String> {
    let inner = tauri_plugin_platform::StandaloneVault::open(data_dir.join("vault"))
        .map_err(|_| "无法打开本机安全存储（与桌面应用相同的原生安全存储）。".to_string())?;
    Ok(LedgerCliVault { inner })
}

/// Same application identifier and platform base as Tauri app_data_dir and
/// app_config_dir. Never fall back to a previous production identifier.
pub fn data_directory() -> Result<PathBuf, String> {
    let identifier = crate::cloud_scope::IDENTIFIER;
    if identifier.is_empty()
        || !identifier
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'-')
    {
        return Err("应用身份配置无效。".into());
    }
    #[cfg(windows)]
    let base = std::env::var_os("APPDATA").map(PathBuf::from);
    #[cfg(target_os = "macos")]
    let base = std::env::var_os("HOME")
        .map(|path| PathBuf::from(path).join("Library/Application Support"));
    let base = base.ok_or("无法定位本机应用目录。")?;
    if !base.is_absolute() {
        return Err("本机应用目录必须是绝对路径。".into());
    }
    Ok(base.join(identifier))
}

pub fn cli_vault() -> Result<LedgerCliVault, String> {
    open_vault(&data_directory()?)
}

pub struct CliTransaction(PathBuf);
pub fn cli_ledger_transaction(dir: &Path) -> CliTransaction {
    CliTransaction(dir.to_path_buf())
}
impl Transaction for CliTransaction {
    fn run<T: Send>(
        &self,
        operation: impl FnOnce() -> Result<T, String> + Send,
    ) -> Result<T, String> {
        with_transaction(&self.0, operation)
    }
}

impl SecretVault for LedgerCliVault {
    fn load_secret(&self, slot: &str) -> Result<Option<Vec<u8>>, String> {
        self.inner
            .load_secret(slot)
            .map_err(|_| "原生安全存储读取失败，请保留现场检查。".into())
    }
    fn store_secret(&self, slot: &str, value: &[u8]) -> Result<(), String> {
        // Initialization and the explicit publish gate are the only CLI writes.
        if slot != crate::ledger::LEDGER_IDENTITY_SLOT
            && slot != crate::ledger::LEDGER_KEY_SLOT
            && slot != crate::ledger::LEDGER_PUBLISH_ENABLED_SLOT
        {
            return Err("命令行不写入该安全存储槽位。".into());
        }
        self.inner
            .store_secret(slot, value)
            .map_err(|_| "原生安全存储写入失败，未保存变更。".into())
    }
    fn delete_secret(&self, _slot: &str) -> Result<(), String> {
        Err("命令行不删除安全存储槽位。".into())
    }
}

fn status_lines(vault: &LedgerCliVault, dir: &Path) -> Vec<String> {
    let summary = match crate::ledger_sync::summarize_ledger(vault, Some(dir)) {
        Ok(summary) => summary,
        Err(error) => return vec![format!("账本状态读取未完成：{error}")],
    };
    vec![
        format!("正式账本已初始化：{}", summary.initialized),
        format!(
            "真实发布开关：{}",
            if summary.publish_enabled {
                "已启用"
            } else {
                "未启用（本阶段默认）"
            }
        ),
        format!(
            "本地缓存版本：{}",
            summary
                .cached_revision
                .map(|r| r.to_string())
                .unwrap_or_else(|| "无".into())
        ),
        format!(
            "最近发布状态：{}",
            summary
                .last_published_revision
                .map(|r| format!("第 {r} 版"))
                .unwrap_or_else(|| "无".into())
        ),
        format!(
            "待发版本：{}",
            summary
                .pending_revision
                .map(|r| format!("第 {r} 版"))
                .unwrap_or_else(|| "无".into())
        ),
    ]
}

fn describe(outcome: &LedgerPrepareOutcome) -> Vec<String> {
    vec![
        format!(
            "{}：版本 {}，月份 {}，导出明细 {} 笔，明文 {} 字节。",
            if outcome.dry_run {
                "试运行完成（未写入）"
            } else {
                "准备完成"
            },
            outcome.revision,
            outcome.months.join("、"),
            outcome.exported_records,
            outcome.payload_bytes
        ),
        if outcome.reused {
            "内容与现有待发一致，已复用同一密文与版本。".into()
        } else {
            "内容有更新，已生成新版本待发。".into()
        },
    ]
}

pub fn run_cli(args: Vec<String>) -> LedgerOutcome {
    let result = (|| -> Result<Vec<String>, String> {
        let dir = data_directory()?;
        let vault = cli_vault()?;
        let command = args.first().map(String::as_str).unwrap_or("");
        match command {
            "--init" if args.len() == 1 => {
                crate::ledger_sync::initialize(&vault)?;
                let mut lines = vec!["正式账本身份与密钥已生成并写入本机安全存储。".into()];
                lines.extend(status_lines(&vault, &dir));
                lines.push("下一步：在桌面 App 中创建正式配对二维码并用手机扫码（复审后安排）。".into());
                Ok(lines)
            }
            "--status" if args.len() == 1 => Ok(status_lines(&vault, &dir)),
            "--status-json" if args.len() == 1 => Ok(vec![status_json(&vault, &dir)?]),
            "--enable-publish" if args.len() == 1 => {
                crate::ledger_sync::enable_publish(&vault)?;
                Ok(vec![format!("真实发布开关已启用。此后 --publish 将真实上传到坚果云 {}。", crate::cloud_scope::LEDGER_DIRECTORY)])
            }
            "--prepare" | "--publish" => {
                let payload = args.get(1).ok_or("缺少账单快照文件路径参数。")?;
                let dry_run = args.get(2).map(|arg| arg == "--dry-run").unwrap_or(false);
                if args.get(2).is_some() && !dry_run {
                    return Err("命令参数无效。".into());
                }
                if command == "--publish" {
                    if dry_run {
                        return Err("命令参数无效。".into());
                    }
                    crate::ledger_sync::publish_gate_open(&vault)?;
                }
                let tx = CliTransaction(dir.clone());
                if command == "--publish" {
                    // Same content as the last confirmed publish: idempotent
                    // success — never a new version, never a stale upload.
                    let staged = stage_pending(&vault, &dir, &tx, Path::new(payload), false)?;
                    if staged.already_published {
                        let mut lines = vec![format!(
                            "内容与最近发布的第 {} 版一致：为幂等确认，未新增版本、未重复上传。",
                            staged.revision
                        )];
                        if staged.stale_pending_cancelled {
                            lines.push("已取消过时的待发内容（最新输入即已发布内容）。".into());
                        }
                        return Ok(lines);
                    }
                    let published = publish_staged(&vault, &dir, &tx)?;
                    let mut lines = vec![format!(
                        "发布成功：版本 {} 已上传并经槽位回读确认（月份 {}，导出明细 {} 笔）。",
                        published.revision,
                        staged.months.join("、"),
                        staged.exported_records
                    )];
                    if published.reused {
                        lines.push("云端已含同一版本，本次为幂等确认，未重复上传。".into());
                    }
                    Ok(lines)
                } else {
                    let outcome = stage_pending(&vault, &dir, &tx, Path::new(payload), dry_run)?;
                    Ok(describe(&outcome))
                }
            }
            _ => Err("命令参数无效。可用：--init / --status / --status-json / --prepare <快照> [--dry-run] / --publish <快照> / --enable-publish。".into()),
        }
    })();
    match result {
        Ok(lines) => LedgerOutcome::Ok(lines),
        Err(error) => LedgerOutcome::Failed(vec![error]),
    }
}

/// Machine-readable onboarding state. Refuse to mislabel inaccessible or
/// partially initialized native storage as a new account. No cloud operations.
fn status_json<V: SecretVault>(vault: &V, dir: &Path) -> Result<String, String> {
    let identity_present = crate::ledger::load_identity(vault)?.is_some();
    let key_present = crate::ledger::load_key(vault)?.is_some();
    if identity_present != key_present {
        return Err("本机账本身份不完整，请保留现场检查。".into());
    }
    // Only status booleans and revision metadata reach stdout, never secrets.
    drop(vault.load_secret(crate::ledger::LEDGER_PUBLISH_ENABLED_SLOT)?);
    serde_json::to_string(&crate::ledger_sync::summarize_ledger(vault, Some(dir))?)
        .map_err(|_| "本机账本状态序列化失败。".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ledger::tests::MemorySlots;

    #[test]
    fn status_json_contains_only_public_summary_fields_as_one_line() {
        let vault = MemorySlots::default();
        let temp = tempfile::tempdir().unwrap();
        let fresh = status_json(&vault, temp.path()).unwrap();
        assert!(!fresh.contains('\n'));
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(&fresh).unwrap(),
            serde_json::json!({
                "initialized": false, "publishEnabled": false,
                "cachedRevision": null, "pendingRevision": null, "lastPublishedRevision": null
            })
        );
        crate::ledger_sync::initialize(&vault).unwrap();
        let initialized = status_json(&vault, temp.path()).unwrap();
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(&initialized).unwrap(),
            serde_json::json!({
                "initialized": true, "publishEnabled": false,
                "cachedRevision": null, "pendingRevision": null, "lastPublishedRevision": 0
            })
        );
        assert!(!initialized.contains("ledger-"));
        assert!(!initialized.contains("account-"));
    }

    #[test]
    fn status_json_rejects_unreadable_or_partial_native_state() {
        let vault = MemorySlots::default();
        let temp = tempfile::tempdir().unwrap();
        vault
            .store_secret(crate::ledger::LEDGER_IDENTITY_SLOT, b"corrupt")
            .unwrap();
        assert!(status_json(&vault, temp.path()).is_err());
        vault
            .delete_secret(crate::ledger::LEDGER_IDENTITY_SLOT)
            .unwrap();
        crate::ledger_sync::initialize(&vault).unwrap();
        vault.delete_secret(crate::ledger::LEDGER_KEY_SLOT).unwrap();
        assert!(status_json(&vault, temp.path()).is_err());
    }
}
