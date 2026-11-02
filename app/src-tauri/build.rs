#[path = "src/install_profile.rs"]
mod install_profile;

fn main() {
    println!("cargo:rerun-if-changed=src/install_profile.rs");
    println!("cargo:rerun-if-changed=tauri.conf.json");
    println!("cargo:rerun-if-env-changed=TAURI_CONFIG");
    let mut configuration: serde_json::Value =
        serde_json::from_slice(&std::fs::read("tauri.conf.json").expect("read Tauri identity"))
            .expect("parse Tauri identity");
    if let Ok(overlay) = std::env::var("TAURI_CONFIG") {
        let overlay: serde_json::Value =
            serde_json::from_str(&overlay).expect("parse Tauri overlay");
        if let Some(identifier) = overlay.get("identifier") {
            configuration["identifier"] = identifier.clone();
        }
    }
    let profile = install_profile::resolve(
        configuration["identifier"]
            .as_str()
            .expect("installation identifier"),
    )
    .expect("select installation cloud scope");
    println!(
        "cargo:rustc-env=ACCOUNT_INSTALL_IDENTIFIER={}",
        profile.identifier
    );
    println!(
        "cargo:rustc-env=ACCOUNT_REMOTE_ROOT={}",
        profile.remote_root
    );
    for (key, legacy, scoped) in [
        (
            "LEDGER_STATE",
            "ledger-publish-state.json",
            "ledger-publish-state-preview-v1.json",
        ),
        (
            "LEDGER_INTENT",
            "ledger-slot-intent.json",
            "ledger-slot-intent-preview-v1.json",
        ),
        (
            "PROBE_STATE",
            "publish-state.json",
            "publish-state-preview-v1.json",
        ),
        (
            "PROBE_INTENT",
            "publish-slot-intent.json",
            "publish-slot-intent-preview-v1.json",
        ),
    ] {
        println!(
            "cargo:rustc-env=ACCOUNT_{key}={}",
            if profile.preview { scoped } else { legacy }
        );
    }
    tauri_build::build()
}
