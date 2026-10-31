pub(crate) const MAX_SECRET_BYTES: usize = 512 * 1024;

pub(crate) fn validate_slot(slot: &str) -> crate::Result<()> {
    if slot.is_empty()
        || slot.len() > 64
        || !slot
            .bytes()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-')
    {
        return Err(crate::Error::InvalidSlot);
    }
    Ok(())
}
pub(crate) fn validate_secret(secret: &[u8]) -> crate::Result<()> {
    if secret.len() > MAX_SECRET_BYTES {
        return Err(crate::Error::SecretTooLarge);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn slot_rejects_traversal_unicode_and_invalid_lengths() {
        for slot in [
            "",
            "../token",
            "a/b",
            "a\\b",
            "A",
            "a.b",
            "a b",
            "令牌",
            &"a".repeat(65),
        ] {
            assert!(validate_slot(slot).is_err(), "accepted invalid slot");
        }
        for slot in ["a", "a-01", &"a".repeat(64)] {
            assert!(validate_slot(slot).is_ok());
        }
    }
    #[test]
    fn secret_size_limit_is_inclusive() {
        assert!(validate_secret(&[]).is_ok());
        assert!(validate_secret(&vec![0; MAX_SECRET_BYTES]).is_ok());
        assert!(validate_secret(&vec![0; MAX_SECRET_BYTES + 1]).is_err());
    }
}
