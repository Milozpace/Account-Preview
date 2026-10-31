use tauri::{
    Manager, Runtime,
    plugin::{Builder, TauriPlugin},
};

#[cfg(not(target_os = "android"))]
mod desktop;
#[cfg(target_os = "android")]
mod mobile;

mod error;
#[cfg(target_os = "macos")]
pub mod macos_vault;
mod validation;
#[cfg(any(target_os = "macos", test))]
mod vault_scope;
#[cfg(windows)]
pub mod windows_vault;

#[cfg(windows)]
type NativeVault = windows_vault::WindowsVault;
#[cfg(target_os = "macos")]
type NativeVault = macos_vault::MacosVault;

/// Standalone handle over the native secret vault, usable without a running
/// Tauri app. It reads and writes exactly the same native vault namespace as the app
/// (Windows DPAPI files / macOS Keychain items, same slot names). Scope is deliberately limited to
/// loading existing secrets: the confined sync-diagnose CLI uses this to
/// reuse the credentials the user already configured, never to create or
/// migrate them. Windows and macOS; other platforms return
/// [`Error::UnsupportedPlatform`] from [`Self::open`].
#[cfg(any(windows, target_os = "macos"))]
pub struct StandaloneVault(NativeVault);

#[cfg(any(windows, target_os = "macos"))]
impl StandaloneVault {
    pub fn open(vault_root: std::path::PathBuf) -> crate::Result<Self> {
        #[cfg(windows)]
        return Ok(Self(NativeVault::new(vault_root)));
        #[cfg(target_os = "macos")]
        return Ok(Self(NativeVault::new(vault_root)?));
    }

    pub fn load_secret(&self, slot: &str) -> crate::Result<Option<Vec<u8>>> {
        self.0.load_secret(slot)
    }

    /// Scoped write access for the business ledger publisher CLI: trusted
    /// local initialization of the ledger identity/key and the explicit
    /// real-publishing gate. The diagnose CLI keeps its read-only contract;
    /// no delete path is exposed here.
    pub fn store_secret(&self, slot: &str, secret: &[u8]) -> crate::Result<()> {
        self.0.store_secret(slot, secret)
    }
}

#[cfg(not(any(windows, target_os = "macos")))]
pub struct StandaloneVault;

#[cfg(not(any(windows, target_os = "macos")))]
impl StandaloneVault {
    pub fn open(_vault_root: std::path::PathBuf) -> crate::Result<Self> {
        Err(crate::Error::UnsupportedPlatform)
    }
}

pub use error::{Error, Result};

#[cfg(not(target_os = "android"))]
pub use desktop::Platform;
#[cfg(target_os = "android")]
pub use mobile::Platform;

/// Extensions to [`tauri::App`], [`tauri::AppHandle`] and [`tauri::Window`] to access the platform APIs.
pub trait PlatformExt<R: Runtime> {
    fn platform(&self) -> &Platform<R>;
}

impl<R: Runtime, T: Manager<R>> crate::PlatformExt<R> for T {
    fn platform(&self) -> &Platform<R> {
        self.state::<Platform<R>>().inner()
    }
}

/// Initializes the plugin.
pub fn init<R: Runtime>() -> TauriPlugin<R> {
    Builder::new("platform")
        // Tauri can fall back to Kotlin commands on mobile when a Rust handler
        // returns false. Consume and reject every WebView invocation explicitly.
        .invoke_handler(|invoke| {
            invoke.resolver.reject("platform services are native-only");
            true
        })
        .setup(|app, api| {
            #[cfg(target_os = "android")]
            let platform = mobile::init(app, api)?;
            #[cfg(not(target_os = "android"))]
            let platform = desktop::init(app, api)?;
            app.manage(platform);
            Ok(())
        })
        .build()
}
