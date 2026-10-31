//! Portable encrypted snapshot transport. This crate treats snapshot bytes as
//! opaque data and does not calculate, classify, or validate financial records.
//! Pairing, key storage, durable replay checkpoints, and key-rotation approval
//! belong to the native host. Never obtain expected identity from the envelope.
#![forbid(unsafe_code)]

use aes_gcm::{
    aead::{rand_core::RngCore, Aead, KeyInit, OsRng, Payload},
    Aes256Gcm, Nonce,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::{Deserialize, Serialize};
use zeroize::Zeroizing;

/// Maximum opaque plaintext snapshot size (8 MiB).
pub const MAX_SNAPSHOT_BYTES: usize = 8 * 1024 * 1024;
/// Maximum UTF-8 byte length of each opaque pairing identifier.
pub const MAX_ID_BYTES: usize = 128;
/// Parser input limit, including base64 expansion and bounded JSON metadata.
pub const MAX_ENVELOPE_BYTES: usize = ((MAX_SNAPSHOT_BYTES + 16).div_ceil(3) * 4) + 2048;
const MAX_CIPHERTEXT_BASE64_BYTES: usize = (MAX_SNAPSHOT_BYTES + 16).div_ceil(3) * 4;
const AAD_DOMAIN: &[u8] = b"account.snapshot.aes256gcm.v1\0";

/// A 256-bit content key, erased on drop. Intentionally neither serializable
/// nor clonable; Debug is redacted. Native adapters own secure persistence.
///
/// ```compile_fail
/// use account_app_core::ContentKey;
/// let key = ContentKey::generate().unwrap();
/// // Envelopes can be serialized; secret keys cannot.
/// let _ = serde_json::to_string(&key);
/// ```
pub struct ContentKey(Zeroizing<[u8; 32]>);
impl ContentKey {
    /// Obtain a fresh key from the operating system CSPRNG; no fallback exists.
    pub fn generate() -> Result<Self, Error> {
        let mut bytes = Zeroizing::new([0; 32]);
        OsRng
            .try_fill_bytes(bytes.as_mut())
            .map_err(|_| Error::RandomnessUnavailable)?;
        Ok(Self(bytes))
    }
    /// Import a key returned by the native secure-store/pairing adapter.
    /// The caller must erase any original buffer; this erases its owned copy.
    pub fn from_bytes(bytes: [u8; 32]) -> Self {
        Self(Zeroizing::new(bytes))
    }
    /// Borrow secret material solely for native secure-store/pairing adapters.
    /// Never log it, put it in JSON, or send it with an encrypted envelope.
    pub fn as_bytes(&self) -> &[u8; 32] {
        &self.0
    }
}
impl std::fmt::Debug for ContentKey {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("ContentKey([REDACTED])")
    }
}

/// All fields participate in authenticated associated data. Format 1 fixes
/// AES-256-GCM, a 96-bit nonce, a 128-bit tag, and canonical struct-field JSON.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SnapshotMetadata {
    pub format_version: u32,
    pub ledger_id: String,
    pub account_id: String,
    pub key_generation: u64,
    pub revision: u64,
}

/// Untrusted wire data: base64 contains ciphertext followed by the GCM tag.
/// Deserialization alone is not authentication. Prefer `from_json` at ingress.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct EncryptedEnvelope {
    pub metadata: SnapshotMetadata,
    pub nonce: String,
    pub ciphertext: String,
}
impl EncryptedEnvelope {
    /// Bound raw input before serde allocates or parses any attacker content.
    /// Reject unknown/duplicate fields and validate format, counters and sizes.
    pub fn from_json(json: &[u8]) -> Result<Self, Error> {
        if json.len() > MAX_ENVELOPE_BYTES {
            return Err(Error::TooLarge);
        }
        let envelope: Self = serde_json::from_slice(json).map_err(|_| Error::MalformedEnvelope)?;
        envelope.validate_and_decode()?;
        Ok(envelope)
    }
    /// Serialize validated ciphertext only. This never serializes a key or
    /// plaintext. Do not use this method as proof of ciphertext authentication.
    pub fn to_json(&self) -> Result<Vec<u8>, Error> {
        self.validate_and_decode()?;
        let json = serde_json::to_vec(self).map_err(|_| Error::MalformedEnvelope)?;
        if json.len() > MAX_ENVELOPE_BYTES {
            return Err(Error::TooLarge);
        }
        Ok(json)
    }

