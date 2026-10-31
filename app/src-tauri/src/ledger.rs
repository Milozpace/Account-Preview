//! Business ledger identity and the App V2 snapshot contract.
//!
//! The live business identity is independent from the synthetic probe: its
//! own secure-storage slots, cache files, and cloud directory. This module
//! validates the exported `account-ledger-view` payload (schemaVersion 2);
//! it never re-derives budget, rollover, or classification. Contract:
//! `docs/App真实快照契约V2.md`.
#![forbid(unsafe_code)]

use account_app_core::{
    ContentKey, EncryptedEnvelope, ExpectedIdentity, MAX_ENVELOPE_BYTES, decrypt,
};
use serde::Deserialize;
use std::{fs, io::Write, path::Path};

pub const LEDGER_REMOTE_DIRECTORY: &str = "Account/ledger";
pub const LEDGER_REMOTE_FILE_A: &str = "snapshot-a.enc";
pub const LEDGER_REMOTE_FILE_B: &str = "snapshot-b.enc";
pub const LEDGER_FILES: [&str; 2] = [LEDGER_REMOTE_FILE_A, LEDGER_REMOTE_FILE_B];

pub const LEDGER_IDENTITY_SLOT: &str = "ledger-identity-v1";
pub const LEDGER_KEY_SLOT: &str = "ledger-content-key-v1";
/// Real cloud publishing stays disabled until the main review explicitly
/// enables it through this slot (native gate, not a hidden UI button).
pub const LEDGER_PUBLISH_ENABLED_SLOT: &str = "ledger-publish-enabled-v1";

pub const LEDGER_CACHE_FILE: &str = "ledger-cache.enc";
pub const LEDGER_PENDING_FILE: &str = "ledger-pending.enc";
pub const LEDGER_STATE_FILE: &str = "ledger-publish-state.json";
pub const LEDGER_INTENT_FILE: &str = "ledger-slot-intent.json";

pub const SAMPLE_PLAINTEXT: &[u8] = b"account-ledger-pairing-sample-v1";
pub const PAIRING_KIND: &str = "account-ledger-pairing-v1";
pub const MAX_PAIRING_BYTES: usize = 4096;

const SNAPSHOT_KIND: &str = "account-ledger-view";
const COVERAGE_KIND: &str = "recent-months";
const MAX_TITLE_BYTES: usize = 72 * 4;

/// Locally trusted business identity. Never derived from cloud content.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct LedgerIdentity {
    pub ledger_id: String,
    pub account_id: String,
    pub key_generation: u64,
}

