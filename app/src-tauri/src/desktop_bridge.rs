//! Explicitly configured Windows/macOS project bridge. No ledger/cache/identity mutation here.

use crate::ledger;
use serde::Deserialize;
use serde_json::Value;
use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
    process::{Command, Output},
};
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ProjectConfig {
    pub(super) project_root: String,
    pub(super) python_executable: String,
}

fn require_desktop(desktop: bool) -> Result<(), String> {
    if desktop {
        Ok(())
    } else {
        Err("本地账本读取与同步仅支持Windows和macOS。".into())
    }
}

fn config_path() -> Result<PathBuf, String> {
    // Explicit override lets integration tests avoid the user's real config.
    if let Some(path) = std::env::var_os("ACCOUNT_DESKTOP_CONFIG") {
        let path = PathBuf::from(path);
        return if path.is_absolute() {
            Ok(path)
        } else {
            Err("本地项目配置路径必须是绝对路径。".into())
        };
    }
    #[cfg(target_os = "macos")]
    let base = std::env::var_os("HOME")
        .map(|path| PathBuf::from(path).join("Library/Application Support"));
    #[cfg(not(target_os = "macos"))]
    let base = std::env::var_os("LOCALAPPDATA").map(PathBuf::from);
    base.map(|path| path.join("AccountPreview/project.json"))
        .ok_or_else(|| "无法定位本机项目配置目录。".into())
}

pub(super) fn validate_config(config: ProjectConfig) -> Result<ProjectConfig, String> {
    let root = PathBuf::from(&config.project_root);
    let python = PathBuf::from(&config.python_executable);
    if !root.is_absolute() || !python.is_absolute() {
        return Err("主项目与Python均须使用完整路径。".into());
    }
    let root = root.canonicalize().map_err(|_| "找不到主项目目录。")?;
    if !root.is_dir()
        || root.join(".git").is_file()
        || root.components().any(|part| {
            let part = part.as_os_str().to_string_lossy();
            part.eq_ignore_ascii_case("worktrees") || part.eq_ignore_ascii_case("archive")
        })
    {
        return Err("拒绝使用开发工作树或历史存档作为正式账本来源。".into());
    }
    // Only the active SQLite source is accepted; migration workbooks are irrelevant.
    let data = root.join("data");
    let source_bytes =
        fs::read(data.join("source.json")).map_err(|_| "找不到本地SQLite来源配置。")?;
    if source_bytes.len() > 4096 {
        return Err("本地SQLite来源配置无效。".into());
    }
    let source: Value =
        serde_json::from_slice(&source_bytes).map_err(|_| "本地SQLite来源配置无效。")?;
    let database = data.join("account.sqlite3");
    if source["kind"] != "sqlite"
        || source["path"] != "account.sqlite3"
        || !database.is_file()
        || database
            .canonicalize()
            .ok()
            .and_then(|p| p.parent().map(Path::to_path_buf))
            != Some(data.canonicalize().map_err(|_| "本地数据目录无效。")?)
        || data
            .canonicalize()
            .ok()
            .and_then(|p| p.parent().map(Path::to_path_buf))
            != Some(root.clone())
    {
        return Err("仅允许读取本项目data目录的当前SQLite账本。".into());
    }
    for name in [
        "account.py",
        "app_export.py",
        "dashboard_data.py",
        "app_desktop.py",
    ] {
        let tool = root.join("scripts").join(name);
        if !tool.is_file()
            || tool
                .canonicalize()
                .ok()
                .and_then(|p| p.parent().map(Path::to_path_buf))
                != Some(root.join("scripts"))
        {
            return Err("主项目工具不完整，请检查配置。".into());
        }
    }
    if !python.is_file() {
        return Err("找不到配置的Python解释器，请填写完整路径。".into());
    }
    // Keep the configured interpreter path: resolving a macOS/Linux venv
    // symlink here would launch the base Python without its environment.
    Ok(ProjectConfig {
        project_root: root.to_string_lossy().into_owned(),
        python_executable: python.to_string_lossy().into_owned(),
    })
}

fn read_config_at(path: &Path) -> Result<ProjectConfig, String> {
    match fs::read(path) {
        Ok(bytes) if bytes.len() <= 16 * 1024 => serde_json::from_slice(&bytes)
            .map_err(|_| "本地项目配置格式无效，请重新保存设置。".into()),
        Ok(_) => Err("本地项目配置超过大小上限。".into()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            Err("尚未配置本地项目。".into())
        }
        Err(_) => Err("无法读取本地项目配置。".into()),
    }
}

pub(super) fn read_configuration() -> Result<ProjectConfig, String> {
    let path = config_path()?;
    read_config_at(&path)
}

