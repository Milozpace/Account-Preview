use account_app_core::*;
use base64::{engine::general_purpose::STANDARD, Engine};

const SYNTHETIC: &[u8] = br#"{"synthetic":true,"message":"technical spike"}"#;
fn key() -> ContentKey {
    ContentKey::from_bytes([0x42; 32])
}
fn metadata(revision: u64) -> SnapshotMetadata {
    SnapshotMetadata {
        format_version: 1,
        ledger_id: "test-ledger".into(),
        account_id: "test-account".into(),
        key_generation: 1,
        revision,
    }
}
fn expected() -> ExpectedIdentity<'static> {
    ExpectedIdentity {
        ledger_id: "test-ledger",
        account_id: "test-account",
        key_generation: 1,
    }
}
fn envelope(revision: u64) -> EncryptedEnvelope {
    encrypt(SYNTHETIC, &key(), metadata(revision)).unwrap()
}

#[test]
fn fixed_openssl_format_one_fixture_decrypts_with_canonical_metadata() {
    // Independently generated with Node's OpenSSL-backed createCipheriv:
    // key=42 repeated 32 times, nonce=000102030405060708090a0b,
    // plaintext=SYNTHETIC, AAD=domain + NUL + compact format-1 metadata JSON.
    // Deliberately reordered wire fields ensure typed AAD normalization.
    let fixture = br#"{"ciphertext":"fvzipN9Ymm7pbycx66kpTlex7AfFOpGIdPwCHbhg2Jw81eDBEFwCipocbXYt/Ky6sTe19hPbVdXd79ZG1p0=","nonce":"AAECAwQFBgcICQoL","metadata":{"revision":1,"key_generation":1,"account_id":"test-account","ledger_id":"test-ledger","format_version":1}}"#;
    let envelope = EncryptedEnvelope::from_json(fixture).unwrap();
    assert_eq!(decrypt(&envelope, &key(), expected()).unwrap(), SYNTHETIC);
}

#[test]
fn nist_aes256gcm_known_answer_cannot_bypass_format_one_metadata_binding() {
    use aes_gcm::{
        aead::{Aead, KeyInit},
        Aes256Gcm, Nonce,
    };
    // First NIST CAVS vector in the official aes-gcm 0.10.3 release's
    // tests/aes256gcm.rs, originating in gcmEncryptExtIV256.rsp:
    // https://csrc.nist.gov/projects/cryptographic-algorithm-validation-program/block-cipher-modes
    let vector_key: [u8; 32] = STANDARD
        .decode("tSxQWjfXjtpd008gwiVA6htYljz45b+P+oX58kklBbQ=")
        .unwrap()
        .try_into()
        .unwrap();
    let nonce = STANDARD.decode("UWwzkp31oyhP9GPX").unwrap();
    let expected_tag = STANDARD.decode("vcGsiE0zJFeh0mZPFox28A==").unwrap();
    let cipher = Aes256Gcm::new((&vector_key).into());
    assert_eq!(
        cipher
            .encrypt(Nonce::from_slice(&nonce), b"".as_ref())
            .unwrap(),
        expected_tag
    );
    assert_eq!(
        cipher
            .decrypt(Nonce::from_slice(&nonce), expected_tag.as_ref())
            .unwrap(),
        b""
    );
    // The NIST vector has no AAD: a valid raw GCM tag is insufficient for our
    // transport unless it also authenticates the required format-1 metadata.
    let envelope = EncryptedEnvelope {
        metadata: metadata(1),
        nonce: STANDARD.encode(nonce),
        ciphertext: STANDARD.encode(expected_tag),
    };
    assert_eq!(
        decrypt(&envelope, &ContentKey::from_bytes(vector_key), expected()),
        Err(Error::AuthenticationFailed)
    );
}

