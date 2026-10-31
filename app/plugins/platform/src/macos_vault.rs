//! Native Keychain storage shared by GUI and standalone helpers. No subprocess
//! or plaintext file is involved; macOS controls Keychain access and unlocking.
use crate::{
    Error, Result,
    validation::{MAX_SECRET_BYTES, validate_secret, validate_slot},
};
use security_framework::passwords::{
    PasswordOptions, delete_generic_password_options, generic_password,
    set_generic_password_options,
};
use sha2::{Digest, Sha256};
use std::path::PathBuf;
use zeroize::Zeroizing;

const ITEM_NOT_FOUND: i32 = -25300; // errSecItemNotFound (Security.framework).
const ENVELOPE: &[u8; 8] = b"ACCTVLT1";

pub struct MacosVault {
    service: String,
}
impl MacosVault {
    pub fn new(root: PathBuf) -> Result<Self> {
        Ok(Self {
            service: crate::vault_scope::keychain_service(&root)?,
        })
    }
    fn options(&self, slot: &str) -> PasswordOptions {
        let mut options = PasswordOptions::new_generic_password(&self.service, slot);
        options.set_access_synchronized(Some(false));
        options
    }
    pub(crate) fn store_secret(&self, slot: &str, secret: &[u8]) -> Result<()> {
        validate_slot(slot)?;
        validate_secret(secret)?;
        // Refuse to overwrite locked, corrupt, or inaccessible existing data.
        let previous = self.load_secret(slot)?.map(Zeroizing::new);
        drop(previous);
        let mut envelope = Zeroizing::new(Vec::with_capacity(40 + secret.len()));
        envelope.extend_from_slice(ENVELOPE);
        envelope.extend_from_slice(&Sha256::digest(secret));
        envelope.extend_from_slice(secret);
        set_generic_password_options(&envelope, self.options(slot)).map_err(|_| Error::Encryption)
    }
    pub(crate) fn load_secret(&self, slot: &str) -> Result<Option<Vec<u8>>> {
        validate_slot(slot)?;
        let bytes = match generic_password(self.options(slot)) {
            Ok(bytes) => Zeroizing::new(bytes),
            Err(error) if error.code() == ITEM_NOT_FOUND => return Ok(None),
            Err(_) => return Err(Error::Storage),
        };
        if bytes.len() < 40
            || bytes.len() > MAX_SECRET_BYTES + 40
            || &bytes[..8] != ENVELOPE
            || Sha256::digest(&bytes[40..]).as_slice() != &bytes[8..40]
        {
            return Err(Error::Decryption);
        }
        Ok(Some(bytes[40..].to_vec()))
    }
    pub(crate) fn delete_secret(&self, slot: &str) -> Result<()> {
        validate_slot(slot)?;
        match delete_generic_password_options(self.options(slot)) {
            Ok(()) => Ok(()),
            Err(error) if error.code() == ITEM_NOT_FOUND => Ok(()),
            Err(_) => Err(Error::Storage),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_keychain_roundtrip_is_scoped_and_delete_is_idempotent() {
        // An ephemeral, unlocked CI Keychain must work without GUI approval.
        // A locked/interactive test fixture should fail, never wait for a dialog.
        let _interaction =
            security_framework::os::macos::keychain::SecKeychain::disable_user_interaction()
                .expect("disable interaction for the synthetic Keychain test");
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("identity-a/vault");
        let gui = MacosVault::new(root.clone()).unwrap();
        let cli = MacosVault::new(root).unwrap();
        let other = MacosVault::new(temp.path().join("identity-b/vault")).unwrap();
        gui.store_secret("synthetic-keychain-test", b"synthetic only")
            .unwrap();
        assert_eq!(
            cli.load_secret("synthetic-keychain-test").unwrap(),
            Some(b"synthetic only".to_vec())
        );
        assert!(
            other
                .load_secret("synthetic-keychain-test")
                .unwrap()
                .is_none()
        );
        gui.delete_secret("synthetic-keychain-test").unwrap();
        gui.delete_secret("synthetic-keychain-test").unwrap();
    }
    #[test]
    fn invalid_slots_are_rejected_before_keychain_access() {
        let temp = tempfile::tempdir().unwrap();
        let vault = MacosVault::new(temp.path().join("vault")).unwrap();
        assert!(matches!(
            vault.load_secret("../outside"),
            Err(Error::InvalidSlot)
        ));
        assert!(matches!(
            vault.store_secret("slot", &vec![0; MAX_SECRET_BYTES + 1]),
            Err(Error::SecretTooLarge)
        ));
    }
}
