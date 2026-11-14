//! Fixed actions only: the WebView cannot supply an update URL, hash or local path.
#[cfg(target_os = "android")]
use tauri_plugin_platform::PlatformExt;

fn valid_action(action: &str) -> bool {
    matches!(action, "auto" | "check" | "download" | "install" | "later" | "ignore")
}

#[tauri::command]
pub async fn mobile_update_status(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    #[cfg(target_os = "android")]
    return app.platform().update_status(env!("ACCOUNT_PRODUCT_VERSION")).await
        .map_err(|_| "更新状态暂不可用。".into());
    #[cfg(not(target_os = "android"))]
    { let _ = app; Err("此更新入口仅供 Android 使用。".into()) }
}

#[tauri::command]
pub async fn mobile_update_action(app: tauri::AppHandle, action: String) -> Result<serde_json::Value, String> {
    if !valid_action(&action) { return Err("不支持的更新操作。".into()); }
    #[cfg(target_os = "android")]
    return app.platform().update_action(env!("ACCOUNT_PRODUCT_VERSION"), &action).await
        .map_err(|_| "更新操作未能完成。".into());
    #[cfg(not(target_os = "android"))]
    { let _ = app; Err("此更新入口仅供 Android 使用。".into()) }
}

#[cfg(test)]
mod tests {
    #[test]
    fn updater_accepts_only_fixed_actions() {
        for action in ["auto", "check", "download", "install", "later", "ignore"] {assert!(super::valid_action(action));}
        for action in ["", "https://github.com/anything", "../update.apk", "update", "check "] {assert!(!super::valid_action(action));}
    }
}
