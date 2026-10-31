use std::path::Path;

/// Hash the resolved namespace path without disclosing local project/user names.
/// Resolve the nearest existing ancestor, so first use and later use agree even
/// when the application support directory itself is a symlink.
pub(crate) fn keychain_service(root: &Path) -> crate::Result<String> {
    use sha2::{Digest, Sha256};
    if !root.is_absolute()
        || root
            .components()
            .any(|c| matches!(c, std::path::Component::ParentDir))
    {
        return Err(crate::Error::Storage);
    }
    let mut ancestor = root.to_path_buf();
    let mut suffix = Vec::new();
    while !ancestor.exists() {
        suffix.push(
            ancestor
                .file_name()
                .ok_or(crate::Error::Storage)?
                .to_os_string(),
        );
        ancestor.pop();
    }
    let mut resolved = ancestor.canonicalize().map_err(|_| crate::Error::Storage)?;
    for part in suffix.into_iter().rev() {
        resolved.push(part);
    }
    let digest = Sha256::digest(resolved.as_os_str().as_encoded_bytes());
    Ok(format!("com.account.preview.vault.v1.{digest:x}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn service_is_private_project_scoped_and_stable_before_directory_creation() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("project-identity/vault");
        let before = keychain_service(&root).unwrap();
        std::fs::create_dir_all(&root).unwrap();
        assert_eq!(before, keychain_service(&root).unwrap());
        assert_ne!(
            before,
            keychain_service(&temp.path().join("other/vault")).unwrap()
        );
        assert!(!before.contains("project-identity"));
        assert!(keychain_service(Path::new("relative/vault")).is_err());
    }
}
