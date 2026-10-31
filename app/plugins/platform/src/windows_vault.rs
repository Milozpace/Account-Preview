use crate::{
    Error, Result,
    validation::{MAX_SECRET_BYTES, validate_secret, validate_slot},
};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    ptr,
};
use windows_sys::Win32::{
    Foundation::LocalFree,
    Security::Cryptography::{
        CRYPT_INTEGER_BLOB, CRYPTPROTECT_UI_FORBIDDEN, CryptProtectData, CryptUnprotectData,
    },
};
use zeroize::{Zeroize, Zeroizing};

const MAX_CIPHERTEXT_BYTES: u64 = (MAX_SECRET_BYTES + 65536) as u64;
const ENVELOPE: &[u8; 8] = b"ACCTVLT1";

pub struct WindowsVault {
    root: PathBuf,
}
impl WindowsVault {
    pub fn new(root: PathBuf) -> Self {
        Self { root }
    }
    fn path(&self, slot: &str) -> Result<PathBuf> {
        validate_slot(slot)?;
        Ok(self.root.join(format!("{slot}.bin")))
    }

    pub(crate) fn store_secret(&self, slot: &str, secret: &[u8]) -> Result<()> {
        let path = self.path(slot)?;
        validate_secret(secret)?;
        // Never replace an existing unreadable secret with a fresh one silently.
        let previous = self.load_secret(slot)?.map(Zeroizing::new);
        drop(previous);
        let mut envelope = Zeroizing::new(Vec::with_capacity(40 + secret.len()));
        envelope.extend_from_slice(ENVELOPE);
        envelope.extend_from_slice(&Sha256::digest(secret));
        envelope.extend_from_slice(secret);
        let ciphertext = dpapi(&envelope, slot, true)?;
        fs::create_dir_all(&self.root).map_err(|_| Error::Storage)?;
        atomic_write(&path, &ciphertext)
    }

    pub(crate) fn load_secret(&self, slot: &str) -> Result<Option<Vec<u8>>> {
        let path = self.path(slot)?;
        let file = match fs::File::open(path) {
            Ok(file) => file,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(_) => return Err(Error::Storage),
        };
        let mut ciphertext = Vec::new();
        file.take(MAX_CIPHERTEXT_BYTES + 1)
            .read_to_end(&mut ciphertext)
            .map_err(|_| Error::Storage)?;
        if ciphertext.is_empty() || ciphertext.len() as u64 > MAX_CIPHERTEXT_BYTES {
            return Err(Error::Decryption);
        }
        let plaintext = Zeroizing::new(dpapi(&ciphertext, slot, false)?);
        if plaintext.len() < 40
            || &plaintext[..8] != ENVELOPE
            || plaintext.len() - 40 > MAX_SECRET_BYTES
        {
            return Err(Error::Decryption);
        }
        let secret = &plaintext[40..];
        // DPAPI's authenticated envelope is supplemented with a format/digest check.
        if Sha256::digest(secret).as_slice() != &plaintext[8..40] {
            return Err(Error::Decryption);
        }
        Ok(Some(secret.to_vec()))
    }

    pub(crate) fn delete_secret(&self, slot: &str) -> Result<()> {
        match fs::remove_file(self.path(slot)?) {
            Ok(()) => Ok(()),
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(_) => Err(Error::Storage),
        }
    }
}

fn atomic_write(path: &Path, ciphertext: &[u8]) -> Result<()> {
    let parent = path.parent().ok_or(Error::Storage)?;
    let mut file = tempfile::NamedTempFile::new_in(parent).map_err(|_| Error::Storage)?;
    file.write_all(ciphertext).map_err(|_| Error::Storage)?;
    file.as_file().sync_all().map_err(|_| Error::Storage)?;
    // tempfile uses MoveFileExW(REPLACE_EXISTING), without deleting the old file first.
    file.persist(path).map_err(|_| Error::Storage)?;
    Ok(())
}

struct DpapiBuffer(CRYPT_INTEGER_BLOB);
impl Drop for DpapiBuffer {
    fn drop(&mut self) {
        if !self.0.pbData.is_null() {
            // SAFETY: Windows returned this allocation and length. Zeroize before LocalFree.
            unsafe {
                std::slice::from_raw_parts_mut(self.0.pbData, self.0.cbData as usize).zeroize();
                LocalFree(self.0.pbData.cast());
            }
        }
    }
}

