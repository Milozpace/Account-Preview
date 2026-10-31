//! Android TLS trust setup for `rustls-platform-verifier`.
//!
//! reqwest's `rustls` feature builds a `rustls_platform_verifier::Verifier`
//! while constructing the client. On Android that verifier needs the JVM,
//! the application context, and the Kotlin helper component; without the
//! setup below the upstream crate panics ("Expect rustls-platform-verifier
//! to be initialized") inside the network task, which previously surfaced as
//! a bare JoinError.
//!
//! Contract enforced here:
//! - Initialization happens before any client is built, i.e. before any
//!   network request can be attempted (lazy, on the first client build).
//! - The JVM context is read from `ndk-context`, which Tauri's Android
//!   activity lifecycle initializes before any command can run.
//! - A failed initialization never blocks with a silent hang and never
//!   panics: it returns a fixed, searchable reason code that the caller
//!   turns into a diagnosable error. A later attempt may retry (the
//!   upstream init is idempotent).
//! - No certificate trust source is ever replaced: system trust only.

#[cfg(target_os = "android")]
mod imp {
    use std::sync::Mutex;

    /// `Ok(())` once the verifier has been initialized in this process.
    /// A `false` here only means "not yet"; failures stay retryable so a
    /// transiently missing context does not permanently brick networking.
    static INITIALIZED: Mutex<bool> = Mutex::new(false);

    fn ensure_lock() -> Result<std::sync::MutexGuard<'static, bool>, String> {
        INITIALIZED.lock().map_err(|_| {
            log::error!("TLS init state poisoned (TLS-INIT-STATE)");
            "HTTPS 初始化状态异常（TLS-INIT-STATE），已阻止网络请求。".to_string()
        })
    }

    /// Initialize the rustls-platform-verifier for Android, or return a
    /// fixed reason code. Never panics on the expected failure paths.
    pub fn ensure_tls_initialized() -> Result<(), String> {
        {
            let ready = ensure_lock()?;
            if *ready {
                return Ok(());
            }
        }
        // The panic inside `ndk_context::android_context()` (context not yet
        // registered) is converted into a diagnosable failure instead of a
        // crash of the network task.
        let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let context = ndk_context::android_context();
            let vm_raw = context.vm().cast::<jni::sys::JavaVM>();
            if vm_raw.is_null() {
                return Err("TLS-INIT-VM".to_string());
            }
            // Safety: non-null JavaVM pointer registered by the platform's
            // activity lifecycle before any command can run.
            let vm = unsafe { jni::JavaVM::from_raw(vm_raw) };
            // The attach error (a jni::errors::Error) and the callback's own
            // fixed reason code are folded into one Result<(), String> here,
            // keeping every failure a searchable code instead of panic text.
            let initialized: std::result::Result<
                std::result::Result<(), String>,
                jni::errors::Error,
            > = vm.attach_current_thread(|env| {
                let raw_context = context.context().cast::<jni::sys::_jobject>();
                if raw_context.is_null() {
                    return Ok(Err("TLS-INIT-CONTEXT".to_string()));
                }
                // Safety: Tao owns a valid global application reference. Cast
                // borrows that reference without taking ownership. NewLocalRef
                // then creates a distinct local reference in this JNI frame;
                // the original global reference remains owned by Tao.
                let borrowed_context = unsafe {
                    jni::refs::Cast::<jni::objects::JObject<'static>>::from_raw_unchecked(
                        &raw_context,
                    )
                };
                let context_object = env.new_local_ref(borrowed_context)?;
                Ok(
                    rustls_platform_verifier::android::init_with_env(env, context_object)
                        .map_err(|_| "TLS-INIT-JNI".to_string()),
                )
            });
            initialized
                .map_err(|_| "TLS-INIT-JNI".to_string())
                .and_then(std::convert::identity)
        }));
        let result = match outcome {
            Err(_) => Err("TLS-INIT-CONTEXT".to_string()),
            // The attach callback folds the jni error into the same String.
            Ok(Err(code)) => Err(code),
            Ok(Ok(())) => Ok(()),
        };
        match result {
            Ok(()) => {
                if let Ok(mut ready) = INITIALIZED.lock() {
                    *ready = true;
                }
                log::info!("rustls-platform-verifier initialized (TLS-INIT-OK)");
                Ok(())
            }
            Err(code) => {
                log::error!("rustls-platform-verifier initialization failed ({code})");
                Err(format!(
                    "Android 证书验证组件初始化失败（{code}），已阻止网络请求。"
                ))
            }
        }
    }
}

#[cfg(target_os = "android")]
pub use imp::ensure_tls_initialized;

#[cfg(not(target_os = "android"))]
pub fn ensure_tls_initialized() -> Result<(), String> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::ensure_tls_initialized;

    #[test]
    fn desktop_platform_is_always_ready() {
        ensure_tls_initialized().unwrap();
    }
}
