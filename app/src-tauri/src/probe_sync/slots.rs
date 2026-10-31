//! Two fixed ciphertext slots. PUT only the non-current slot; retries keep
//! durable target intent and exact ciphertext. Legacy files are read-only.
use super::*;
use std::io::Write;

pub(super) const FILES: [&str; 2] = ["snapshot-a.enc", "snapshot-b.enc"];
const INTENT_FILE: &str = "publish-slot-intent.json";

enum Read {
    Missing,
    Valid(EncryptedEnvelope),
    Invalid { malformed: bool, message: String },
    Unavailable(String),
}

fn read(client: &WebDavClient, file: &str, key: &ContentKey) -> Read {
    let bytes = match client.get(file) {
        Ok(Some(bytes)) => bytes,
        Ok(None) => return Read::Missing,
        Err(error) => {
            let step = match file {
                "snapshot-a.enc" => "槽位 A 下载",
                "snapshot-b.enc" => "槽位 B 下载",
                _ => "旧快照下载",
            };
            return Read::Unavailable(error.at_step(step, Some("GET")).into());
        }
    };
    let envelope = match EncryptedEnvelope::from_json(&bytes) {
        Ok(envelope) => envelope,
        Err(_) => {
            return Read::Invalid {
                malformed: true,
                message: "云端文件格式异常，已保留本地缓存。".into(),
            };
        }
    };
    let plaintext = match decrypt(&envelope, key, identity()) {
        Ok(plaintext) => Zeroizing::new(plaintext),
        Err(_) => {
            return Read::Invalid {
                malformed: false,
                message: "云端文件不属于本配对或无法通过认证，已保留本地缓存。".into(),
            };
        }
    };
    if parse_payload(&plaintext).is_err() {
        return Read::Invalid {
            malformed: false,
            message: PAYLOAD_ERROR.into(),
        };
    }
    Read::Valid(envelope)
}

fn highest(reads: &[Read]) -> Result<Option<(usize, &EncryptedEnvelope)>, String> {
    let mut best: Option<(usize, &EncryptedEnvelope)> = None;
    for (index, read) in reads.iter().enumerate() {
        if let Read::Valid(envelope) = read {
            if let Some((_, previous)) = best {
                // Check all valid equal-version pairs, including old slots.
                if reads.iter().any(|r| matches!(r, Read::Valid(other) if other.metadata.revision == envelope.metadata.revision && other != envelope)) {
                    return Err(step_err(STEP_CONTRACT, "云端同版本内容不一致，已保留待发内容与本地缓存，未改动云端正式文件。".into()));
                }
                if previous.metadata.revision >= envelope.metadata.revision {
                    continue;
                }
            }
            best = Some((index, envelope));
        }
    }
    Ok(best)
}

#[derive(serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct Intent {
    target: usize,
    envelope: EncryptedEnvelope,
    // First publication may have no independent cloud copy yet. A durable
    // observation that all paths were absent authorizes retry of that target.
    bootstrap: bool,
}

fn load_intent(dir: &Path, envelope: &EncryptedEnvelope) -> Result<Option<Intent>, String> {
    let bytes = match fs::read(dir.join(INTENT_FILE)) {
        Ok(bytes) => bytes,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err("无法读取待发槽位，请保留现场检查。".into()),
    };
    if bytes.len() > account_app_core::MAX_ENVELOPE_BYTES + 1024 {
        return Err("待发槽位状态损坏，请保留现场检查。".into());
    }
    let intent: Intent =
        serde_json::from_slice(&bytes).map_err(|_| "待发槽位状态损坏，请保留现场检查。")?;
    if intent.target >= FILES.len() {
        return Err("待发槽位状态损坏，请保留现场检查。".into());
    }
    if intent.envelope == *envelope {
        return Ok(Some(intent));
    }
    if intent.envelope.metadata.revision <= load_publish_state(dir)? {
        return Ok(None);
    }
    Err("待发槽位与密文不一致，请保留现场检查。".into())
}

fn save_intent(
    dir: &Path,
    target: usize,
    envelope: &EncryptedEnvelope,
    bootstrap: bool,
) -> Result<(), String> {
    fs::create_dir_all(dir).map_err(|_| "无法建立发布状态目录。")?;
    let bytes = serde_json::to_vec(&Intent {
        target,
        envelope: envelope.clone(),
        bootstrap,
    })
    .map_err(|_| "无法编码待发槽位。")?;
    let mut temp = tempfile::NamedTempFile::new_in(dir).map_err(|_| "无法准备待发槽位。")?;
    temp.write_all(&bytes).map_err(|_| "待发槽位写入未完成。")?;
    temp.as_file()
        .sync_all()
        .map_err(|_| "待发槽位写入未完成。")?;
    temp.persist(dir.join(INTENT_FILE))
        .map_err(|_| "待发槽位替换未完成。")?;
    Ok(())
}

