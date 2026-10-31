# Account portable snapshot core

This standalone Rust crate provides transport encryption and replay validation.
It receives opaque bytes: it does not read Excel, calculate financial totals,
reimplement accounting rules, or establish whether a snapshot is authoritative.
The native host must supply only an already validated Excel-derived snapshot.
Tests contain synthetic messages and fixed test keys only.

## Native host integration

1. Obtain the expected ledger ID, account ID and key generation from locally
   verified pairing. Obtain the content key from the native secure-store adapter.
2. For publishing, call `encrypt(snapshot, &key, metadata)` and
   `envelope.to_json()`. The metadata revision and key generation must be positive.
3. For receiving, call `EncryptedEnvelope::from_json(&raw_bytes)`. This bounds
   input **before** serde parsing. Deserializing through another path still
   requires `decrypt` or `decrypt_and_advance`; JSON is never authentication.
4. Restore `RevisionGuard::from_trusted_checkpoint(expected, highest_revision)`
   from trusted local state and call `decrypt_and_advance`. Revision zero is only
   the explicit starting checkpoint before this generation's first snapshot.
   The helper authenticates before comparing/advancing revisions and does not
   alter the checkpoint on failure. Revision gaps are allowed; repeats and
   decreases are rejected. Use `decrypt` alone only when replay enforcement is
   deliberately outside this API, such as re-opening a trusted local snapshot.
5. Atomically persist accepted snapshot and checkpoint before making it visible
   or processing another receive. If persistence fails, discard/reload the
   in-memory guard from the durable checkpoint. A new guard starting at zero
   after every app restart would lose replay protection. This crate implements
   no persistence, synchronization, locking, key recovery, or pairing protocol.

An incoming envelope cannot authorize a generation transition. After separately
verifying local rotation approval and securely installing the new key, the host
may explicitly call `guard.authorize_new_generation(next_generation,
trusted_revision)`. The new generation must be strictly greater than the prior
one. The ledger/account identity stays fixed; older generations remain rejected.
A trusted revision of zero starts the new generation without an accepted
snapshot. Never derive this approval/checkpoint from cloud claims alone.

`ContentKey::generate()` uses the OS CSPRNG for a random 32-byte key;
`ContentKey::from_bytes` reloads native key material. The key has no serde, Clone
or Copy implementation and its Debug output is redacted. `as_bytes()` is only
for native secure-store/pairing operations. It must never appear in logs, JSON
or the envelope, and the native host must not return raw secret material to
JavaScript. `ContentKey` zeroizes its own 32-byte copy on drop. AES, AES-GCM,
GHASH and POLYVAL enable their supported `zeroize` features, which erase only
the buffers/backends for which those locked releases implement cleanup.
This does **not** guarantee complete derived-key or process-memory erasure.
Callers must erase their own imported buffers; memory moves, OS dumps and
external copies are outside these guarantees.
Decrypted `Vec<u8>` is caller-owned plaintext and must also be erased when done.
Decrypted plaintext rejected by the revision guard is zeroized internally.

The locked POLYVAL 0.6.2 x86/x64 runtime autodetection layer stores backend
state in `ManuallyDrop` without its own `Drop` implementation, so enabling
`zeroize` does not guarantee cleanup of all retained GHASH/POLYVAL derived
state. Its optional ARM PMULL backend also lacks a zeroizing `Drop`
implementation. This spike retains upstream backend selection and does not
vendor, patch, or replace the cipher to work around that limitation.
Complete derived-state cleanup across target backends is future hardening
work requiring upstream/library review and target-specific verification.

V1 protects cloud transport and stored encrypted snapshots. It does not claim
protection against an adversary reading the active application's process
memory. Displayed/decrypted plaintext necessarily exists in memory. Tests and
the enabled cleanup features are not proof that all copies, registers or
derived state have been erased.

## Format 1

The JSON object has exactly three fields: `metadata`, `nonce`, and `ciphertext`.
Metadata has `format_version` (exactly 1), `ledger_id`, `account_id`,
`key_generation`, and `revision`. IDs are exact, nonblank UTF-8 strings of at
most 128 bytes without control characters. Counters are positive Rust `u64`
values. The host should keep the envelope opaque to JavaScript to avoid loss of
integer precision above `Number.MAX_SAFE_INTEGER`.

Format 1 fixes AES-256-GCM, a fresh random 12-byte nonce per encryption, and a
16-byte authentication tag appended to ciphertext. Both byte strings use
canonical padded standard base64. There is no caller-supplied nonce API.
Random 96-bit nonces have negligible collision probability; randomness does
not mathematically guarantee uniqueness. The host must rotate keys well before
2^32 encryptions under one content key, and must never reuse deterministic
test keys in a real deployment.

Authenticated associated data is the literal bytes
`account.snapshot.aes256gcm.v1` followed by a zero byte, then compact serde JSON
of the typed metadata in this fixed field order:

```json
{"format_version":1,"ledger_id":"test-ledger","account_id":"test-account","key_generation":1,"revision":1}
```

Incoming JSON whitespace/field ordering are immaterial because metadata is
re-serialized before authentication. Changing the authenticated field order,
algorithm, domain string or metadata encoding requires a new format version.
Unknown/duplicate fields, unsupported versions, invalid counters, malformed
base64, incorrect nonce/tag lengths, and identity mismatches are rejected.
Ciphertext changes and supported metadata changes fail authentication even when
an attacker also supplies matching claimed IDs to the decrypt call.

Plaintext is bounded at 8 MiB. Base64 ciphertext is bounded before decoding and
raw envelope JSON is bounded at `MAX_ENVELOPE_BYTES` before parsing. `decrypt`
rechecks limits even if the caller directly constructs/deserializes the struct.
Errors contain no attacker inputs, identity values, plaintext or key material.
Envelope metadata remains visible on the wire; host IDs should be opaque pairing
identifiers, not personally identifying account numbers.

## Verification

```powershell
cargo test --manifest-path app/core/Cargo.toml --locked
cargo fmt --manifest-path app/core/Cargo.toml --check
cargo clippy --manifest-path app/core/Cargo.toml --locked --all-targets -- -D warnings
```

The crate uses the audited RustCrypto AES-GCM 0.10 line. Consult the upstream
[AES-GCM API/security notes](https://docs.rs/aes-gcm/0.10.3/aes_gcm/) and
[zeroize guarantees](https://docs.rs/zeroize/1.9.1/zeroize/) when changing its
security behavior. Cargo.lock pins the independently tested dependency set.
Windows compilation/tests do not establish Android build or secure-store
interoperability; those checks belong to the host integration work.
The suite includes an official NIST CAVS AES-256-GCM known-answer vector
from `gcmEncryptExtIV256.rsp` as included in the upstream aes-gcm 0.10.3
tests, and a fixed format-1 fixture independently generated through Node's
OpenSSL-backed AES-256-GCM API. Those fixtures test compatibility and metadata
binding; they do not constitute CAVP certification or memory-erasure proof.
