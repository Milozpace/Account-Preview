pub type Result<T> = std::result::Result<T, Error>;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("invalid vault slot")]
    InvalidSlot,
    #[error("secret exceeds the vault size limit")]
    SecretTooLarge,
    #[error("vault storage operation failed")]
    Storage,
    #[error("vault secret cannot be decrypted")]
    Decryption,
    #[error("vault encryption operation failed")]
    Encryption,
    #[error("native vault bridge failed")]
    NativeBridge,
    #[error("secure storage is unsupported on this platform")]
    UnsupportedPlatform,
}