#[test]
fn empty_message_round_trips_with_only_an_authentication_tag() {
    let encrypted = encrypt(b"", &key(), metadata(1)).unwrap();
    assert_eq!(STANDARD.decode(&encrypted.ciphertext).unwrap().len(), 16);
    let reloaded = EncryptedEnvelope::from_json(&encrypted.to_json().unwrap()).unwrap();
    assert_eq!(decrypt(&reloaded, &key(), expected()).unwrap(), b"");
}

#[test]
fn maximum_u64_counters_round_trip_and_never_wrap_replay_state() {
    let mut meta = metadata(u64::MAX);
    meta.key_generation = u64::MAX;
    let identity = ExpectedIdentity {
        key_generation: u64::MAX,
        ..expected()
    };
    let encrypted = encrypt(SYNTHETIC, &key(), meta.clone()).unwrap();
    let reloaded = EncryptedEnvelope::from_json(&encrypted.to_json().unwrap()).unwrap();
    assert_eq!(reloaded.metadata, meta);
    let mut guard = RevisionGuard::from_trusted_checkpoint(identity, u64::MAX - 1).unwrap();
    assert_eq!(
        decrypt_and_advance(&reloaded, &key(), identity, &mut guard).unwrap(),
        SYNTHETIC
    );
    assert_eq!(guard.highest_revision(), u64::MAX);
    assert_eq!(
        decrypt_and_advance(&reloaded, &key(), identity, &mut guard),
        Err(Error::Replay)
    );
    assert_eq!(
        guard.authorize_new_generation(0, 0),
        Err(Error::GenerationTransitionRequired)
    );
    assert_eq!(
        guard.authorize_new_generation(u64::MAX, 0),
        Err(Error::GenerationTransitionRequired)
    );
    assert_eq!(guard.highest_revision(), u64::MAX);
}

#[test]
fn noncanonical_base64_padding_trailing_bits_and_whitespace_are_rejected() {
    let original = encrypt(b"", &key(), metadata(1)).unwrap();
    assert!(original.ciphertext.ends_with("=="));
    let mut bad_trailing_bits = original.ciphertext.as_bytes().to_vec();
    let index = bad_trailing_bits.len() - 3;
    let alphabet = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let symbol = alphabet
        .iter()
        .position(|byte| *byte == bad_trailing_bits[index])
        .unwrap();
    // A one-byte final group has four unused zero bits. Change an unused bit
    // without changing decoded bytes under a permissive decoder.
    assert_eq!(symbol % 16, 0);
    bad_trailing_bits[index] = alphabet[symbol + 1];
    for ciphertext in [
        original.ciphertext.trim_end_matches('=').to_owned(),
        format!("{}\n", original.ciphertext),
        String::from_utf8(bad_trailing_bits).unwrap(),
    ] {
        let mut encrypted = original.clone();
        encrypted.ciphertext = ciphertext;
        assert_eq!(
            decrypt(&encrypted, &key(), expected()),
            Err(Error::MalformedEnvelope)
        );
        assert_eq!(
            EncryptedEnvelope::from_json(&serde_json::to_vec(&encrypted).unwrap()),
            Err(Error::MalformedEnvelope)
        );
    }
}

#[test]
fn serialized_envelope_decrypts_with_a_reloaded_key_in_another_session() {
    let serialized = { envelope(1).to_json().unwrap() };
    let loaded = EncryptedEnvelope::from_json(&serialized).unwrap();
    assert_eq!(
        decrypt(&loaded, &ContentKey::from_bytes([0x42; 32]), expected()).unwrap(),
        SYNTHETIC
    );
    let json: serde_json::Value = serde_json::from_slice(&serialized).unwrap();
    assert!(json.get("key").is_none());
    assert!(!String::from_utf8(serialized)
        .unwrap()
        .contains("technical spike"));
}

#[test]
fn wrong_key_fails_authentication() {
    assert_eq!(
        decrypt(
            &envelope(1),
            &ContentKey::from_bytes([0x43; 32]),
            expected()
        ),
        Err(Error::AuthenticationFailed)
    );
}

