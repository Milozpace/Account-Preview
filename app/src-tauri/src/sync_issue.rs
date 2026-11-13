//! Public, credential-free diagnostics for native sync command boundaries.
use serde::Serialize;

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct SyncIssue {
    pub code: &'static str,
    pub stage: &'static str,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub http_status: Option<u16>,
    pub retryable: bool,
}

impl SyncIssue {
    pub fn new(
        code: &'static str,
        stage: &'static str,
        message: impl Into<String>,
        retryable: bool,
    ) -> Self {
        Self {
            code,
            stage,
            message: message.into(),
            http_status: None,
            retryable,
        }
    }

    pub fn dav(error: crate::webdav::DavError, stage: &'static str) -> Self {
        let (code, retryable) = match error.status {
            Some(401) => ("auth-unauthorized", false),
            Some(403) => ("access-forbidden", false),
            Some(404) => ("cloud-path-missing", false),
            Some(409) => ("cloud-path-conflict", false),
            Some(429) => ("rate-limited", true),
            Some(500..=599) => ("service-unavailable", true),
            Some(300..=399) => ("redirect-refused", false),
            Some(_) => ("http-error", false),
            None if error.is_timeout() => ("request-timeout", true),
            None => ("network-unavailable", true),
        };
        Self {
            code,
            stage,
            http_status: error.status,
            message: error.to_string(),
            retryable,
        }
    }
}

impl std::fmt::Display for SyncIssue {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}

impl From<SyncIssue> for String {
    fn from(issue: SyncIssue) -> Self {
        issue.message
    }
}
