# Account native platform plugin

Supports Windows, macOS and Android. Other platforms return UnsupportedPlatform;
there is no iOS binding or implied iOS support.

## Rust API (host only)

Register tauri_plugin_platform::init() in the native host, then import
PlatformExt to use the app handle's platform() service:

```rust,ignore
use tauri_plugin_platform::PlatformExt;

app.platform().store_secret("msal-cache", &cache_bytes)?;
let cache: Option<Vec<u8>> = app.platform().load_secret("msal-cache")?;
app.platform().delete_secret("msal-cache")?;
```

All methods return tauri_plugin_platform::Result. A missing slot returns
Ok(None); an empty secret returns Some(vec![]). Deletion is idempotent.
Slots must contain 1–64 ASCII lowercase letters, digits, or hyphens. Secrets
are limited to 512 KiB. Slots cannot inject directories or traversal paths.
Read errors, malformed ciphertext, key loss and failed decryption are errors;
store never silently overwrites an unreadable existing secret. Explicit
host-authorized deletion/re-authentication is required for recovery.

The host owns returned plaintext buffers and must avoid logging/Debug output
and discard/zeroize them after use. Run these synchronous methods off the UI
thread (e.g. Tauri spawn_blocking). Android uses an IO worker internally; the
synchronous Rust bridge waits for its completion.

There are no guest-JS APIs, no raw-secret permission identifiers, and no
Rust secret commands. The plugin explicitly rejects every WebView invocation
so Tauri's mobile command fallback cannot expose internal Kotlin commands.
The Kotlin @Command methods are reached only by native PluginHandle calls.
Neither request nor response structs containing secrets implement Debug.
Error strings are fixed and contain no native exception details, path names,
plaintext, base64 payloads or account identifiers.

## Windows

Files live under Tauri app_config_dir()/vault/<slot>.bin. The current Windows
user's DPAPI encrypts/decrypts with CRYPTPROTECT_UI_FORBIDDEN; the
CRYPTPROTECT_LOCAL_MACHINE flag is never used. App/format/slot binding is
provided as DPAPI entropy, and the encrypted versioned envelope validates its
format and SHA-256 digest after decryption. DPAPI allocations are zeroized and
released with LocalFree. Only ciphertext is written to disk, including
same-directory temporary files. The flushed temporary file is atomically
persisted using tempfile's Windows MoveFileExW replacement; failure never
deletes the previous file, and the temporary file is cleaned up.

Windows is protected against offline file disclosure under the current user's
DPAPI identity; other processes running as that same user are outside the
isolation boundary. Copying a vault to a different identity may make it
undecryptable. Treat encrypted files as device/user-bound credentials, not as
a portable sync artifact.

## macOS

The native Security.framework generic-password APIs store versioned, SHA-256
checked secret envelopes in the current user's Keychain. The service is
`com.account.preview.vault.v1.<sha256>` where the hash binds the resolved
absolute Tauri `app_config_dir()/vault` namespace (including the application
identifier). The account is the validated slot name. All queries explicitly select the
non-synchronized Keychain store. Neither secrets nor
local directory names appear in command arguments or plaintext vault files.

The GUI and standalone helpers use the same namespace. The helpers derive the
application identifier from their bundled `tauri.conf.json`, so Preview never
opens production's `com.account.probe` store. Locked/inaccessible Keychain,
malformed envelopes and lost permissions are surfaced as fixed errors; no
silent replacement or plaintext fallback occurs. macOS may ask the user to
allow an app/helper to access its Keychain entry. App and helpers must be
accepted under the same user's native Keychain access policy.

Moving/renaming the application support namespace changes its service hash;
this is identity isolation, not a portable credentials migration mechanism.
Native Keychain roundtrip tests use unique temporary namespace hashes and
synthetic secrets. They require a real macOS runner with an available test
Keychain. Windows test success does not establish macOS runtime acceptance.

## Android and backup requirements

The native plugin namespace/package is com.account.platform. compileSdk=36,
minSdk=24. SecureVault uses a non-exportable AndroidKeyStore AES-256 wrapping
key and AES/GCM/NoPadding (128-bit tag). Android generates a fresh randomized
12-byte nonce for every write. The app/format/slot binding is authenticated
associated data; swapping files between slots fails authentication.

Ciphertext lives in context.noBackupFilesDir/account-vault, including any
AtomicFile .bak/.new intermediates. Android excludes noBackupFilesDir from
cloud backups and device-to-device transfer, so unbacked-up Keystore keys
cannot leave restored unusable secrets. This location must remain unchanged
unless equivalent explicit backup/transfer exclusions are provided.

**Native host manifest requirement:** the Account host must explicitly set
android:allowBackup="false" on its application and preserve exclusion of
credential/cache files in any android:fullBackupContent or
android:dataExtractionRules policy it adds. Do not move this vault into
filesDir, preferences, external storage or a sync folder without excluding it
in BOTH legacy backup and Android 12+ cloud/device-transfer rules. Some OEMs
ignore allowBackup=false for device transfer; noBackupFilesDir is the vault's
actual exclusion boundary. The library does not silently alter the host's
global backup policy.

Writes use Android AtomicFile and an explicit throwing file-descriptor sync.
Since AtomicFile.finishWrite can log commit failures and return normally, a
store succeeds only after bounded readback through AtomicFile.openRead recovers
pending/legacy files, matches the intended ciphertext exactly and leaves no
unresolved .bak/.new recovery files. A failed
rename normally retains the prior record; if readback finds it missing,
different or unreadable, the vault attempts to restore the previously
authenticated encrypted envelope and verifies that recovery. If storage also
prevents recovery the operation still returns a fixed error, with no promise
that unavailable storage can be repaired. No plaintext recovery file is used.
A lost/invalidated Keystore key or authentication
failure is surfaced as a fixed safe bridge error; no replacement key or
plaintext fallback is created for existing encrypted records. Deleting a
slot retains the wrapping key so other slots remain usable.

## Verification

Windows tests use throwaway directories and synthetic bytes (never real user
tokens). They exercise real DPAPI: missing/empty/max-size roundtrips,
ciphertext plaintext absence, tamper failure, slot swapping, replacement
failure preserving the old secret, invalid slots, malformed existing records,
and valid Windows device-name slots.

```powershell
cargo fmt --check
cargo test --lib --target-dir .runtime/test-target
cargo clippy --all-targets --target-dir .runtime/test-target -- -D warnings
```

Android VaultFormatTest covers validation and corrupt/bounded envelopes on
the JVM. VaultCommitTest uses synthetic encrypted envelopes and temporary
disk records to inject silent commit failure, corrupt/read-error readback,
failed recovery, successful replacement and uncommitted first write.
SecureVaultInstrumentedTest requires an Android emulator/device and covers
actual Keystore wrapping, nonce freshness, corruption/slot binding, validation
and silent AtomicFile commit failure preserving the old secret across a new
vault instance. Compiling the instrumentation APK is not proof those native
device tests ran.

Primary API references:
- https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata
- https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptunprotectdata
- https://developer.android.com/privacy-and-security/keystore
- https://developer.android.com/identity/data/autobackup
- https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/core/java/android/util/AtomicFile.java
