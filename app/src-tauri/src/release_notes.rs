use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct ReleaseNotes {
    version: String,
    notes: String,
}

#[tauri::command]
pub async fn desktop_release_notes(app: tauri::AppHandle) -> Option<ReleaseNotes> {
    #[cfg(any(windows, target_os = "macos"))]
    {
        let profile = match crate::cloud_scope::IDENTIFIER {
            "com.account.preview" => "preview",
            "com.account.probe" => "personal",
            _ => return None,
        };
        let native_version = app.package_info().version.to_string();
        return tauri::async_runtime::spawn_blocking(move || {
            let config = crate::desktop_bridge::read_configuration().ok()?;
            installed::read(
                std::path::Path::new(&config.project_root),
                profile,
                crate::cloud_scope::IDENTIFIER,
                &native_version,
            )
        })
        .await
        .ok()
        .flatten();
    }
    #[cfg(not(any(windows, target_os = "macos")))]
    {
        let _ = app;
        None
    }
}

#[cfg(any(windows, target_os = "macos", test))]
mod installed {
    use super::ReleaseNotes;
    use serde_json::Value;
    use sha2::{Digest, Sha256};
    use std::{fs::File, io::Read, path::Path};

    fn read_local(root: &Path, path: &str) -> Option<Vec<u8>> {
        let path = root.join(path).canonicalize().ok()?;
        if !path.starts_with(root) || !path.is_file() {
            return None;
        }
        let limit = 1024 * 1024;
        let mut bytes = Vec::new();
        File::open(path)
            .ok()?
            .take(limit + 1)
            .read_to_end(&mut bytes)
            .ok()?;
        (bytes.len() <= limit as usize).then_some(bytes)
    }

    pub(super) fn read(
        root: &Path,
        profile: &str,
        identifier: &str,
        native: &str,
    ) -> Option<ReleaseNotes> {
        if !root.is_absolute() {
            return None;
        }
        let root = root.canonicalize().ok()?;
        if root
            .join(".runtime/product/pending.json")
            .symlink_metadata()
            .is_ok()
        {
            return None;
        }
        let state: Value =
            serde_json::from_slice(&read_local(&root, ".runtime/product/state.json")?).ok()?;
        if state["profile"].as_str()? != profile || state["nativeVersion"].as_str()? != native {
            return None;
        }
        let version = state["version"].as_str()?;
        if !version.as_bytes().first()?.is_ascii_digit()
            || !version
                .bytes()
                .all(|char| char.is_ascii_alphanumeric() || b".-+".contains(&char))
        {
            return None;
        }
        let raw = read_local(
            &root,
            &format!(".runtime/product/releases/{version}/release-manifest.json"),
        )?;
        if format!("{:x}", Sha256::digest(&raw)) != state["manifestSha256"].as_str()? {
            return None;
        }
        let manifest: Value = serde_json::from_slice(&raw).ok()?;
        if manifest["format"].as_u64()? != 1
            || manifest["version"].as_str()? != version
            || manifest["nativeVersion"].as_str()? != native
            || manifest["commit"].as_str()? != state["commit"].as_str()?
            || manifest["profiles"][profile]["identifier"].as_str()? != identifier
        {
            return None;
        }
        let notes = manifest["appNotes"].as_str()?.trim();
        (!notes.is_empty() && notes.len() <= 32 * 1024).then(|| ReleaseNotes {
            version: version.into(),
            notes: notes.into(),
        })
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use serde_json::json;
        use std::fs;

        #[test]
        fn notes_require_a_completed_matching_desktop_install() {
            let temp = tempfile::tempdir().unwrap();
            let base = temp.path().join(".runtime/product");
            let archived = base.join("releases/0.4.1/release-manifest.json");
            fs::create_dir_all(archived.parent().unwrap()).unwrap();
            let commit = "a".repeat(40);
            let data = serde_json::to_vec(&json!({
                "format": 1, "version": "0.4.1", "nativeVersion": "0.4.12", "commit": commit,
                "profiles": {"preview": {"identifier": "com.account.preview"}},
                "appNotes": "- 更快打开账本"
            }))
            .unwrap();
            fs::write(&archived, &data).unwrap();
            fs::write(base.join("state.json"), serde_json::to_vec(&json!({
                "profile": "preview", "version": "0.4.1", "nativeVersion": "0.4.12", "commit": commit,
                "manifestSha256": format!("{:x}", Sha256::digest(&data))
            })).unwrap()).unwrap();
            let get = |native| read(temp.path(), "preview", "com.account.preview", native);
            let release = get("0.4.12").unwrap();
            assert_eq!(release.version, "0.4.1");
            assert_eq!(release.notes, "- 更快打开账本");
            assert!(get("0.4.11").is_none());
            fs::write(base.join("pending.json"), "{}").unwrap();
            assert!(get("0.4.12").is_none());
            fs::remove_file(base.join("pending.json")).unwrap();
            fs::write(archived, "{}").unwrap();
            assert!(get("0.4.12").is_none());
        }
    }
}