    fn validate_and_decode(&self) -> Result<([u8; 12], Vec<u8>), Error> {
        self.metadata.validate()?;
        // A 12-byte nonce is exactly 16 base64 characters. Check before decode.
        if self.nonce.len() != 16 {
            return Err(Error::MalformedEnvelope);
        }
        if self.ciphertext.len() > MAX_CIPHERTEXT_BASE64_BYTES {
            return Err(Error::TooLarge);
        }
        let nonce: [u8; 12] = STANDARD
            .decode(&self.nonce)
            .map_err(|_| Error::MalformedEnvelope)?
            .try_into()
            .map_err(|_| Error::MalformedEnvelope)?;
        let ciphertext = STANDARD
            .decode(&self.ciphertext)
            .map_err(|_| Error::MalformedEnvelope)?;
        if ciphertext.len() > MAX_SNAPSHOT_BYTES + 16 {
            return Err(Error::TooLarge);
        }
        if ciphertext.len() < 16 {
            return Err(Error::MalformedEnvelope);
        }
        Ok((nonce, ciphertext))
    }
}

/// Identity from locally verified pairing/key storage, never from cloud data.
#[derive(Clone, Copy)]
pub struct ExpectedIdentity<'a> {
    pub ledger_id: &'a str,
    pub account_id: &'a str,
    pub key_generation: u64,
}

/// Errors intentionally contain no keys, plaintext, account values, or inputs.
#[derive(Debug, PartialEq, Eq, thiserror::Error)]
pub enum Error {
    #[error("unsupported format")]
    UnsupportedFormat,
    #[error("invalid metadata")]
    InvalidMetadata,
    #[error("input too large")]
    TooLarge,
    #[error("malformed envelope")]
    MalformedEnvelope,
    #[error("wrong identity or generation")]
    IdentityMismatch,
    #[error("authentication failed")]
    AuthenticationFailed,
    #[error("randomness unavailable")]
    RandomnessUnavailable,
    #[error("replayed revision")]
    Replay,
    #[error("generation transition requires local authorization")]
    GenerationTransitionRequired,
}

/// Encrypt opaque snapshot bytes with authenticated pairing/version metadata.
/// Every call obtains a fresh random 96-bit nonce from the OS CSPRNG. Random
/// nonces have a negligible collision probability, not an absolute guarantee;
/// the host must rotate keys well before 2^32 encryptions under one key.
pub fn encrypt(
    snapshot: &[u8],
    key: &ContentKey,
    metadata: SnapshotMetadata,
) -> Result<EncryptedEnvelope, Error> {
    metadata.validate()?;
    if snapshot.len() > MAX_SNAPSHOT_BYTES {
        return Err(Error::TooLarge);
    }
    let aad = metadata.associated_data()?;
    let mut nonce = [0; 12];
    OsRng
        .try_fill_bytes(&mut nonce)
        .map_err(|_| Error::RandomnessUnavailable)?;
    let cipher = Aes256Gcm::new(key.as_bytes().into());
    let ciphertext = cipher
        .encrypt(
            Nonce::from_slice(&nonce),
            Payload {
                msg: snapshot,
                aad: &aad,
            },
        )
        .map_err(|_| Error::AuthenticationFailed)?;
    Ok(EncryptedEnvelope {
        metadata,
        nonce: STANDARD.encode(nonce),
        ciphertext: STANDARD.encode(ciphertext),
    })
}

/// Authenticate and decrypt against locally trusted identity. This function
/// does not enforce replay policy; use `decrypt_and_advance` for cloud ingress.
/// The returned plaintext is owned by the host, which must avoid logging and
/// erase it when no longer needed.
pub fn decrypt(
    envelope: &EncryptedEnvelope,
    key: &ContentKey,
    expected: ExpectedIdentity<'_>,
) -> Result<Vec<u8>, Error> {
    let (nonce, ciphertext) = envelope.validate_and_decode()?;
    expected.validate()?;
    if !expected.matches(&envelope.metadata) {
        return Err(Error::IdentityMismatch);
    }
    let aad = envelope.metadata.associated_data()?;
    let cipher = Aes256Gcm::new(key.as_bytes().into());
    cipher
        .decrypt(
            Nonce::from_slice(&nonce),
            Payload {
                msg: &ciphertext,
                aad: &aad,
            },
        )
        .map_err(|_| Error::AuthenticationFailed)
}