#[test]
fn ciphertext_tampering_fails_authentication() {
    let mut encrypted = envelope(1);
    let mut bytes = STANDARD.decode(&encrypted.ciphertext).unwrap();
    bytes[0] ^= 1;
    encrypted.ciphertext = STANDARD.encode(bytes);
    assert_eq!(
        decrypt(&encrypted, &key(), expected()),
        Err(Error::AuthenticationFailed)
    );
}

#[test]
fn nonce_tampering_fails_authentication() {
    let mut encrypted = envelope(1);
    let mut bytes = STANDARD.decode(&encrypted.nonce).unwrap();
    bytes[0] ^= 1;
    encrypted.nonce = STANDARD.encode(bytes);
    assert_eq!(
        decrypt(&encrypted, &key(), expected()),
        Err(Error::AuthenticationFailed)
    );
}

#[test]
fn every_mutable_metadata_field_is_authenticated() {
    let original = envelope(1);
    let mut mutations = Vec::new();
    let mut changed = original.clone();
    changed.metadata.revision = 2;
    mutations.push(changed);
    let mut changed = original.clone();
    changed.metadata.ledger_id = "other-ledger".into();
    mutations.push(changed);
    let mut changed = original.clone();
    changed.metadata.account_id = "other-account".into();
    mutations.push(changed);
    let mut changed = original;
    changed.metadata.key_generation = 2;
    mutations.push(changed);
    for changed in mutations {
        let claimed_identity = ExpectedIdentity {
            ledger_id: &changed.metadata.ledger_id,
            account_id: &changed.metadata.account_id,
            key_generation: changed.metadata.key_generation,
        };
        assert_eq!(
            decrypt(&changed, &key(), claimed_identity),
            Err(Error::AuthenticationFailed)
        );
    }
}

#[test]
fn expected_identity_is_checked_independently_of_ciphertext_authentication() {
    for identity in [
        ExpectedIdentity {
            account_id: "other-account",
            ..expected()
        },
        ExpectedIdentity {
            ledger_id: "other-ledger",
            ..expected()
        },
        ExpectedIdentity {
            key_generation: 2,
            ..expected()
        },
    ] {
        assert_eq!(
            decrypt(&envelope(1), &key(), identity),
            Err(Error::IdentityMismatch)
        );
    }
}

#[test]
fn fresh_random_nonces_are_twelve_bytes_and_distinct() {
    let mut nonces = std::collections::HashSet::new();
    for _ in 0..256 {
        let encrypted = envelope(1);
        assert_eq!(STANDARD.decode(&encrypted.nonce).unwrap().len(), 12);
        assert!(nonces.insert(encrypted.nonce));
    }
}

#[test]
fn generated_keys_are_random_and_debug_output_is_redacted() {
    let a = ContentKey::generate().unwrap();
    let b = ContentKey::generate().unwrap();
    assert_ne!(a.as_bytes(), b.as_bytes());
    assert_ne!(a.as_bytes(), &[0; 32]);
    assert_eq!(format!("{a:?}"), "ContentKey([REDACTED])");
    assert_eq!(format!("{:?}", key()), "ContentKey([REDACTED])");
}

#[test]
fn unsupported_formats_and_zero_counters_are_rejected() {
    for version in [0, 2, u32::MAX] {
        let mut encrypted = envelope(1);
        encrypted.metadata.format_version = version;
        assert_eq!(
            decrypt(&encrypted, &key(), expected()),
            Err(Error::UnsupportedFormat)
        );
        assert_eq!(
            encrypt(SYNTHETIC, &key(), encrypted.metadata),
            Err(Error::UnsupportedFormat)
        );
    }
    for mut meta in [metadata(0), metadata(1)] {
        if meta.revision == 1 {
            meta.key_generation = 0;
        }
        assert_eq!(
            encrypt(SYNTHETIC, &key(), meta),
            Err(Error::InvalidMetadata)
        );
    }
}