fn save_config_at(path: &Path, config: ProjectConfig) -> Result<ProjectConfig, String> {
    let config = validate_config(config)?;
    if path.exists() {
        let previous = read_config_at(path)?;
        let previous_root = PathBuf::from(&previous.project_root)
            .canonicalize()
            .map_err(|_| "原项目目录无法验证，请使用独立试用环境。")?;
        if previous_root != PathBuf::from(&config.project_root) {
            return Err("此试用环境已绑定其他项目，请为其他账本使用独立账户环境。".into());
        }
    }
    let parent = path.parent().ok_or("本地项目配置路径无效。")?;
    fs::create_dir_all(parent).map_err(|_| "无法建立本地项目配置目录。")?;
    let mut draft =
        tempfile::NamedTempFile::new_in(parent).map_err(|_| "无法写入本地项目配置。")?;
    draft
        .write_all(&serde_json::to_vec(&config).map_err(|_| "配置序列化失败。")?)
        .map_err(|_| "无法写入本地项目配置。")?;
    draft
        .as_file()
        .sync_all()
        .map_err(|_| "无法保存本地项目配置。")?;
    draft.persist(path).map_err(|_| "无法替换本地项目配置。")?;
    Ok(config)
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct LoadResult {
    snapshot: Option<Value>,
    source_changed: bool,
    month_changed: bool,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RefreshResult {
    snapshot: Value,
    app_publish: PublicationResult,
    source_changed: bool,
    month_changed: bool,
    #[serde(default)]
    warning: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PublicationResult {
    ok: Option<bool>,
    published: bool,
    #[serde(default)]
    skipped: bool,
    #[serde(default)]
    error: Option<String>,
    #[serde(default)]
    reason: Option<String>,
}

fn validate_snapshot(value: &Value) -> Result<(), String> {
    let bytes = serde_json::to_vec(value).map_err(|_| "本地快照响应无效。")?;
    let snapshot = ledger::parse_payload(&bytes)?;
    // Local provenance is checked by the helper against the canonical source
    // SHA-256 and Beijing calendar month, both before and after the read.
    if snapshot.source_hash.len() != 64
        || !snapshot
            .source_hash
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit())
    {
        return Err("本地快照来源哈希无效。".into());
    }
    Ok(())
}

fn validate_load(value: Value) -> Result<Value, String> {
    let result: LoadResult =
        serde_json::from_value(value.clone()).map_err(|_| "本地快照响应格式无效。")?;
    if let Some(snapshot) = result.snapshot {
        if result.source_changed || result.month_changed {
            return Err("本地快照已过期，请同步展示。".into());
        }
        validate_snapshot(&snapshot)?;
    }
    Ok(value)
}

fn validate_refresh(value: Value) -> Result<Value, String> {
    let result: RefreshResult =
        serde_json::from_value(value.clone()).map_err(|_| "本地同步响应格式无效。")?;
    validate_snapshot(&result.snapshot)?;
    // Unlike a read-only load, refresh retains its independently validated
    // prepublication snapshot and exact cloud outcome even when stale. The
    // frontend consumes these explicit flags/warning to avoid claiming latest.
    let _freshness_changed = result.source_changed || result.month_changed;
    if result
        .warning
        .as_ref()
        .is_some_and(|warning| warning.trim().is_empty() || warning.len() > 1024)
    {
        return Err("本地同步状态提示无效。".into());
    }
    let publication = result.app_publish;
    if publication.published != (publication.ok == Some(true))
        || (publication.skipped && (publication.published || publication.ok.is_some()))
        || publication.error.as_ref().is_some_and(|v| v.len() > 1024)
        || publication.reason.as_ref().is_some_and(|v| v.len() > 1024)
    {
        return Err("App发布结果无效。".into());
    }
    Ok(value)
}

fn bridge_with(
    config: ProjectConfig,
    operation: &str,
    execute: impl FnOnce(Command) -> Result<Output, String>,
) -> Result<Value, String> {
    let config = validate_config(config)?;
    let root = PathBuf::from(&config.project_root);
    let mut command = Command::new(&config.python_executable);
    command
        .args(["-X", "utf8"])
        .arg(root.join("scripts/app_desktop.py"))
        .arg(operation)
        .arg("--project-root")
        .arg(&root)
        .current_dir(&root);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    let output = execute(command)?;
    if !output.status.success() {
        return Err("本地展示读取或同步失败；请检查本地项目配置及SQLite账本状态后重试。".into());
    }
    if output.stdout.len() > account_app_core::MAX_SNAPSHOT_BYTES + 16 * 1024 {
        return Err("本地快照响应超过大小上限。".into());
    }
    let value = serde_json::from_slice(&output.stdout).map_err(|_| "本地快照响应不是有效JSON。")?;
    match operation {
        "load" => validate_load(value),
        "refresh" => validate_refresh(value),
        _ => Err("不支持的本地快照操作。".into()),
    }
}

async fn run_bridge(operation: &'static str) -> Result<Value, String> {
    require_desktop(cfg!(any(windows, target_os = "macos")))?; // Must precede configuration/filesystem/process access.
    tauri::async_runtime::spawn_blocking(move || {
        let config = read_configuration()?;
        bridge_with(config, operation, |mut command| {
            command
                .output()
                .map_err(|_| "无法启动配置的Python解释器。".into())
        })
    })
    .await
    .map_err(|_| "本地快照任务未完成。".to_string())?
}

#[tauri::command]
pub async fn desktop_project_config() -> Result<ProjectConfig, String> {
    require_desktop(cfg!(any(windows, target_os = "macos")))?;
    tauri::async_runtime::spawn_blocking(read_configuration)
        .await
        .map_err(|_| "读取项目配置未完成。".to_string())?
}

#[tauri::command]
pub async fn desktop_configure_project(
    project_root: String,
    python_executable: String,
) -> Result<ProjectConfig, String> {
    require_desktop(cfg!(any(windows, target_os = "macos")))?;
    tauri::async_runtime::spawn_blocking(move || {
        save_config_at(
            &config_path()?,
            ProjectConfig {
                project_root,
                python_executable,
            },
        )
    })
    .await
    .map_err(|_| "保存项目配置未完成。".to_string())?
}

#[tauri::command]
pub async fn desktop_load_snapshot() -> Result<Value, String> {
    run_bridge("load").await
}

#[tauri::command]
pub async fn desktop_refresh_snapshot() -> Result<Value, String> {
    run_bridge("refresh").await
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::{fs, path::PathBuf};

    fn fixture() -> serde_json::Value {
        serde_json::from_str(include_str!("../../../tests/fixtures/app-v2-snapshot.json")).unwrap()
    }

    fn fake_project() -> (tempfile::TempDir, ProjectConfig) {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("primary project");
        fs::create_dir_all(root.join("scripts")).unwrap();
        for file in [
            "account.py",
            "app_export.py",
            "dashboard_data.py",
            "app_desktop.py",
        ] {
            fs::write(root.join("scripts").join(file), "# synthetic").unwrap();
        }
        fs::create_dir(root.join("data")).unwrap();
        fs::write(
            root.join("data/source.json"),
            r#"{"kind":"sqlite","path":"account.sqlite3"}"#,
        )
        .unwrap();
        fs::write(
            root.join("data/account.sqlite3"),
            b"SQLite format 3\0synthetic",
        )
        .unwrap();
        let python = temp.path().join("synthetic python3");
        fs::write(&python, "not executable").unwrap();
        let config = ProjectConfig {
            project_root: root.to_string_lossy().into_owned(),
            python_executable: python.to_string_lossy().into_owned(),
        };
        (temp, config)
    }

    #[test]
    fn mobile_rejects_before_any_path_or_process_work() {
        assert!(require_desktop(false).is_err());
        assert!(require_desktop(true).is_ok());
    }

    #[test]
    fn configuration_roundtrip_uses_only_explicit_temporary_file() {
        let (temp, config) = fake_project();
        let path = temp.path().join("config/project.json");
        let saved = save_config_at(&path, config).unwrap();
        assert_eq!(read_config_at(&path).unwrap(), saved);
    }

    #[test]
    fn existing_configuration_cannot_rebind_to_a_different_project() {
        let (temp, first) = fake_project();
        let (_other, second) = fake_project();
        let path = temp.path().join("project.json");
        save_config_at(&path, first).unwrap();
        let before = fs::read(&path).unwrap();
        assert!(save_config_at(&path, second).is_err());
        assert_eq!(fs::read(path).unwrap(), before);
    }

    #[test]
    fn rejects_legacy_workbook_source_and_external_database() {
        let (_temp, config) = fake_project();
        let source = PathBuf::from(&config.project_root).join("data/source.json");
        for invalid in [
            r#"{"kind":"excel","path":"backup.xlsx"}"#,
            r#"{"kind":"sqlite","path":"../outside.sqlite3"}"#,
        ] {
            fs::write(&source, invalid).unwrap();
            assert!(validate_config(config.clone()).is_err());
        }
    }

    #[test]
    fn worktree_configuration_is_rejected_and_old_config_preserved() {
        let (temp, config) = fake_project();
        let path = temp.path().join("config/project.json");
        save_config_at(&path, config.clone()).unwrap();
        let before = fs::read(&path).unwrap();
        fs::write(
            PathBuf::from(&config.project_root).join(".git"),
            "gitdir: worktree",
        )
        .unwrap();
        assert!(save_config_at(&path, config).is_err());
        assert_eq!(fs::read(&path).unwrap(), before);
    }

    #[cfg(unix)]
    #[test]
    fn virtualenv_python_symlink_is_preserved() {
        use std::os::unix::fs::symlink;
        let (temp, mut config) = fake_project();
        let link = temp.path().join("venv-python3");
        symlink(&config.python_executable, &link).unwrap();
        config.python_executable = link.to_string_lossy().into_owned();
        assert_eq!(
            validate_config(config.clone()).unwrap().python_executable,
            config.python_executable
        );
    }

    #[test]
    fn invalid_python_and_relative_project_are_rejected() {
        let (_temp, mut config) = fake_project();
        config.python_executable = "missing.exe".into();
        assert!(validate_config(config.clone()).is_err());
        config.project_root = ".".into();
        assert!(validate_config(config).is_err());
    }

    #[test]
    fn missing_explicit_config_cannot_fall_back_to_live_project() {
        let temp = tempfile::tempdir().unwrap();
        assert!(read_config_at(&temp.path().join("missing/project.json")).is_err());
    }

    #[test]
    fn missing_snapshot_and_stale_flags_are_preserved() {
        let value = json!({"snapshot":null,"sourceChanged":true,"monthChanged":false});
        assert_eq!(validate_load(value.clone()).unwrap(), value);
    }

    #[test]
    fn native_contract_is_checked_before_returning_python_result() {
        let mut broken = fixture();
        broken["months"][0]["summary"]["totalCents"] = json!(999);
        assert!(
            validate_load(json!({"snapshot":broken,"sourceChanged":false,"monthChanged":false}))
                .is_err()
        );
        assert!(
            validate_load(json!({"snapshot":fixture(),"sourceChanged":true,"monthChanged":false}))
                .is_err()
        );
        assert!(
            validate_load(json!({"snapshot":fixture(),"sourceChanged":false,"monthChanged":false}))
                .is_ok()
        );
    }

    #[test]
    fn publication_failure_does_not_hide_valid_local_snapshot() {
        let value = json!({"snapshot":fixture(),"appPublish":{"ok":false,"published":false,"error":"synthetic failure"},"sourceChanged":false,"monthChanged":false});
        assert_eq!(validate_refresh(value.clone()).unwrap(), value);
        assert!(
            validate_refresh(
                json!({"snapshot":fixture(),"appPublish":{"ok":false,"published":true},"sourceChanged":false,"monthChanged":false})
            )
            .is_err()
        );
    }

    #[test]
    fn refresh_retains_publication_result_with_stale_or_unverified_snapshot() {
        for publication in [
            json!({"ok":true,"published":true}),
            json!({"ok":false,"published":false,"error":"synthetic failure"}),
            json!({"ok":null,"published":false,"skipped":true,"reason":"synthetic disabled"}),
        ] {
            for (source, month, warning) in [
                (false, false, None),
                (true, false, None),
                (false, true, None),
                (true, true, None),
                (false, false, Some("再次核验未完成，请重新同步展示。")),
            ] {
                let mut value = json!({"snapshot":fixture(),"appPublish":publication,"sourceChanged":source,"monthChanged":month});
                if let Some(warning) = warning {
                    value["warning"] = json!(warning);
                }
                assert_eq!(validate_refresh(value.clone()).unwrap(), value);
            }
        }
    }

    #[test]
    fn refresh_requires_explicit_freshness_flags_and_bounded_warning() {
        assert!(
            validate_refresh(
                json!({"snapshot":fixture(),"appPublish":{"ok":true,"published":true}})
            )
            .is_err()
        );
        assert!(validate_refresh(json!({"snapshot":fixture(),"appPublish":{"ok":true,"published":true},"sourceChanged":false,"monthChanged":false,"warning":"x".repeat(1025)})).is_err());
    }

    #[test]
    fn subprocess_failure_does_not_touch_existing_snapshot() {
        let (_temp, config) = fake_project();
        let root = PathBuf::from(&config.project_root);
        fs::create_dir_all(root.join(".runtime/ledger")).unwrap();
        let snapshot = root.join(".runtime/ledger/snapshot-v2.json");
        fs::write(&snapshot, "synthetic old snapshot").unwrap();
        let result = bridge_with(config, "refresh", |command| {
            let argv: Vec<_> = command
                .get_args()
                .map(|v| v.to_string_lossy().into_owned())
                .collect();
            assert_eq!(argv[0], "-X");
            assert_eq!(argv[1], "utf8");
            assert_eq!(argv[3], "refresh");
            assert_eq!(argv[4], "--project-root");
            assert_eq!(
                command.get_current_dir(),
                Some(root.as_path().canonicalize().unwrap().as_path())
            );
            Err("synthetic process failure".into())
        });
        assert!(result.is_err());
        assert_eq!(
            fs::read_to_string(snapshot).unwrap(),
            "synthetic old snapshot"
        );
    }
}
