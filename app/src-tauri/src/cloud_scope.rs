//! Build-bound remote namespace. No user-entered path or old-cloud fallback.
pub const IDENTIFIER: &str = env!("ACCOUNT_INSTALL_IDENTIFIER");
pub const LEDGER_DIRECTORY: &str = concat!(env!("ACCOUNT_REMOTE_ROOT"), "/ledger");
pub const PROBE_DIRECTORY: &str = concat!(env!("ACCOUNT_REMOTE_ROOT"), "/probe");
pub const INBOX_DIRECTORY: &str = concat!(env!("ACCOUNT_REMOTE_ROOT"), "/inbox");
pub const LEDGER_STATE: &str = env!("ACCOUNT_LEDGER_STATE");
pub const LEDGER_INTENT: &str = env!("ACCOUNT_LEDGER_INTENT");
pub const PROBE_STATE: &str = env!("ACCOUNT_PROBE_STATE");
pub const PROBE_INTENT: &str = env!("ACCOUNT_PROBE_INTENT");

pub fn accepts_stored_probe_directory(directory: &str) -> bool {
    directory == PROBE_DIRECTORY
        || (IDENTIFIER == "com.account.preview" && directory == "Account/probe")
}