#[test]
fn identity_strings_are_nonempty_bounded_and_have_no_control_characters() {
    for id in [
        "".into(),
        "  ".into(),
        "bad\nidentifier".into(),
        "x".repeat(MAX_ID_BYTES + 1),
    ] {
        let mut meta = metadata(1);
        meta.ledger_id = id.clone();
        assert_eq!(
            encrypt(SYNTHETIC, &key(), meta),
            Err(Error::InvalidMetadata)
        );
        let mut meta = metadata(1);
        meta.account_id = id;
        assert_eq!(
            encrypt(SYNTHETIC, &key(), meta),
            Err(Error::InvalidMetadata)
        );
    }
}

#[test]
fn snapshot_size_limit_is_enforced_at_the_boundary() {
    let allowed = vec![b'x'; MAX_SNAPSHOT_BYTES];
    let encrypted = encrypt(&allowed, &key(), metadata(1)).unwrap();
    assert_eq!(decrypt(&encrypted, &key(), expected()).unwrap(), allowed);
    assert!(encrypted.to_json().unwrap().len() <= MAX_ENVELOPE_BYTES);
    assert_eq!(
        encrypt(&vec![b'x'; MAX_SNAPSHOT_BYTES + 1], &key(), metadata(1)),
        Err(Error::TooLarge)
    );
}

#[test]
fn malformed_json_and_unknown_or_duplicate_fields_are_rejected() {
    for bytes in [b"not json".as_slice(), b"{}", b"null"] {
        assert_eq!(
            EncryptedEnvelope::from_json(bytes),
            Err(Error::MalformedEnvelope)
        );
    }
    let encoded = String::from_utf8(envelope(1).to_json().unwrap()).unwrap();
    let with_unknown = encoded.replacen('{', "{\"unknown\":true,", 1);
    assert_eq!(
        EncryptedEnvelope::from_json(with_unknown.as_bytes()),
        Err(Error::MalformedEnvelope)
    );
    let with_duplicate = encoded.replacen('{', "{\"nonce\":\"AAAAAAAAAAAAAAAA\",", 1);
    assert_eq!(
        EncryptedEnvelope::from_json(with_duplicate.as_bytes()),
        Err(Error::MalformedEnvelope)
    );
    assert_eq!(
        EncryptedEnvelope::from_json(format!("{encoded} trailing").as_bytes()),
        Err(Error::MalformedEnvelope)
    );
}

#[test]
fn json_is_bounded_before_parsing() {
    assert_eq!(
        EncryptedEnvelope::from_json(&vec![b' '; MAX_ENVELOPE_BYTES + 1]),
        Err(Error::TooLarge)
    );
}

#[test]
fn malformed_base64_and_wrong_nonce_or_tag_lengths_are_rejected() {
    for nonce in [
        "not base64!".into(),
        STANDARD.encode([0; 11]),
        STANDARD.encode([0; 13]),
    ] {
        let mut encrypted = envelope(1);
        encrypted.nonce = nonce;
        assert_eq!(
            decrypt(&encrypted, &key(), expected()),
            Err(Error::MalformedEnvelope)
        );
        assert_eq!(
            EncryptedEnvelope::from_json(&serde_json::to_vec(&encrypted).unwrap()),
            Err(Error::MalformedEnvelope)
        );
    }
    for ciphertext in ["not base64!".into(), STANDARD.encode([0; 15])] {
        let mut encrypted = envelope(1);
        encrypted.ciphertext = ciphertext;
        assert_eq!(
            decrypt(&encrypted, &key(), expected()),
            Err(Error::MalformedEnvelope)
        );
    }
}

#[test]
fn oversized_ciphertext_is_rejected_even_when_deserialized_directly() {
    let mut encrypted = envelope(1);
    encrypted.ciphertext = STANDARD.encode(vec![0; MAX_SNAPSHOT_BYTES + 17]);
    assert_eq!(
        decrypt(&encrypted, &key(), expected()),
        Err(Error::TooLarge)
    );
    assert_eq!(encrypted.to_json(), Err(Error::TooLarge));
}