pub(super) fn publish<V: SecretVault + Sync, T: Transaction>(
    vault: &V,
    dir: &Path,
    tx: &T,
) -> Result<PublishOutcome, String> {
    let (key, client, envelope, intent) = tx.run(|| {
        let key = ensure_content_key(vault, &dir.join(CACHE_FILE))
            .map_err(|e| step_err(STEP_KEY_CACHE, e))?;
        let client = build_client(vault).map_err(|e| step_err(STEP_CONFIG, e))?;
        let envelope = prepare_pending(dir, &key).map_err(|e| step_err(STEP_PENDING, e))?;
        let intent = load_intent(dir, &envelope).map_err(|e| step_err(STEP_PENDING, e))?;
        Ok((key, client, envelope, intent))
    })?;
    client
        .ensure_collection()
        .map_err(|e| e.at_step(STEP_DIRECTORY, Some("MKCOL/PROPFIND")))?;
    let mut reads: Vec<_> = FILES.iter().map(|file| read(&client, file, &key)).collect();
    // Bootstrap from the previous protocol, including a retry of the first
    // slot upload which left a partial file. Never overwrite the legacy file.
    if highest(&reads)?.is_none() {
        reads.push(read(&client, REMOTE_FILE, &key));
    }
    for read in &reads {
        if let Read::Unavailable(message) = read {
            return Err(message.clone());
        }
        if let Read::Invalid {
            malformed: false,
            message,
        } = read
        {
            return Err(step_err(STEP_OWNERSHIP, message.clone()));
        }
    }
    let best = highest(&reads)?;
    let intended_target = intent.as_ref().map(|intent| intent.target);
    let bootstrap = intent.as_ref().is_some_and(|intent| intent.bootstrap)
        || reads.iter().all(|r| matches!(r, Read::Missing));
    let matching = reads
        .iter()
        .take(2)
        .position(|r| matches!(r, Read::Valid(e) if e == &envelope));
    if let Some((_, current)) = best {
        if current.metadata.revision > envelope.metadata.revision {
            return Err(step_err(
                STEP_CONTRACT,
                "云端已有更高版本，已保留待发内容，未改动云端正式文件。".into(),
            ));
        }
        if current.metadata.revision == envelope.metadata.revision && current != &envelope {
            return Err(step_err(
                STEP_CONTRACT,
                "云端同版本内容与待发内容不一致，已保留待发内容，未改动云端正式文件。".into(),
            ));
        }
    }
    let target = if let Some(matching) = matching {
        if intended_target.is_some_and(|target| target != matching) {
            return Err(step_err(
                STEP_PENDING,
                "云端待发槽位与本机意图冲突。".into(),
            ));
        }
        matching
    } else if let Some(target) = intended_target {
        if best.is_some_and(|(index, _)| index == target) {
            return Err(step_err(
                STEP_PENDING,
                "待发目标已成为最新有效槽位，已停止覆盖。".into(),
            ));
        }
        target
    } else {
        match best {
            Some((0, _)) => 1,
            _ => 0,
        }
    };
    // Only a durable known target may be repaired, with an independent
    // authenticated copy still available (slot or the frozen legacy file).
    for (index, read) in reads.iter().enumerate() {
        if let Read::Invalid {
            malformed: true,
            message,
        } = read
            && (intended_target != Some(index) || index != target || (best.is_none() && !bootstrap))
        {
            return Err(step_err(STEP_OWNERSHIP, message.clone()));
        }
    }
    tx.run(|| save_intent(dir, target, &envelope, bootstrap))
        .map_err(|e| step_err(STEP_PENDING, e))?;
    let bytes = envelope.to_json().map_err(|_| "待发密文无效。")?;
    if matching.is_none() {
        client
            .put(FILES[target], &bytes)
            .map_err(|e| e.at_step("非最新槽位上传", Some("PUT")))?;
    }
    let confirmed = client
        .get(FILES[target])
        .map_err(|e| e.at_step(STEP_OFFICIAL_READBACK, Some("GET")))?;
    if confirmed.as_deref() != Some(bytes.as_slice()) {
        return Err(step_err(
            STEP_OFFICIAL_READBACK,
            "发布槽位回读不一致，已保留缓存与待发内容，可重试。".into(),
        ));
    }
    tx.run(|| {
        persist_cache(&dir.join(CACHE_FILE), &envelope)
            .map_err(|e| step_err(STEP_LOCAL_SAVE, e))?;
        save_publish_state(dir, envelope.metadata.revision)
            .map_err(|e| step_err(STEP_LOCAL_SAVE, e))?;
        let _ = fs::remove_file(dir.join(PENDING_FILE));
        let _ = fs::remove_file(dir.join(INTENT_FILE));
        Ok(())
    })?;
    Ok(PublishOutcome {
        revision: envelope.metadata.revision,
        method: "put-slot",
    })
}

pub(super) fn download(
    client: &WebDavClient,
    key: &ContentKey,
) -> Result<(Option<EncryptedEnvelope>, Option<String>), String> {
    let mut reads: Vec<_> = FILES.iter().map(|file| read(client, file, key)).collect();
    if highest(&reads)?.is_none() {
        reads.push(read(client, REMOTE_FILE, key));
    }
    let best = highest(&reads)?.map(|(_, e)| e.clone());
    let warning = reads.iter().find_map(|r| match r {
        Read::Unavailable(message) | Read::Invalid { message, .. } => {
            Some(format!("部分云端文件检查未完成：{message}可重试。"))
        }
        _ => None,
    });
    if best.is_none()
        && let Some(message) = warning
    {
        return Err(step_err(STEP_CONTRACT, message));
    }
    Ok((best, warning))
}
