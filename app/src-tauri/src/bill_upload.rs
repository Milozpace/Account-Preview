//! Narrow native inbox commands. Raw bill bytes never cross the WebView boundary.

use std::sync::atomic::{AtomicBool, Ordering};

struct OperationLease<'a>(&'a AtomicBool);
impl Drop for OperationLease<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}
fn acquire(active: &AtomicBool) -> Result<OperationLease<'_>, String> {
    active
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .map_err(|_| "已有账单操作正在进行，请稍后重试".to_string())?;
    Ok(OperationLease(active))
}

static ACTIVE: AtomicBool = AtomicBool::new(false);

#[derive(serde::Serialize)]
pub struct BatchResult {
    items: Vec<crate::bill_inbox::UploadSummary>,
    failed: usize,
    completed: usize,
    failures: Vec<BatchFailure>,
}
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct BatchFailure { file_name: String, reason: &'static str }

#[tauri::command]
pub async fn bill_upload_choose(
    app: tauri::AppHandle,
) -> Result<Option<BatchResult>, String> {
    let lease = acquire(&ACTIVE)?;
    #[cfg(target_os = "android")]
    {
        use tauri::Manager;
        use tauri_plugin_platform::PlatformExt;
        let count = match app.platform().select_bills().await {
            Ok(Some(count)) => count,
            Ok(None) => return Ok(None),
            Err(_) => return Err("账单选择失败，请选择 CSV 或 XLSX 文件".into()),
        };
        let directory = app.path().app_data_dir().map_err(|_| "账单暂存目录不可用".to_string())?;
        let lease = std::sync::Arc::new(lease);
        let mut items = Vec::new();
        let mut failed = 0;
        let mut failures = Vec::new();
        for index in 0..count {
            let selected = match app.platform().read_selected_bill(index).await {
                Ok((Some(value), _)) => value,
                Ok((None, name)) => {
                    failed += 1;
                    failures.push(BatchFailure { file_name: name.unwrap_or_else(|| format!("文件 {}", index + 1)), reason: "文件无效或读取失败" });
                    continue;
                },
                Err(_) => {
                    failed += 1;
                    failures.push(BatchFailure { file_name: format!("文件 {}", index + 1), reason: "读取失败" });
                    continue;
                }
            };
            let file_name = selected.file_name.clone();
            let worker_app = app.clone();
            let worker_dir = directory.clone();
            let worker_lease = lease.clone();
            let result = tauri::async_runtime::spawn_blocking(move || {
                let _lease = worker_lease;
                let vault = crate::probe_sync::SyncVault(worker_app.platform());
                crate::bill_inbox::upload(&vault, &worker_dir, &selected.file_name, &selected.bytes)
            }).await;
            match result {
                Ok(Ok(summary)) => items.push(summary),
                _ => { failed += 1; failures.push(BatchFailure { file_name, reason: "上传未确认" }); },
            }
        }
        let _ = app.platform().clear_selected_bills().await;
        // A failed upload may already have an encrypted pending copy; recover it.
        if failed > 0 {
            let worker_app = app.clone();
            let worker_lease = lease.clone();
            if let Ok(Ok(saved)) = tauri::async_runtime::spawn_blocking(move || {
                let _lease = worker_lease;
                let vault = crate::probe_sync::SyncVault(worker_app.platform());
                crate::bill_inbox::status(&vault, &directory, false)
            }).await { items.extend(saved); }
        }
        Ok(Some(BatchResult { items, failed, completed: count - failed, failures }))
    }
    #[cfg(not(target_os = "android"))]
    {
        let _ = (app, lease);
        Err("请在 Android 手机端上传原始账单".into())
    }
}

#[tauri::command]
pub async fn bill_upload_status(
    app: tauri::AppHandle,
    refresh: bool,
) -> Result<Vec<crate::bill_inbox::UploadSummary>, String> {
    let lease = acquire(&ACTIVE)?;
    #[cfg(target_os = "android")]
    {
        use tauri::Manager;
        use tauri_plugin_platform::PlatformExt;
        let directory = app
            .path()
            .app_data_dir()
            .map_err(|_| "账单暂存目录不可用".to_string())?;
        tauri::async_runtime::spawn_blocking(move || {
            let _lease = lease;
            let vault = crate::probe_sync::SyncVault(app.platform());
            crate::bill_inbox::status(&vault, &directory, refresh)
        })
        .await
        .map_err(|_| "账单状态读取失败，请稍后重试".to_string())?
    }
    #[cfg(not(target_os = "android"))]
    {
        let _ = (app, refresh, lease);
        Err("请在 Android 手机端查看上传状态".into())
    }
}

#[tauri::command]
pub async fn bill_upload_retry(
    app: tauri::AppHandle,
    id: String,
) -> Result<crate::bill_inbox::UploadSummary, String> {
    let lease = acquire(&ACTIVE)?;
    #[cfg(target_os = "android")]
    {
        use tauri::Manager;
        use tauri_plugin_platform::PlatformExt;
        let directory = app
            .path()
            .app_data_dir()
            .map_err(|_| "账单暂存目录不可用".to_string())?;
        tauri::async_runtime::spawn_blocking(move || {
            let _lease = lease;
            let vault = crate::probe_sync::SyncVault(app.platform());
            crate::bill_inbox::retry(&vault, &directory, &id)
        })
        .await
        .map_err(|_| "账单重试失败，请稍后重试".to_string())?
    }
    #[cfg(not(target_os = "android"))]
    {
        let _ = (app, id, lease);
        Err("请在 Android 手机端重试上传".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicBool;

    #[test]
    fn one_operation_at_a_time_and_cancellation_releases_the_gate() {
        let active = AtomicBool::new(false);
        let lease = acquire(&active).unwrap();
        assert!(acquire(&active).is_err());
        drop(lease);
        assert!(acquire(&active).is_ok());
    }

    #[test]
    fn early_error_releases_the_gate() {
        let active = AtomicBool::new(false);
        let failed = (|| -> Result<(), String> {
            let _lease = acquire(&active)?;
            Err("selection failed".into())
        })();
        assert!(failed.is_err());
        assert!(acquire(&active).is_ok());
    }
}
