mod analysis_export;
pub mod bill_inbox;
mod bill_upload;
mod desktop_bridge;
mod ledger;
mod ledger_sync;
mod probe;
mod probe_cache;
mod probe_sync;
mod probe_transaction;
mod tls;
mod webdav;

/// Windows CLI support for the ledger publisher binary.
#[cfg(any(windows, target_os = "macos"))]
pub mod ledger_cli_support;

/// Package version surfaced in the UI so "is the new build installed" is
/// answerable on screen without guessing.
#[tauri::command]
fn app_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

#[cfg(test)]
mod app_context_tests {
    #[test]
    fn macos_context_supports_explicit_desktop_preview() {
        assert_eq!(
            super::context_for("macOS", &["--ui-preview".into()]),
            serde_json::json!({"platform":"macOS","preview":true})
        );
    }

    #[test]
    fn preview_requires_exact_explicit_windows_flag() {
        assert_eq!(
            super::context_for("Windows", &["--ui-preview".into()]),
            serde_json::json!({"platform":"Windows","preview":true})
        );
        assert_eq!(
            super::context_for("Windows", &[]),
            serde_json::json!({"platform":"Windows","preview":false})
        );
        assert_eq!(
            super::context_for("Android", &["--ui-preview".into()]),
            serde_json::json!({"platform":"Android","preview":false})
        );
        assert_eq!(
            super::context_for("Other", &["--ui-preview".into()]),
            serde_json::json!({"platform":"Other","preview":false})
        );
        assert_eq!(
            super::context_for("Windows", &["--ui-preview=false".into()]),
            serde_json::json!({"platform":"Windows","preview":false})
        );
    }
}

fn context_for(platform: &str, args: &[String]) -> serde_json::Value {
    serde_json::json!({"platform":platform,"preview":matches!(platform, "Windows" | "macOS") && args.iter().any(|arg| arg == "--ui-preview")})
}

/// Platform/preview discovery intentionally reads no secure storage or cache.
#[tauri::command]
fn app_context() -> serde_json::Value {
    let platform = if cfg!(windows) {
        "Windows"
    } else if cfg!(target_os = "macos") {
        "macOS"
    } else if cfg!(target_os = "android") {
        "Android"
    } else {
        "Other"
    };
    context_for(platform, &std::env::args().collect::<Vec<_>>())
}

/// CLI entry for the confined sync diagnostic (see `bin/sync_diagnose.rs`).
#[cfg(any(windows, target_os = "macos"))]
pub fn run_cli_diagnostic() -> i32 {
    probe_sync::run_cli_diagnostic()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_platform::init())
        .manage(probe::ProbeState::default())
        .manage(probe_sync::SyncState::default())
        .manage(ledger_sync::LedgerState::default())
        .invoke_handler(tauri::generate_handler![
            probe::run_probe,
            probe::load_probe,
            probe::create_pairing,
            probe::import_pairing,
            probe_sync::sync_configure,
            probe_sync::sync_status,
            probe_sync::publish_snapshot,
            probe_sync::sync_fetch,
            probe_sync::sync_diagnose,
            probe_sync::prepare_new_test_round,
            ledger_sync::ledger_status_command,
            ledger_sync::ledger_create_pairing,
            ledger_sync::ledger_import_pairing,
            ledger_sync::ledger_fetch,
            ledger_sync::ledger_cache,
            ledger_sync::ledger_inspect_snapshot,
            ledger_sync::ledger_prepare,
            ledger_sync::ledger_publish,
            desktop_bridge::desktop_project_config,
            desktop_bridge::desktop_configure_project,
            desktop_bridge::desktop_load_snapshot,
            desktop_bridge::desktop_refresh_snapshot,
            analysis_export::analysis_export_save,
            bill_upload::bill_upload_choose,
            bill_upload::bill_upload_status,
            bill_upload::bill_upload_retry,
            app_context,
            app_version
        ])
        .setup(|app| {
            #[cfg(any(windows, target_os = "macos"))]
            if std::env::args().any(|arg| arg == "--prepare-new-test-round") {
                let handle = app.handle().clone();
                tauri::async_runtime::spawn_blocking(move || {
                    match probe_sync::prepare_gui_test_round(&handle) {
                        Ok(message) => { println!("{message}"); handle.exit(0); }
                        Err(error) => { eprintln!("{error}"); handle.exit(1); }
                    }
                });
            }
            #[cfg(mobile)]
            app.handle().plugin(tauri_plugin_barcode_scanner::init())?;
            #[cfg(any(windows, target_os = "macos"))]
            if std::env::args().any(|arg| arg == "--inspect-sync-read-only") {
                let handle = app.handle().clone();
                tauri::async_runtime::spawn_blocking(move || {
                    match probe_sync::inspect_gui_host(&handle) {
                        Ok(lines) => { for line in lines { println!("{line}"); } handle.exit(0); }
                        Err(error) => { eprintln!("{error}"); handle.exit(1); }
                    }
                });
            }
            #[cfg(any(windows, target_os = "macos"))]
            if std::env::args().any(|arg| arg == "--self-test") {
                let handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    match probe::self_test(handle.clone()).await {
                        Ok(()) => {
                            println!("Native probe self-test passed: native secure storage, AES-256-GCM, cache reopening, pairing retry.");
                            handle.exit(0);
                        }
                        Err(error) => {
                            eprintln!("Native probe self-test failed: {error}");
                            handle.exit(1);
                        }
                    }
                });
            }
            // Native security log: on Android stdout/stderr never reaches
            // logcat by itself, so the log plugin is registered in release
            // builds too and forwards to the platform logger. Only fixed
            // diagnostic strings and fixed reason codes are ever logged.
            #[cfg(any(target_os = "android", debug_assertions))]
            {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while building tauri application");
}