#[test]
fn revision_guard_only_accepts_authenticated_strictly_increasing_revisions() {
    let mut guard = RevisionGuard::from_trusted_checkpoint(expected(), 4).unwrap();
    for revision in [3, 4] {
        assert_eq!(
            decrypt_and_advance(&envelope(revision), &key(), expected(), &mut guard),
            Err(Error::Replay)
        );
        assert_eq!(guard.highest_revision(), 4);
    }
    assert_eq!(
        decrypt_and_advance(&envelope(7), &key(), expected(), &mut guard).unwrap(),
        SYNTHETIC
    );
    assert_eq!(guard.highest_revision(), 7);
    assert_eq!(
        decrypt_and_advance(&envelope(7), &key(), expected(), &mut guard),
        Err(Error::Replay)
    );
    let mut tampered = envelope(8);
    tampered.metadata.revision = 100;
    assert_eq!(
        decrypt_and_advance(&tampered, &key(), expected(), &mut guard),
        Err(Error::AuthenticationFailed)
    );
    assert_eq!(guard.highest_revision(), 7);
    let mut corrupted = envelope(8);
    let mut ciphertext = STANDARD.decode(&corrupted.ciphertext).unwrap();
    ciphertext[0] ^= 1;
    corrupted.ciphertext = STANDARD.encode(ciphertext);
    assert_eq!(
        decrypt_and_advance(&corrupted, &key(), expected(), &mut guard),
        Err(Error::AuthenticationFailed)
    );
    assert_eq!(guard.highest_revision(), 7);
}

#[test]
fn checkpoint_identity_cannot_be_swapped_by_an_authenticated_envelope() {
    let mut guard = RevisionGuard::from_trusted_checkpoint(expected(), 1).unwrap();
    let mut meta = metadata(2);
    meta.account_id = "other-account".into();
    let encrypted = encrypt(SYNTHETIC, &key(), meta).unwrap();
    let other = ExpectedIdentity {
        account_id: "other-account",
        ..expected()
    };
    assert_eq!(
        decrypt_and_advance(&encrypted, &key(), other, &mut guard),
        Err(Error::IdentityMismatch)
    );
    assert_eq!(guard.highest_revision(), 1);
}

#[test]
fn cloud_cannot_authorize_a_new_key_generation_or_downgrade_it() {
    let mut guard = RevisionGuard::from_trusted_checkpoint(expected(), 10).unwrap();
    let mut meta = metadata(1);
    meta.key_generation = 2;
    let next = encrypt(SYNTHETIC, &key(), meta).unwrap();
    let next_expected = ExpectedIdentity {
        key_generation: 2,
        ..expected()
    };
    assert_eq!(
        decrypt_and_advance(&next, &key(), next_expected, &mut guard),
        Err(Error::GenerationTransitionRequired)
    );
    assert_eq!(guard.highest_revision(), 10);
    guard.authorize_new_generation(2, 0).unwrap();
    assert_eq!(
        decrypt_and_advance(&next, &key(), next_expected, &mut guard).unwrap(),
        SYNTHETIC
    );
    assert_eq!(guard.highest_revision(), 1);
    assert_eq!(
        guard.authorize_new_generation(1, 0),
        Err(Error::GenerationTransitionRequired)
    );
    assert_eq!(
        guard.authorize_new_generation(2, 0),
        Err(Error::GenerationTransitionRequired)
    );
    assert_eq!(
        decrypt_and_advance(&envelope(100), &key(), expected(), &mut guard),
        Err(Error::GenerationTransitionRequired)
    );
    assert_eq!(guard.highest_revision(), 1);
}

#[test]
fn unpaired_identity_and_zero_generation_cannot_create_a_checkpoint() {
    assert!(matches!(
        RevisionGuard::from_trusted_checkpoint(
            ExpectedIdentity {
                key_generation: 0,
                ..expected()
            },
            0
        ),
        Err(Error::InvalidMetadata)
    ));
    assert!(matches!(
        RevisionGuard::from_trusted_checkpoint(
            ExpectedIdentity {
                ledger_id: "",
                ..expected()
            },
            0
        ),
        Err(Error::InvalidMetadata)
    ));
}
