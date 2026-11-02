//! Exact, fixed installation identities shared by build.rs and unit tests.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct InstallProfile {
    pub identifier: &'static str,
    pub remote_root: &'static str,
    pub preview: bool,
}

pub fn resolve(identifier: &str) -> Result<InstallProfile, &'static str> {
    match identifier {
        "com.account.preview" => Ok(InstallProfile {
            identifier: "com.account.preview",
            remote_root: "Account-Preview",
            preview: true,
        }),
        "com.account.probe" => Ok(InstallProfile {
            identifier: "com.account.probe",
            remote_root: "Account",
            preview: false,
        }),
        _ => Err("Unsupported Account installation identifier; cloud scope is not inferred."),
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn cloud_namespace_profiles_are_exact_and_disjoint() {
        let preview = super::resolve("com.account.preview").unwrap();
        let personal = super::resolve("com.account.probe").unwrap();
        assert_eq!(preview.remote_root, "Account-Preview");
        assert_eq!(personal.remote_root, "Account");
        assert_ne!(preview.remote_root, personal.remote_root);
        for unknown in [
            "",
            "com.account.preview.other",
            "com.account.preview/../probe",
            "Account Preview",
        ] {
            assert!(super::resolve(unknown).is_err());
        }
    }
}