fn dpapi(input: &[u8], slot: &str, protect: bool) -> Result<Vec<u8>> {
    let input_blob = CRYPT_INTEGER_BLOB {
        cbData: input.len() as u32,
        pbData: input.as_ptr().cast_mut(),
    };
    // Slot binding makes swapping two otherwise valid ciphertexts fail.
    let entropy = format!("com.account.app:vault:v1:{slot}");
    let entropy_blob = CRYPT_INTEGER_BLOB {
        cbData: entropy.len() as u32,
        pbData: entropy.as_ptr().cast_mut(),
    };
    let mut output = DpapiBuffer(CRYPT_INTEGER_BLOB {
        cbData: 0,
        pbData: ptr::null_mut(),
    });
    // SAFETY: input and entropy remain live for these synchronous calls. Output is
    // owned by the guard. No machine-wide flag, UI, or plaintext description is used.
    let succeeded = unsafe {
        if protect {
            CryptProtectData(
                &input_blob,
                ptr::null(),
                &entropy_blob,
                ptr::null(),
                ptr::null(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut output.0,
            )
        } else {
            CryptUnprotectData(
                &input_blob,
                ptr::null_mut(),
                &entropy_blob,
                ptr::null(),
                ptr::null(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut output.0,
            )
        }
    };
    if succeeded == 0 || output.0.pbData.is_null() {
        return Err(if protect {
            Error::Encryption
        } else {
            Error::Decryption
        });
    }
    // SAFETY: a successful DPAPI call returns the stated allocated buffer.
    Ok(unsafe { std::slice::from_raw_parts(output.0.pbData, output.0.cbData as usize) }.to_vec())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::Error;
    #[test]
    fn real_dpapi_roundtrip_writes_only_ciphertext_and_deletes_idempotently() {
        let dir = tempfile::tempdir().unwrap();
        let vault = WindowsVault::new(dir.path().join("vault"));
        let secret = b"account-vault-test-only-no-real-token-9387193";
        assert!(vault.load_secret("test-secret").unwrap().is_none());
        vault.store_secret("test-secret", secret).unwrap();
        assert_eq!(
            vault.load_secret("test-secret").unwrap().as_deref(),
            Some(secret.as_slice())
        );
        let ciphertext = std::fs::read(vault.root.join("test-secret.bin")).unwrap();
        assert!(!ciphertext.windows(secret.len()).any(|w| w == secret));
        vault.delete_secret("test-secret").unwrap();
        vault.delete_secret("test-secret").unwrap();
        assert!(vault.load_secret("test-secret").unwrap().is_none());
    }
    #[test]
    fn real_dpapi_tampering_and_slot_swaps_are_errors() {
        let dir = tempfile::tempdir().unwrap();
        let vault = WindowsVault::new(dir.path().join("vault"));
        vault.store_secret("first", b"test secret").unwrap();
        std::fs::copy(vault.root.join("first.bin"), vault.root.join("second.bin")).unwrap();
        assert!(matches!(
            vault.load_secret("second"),
            Err(Error::Decryption)
        ));
        let path = vault.root.join("first.bin");
        let mut ciphertext = std::fs::read(&path).unwrap();
        *ciphertext.last_mut().unwrap() ^= 0x80;
        std::fs::write(&path, ciphertext).unwrap();
        assert!(matches!(vault.load_secret("first"), Err(Error::Decryption)));
    }
    #[test]
    fn failed_replace_preserves_the_previous_secret() {
        use std::os::windows::fs::OpenOptionsExt;
        let dir = tempfile::tempdir().unwrap();
        let vault = WindowsVault::new(dir.path().join("vault"));
        vault.store_secret("test", b"previous").unwrap();
        let lock = std::fs::OpenOptions::new()
            .read(true)
            .share_mode(1)
            .open(vault.root.join("test.bin"))
            .unwrap();
        assert!(vault.store_secret("test", b"new").is_err());
        drop(lock);
        assert_eq!(
            vault.load_secret("test").unwrap().as_deref(),
            Some(b"previous".as_slice())
        );
        assert_eq!(std::fs::read_dir(&vault.root).unwrap().count(), 1);
    }
    #[test]
    fn invalid_inputs_never_create_files() {
        let dir = tempfile::tempdir().unwrap();
        let vault = WindowsVault::new(dir.path().join("vault"));
        for slot in ["../outside", "", "token/slash"] {
            assert!(matches!(
                vault.store_secret(slot, b"x"),
                Err(Error::InvalidSlot)
            ));
            assert!(matches!(vault.load_secret(slot), Err(Error::InvalidSlot)));
            assert!(matches!(vault.delete_secret(slot), Err(Error::InvalidSlot)));
        }
        assert!(matches!(
            vault.store_secret("token", &vec![0; 512 * 1024 + 1]),
            Err(Error::SecretTooLarge)
        ));
        assert!(!vault.root.exists());
    }
    #[test]
    fn empty_and_maximum_size_secrets_roundtrip() {
        let dir = tempfile::tempdir().unwrap();
        let vault = WindowsVault::new(dir.path().join("vault"));
        for secret in [vec![], vec![0x5a; 512 * 1024]] {
            vault.store_secret("bounded", &secret).unwrap();
            assert_eq!(vault.load_secret("bounded").unwrap(), Some(secret));
        }
    }

    #[test]
    fn every_valid_slot_including_windows_device_names_roundtrips() {
        let dir = tempfile::tempdir().unwrap();
        let vault = WindowsVault::new(dir.path().join("vault"));
        for slot in [
            "con",
            "nul",
            "aux",
            "prn",
            "com1",
            "lpt1",
            "-",
            &"a".repeat(64),
        ] {
            vault.store_secret(slot, b"native test").unwrap();
            assert_eq!(
                vault.load_secret(slot).unwrap().as_deref(),
                Some(b"native test".as_slice())
            );
        }
    }

    #[test]
    fn corrupt_existing_secret_is_not_silently_overwritten() {
        let dir = tempfile::tempdir().unwrap();
        let vault = WindowsVault::new(dir.path().join("vault"));
        std::fs::create_dir_all(&vault.root).unwrap();
        let path = vault.root.join("corrupt.bin");
        std::fs::write(&path, b"invalid encrypted bytes").unwrap();
        assert!(matches!(
            vault.store_secret("corrupt", b"replacement"),
            Err(Error::Decryption)
        ));
        assert_eq!(std::fs::read(path).unwrap(), b"invalid encrypted bytes");
    }
}