/// Monotonic revision checkpoint for one locally paired identity/generation.
/// In-memory only: the host must durably persist the accepted checkpoint with
/// its snapshot, atomically, and restore it before processing cloud content.
/// Recreating a guard at revision zero loses replay protection across sessions.
pub struct RevisionGuard {
    ledger_id: String,
    account_id: String,
    key_generation: u64,
    highest_revision: u64,
}
impl RevisionGuard {
    /// Restore from local pairing and a trusted local checkpoint. Revision
    /// zero is the explicit baseline before the first accepted snapshot.
    pub fn from_trusted_checkpoint(
        identity: ExpectedIdentity<'_>,
        highest_revision: u64,
    ) -> Result<Self, Error> {
        identity.validate()?;
        Ok(Self {
            ledger_id: identity.ledger_id.into(),
            account_id: identity.account_id.into(),
            key_generation: identity.key_generation,
            highest_revision,
        })
    }
    /// Last accepted revision, for native durable checkpoint persistence.
    pub fn highest_revision(&self) -> u64 {
        self.highest_revision
    }
    /// Explicitly authorize a strictly newer generation after the host has
    /// independently verified rotation and securely installed the new key.
    /// Never call merely because an envelope/cloud server claims a new value.
    /// The same ledger/account remain paired. Zero trusted revision means the
    /// new generation has no accepted snapshot yet; older generations never
    /// become acceptable again. Durable host state must advance atomically.
    pub fn authorize_new_generation(
        &mut self,
        next_generation: u64,
        trusted_revision: u64,
    ) -> Result<(), Error> {
        if next_generation <= self.key_generation {
            return Err(Error::GenerationTransitionRequired);
        }
        self.key_generation = next_generation;
        self.highest_revision = trusted_revision;
        Ok(())
    }

    fn advance_authenticated(&mut self, metadata: &SnapshotMetadata) -> Result<(), Error> {
        if metadata.ledger_id != self.ledger_id || metadata.account_id != self.account_id {
            return Err(Error::IdentityMismatch);
        }
        if metadata.key_generation != self.key_generation {
            return Err(Error::GenerationTransitionRequired);
        }
        if metadata.revision <= self.highest_revision {
            return Err(Error::Replay);
        }
        self.highest_revision = metadata.revision;
        Ok(())
    }
}

/// Authenticate, then enforce strictly increasing revisions for the paired
/// identity/generation. Failure leaves the guard unchanged and erases any
/// decrypted-but-rejected plaintext. On success the host owns the plaintext
/// and must atomically persist it with the updated local checkpoint.
pub fn decrypt_and_advance(
    envelope: &EncryptedEnvelope,
    key: &ContentKey,
    expected: ExpectedIdentity<'_>,
    guard: &mut RevisionGuard,
) -> Result<Vec<u8>, Error> {
    let mut plaintext = Zeroizing::new(decrypt(envelope, key, expected)?);
    guard.advance_authenticated(&envelope.metadata)?;
    Ok(std::mem::take(&mut *plaintext))
}

impl SnapshotMetadata {
    fn validate(&self) -> Result<(), Error> {
        if self.format_version != 1 {
            return Err(Error::UnsupportedFormat);
        }
        ExpectedIdentity {
            ledger_id: &self.ledger_id,
            account_id: &self.account_id,
            key_generation: self.key_generation,
        }
        .validate()?;
        if self.revision == 0 {
            return Err(Error::InvalidMetadata);
        }
        Ok(())
    }

    fn associated_data(&self) -> Result<Vec<u8>, Error> {
        // Re-serialize the typed struct, never attacker JSON text or a map.
        // Field declaration order is part of wire format 1 and must stay fixed.
        let mut aad = AAD_DOMAIN.to_vec();
        aad.extend(serde_json::to_vec(self).map_err(|_| Error::InvalidMetadata)?);
        Ok(aad)
    }
}

impl ExpectedIdentity<'_> {
    fn validate(&self) -> Result<(), Error> {
        for id in [self.ledger_id, self.account_id] {
            if id.trim().is_empty() || id.len() > MAX_ID_BYTES || id.chars().any(char::is_control) {
                return Err(Error::InvalidMetadata);
            }
        }
        if self.key_generation == 0 {
            return Err(Error::InvalidMetadata);
        }
        Ok(())
    }
    fn matches(&self, metadata: &SnapshotMetadata) -> bool {
        metadata.ledger_id == self.ledger_id
            && metadata.account_id == self.account_id
            && metadata.key_generation == self.key_generation
    }
}
