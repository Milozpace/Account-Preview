/**
 * Pure bill-view logic for the App ledger UI: month selection, record
 * filtering, and row rendering. No Tauri or DOM access here, so every rule
 * is unit-testable. Types come from the shared web dashboard sources; the
 * App renders the same validated App V3 snapshot shape.
 */
import { escapeHtml, money, monthTitle } from '../../dashboard/src/helpers';
import type { LedgerRecord, Pool, PoolId } from '../../dashboard/src/types';

// ---------------------------------------------------------------------------
// App V3 contract types — the TypeScript mirror of src-tauri/src/ledger.rs.
// All cents are safe integers (money() throws on anything else); pool and
// summary cents are mandatory, exactly like the real native result. Only
// usageRatio (when opening <= 0) and timestamps may be null. The Rust parser
// enforces this before any display, so these types document the trusted shape.
// Income records ride in the snapshot (scope INCOME, pool unknown) but the
// mobile UI does not render them yet.
// ---------------------------------------------------------------------------
export interface AppDaily {
  date: string; amountCents: number; operationCumulativeCents: number;
  referenceCents: number; count: number;
}
export interface AppPool {
  id: PoolId; name: string; budgetCents: number; carryCents: number;
  openingCents: number; spentCents: number; endingCents: number;
  availableCents: number; overrunCents: number; usageRatio: number | null;
}
export interface AppSummary {
  personalCents: number; structuralCents: number; totalCents: number; unknownCents: number;
  monthlyBudgetCents: number; openingBudgetCents: number;
  availableCents: number; overrunCents: number; safetyCents: number;
  incomeCents: number;
}
export interface AppMonth {
  month: string; lastRecordedAt: string | null; mechanismActive: boolean;
  summary: AppSummary; pools: AppPool[]; daily: AppDaily[];
  categories: Array<{ name: string; amountCents: number; count: number }>;
  records: LedgerRecord[]; structuralRecords: LedgerRecord[];
  incomeRecords: LedgerRecord[]; incomeCategories: Array<{ name: string; amountCents: number; count: number }>;
  topIds: string[];
}
export interface AppSnapshot {
  schemaVersion: 3; snapshotKind: string; sourceHash: string;
  calendarMonth: string; generatedAt: string; sourceLastRecordedAt: string | null;
  ledgerCount: number; coverage: { kind: string; months: string[] };
  months: AppMonth[];
}

// Same palette as the web dashboard (dashboard/src/charts.ts), duplicated as
// plain constants so the App never pulls the echarts bundle.
export const poolColors: Record<string, string> = {
  operation: '#6c8277', free: '#b88670', travel: '#7896ad', growth: '#ad965b', unknown: '#9b9891',
};
export const poolNames: Record<string, string> = {
  operation: '运行', free: '随心', travel: '旅行', growth: '成长', unknown: '待归类',
};

export interface BillFilters {
  query: string;
  category: string;
  pool: string;
}

export const emptyFilters = (): BillFilters => ({ query: '', category: '', pool: '' });

/** Only months inside the snapshot coverage can ever be selected. */
export function availableMonths(snapshot: AppSnapshot | null): string[] {
  return snapshot ? snapshot.months.map((month) => month.month) : [];
}

export function resolveMonth(snapshot: AppSnapshot | null, wanted: string | null): string | null {
  const months = availableMonths(snapshot);
  if (!months.length) return null;
  return wanted != null && months.includes(wanted) ? wanted : months[0];
}

export function getMonth(snapshot: AppSnapshot | null, month: string | null): AppMonth | null {
  return snapshot?.months.find((entry) => entry.month === month) ?? null;
}

/** The current Beijing-calendar month, timezone-safe in any host locale. */
export function beijingMonth(now: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(now).slice(0, 7);
}

export function previousOfMonth(month: string): string {
  const year = Number(month.slice(0, 4));
  const number = Number(month.slice(5, 7));
  return number === 1 ? `${year - 1}-12` : `${year}-${String(number - 1).padStart(2, '0')}`;
}

/**
 * Relative labels follow the real Beijing-calendar month, not the snapshot's
 * own calendarMonth: a cache frozen at 2026-10 viewed in 2026-11 must not
 * call October "本月". Historical months keep their absolute title.
 */
export function monthLabel(month: string, now: Date = new Date()): string {
  const current = beijingMonth(now);
  if (month === current) return `${monthTitle(month)} · 本月`;
  if (month === previousOfMonth(current)) return `${monthTitle(month)} · 上月`;
  return monthTitle(month);
}