impl LedgerIdentity {
    pub fn expected(&self) -> ExpectedIdentity<'_> {
        ExpectedIdentity {
            ledger_id: &self.ledger_id,
            account_id: &self.account_id,
            key_generation: self.key_generation,
        }
    }

    pub fn validate(&self) -> Result<(), String> {
        for id in [&self.ledger_id, &self.account_id] {
            if id.trim().is_empty()
                || id.len() > account_app_core::MAX_ID_BYTES
                || id.chars().any(char::is_control)
            {
                return Err("正式账本身份字段无效。".into());
            }
        }
        if self.key_generation == 0 {
            return Err("正式账本密钥代次无效。".into());
        }
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// App V2 payload contract. Deserialization enforces the field whitelist;
// validation enforces reconciliation and range rules before any cache write.
// ---------------------------------------------------------------------------

// Fields are declared so `deny_unknown_fields` validates presence even when
// this module does not read them; the frontend consumes the validated JSON.
#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct LedgerSnapshot {
    pub schema_version: u32,
    pub snapshot_kind: String,
    pub source_hash: String,
    pub calendar_month: String,
    pub generated_at: String,
    pub source_last_recorded_at: Option<String>,
    pub ledger_count: u64,
    pub coverage: Coverage,
    pub months: Vec<LedgerMonth>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct Coverage {
    pub kind: String,
    pub months: Vec<String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct LedgerMonth {
    pub month: String,
    pub last_recorded_at: Option<String>,
    pub mechanism_active: bool,
    pub summary: MonthSummary,
    pub pools: Vec<Pool>,
    pub daily: Vec<DailyEntry>,
    pub categories: Vec<Category>,
    pub records: Vec<LedgerRecord>,
    pub structural_records: Vec<LedgerRecord>,
    pub top_ids: Vec<String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct MonthSummary {
    pub personal_cents: i64,
    pub structural_cents: i64,
    pub total_cents: i64,
    pub unknown_cents: i64,
    pub monthly_budget_cents: i64,
    pub opening_budget_cents: i64,
    pub available_cents: i64,
    pub overrun_cents: i64,
    pub safety_cents: i64,
}

/// One funding pool. All cents fields are mandatory integers in the real V1
/// native result (`required_cents` fails loudly on missing formulas), so a
/// missing formula is never smuggled through as a legal null.
#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct Pool {
    pub id: String,
    pub name: String,
    pub budget_cents: i64,
    pub carry_cents: i64,
    pub opening_cents: i64,
    pub spent_cents: i64,
    pub ending_cents: i64,
    pub available_cents: i64,
    pub overrun_cents: i64,
    /// Null exactly when `openingCents <= 0`; otherwise a finite number.
    pub usage_ratio: Option<f64>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct Category {
    pub name: String,
    pub amount_cents: i64,
    pub count: u64,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct DailyEntry {
    pub date: String,
    pub amount_cents: i64,
    pub operation_cumulative_cents: i64,
    pub reference_cents: i64,
    pub count: u64,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct LedgerRecord {
    pub id: String,
    pub time: String,
    pub date: String,
    pub title: String,
    pub category: String,
    pub pool: String,
    pub scope: String,
    pub amount_cents: i64,
}

/// Every cents value reaches JavaScript display code, so the whole chain
/// (records, daily, pools, categories, summaries, aggregates) must stay
/// inside the JS safe-integer range.
const MAX_SAFE_CENTS: i64 = 9_007_199_254_740_991;

fn check_cents(value: i64, label: &str) -> Result<(), String> {
    if value.unsigned_abs() > MAX_SAFE_CENTS as u64 {
        return Err(format!("{label}超出安全整数范围，已拒绝。"));
    }
    Ok(())
}

fn checked_sum(total: i64, amount: i64, label: &str) -> Result<i64, String> {
    let sum = total
        .checked_add(amount)
        .ok_or(format!("{label}累加溢出，已拒绝。"))?;
    check_cents(sum, label)?;
    Ok(sum)
}

fn valid_month(month: &str) -> bool {
    let Some((year, number)) = month.split_once('-') else {
        return false;
    };
    let Ok(year) = year.parse::<i32>() else {
        return false;
    };
    let Ok(number) = number.parse::<u32>() else {
        return false;
    };
    (1..=12).contains(&number) && year > 0 && month.len() == 7
}

fn previous_month(month: &str) -> Option<String> {
    let (year, number) = month.split_once('-')?;
    let year = year.parse::<i32>().ok()?;
    let number = number.parse::<u32>().ok()?;
    if !(1..=12).contains(&number) {
        return None;
    }
    let (new_year, new_number) = if number == 1 {
        (year - 1, 12)
    } else {
        (year, number - 1)
    };
    Some(format!("{new_year:04}-{new_number:02}"))
}

fn days_in_month(month: &str) -> Option<usize> {
    let (year, number) = month.split_once('-')?;
    let year = year.parse::<i32>().ok()?;
    let number = number.parse::<u32>().ok()?;
    if !(1..=12).contains(&number) {
        return None;
    }
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let days = match number {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if leap => 29,
        2 => 28,
        _ => return None,
    };
    Some(days)
}

fn require_timestamp(value: &str, label: &str) -> Result<(), String> {
    if value.len() < 20 || !value.ends_with("+08:00") {
        return Err(format!("{label}必须是+08:00时区时间，已拒绝。"));
    }
    Ok(())
}

fn month_day_valid(month: &str, date: &str) -> bool {
    let Some((year, month_number)) = month.split_once('-') else {
        return false;
    };
    let Some((date_year, rest)) = date.split_once('-') else {
        return false;
    };
    let Some((date_month, date_day)) = rest.split_once('-') else {
        return false;
    };
    let Some(days) = (|| {
        let year: u32 = year.parse().ok()?;
        let month_number: u32 = month_number.parse().ok()?;
        let date_year: u32 = date_year.parse().ok()?;
        let date_month: u32 = date_month.parse().ok()?;
        let date_day: u32 = date_day.parse().ok()?;
        if !(1..=12).contains(&month_number) || !(1..=12).contains(&date_month) {
            return None;
        }
        if date_year != year || date_month != month_number || date_day == 0 {
            return None;
        }
        let leap =
            year.is_multiple_of(4) && (!year.is_multiple_of(100) || year.is_multiple_of(400));
        let total = match month_number {
            1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
            4 | 6 | 9 | 11 => 30,
            2 if leap => 29,
            2 => 28,
            _ => return None,
        };
        (date_day <= total).then_some(total)
    })() else {
        return false;
    };
    let _ = days;
    true
}

fn check_record(
    record: &LedgerRecord,
    month: &str,
    seen: &mut std::collections::HashSet<String>,
) -> Result<i64, String> {
    if record.id.is_empty() || !seen.insert(record.id.clone()) {
        return Err(format!("{month} 记录ID缺失或重复，已拒绝。"));
    }
    if record.date.len() != 10
        || !record.date.starts_with(month)
        || !month_day_valid(month, &record.date)
    {
        return Err(format!("{month} 记录日期越界，已拒绝。"));
    }
    // The record instant must belong to its own ledger date.
    if record.time.len() < 10
        || record.time.get(..10) != Some(record.date.as_str())
        || !record.time.ends_with("+08:00")
    {
        return Err(format!("{month} 记录时间与日期不一致或缺少时区，已拒绝。"));
    }
    if record.title.is_empty() || record.title.len() > MAX_TITLE_BYTES {
        return Err(format!("{month} 记录标题缺失或超长，已拒绝。"));
    }
    if record.category.is_empty() {
        return Err(format!("{month} 记录分类缺失，已拒绝。"));
    }
    if !matches!(
        record.pool.as_str(),
        "operation" | "free" | "travel" | "growth" | "unknown"
    ) {
        return Err(format!("{month} 记录资金池越界，已拒绝。"));
    }
    if !matches!(record.scope.as_str(), "PERSONAL" | "PROJECT" | "STRUCTURAL") {
        return Err(format!("{month} 记录口径越界，已拒绝。"));
    }
    check_cents(record.amount_cents, &format!("{month} 记录金额"))?;
    Ok(record.amount_cents)
}

const FIXED_POOL_IDS: [&str; 4] = ["operation", "free", "travel", "growth"];

fn check_pools(
    pools: &[Pool],
    month: &str,
    pool_used: &std::collections::HashMap<String, i64>,
) -> Result<(), String> {
    if pools.len() != FIXED_POOL_IDS.len() {
        return Err(format!("{month} 资金池必须为四池结构，已拒绝。"));
    }
    let mut seen = std::collections::HashSet::new();
    for pool in pools {
        if !FIXED_POOL_IDS.contains(&pool.id.as_str()) || !seen.insert(pool.id.as_str()) {
            return Err(format!("{month} 资金池ID无效或重复，已拒绝。"));
        }
        if pool.name.is_empty() {
            return Err(format!("{month} 资金池名称缺失，已拒绝。"));
        }
        for (label, value) in [
            ("budgetCents", pool.budget_cents),
            ("carryCents", pool.carry_cents),
            ("openingCents", pool.opening_cents),
            ("spentCents", pool.spent_cents),
            ("endingCents", pool.ending_cents),
            ("availableCents", pool.available_cents),
            ("overrunCents", pool.overrun_cents),
        ] {
            check_cents(value, &format!("{month} 资金池{label}"))?;
        }
        match pool.usage_ratio {
            Some(ratio) => {
                if pool.opening_cents <= 0 || !ratio.is_finite() {
                    return Err(format!("{month} 资金池使用率无效，已拒绝。"));
                }
            }
            None if pool.opening_cents > 0 => {
                return Err(format!("{month} 资金池使用率缺失，已拒绝。"));
            }
            None => {}
        }
        let expected = pool_used.get(&pool.id).copied().unwrap_or(0);
        if pool.spent_cents != expected {
            return Err(format!("{month} 资金池已用与流水对账失败，已拒绝。"));
        }
    }
    Ok(())
}

fn check_summary(
    summary: &MonthSummary,
    month: &str,
    personal: i64,
    structural: i64,
    unknown: i64,
    pools: &[Pool],
) -> Result<(), String> {
    for (label, value) in [
        ("personalCents", summary.personal_cents),
        ("structuralCents", summary.structural_cents),
        ("totalCents", summary.total_cents),
        ("unknownCents", summary.unknown_cents),
        ("monthlyBudgetCents", summary.monthly_budget_cents),
        ("openingBudgetCents", summary.opening_budget_cents),
        ("availableCents", summary.available_cents),
        ("overrunCents", summary.overrun_cents),
        ("safetyCents", summary.safety_cents),
    ] {
        check_cents(value, &format!("{month} 汇总{label}"))?;
    }
    let total = checked_sum(personal, structural, &format!("{month} 总净额"))?;
    if summary.total_cents != total {
        return Err(format!("{month} 总净额与分项对账失败，已拒绝。"));
    }
    if summary.unknown_cents != unknown {
        return Err(format!("{month} 待归类净额与明细对账失败，已拒绝。"));
    }
    let pool_sum = |pick: fn(&Pool) -> i64| -> Result<i64, String> {
        pools.iter().try_fold(0i64, |acc, pool| {
            checked_sum(acc, pick(pool), &format!("{month} 四池累加"))
        })
    };
    if summary.monthly_budget_cents != pool_sum(|p| p.budget_cents)? {
        return Err(format!("{month} 月预算与四池对账失败，已拒绝。"));
    }
    if summary.opening_budget_cents != pool_sum(|p| p.opening_cents)? {
        return Err(format!("{month} 期初预算与四池对账失败，已拒绝。"));
    }
    if summary.available_cents != pool_sum(|p| p.available_cents)? {
        return Err(format!("{month} 可用额度与四池对账失败，已拒绝。"));
    }
    if summary.overrun_cents != pool_sum(|p| p.overrun_cents)? {
        return Err(format!("{month} 超支额与四池对账失败，已拒绝。"));
    }
    Ok(())
}

fn check_daily(
    daily: &[DailyEntry],
    month: &str,
    by_date: &std::collections::HashMap<String, i64>,
    by_count: &std::collections::HashMap<String, u64>,
) -> Result<(), String> {
    let Some(days) = days_in_month(month) else {
        return Err(format!("{month} 月份格式无效，已拒绝。"));
    };
    if daily.len() != days {
        return Err(format!("{month} 日消费必须覆盖完整月历，已拒绝。"));
    }
    let mut seen = std::collections::HashSet::new();
    for entry in daily {
        if entry.date.len() != 10
            || !entry.date.starts_with(month)
            || !month_day_valid(month, &entry.date)
            || !seen.insert(entry.date.clone())
        {
            return Err(format!("{month} 日消费日期越界或重复，已拒绝。"));
        }
        for (label, value) in [
            ("amountCents", entry.amount_cents),
            ("operationCumulativeCents", entry.operation_cumulative_cents),
            ("referenceCents", entry.reference_cents),
        ] {
            check_cents(value, &format!("{month} 日消费{label}"))?;
        }
        if entry.count > MAX_SAFE_CENTS as u64 {
            return Err(format!("{month} 日消费笔数超出范围，已拒绝。"));
        }
        if by_date.get(&entry.date).copied().unwrap_or(0) != entry.amount_cents
            || by_count.get(&entry.date).copied().unwrap_or(0) != entry.count
        {
            return Err(format!("{month} 日消费与明细对账失败，已拒绝。"));
        }
    }
    Ok(())
}

fn check_categories(
    categories: &[Category],
    month: &str,
    personal: i64,
    by_category: &std::collections::HashMap<String, i64>,
    by_category_count: &std::collections::HashMap<String, u64>,
) -> Result<(), String> {
    let mut seen = std::collections::HashSet::new();
    let mut total = 0i64;
    for category in categories {
        if category.name.is_empty() || !seen.insert(category.name.clone()) {
            return Err(format!("{month} 分类名称缺失或重复，已拒绝。"));
        }
        check_cents(category.amount_cents, &format!("{month} 分类金额"))?;
        if category.count > MAX_SAFE_CENTS as u64 {
            return Err(format!("{month} 分类笔数超出范围，已拒绝。"));
        }
        if by_category.get(&category.name).copied().unwrap_or(0) != category.amount_cents
            || by_category_count.get(&category.name).copied().unwrap_or(0) != category.count
        {
            return Err(format!("{month} 分类消费与明细对账失败，已拒绝。"));
        }
        total = checked_sum(total, category.amount_cents, &format!("{month} 分类合计"))?;
    }
    if total != personal {
        return Err(format!("{month} 分类合计与个人净额对账失败，已拒绝。"));
    }
    Ok(())
}

/// Validate the full App V2 contract. Returns the parsed snapshot; the
/// plaintext is rejected before any cache write when anything is off.
pub fn parse_payload(plaintext: &[u8]) -> Result<LedgerSnapshot, String> {
    if plaintext.len() > account_app_core::MAX_SNAPSHOT_BYTES {
        return Err("账单快照超过大小上限，已拒绝。".into());
    }
    let snapshot: LedgerSnapshot = serde_json::from_slice(plaintext)
        .map_err(|_| "账单快照不是受支持的 App V2 契约，已拒绝。".to_string())?;
    if snapshot.schema_version != 2 || snapshot.snapshot_kind != SNAPSHOT_KIND {
        return Err("账单快照不是受支持的 App V2 契约，已拒绝。".into());
    }
    if snapshot.coverage.kind != COVERAGE_KIND {
        return Err("账单快照范围声明无效，已拒绝。".into());
    }
    if snapshot.months.is_empty() {
        return Err("账单快照必须包含至少一个月份。".into());
    }
    if snapshot.ledger_count > MAX_SAFE_CENTS as u64 {
        return Err("ledgerCount 超出安全范围，已拒绝。".into());
    }
    if !valid_month(&snapshot.calendar_month) {
        return Err("日历月份无效，已拒绝。".into());
    }
    require_timestamp(&snapshot.generated_at, "生成时间")?;
    if let Some(value) = &snapshot.source_last_recorded_at {
        require_timestamp(value, "来源最新记录时间")?;
    }
    let month_list: Vec<String> = snapshot.months.iter().map(|m| m.month.clone()).collect();
    if snapshot.coverage.months != month_list {
        return Err("coverage 月份与实际 months 不一致，已拒绝。".into());
    }
    // The frozen recent-months window is exactly [calendar month, previous month].
    let expected_window = [
        snapshot.calendar_month.clone(),
        previous_month(&snapshot.calendar_month).ok_or("日历月份无效，已拒绝。")?,
    ];
    if month_list != expected_window {
        return Err("快照必须正好覆盖当月与上月，已拒绝。".into());
    }
    let mut seen = std::collections::HashSet::new();
    let mut exported = 0usize;
    for view in &snapshot.months {
        let month = &view.month;
        if !valid_month(month) {
            return Err("月份格式无效，已拒绝。".into());
        }
        if let Some(value) = &view.last_recorded_at {
            require_timestamp(value, &format!("{month} 最新记录时间"))?;
        }
        let mut personal = 0i64;
        for record in &view.records {
            personal = checked_sum(
                personal,
                check_record(record, month, &mut seen)?,
                &format!("{month} 个人净额"),
            )?;
        }
        let mut structural = 0i64;
        for record in &view.structural_records {
            structural = checked_sum(
                structural,
                check_record(record, month, &mut seen)?,
                &format!("{month} 结构性净额"),
            )?;
        }
        exported += view.records.len() + view.structural_records.len();
        if view.summary.personal_cents != personal {
            return Err(format!("{month} 个人净额与明细对账失败，已拒绝。"));
        }
        if view.summary.structural_cents != structural {
            return Err(format!("{month} 结构性净额与明细对账失败，已拒绝。"));
        }
        let mut by_date: std::collections::HashMap<String, i64> = std::collections::HashMap::new();
        let mut by_count: std::collections::HashMap<String, u64> = std::collections::HashMap::new();
        let mut by_category: std::collections::HashMap<String, i64> =
            std::collections::HashMap::new();
        let mut by_category_count: std::collections::HashMap<String, u64> =
            std::collections::HashMap::new();
        let mut pool_used: std::collections::HashMap<String, i64> =
            std::collections::HashMap::new();
        for record in &view.records {
            let entry = by_date.entry(record.date.clone()).or_insert(0);
            *entry = checked_sum(*entry, record.amount_cents, &format!("{month} 日合计"))?;
            *by_count.entry(record.date.clone()).or_insert(0) += 1;
            let category = by_category.entry(record.category.clone()).or_insert(0);
            *category = checked_sum(*category, record.amount_cents, &format!("{month} 分类合计"))?;
            *by_category_count
                .entry(record.category.clone())
                .or_insert(0) += 1;
            let pool = pool_used.entry(record.pool.clone()).or_insert(0);
            *pool = checked_sum(*pool, record.amount_cents, &format!("{month} 资金池累加"))?;
        }
        let unknown = pool_used.get("unknown").copied().unwrap_or(0);
        check_summary(
            &view.summary,
            month,
            personal,
            structural,
            unknown,
            &view.pools,
        )?;
        check_pools(&view.pools, month, &pool_used)?;
        check_daily(&view.daily, month, &by_date, &by_count)?;
        check_categories(
            &view.categories,
            month,
            personal,
            &by_category,
            &by_category_count,
        )?;
        if view.top_ids.len() > 10
            || view
                .top_ids
                .iter()
                .any(|id| !view.records.iter().any(|r| &r.id == id))
            || view
                .top_ids
                .iter()
                .collect::<std::collections::HashSet<_>>()
                .len()
                != view.top_ids.len()
        {
            return Err(format!("{month} 重点记录引用无效，已拒绝。"));
        }
    }
    if snapshot.ledger_count < exported as u64 {
        return Err("ledgerCount 不能小于已导出明细笔数，已拒绝。".into());
    }
    Ok(snapshot)
}

/// Content signature: canonical JSON without `generatedAt`. Retries and
/// re-exports of the same business content must never manufacture a new
/// publish version; only this signature decides "same content".
pub fn content_signature(plaintext: &[u8]) -> Result<Vec<u8>, String> {
    let mut value: serde_json::Value = serde_json::from_slice(plaintext)
        .map_err(|_| "账单快照不是有效 JSON，已拒绝。".to_string())?;
    value
        .as_object_mut()
        .ok_or("账单快照必须是对象。")?
        .remove("generatedAt");
    serde_json::to_vec(&value).map_err(|_| "无法规范化账单快照。".to_string())
}

// ---------------------------------------------------------------------------
// Ledger-scoped encrypted cache primitives (independent from probe state).
// ---------------------------------------------------------------------------

pub fn load_identity<V: crate::probe_cache::SecretVault>(
    vault: &V,
) -> Result<Option<LedgerIdentity>, String> {
    let Some(bytes) = vault
        .load_secret(LEDGER_IDENTITY_SLOT)?
        .map(zeroize::Zeroizing::new)
    else {
        return Ok(None);
    };
    let identity: LedgerIdentity = serde_json::from_slice(&bytes)
        .map_err(|_| "正式账本身份损坏，请保留现场检查。".to_string())?;
    identity.validate()?;
    Ok(Some(identity))
}

pub fn persist_identity<V: crate::probe_cache::SecretVault>(
    vault: &V,
    identity: &LedgerIdentity,
) -> Result<(), String> {
    identity.validate()?;
    let bytes = serde_json::to_vec(identity).map_err(|_| "无法编码正式账本身份。")?;
    vault.store_secret(LEDGER_IDENTITY_SLOT, &bytes)?;
    let restored = load_identity(vault)?.ok_or("安全存储未保留正式账本身份。")?;
    if restored != *identity {
        return Err("正式账本身份回验失败。".into());
    }
    Ok(())
}

pub fn load_key<V: crate::probe_cache::SecretVault>(
    vault: &V,
) -> Result<Option<ContentKey>, String> {
    crate::probe_cache::load_key(&crate::probe_cache::SlotVault {
        vault,
        slot: LEDGER_KEY_SLOT,
    })
}

pub fn persist_key<V: crate::probe_cache::SecretVault>(
    vault: &V,
    key: &ContentKey,
) -> Result<(), String> {
    crate::probe_cache::persist_key(
        &crate::probe_cache::SlotVault {
            vault,
            slot: LEDGER_KEY_SLOT,
        },
        key,
    )
}

pub fn read_cache(
    path: &Path,
    key: &ContentKey,
    identity: &LedgerIdentity,
) -> Result<Option<EncryptedEnvelope>, String> {
    let metadata = match fs::metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err("无法读取正式账本缓存。".into()),
    };
    if metadata.len() > MAX_ENVELOPE_BYTES as u64 {
        return Err("正式账本缓存过大，拒绝读取。".into());
    }
    let bytes = fs::read(path).map_err(|_| "无法读取正式账本缓存。".to_string())?;
    let envelope =
        EncryptedEnvelope::from_json(&bytes).map_err(|_| "正式账本缓存格式损坏。".to_string())?;
    let _plaintext = zeroize::Zeroizing::new(
        decrypt(&envelope, key, identity.expected())
            .map_err(|_| "正式账本缓存无法认证，请保留现场检查。".to_string())?,
    );
    Ok(Some(envelope))
}

pub fn persist_cache(path: &Path, envelope: &EncryptedEnvelope) -> Result<(), String> {
    let parent = path.parent().ok_or("缓存位置无效。")?;
    fs::create_dir_all(parent).map_err(|_| "无法建立缓存目录。".to_string())?;
    let bytes = envelope
        .to_json()
        .map_err(|_| "拒绝保存无效缓存。".to_string())?;
    let mut pending = tempfile::NamedTempFile::new_in(parent).map_err(|_| "无法准备缓存文件。")?;
    pending.write_all(&bytes).map_err(|_| "缓存写入未完成。")?;
    pending
        .as_file()
        .sync_all()
        .map_err(|_| "缓存写入未完成。")?;
    pending
        .persist(path)
        .map_err(|_| "缓存替换未完成，旧数据保留。")?;
    Ok(())
}

#[cfg(test)]
pub mod tests {
    use super::*;
    use crate::probe_cache::SecretVault;
    use account_app_core::{SnapshotMetadata, encrypt};
    use std::collections::BTreeMap;
    use std::sync::{Arc, Mutex as StdMutex};

    #[derive(Clone, Default)]
    pub struct MemorySlots {
        pub slots: Arc<StdMutex<BTreeMap<String, Vec<u8>>>>,
    }
    impl SecretVault for MemorySlots {
        fn load_secret(&self, slot: &str) -> Result<Option<Vec<u8>>, String> {
            Ok(self.slots.lock().unwrap().get(slot).cloned())
        }
        fn store_secret(&self, slot: &str, value: &[u8]) -> Result<(), String> {
            self.slots
                .lock()
                .unwrap()
                .insert(slot.to_string(), value.to_vec());
            Ok(())
        }
        fn delete_secret(&self, slot: &str) -> Result<(), String> {
            self.slots.lock().unwrap().remove(slot);
            Ok(())
        }
    }

    /// The canonical shared fixture (same file the Python and TypeScript
    /// suites load); full two-month snapshot with four pools and complete
    /// daily calendars, valid by construction.
    pub const SHARED_FIXTURE: &str = include_str!("../../../tests/fixtures/app-v2-snapshot.json");

    pub fn valid_payload() -> Vec<u8> {
        SHARED_FIXTURE.as_bytes().to_vec()
    }

    fn mutate_payload(change: impl FnOnce(&mut serde_json::Value)) -> Vec<u8> {
        let mut value: serde_json::Value = serde_json::from_slice(&valid_payload()).unwrap();
        change(&mut value);
        value.to_string().into_bytes()
    }

    #[test]
    fn valid_v2_payload_and_negative_amounts_pass() {
        let snapshot = parse_payload(&valid_payload()).unwrap();
        assert_eq!(snapshot.months.len(), 2);
        assert_eq!(snapshot.months[1].records[0].amount_cents, -500);
    }

    #[test]
    fn shared_fixture_full_contract_passes() {
        let snapshot = parse_payload(SHARED_FIXTURE.as_bytes()).unwrap();
        assert_eq!(snapshot.months[0].pools.len(), 4);
        assert_eq!(snapshot.months[0].daily.len(), 31);
        assert_eq!(snapshot.months[1].daily.len(), 30);
        assert_eq!(snapshot.months[0].summary.total_cents, -537000);
    }

    #[test]
    fn unsafe_integer_beyond_js_range_rejects() {
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][0]["records"][0]["amountCents"] = serde_json::json!(2i64.pow(60))
            ))
            .is_err()
        );
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][0]["daily"][0]["referenceCents"] = serde_json::json!(2i64.pow(60))
            ))
            .is_err()
        );
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][0]["summary"]["safetyCents"] = serde_json::json!(2i64.pow(60))
            ))
            .is_err()
        );
    }

    #[test]
    fn bad_pool_entry_rejects() {
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][0]["pools"][0]["id"] = serde_json::json!("safety")
            ))
            .is_err()
        );
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][0]["pools"][0]["spentCents"] = serde_json::json!(1)
            ))
            .is_err()
        );
        assert!(
            parse_payload(&mutate_payload(|v| {
                v["months"][0]["pools"].as_array_mut().unwrap().pop();
            }))
            .is_err()
        );
    }

    #[test]
    fn summary_cross_checks_reject() {
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][0]["summary"]["totalCents"] = serde_json::json!(999)
            ))
            .is_err()
        );
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][0]["summary"]["unknownCents"] = serde_json::json!(1)
            ))
            .is_err()
        );
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][0]["summary"]["monthlyBudgetCents"] = serde_json::json!(1)
            ))
            .is_err()
        );
    }

    #[test]
    fn phantom_missing_and_duplicate_daily_reject() {
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][0]["daily"][2]["amountCents"] = serde_json::json!(777)
            ))
            .is_err()
        );
        assert!(
            parse_payload(&mutate_payload(|v| {
                v["months"][0]["daily"].as_array_mut().unwrap().pop();
            }))
            .is_err()
        );
        assert!(
            parse_payload(&mutate_payload(|v| {
                let first = v["months"][0]["daily"][0].clone();
                v["months"][0]["daily"][1] = first;
            }))
            .is_err()
        );
    }

    #[test]
    fn time_date_mismatch_rejects() {
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][0]["records"][0]["time"] = "2026-10-03T12:00:00+08:00".into()
            ))
            .is_err()
        );
    }

    #[test]
    fn months_window_must_be_calendar_and_previous() {
        assert!(
            parse_payload(&mutate_payload(|v| {
                v["months"].as_array_mut().unwrap().reverse();
                v["coverage"]["months"] = v["months"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|m| m["month"].clone())
                    .collect();
            }))
            .is_err()
        );
        assert!(
            parse_payload(&mutate_payload(|v| {
                v["months"].as_array_mut().unwrap().pop();
            }))
            .is_err()
        );
        assert!(
            parse_payload(&mutate_payload(|v| {
                let october = v["months"][0].clone();
                v["months"][1] = october;
                v["coverage"]["months"] = serde_json::json!(["2026-10", "2026-10"]);
            }))
            .is_err()
        );
    }

    #[test]
    fn category_mismatch_rejects() {
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][0]["categories"][0]["amountCents"] = serde_json::json!(1)
            ))
            .is_err()
        );
        assert!(
            parse_payload(&mutate_payload(|v| {
                v["months"][0]["categories"]
                    .as_array_mut()
                    .unwrap()
                    .push(serde_json::json!({"name": "幽灵分类", "amountCents": 100, "count": 0}));
            }))
            .is_err()
        );
    }

    #[test]
    fn invalid_top_ids_reject() {
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][0]["topIds"] = serde_json::json!(["tx_not_present"])
            ))
            .is_err()
        );
    }

    #[test]
    fn null_usage_ratio_with_positive_opening_rejects() {
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][0]["pools"][0]["usageRatio"] = serde_json::Value::Null
            ))
            .is_err()
        );
    }

    #[test]
    fn calendar_month_format_rejects() {
        assert!(
            parse_payload(&mutate_payload(
                |v| v["calendarMonth"] = serde_json::json!("2026-13")
            ))
            .is_err()
        );
    }

    #[test]
    fn v1_or_unknown_kind_is_rejected() {
        assert!(
            parse_payload(&mutate_payload(
                |v| v["schemaVersion"] = serde_json::json!(1)
            ))
            .is_err()
        );
        assert!(
            parse_payload(&mutate_payload(
                |v| v["snapshotKind"] = serde_json::json!("other")
            ))
            .is_err()
        );
    }

    #[test]
    fn coverage_mismatch_and_missing_months_are_rejected() {
        assert!(
            parse_payload(&mutate_payload(
                |v| v["coverage"]["months"] = serde_json::json!(["2026-10"])
            ))
            .is_err()
        );
        assert!(parse_payload(&mutate_payload(|v| v["months"] = serde_json::json!([]))).is_err());
    }

    #[test]
    fn duplicate_ids_reject_whole_payload() {
        let broken = mutate_payload(|v| v["months"][1]["records"][0]["id"] = "tx_oct_lunch".into());
        assert!(parse_payload(&broken).is_err());
    }

    #[test]
    fn cross_month_date_and_bad_time_reject() {
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][1]["records"][0]["date"] = "2026-10-02".into()
            ))
            .is_err()
        );
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][0]["records"][0]["time"] = "2026-10-01T12:00:00Z".into()
            ))
            .is_err()
        );
    }

    #[test]
    fn summary_and_daily_reconciliation_reject() {
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][0]["summary"]["personalCents"] = serde_json::json!(999)
            ))
            .is_err()
        );
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][0]["daily"][0]["amountCents"] = serde_json::json!(5)
            ))
            .is_err()
        );
    }

    #[test]
    fn non_integer_amounts_reject() {
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][0]["records"][0]["amountCents"] = serde_json::json!(10.5)
            ))
            .is_err()
        );
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][0]["records"][0]["amountCents"] = "1000".into()
            ))
            .is_err()
        );
    }

    #[test]
    fn extra_record_field_rejects_whitelist() {
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][0]["records"][0]["orderNumber"] = "AB123456789012".into()
            ))
            .is_err()
        );
    }

    #[test]
    fn ledger_count_below_exported_rejects() {
        assert!(
            parse_payload(&mutate_payload(|v| v["ledgerCount"] = serde_json::json!(2))).is_err()
        );
    }

    #[test]
    fn invalid_pool_scope_reject() {
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][0]["records"][0]["pool"] = "safety".into()
            ))
            .is_err()
        );
        assert!(
            parse_payload(&mutate_payload(
                |v| v["months"][0]["records"][0]["scope"] = "EXCLUDED".into()
            ))
            .is_err()
        );
    }

    #[test]
    fn content_signature_ignores_generated_at_only() {
        let first = content_signature(&valid_payload()).unwrap();
        let second = content_signature(&mutate_payload(|v| {
            v["generatedAt"] = serde_json::json!("2026-10-15T13:00:00+08:00")
        }))
        .unwrap();
        assert_eq!(first, second);
        let changed = content_signature(&mutate_payload(|v| {
            v["months"][0]["records"][0]["amountCents"] = serde_json::json!(2000)
        }))
        .unwrap();
        assert_ne!(first, changed);
    }

    #[test]
    fn identity_roundtrip_and_validation() {
        let vault = MemorySlots::default();
        assert!(load_identity(&vault).unwrap().is_none());
        let identity = LedgerIdentity {
            ledger_id: "ledger-abc".into(),
            account_id: "account-xyz".into(),
            key_generation: 1,
        };
        persist_identity(&vault, &identity).unwrap();
        assert_eq!(load_identity(&vault).unwrap().unwrap(), identity);
        let broken = LedgerIdentity {
            ledger_id: String::new(),
            account_id: "a".into(),
            key_generation: 1,
        };
        assert!(persist_identity(&vault, &broken).is_err());
    }

    #[test]
    fn cache_roundtrip_and_atomic_replacement() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join(LEDGER_CACHE_FILE);
        let key = ContentKey::generate().unwrap();
        let identity = LedgerIdentity {
            ledger_id: "l".into(),
            account_id: "a".into(),
            key_generation: 1,
        };
        let envelope = encrypt(
            &valid_payload(),
            &key,
            SnapshotMetadata {
                format_version: 1,
                ledger_id: identity.ledger_id.clone(),
                account_id: identity.account_id.clone(),
                key_generation: 1,
                revision: 1,
            },
        )
        .unwrap();
        persist_cache(&path, &envelope).unwrap();
        let reopened = ContentKey::from_bytes(*key.as_bytes());
        assert_eq!(
            read_cache(&path, &reopened, &identity)
                .unwrap()
                .unwrap()
                .metadata
                .revision,
            1
        );
        let wrong = LedgerIdentity {
            ledger_id: "other".into(),
            account_id: "a".into(),
            key_generation: 1,
        };
        assert!(read_cache(&path, &reopened, &wrong).is_err());
    }
}
