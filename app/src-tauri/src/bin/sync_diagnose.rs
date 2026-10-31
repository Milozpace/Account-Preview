//! Confined sync diagnostic entry point (Windows/macOS).
//!
//! Prints the same copyable diagnostic lines as the in-app "运行同步诊断"
//! button, reusing the credentials already stored in the machine's native secure storage
//! vault. Never prints credentials, ciphertext, or URLs. Default mode performs
//! confined placeholder probes. --read-only performs no writes; --fetch reads
//! authenticated snapshots. Explicit --publish-pending N / --publish-next
//! perform the normal fixed-slot publication and preserve both legacy files.

fn main() {
    #[cfg(any(windows, target_os = "macos"))]
    {
        std::process::exit(app_lib::run_cli_diagnostic());
    }
    #[cfg(not(any(windows, target_os = "macos")))]
    {
        eprintln!("This diagnostic CLI is Windows/macOS only.");
        std::process::exit(2);
    }
}