/** Honest notice when the snapshot no longer covers the current month. */
export function coverageNote(snapshot: AppSnapshot | null, now: Date = new Date()): string | null {
  if (!snapshot) return null;
  const current = beijingMonth(now);
  if (snapshot.months.some((month) => month.month === current)) return null;
  const latest = snapshot.months[0]?.month ?? snapshot.calendarMonth;
  return `快照数据停留在 ${monthTitle(latest)}；等待电脑发布 ${monthTitle(current)}账单后同步。`;
}

/** Title search (case-insensitive substring), category and pool exact match. */
export function filterRecords(records: LedgerRecord[], filters: BillFilters): LedgerRecord[] {
  const query = filters.query.trim().toLowerCase();
  return records
    .filter((record) => (filters.category ? record.category === filters.category : true))
    .filter((record) => (filters.pool ? record.pool === filters.pool : true))
    .filter((record) => (query ? record.title.toLowerCase().includes(query) : true))
    .slice()
    .sort((a, b) => {
      if (a.date !== b.date) return a.date < b.date ? 1 : -1;
      if (a.time !== b.time) return a.time < b.time ? 1 : -1;
      return a.id.localeCompare(b.id);
    });
}

/** Category choices for the filter dropdown, taken from the month data. */
export function categoryOptions(month: AppMonth | null): string[] {
  return month ? month.categories.map((category) => category.name).sort((a, b) => a.localeCompare(b, 'zh-CN')) : [];
}

/** One list row. All dynamic content goes through escapeHtml. */
export function recordRow(record: LedgerRecord): string {
  const negative = record.amountCents < 0;
  const poolColor = poolColors[record.pool] || poolColors.unknown;
  const poolName = poolNames[record.pool] ?? record.pool;
  return `<button class="record-row" type="button" data-record="${escapeHtml(record.id)}">`
    + `<span class="record-main"><strong>${escapeHtml(record.title)}</strong>`
    + `<small>${escapeHtml(record.date.slice(5).replace('-', '.'))} `
    + `<span class="record-pool" style="--pool-color:${poolColor}">${escapeHtml(poolName)}</span>`
    + ` · ${escapeHtml(record.category)}${negative ? ' · 负调整' : ''}</small></span>`
    + `<span class="record-money${negative ? ' negative-money' : ''}">¥ ${money(record.amountCents)}</span>`
    + '<span class="record-arrow" aria-hidden="true">↗</span></button>';
}

/** Detail fields for one record, as raw label/value pairs (rendered with textContent). */
export function recordDetail(record: LedgerRecord): Array<[string, string]> {
  return [
    ['日期', record.date],
    ['时间', record.time || '—'],
    ['金额', `¥ ${money(record.amountCents)}${record.amountCents < 0 ? '（负调整）' : ''}`],
    ['分类', record.category],
    ['池', poolNames[record.pool] ?? record.pool],
    ['范围', record.scope],
    ['记录编号', record.id],
  ];
}

export interface HomeCards {
  personal: string;
  income: string;
  available: string;
  safety: string;
}

/** Four headline numbers; budget-derived values are only meaningful while the mechanism is active. */
export function homeCards(month: AppMonth): HomeCards {
  const active = month.mechanismActive;
  return {
    personal: money(month.summary.personalCents),
    income: money(month.summary.incomeCents),
    available: money(active ? month.summary.availableCents : null),
    safety: money(active ? month.summary.safetyCents : null),
  };
}

/** One pool line for the home overview; `interactive` renders it as a button that filters bills. */
export function poolRow(pool: AppPool, interactive = false): string {
  const overspent = (pool.overrunCents ?? 0) > 0;
  const ratio = pool.usageRatio;
  const open = interactive
    ? `<button class="pool-row${overspent ? ' is-overrun' : ''}" type="button" data-pool="${escapeHtml(pool.id)}" style="--pool-color:${poolColors[pool.id] || poolColors.unknown}">`
    : `<div class="pool-row${overspent ? ' is-overrun' : ''}" style="--pool-color:${poolColors[pool.id] || poolColors.unknown}">`;
  const close = interactive ? '</button>' : '</div>';
  return `${open}`
    + `<span class="pool-dot" aria-hidden="true"></span>`
    + `<span class="pool-name">${escapeHtml(pool.name)}</span>`
    + `<span class="pool-track" aria-hidden="true"><i style="width:${boundedRatio(ratio)}%"></i></span>`
    + `<span class="pool-figures">已用 <b>¥ ${money(pool.spentCents)}</b> · 可用 <b>¥ ${money(pool.availableCents)}</b></span>`
    + `${overspent ? `<span class="pool-overrun">超支 ¥ ${money(pool.overrunCents)}</span>` : ''}`
    + `${close}`;
}

function boundedRatio(value: number | null): number {
  return value == null ? 0 : Math.min(100, Math.max(0, value * 100));
}
