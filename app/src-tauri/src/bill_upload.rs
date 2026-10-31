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

#[tauri::command]
pub async fn bill_upload_choose(
    app: tauri::AppHandle,
) -> Result<Option<crate::bill_inbox::UploadSummary>, String> {
    let lease = acquire(&ACTIVE)?;
    #[cfg(target_os = "android")]
    {
        use tauri::Manager;
        use tauri_plugin_platform::PlatformExt;
        let Some(selected) = app
            .platform()
            .select_bill()
            .await
            .map_err(|_| "账单选择失败，请选择不超过 4 MiB 的 CSV 或 XLSX 文件".to_string())?
        else {
            return Ok(None);
        };
        let directory = app
            .path()
            .app_data_dir()
            .map_err(|_| "账单暂存目录不可用".to_string())?;
        tauri::async_runtime::spawn_blocking(move || {
            // Retain the busy lease until native I/O actually finishes, even if
            // the WebView stops waiting for its response.
            let _lease = lease;
            let vault = crate::probe_sync::SyncVault(app.platform());
            crate::bill_inbox::upload(&vault, &directory, &selected.file_name, &selected.bytes)
                .map(Some)
        })
        .await
        .map_err(|_| "账单上传失败，请稍后重试".to_string())?
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
