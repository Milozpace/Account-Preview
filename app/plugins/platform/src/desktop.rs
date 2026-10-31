use serde::de::DeserializeOwned;
use std::marker::PhantomData;
#[cfg(any(windows, target_os = "macos"))]
use tauri::Manager;
use tauri::{AppHandle, Runtime, plugin::PluginApi};

pub fn init<R: Runtime, C: DeserializeOwned>(
    app: &AppHandle<R>,
    _api: PluginApi<R, C>,
) -> crate::Result<Platform<R>> {
    #[cfg(any(windows, target_os = "macos"))]
    return Ok(Platform {
        vault: crate::StandaloneVault::open(
            app.path()
                .app_config_dir()
                .map_err(|_| crate::Error::Storage)?
                .join("vault"),
        )?,
        runtime: PhantomData,
    });
    #[cfg(not(any(windows, target_os = "macos")))]
    {
        let _ = app;
        Ok(Platform {
            runtime: PhantomData,
        })
    }
}

/// Native-only secure storage. Supports Windows, macOS and Android.
pub struct Platform<R: Runtime> {
    #[cfg(any(windows, target_os = "macos"))]
    vault: crate::StandaloneVault,
    runtime: PhantomData<fn() -> R>,
}

impl<R: Runtime> Platform<R> {
    pub fn store_secret(&self, slot: &str, secret: &[u8]) -> crate::Result<()> {
        crate::validation::validate_slot(slot)?;
        crate::validation::validate_secret(secret)?;
        #[cfg(any(windows, target_os = "macos"))]
        return self.vault.store_secret(slot, secret);
        #[cfg(not(any(windows, target_os = "macos")))]
        Err(crate::Error::UnsupportedPlatform)
    }

    pub fn load_secret(&self, slot: &str) -> crate::Result<Option<Vec<u8>>> {
        crate::validation::validate_slot(slot)?;
        #[cfg(any(windows, target_os = "macos"))]
        return self.vault.load_secret(slot);
        #[cfg(not(any(windows, target_os = "macos")))]
        Err(crate::Error::UnsupportedPlatform)
    }

    pub fn delete_secret(&self, slot: &str) -> crate::Result<()> {
        crate::validation::validate_slot(slot)?;
        #[cfg(any(windows, target_os = "macos"))]
        return self.vault.0.delete_secret(slot);
        #[cfg(not(any(windows, target_os = "macos")))]
        Err(crate::Error::UnsupportedPlatform)
    }
}
