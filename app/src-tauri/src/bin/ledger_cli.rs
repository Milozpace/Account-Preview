//! Business ledger publisher CLI (Windows/macOS).
//!
//! Standalone entry that works with the desktop app closed. All secrets stay
//! inside the machine's native secure storage vault; no password or key is ever a command
//! argument, and no plaintext is written to disk by this tool.
//!
//! Commands:
//!   --init                      Generate and persist the business identity + key.
//!   --status                    Show local public status (revisions, gates).
//!   --status-json               One JSON line with public LedgerStatusSummary fields.
//!   --prepare <snapshot>        Validate and stage the pending ciphertext.
//!   --prepare <snapshot> --dry-run   Validate only; write nothing.
//!   --publish <snapshot>        Stage + upload + confirm (requires the native
//!                               publish-enable gate set by --enable-publish).
//!   --enable-publish            Set the native real-publishing gate. Only run
//!                               after the main conversation's review approval.
//!
//! Exit codes: 0 success, 2 local setup/validation failure.

fn main() {
    #[cfg(any(windows, target_os = "macos"))]
    {
        let outcome = app_lib::ledger_cli_support::run_cli(std::env::args().skip(1).collect());
        match outcome {
            app_lib::ledger_cli_support::LedgerOutcome::Ok(lines) => {
                for line in lines {
                    println!("{line}");
                }
            }
            app_lib::ledger_cli_support::LedgerOutcome::Failed(lines) => {
                for line in lines {
                    eprintln!("{line}");
                }
                std::process::exit(2);
            }
        }
    }
    #[cfg(not(any(windows, target_os = "macos")))]
    {
        eprintln!("The ledger publisher CLI is Windows/macOS only.");
        std::process::exit(2);
    }
}
