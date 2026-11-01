//! Budget settings use the configured local Python API, never source edits or cloud publishing.
use crate::desktop_bridge::{read_configuration, validate_config};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{collections::BTreeMap, io::Write, process::{Command, Stdio}};

const POOLS: [&str; 4] = ["运行账户", "随心消费", "旅行/体验", "成长/项目/耐用品"];

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all="camelCase", deny_unknown_fields)]
pub struct BudgetRequest {
    effective: String,
    amounts: BTreeMap<String, String>,
    expected_source_hash: String,
    reason: String,
}

fn month(value: &str) -> bool {
    let bytes=value.as_bytes();
    bytes.len()==7 && bytes[4]==b'-' && bytes[..4].iter().all(u8::is_ascii_digit)
        && bytes[5..].iter().all(u8::is_ascii_digit)
        && &value[..4]!="0000" && ("01"..="12").contains(&&value[5..])
}
fn hash(value: &str) -> bool { value.len()==64 && value.bytes().all(|b| b.is_ascii_hexdigit()) }
fn amounts(value: &Value) -> bool {
    value.as_object().is_some_and(|obj| obj.len()==4 && POOLS.iter().all(|key| {
        obj.get(*key).and_then(Value::as_str).is_some_and(|v| {
            let parts:Vec<_>=v.split('.').collect();
            parts.len()==2 && !parts[0].is_empty() && parts[0].len()<=14 && parts[1].len()==2
                && parts.iter().all(|p|p.bytes().all(|b|b.is_ascii_digit()))
        })
    }))
}
fn request_bytes(request: &BudgetRequest) -> Result<Vec<u8>, String> {
    let value=serde_json::to_value(request).map_err(|_|"预算参数无效。")?;
    if !month(&request.effective) || !hash(&request.expected_source_hash)
        || !amounts(&value["amounts"]) || request.reason.trim().is_empty() || request.reason.len()>2048 {
        return Err("请填写完整的四池金额、生效月份和修改说明，并重新预览。".into());
    }
    serde_json::to_vec(request).map_err(|_|"预算参数无效。".into())
}
fn validate_response(operation: &str, value: Value) -> Result<Value,String> {
    let valid=value["ok"]==true && value["sourceHash"].as_str().is_some_and(hash)
        && value["revision"].as_u64().is_some();
    let valid=valid && if operation=="budget-status" {
        value["suggestedMonth"].as_str().is_some_and(month) && value["rates"].as_array().is_some_and(|rates| !rates.is_empty() && rates.iter().all(|rate|
            rate["effective"].as_str().is_some_and(month) && amounts(&rate["amounts"])))
    } else {
        value["effective"].as_str().is_some_and(month) && amounts(&value["amounts"])
            && (value["previousAmounts"].is_null() || amounts(&value["previousAmounts"]))
            && (value["affectedThrough"].is_null() || value["affectedThrough"].as_str().is_some_and(month))
            && value["changed"].is_boolean() && value["applied"].is_boolean()
    };
    if valid { Ok(value) } else { Err("预算工具响应无效，请更新同版项目程序后重试。".into()) }
}
async fn run(operation: &'static str, request: Option<BudgetRequest>) -> Result<Value,String> {
    if !cfg!(any(windows,target_os="macos")) { return Err("四池预算设置仅支持电脑端。".into()); }
    // Validate bounded data before touching the configured project.
    let input=request.as_ref().map(request_bytes).transpose()?;
    tauri::async_runtime::spawn_blocking(move || {
        let config=validate_config(read_configuration()?)?;
        let root=std::path::Path::new(&config.project_root);
        let mut command=Command::new(config.python_executable);
        command.args(["-X","utf8"]).arg(root.join("scripts/app_desktop.py"))
            .arg(operation).arg("--project-root").arg(root).current_dir(root)
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
        #[cfg(windows)] {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut child=command.spawn().map_err(|_|"无法启动本地预算工具。")?;
        if let Some(bytes)=input {
            child.stdin.take().ok_or("无法提交预算参数。")?.write_all(&bytes).map_err(|_|"无法提交预算参数。")?;
        } else { drop(child.stdin.take()); }
        let output=child.wait_with_output().map_err(|_|"本地预算工具未完成。")?;
        if !output.status.success() {
            return Err("预算操作未完成。请核对金额、月份及项目工具版本；账本变化后需重新读取和预览。".into());
        }
        if output.stdout.len()>256*1024 { return Err("预算响应超过大小上限。".into()); }
        let value=serde_json::from_slice(&output.stdout).map_err(|_|"预算响应无效。")?;
        validate_response(operation,value)
    }).await.map_err(|_|"预算任务未完成。".to_string())?
}
#[tauri::command]
pub async fn desktop_budget_status() -> Result<Value,String> {run("budget-status",None).await}
#[tauri::command]
pub async fn desktop_budget_preview(request: BudgetRequest) -> Result<Value,String> {run("budget-preview",Some(request)).await}
#[tauri::command]
pub async fn desktop_budget_save(request: BudgetRequest) -> Result<Value,String> {run("budget-save",Some(request)).await}

#[cfg(test)] mod tests {
    use super::*;
    use serde_json::json;
    fn request() -> Value {json!({"effective":"2026-10","amounts":{"运行账户":"100.00","随心消费":"20.00","旅行/体验":"10.00","成长/项目/耐用品":"0.00"},"expectedSourceHash":"a".repeat(64),"reason":"本人确认修订预算"})}
    #[test] fn rejects_unknown_paths_negative_inexact_and_stale_hash_shapes() {
        let original=request();
        assert!(request_bytes(&serde_json::from_value(original.clone()).unwrap()).is_ok());
        let mut path=original.clone();path["projectRoot"]=json!("another project");
        assert!(serde_json::from_value::<BudgetRequest>(path).is_err());
        for (key,invalid) in [("effective",json!("2026-13")),("expectedSourceHash",json!("stale")),("reason",json!(""))] {
            let mut value=original.clone();value[key]=invalid;
            assert!(request_bytes(&serde_json::from_value(value).unwrap()).is_err());
        }
        for invalid in ["-1.00","1.001","NaN"] {
            let mut value=original.clone();value["amounts"]["运行账户"]=json!(invalid);
            assert!(request_bytes(&serde_json::from_value(value).unwrap()).is_err());
        }
    }
    #[test] fn requires_budget_reply_contract_not_an_unchecked_generic_success() {
        assert!(validate_response("budget-status",json!({"ok":true})).is_err());
        let req=request();
        let value=json!({"ok":true,"sourceHash":"b".repeat(64),"revision":2,"suggestedMonth":"2026-10","rates":[{"effective":"2026-10","amounts":req["amounts"]}]});
        assert!(validate_response("budget-status",value).is_ok());
        let saved=json!({"ok":true,"sourceHash":"b".repeat(64),"revision":3,"effective":"2026-10","amounts":req["amounts"],"previousAmounts":null,"affectedThrough":null,"changed":true,"applied":true});
        assert!(validate_response("budget-save",saved).is_ok());
    }
}
