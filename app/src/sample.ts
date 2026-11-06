/**
 * Synthetic preview snapshot for the App UI.
 *
 * Used only when there is no real ledger cache and no pairing (fresh Android
 * build, browser preview, or a device before first pairing). It always carries
 * a visible "合成预览" label in the UI and never claims to be real data.
 * The shape mirrors the frozen App V3 contract (docs/快照与月份.md):
 * full daily calendars, integer pool/summary cents, usageRatio null exactly
 * when opening <= 0 — the Rust parser would reject anything looser.
 */
import type { AppV2Snapshot } from './bills';

function record(
  id: string, date: string, time: string, title: string, category: string,
  pool: 'operation' | 'free' | 'travel' | 'growth' | 'unknown', scope: string, amountCents: number,
) {
  return { id, time, date, title, category, pool, scope, amountCents };
}

const octoberRecords = [
  record('sample-001', '2026-10-02', '2026-10-02T08:31:00+08:00', '合成预览 · 早餐', '餐饮', 'operation', 'PERSONAL', -2450),
  record('sample-002', '2026-10-05', '2026-10-05T19:12:00+08:00', '合成预览 · 地铁充值', '交通', 'operation', 'PERSONAL', -5000),
  record('sample-003', '2026-10-09', '2026-10-09T14:05:00+08:00', '合成预览 · 待归类票务', '其他', 'unknown', 'PERSONAL', -12800),
  record('sample-004', '2026-10-12', '2026-10-12T21:40:00+08:00', '合成预览 · 图书', '学习', 'growth', 'PERSONAL', -6600),
  record('sample-005', '2026-10-15', '2026-10-15T10:00:00+08:00', '合成预览 · 负调整修正', '修正', 'operation', 'PERSONAL', 1200),
];
const septemberRecords = [
  record('sample-101', '2026-09-03', '2026-09-03T09:20:00+08:00', '合成预览 · 上月餐饮', '餐饮', 'operation', 'PERSONAL', -3200),
  record('sample-102', '2026-09-18', '2026-09-18T16:45:00+08:00', '合成预览 · 上月旅行门票', '门票', 'travel', 'PERSONAL', -12000),
];
const octoberStructural = [
  record('sample-struct-2026-10', '2026-10-01', '2026-10-01T00:01:00+08:00', '合成预览 · 云服务年费', '订阅服务', 'unknown', 'STRUCTURAL', 30000),
];
const octoberIncome = [
  record('sample-income-2026-10', '2026-10-10', '2026-10-10T12:00:00+08:00', '合成预览 · 工资入账', '工资薪酬', 'unknown', 'INCOME', 1200000),
];
const septemberStructural = [
  record('sample-struct-2026-09', '2026-09-01', '2026-09-01T00:01:00+08:00', '合成预览 · 上月云服务年费', '订阅服务', 'unknown', 'STRUCTURAL', 30000),
];
const septemberIncome = [
  record('sample-income-2026-09', '2026-09-10', '2026-09-10T12:00:00+08:00', '合成预览 · 上月工资入账', '工资薪酬', 'unknown', 'INCOME', 1200000),
];

/** Full-month daily calendar derived from the records, like the real V1. */
function dailyFor(month: string, records: typeof octoberRecords) {
  const year = Number(month.slice(0, 4));
  const number = Number(month.slice(5, 7));
  const days = new Date(year, number, 0).getDate();
  let cumulative = 0;
  return Array.from({ length: days }, (_, index) => {
    const label = `${month}-${String(index + 1).padStart(2, '0')}`;
    const dayRecords = records.filter((entry) => entry.date === label);
    cumulative += dayRecords
      .filter((entry) => entry.pool === 'operation')
      .reduce((sum, entry) => sum + entry.amountCents, 0);
    return {
      date: label,
      amountCents: dayRecords.reduce((sum, entry) => sum + entry.amountCents, 0),
      operationCumulativeCents: cumulative,
      referenceCents: -20000,
      count: dayRecords.length,
    };
  });
}

function poolView(spent: number, opening: number) {
  const ending = opening - spent;
  return {
    budgetCents: opening,
    carryCents: 0,
    openingCents: opening,
    spentCents: spent,
    endingCents: ending,
    availableCents: Math.max(0, ending),
    overrunCents: ending < 0 ? -ending : 0,
    usageRatio: opening > 0 ? Math.round((spent / opening) * 1e6) / 1e6 : null,
  };
}

function summaryFor(records: typeof octoberRecords, structural: typeof octoberStructural, income: typeof octoberIncome, pools: ReturnType<typeof poolView>[]) {
  const personal = records.reduce((sum, entry) => sum + entry.amountCents, 0);
  const structuralSum = structural.reduce((sum, entry) => sum + entry.amountCents, 0);
  const incomeSum = income.reduce((sum, entry) => sum + entry.amountCents, 0);
  const sum = (pick: (pool: ReturnType<typeof poolView>) => number) =>
    pools.reduce((total, pool) => total + pick(pool), 0);
  return {
    personalCents: personal,
    structuralCents: structuralSum,
    totalCents: personal + structuralSum,
    unknownCents: records.filter((entry) => entry.pool === 'unknown').reduce((sum, entry) => sum + entry.amountCents, 0),
    monthlyBudgetCents: sum((pool) => pool.budgetCents),
    openingBudgetCents: sum((pool) => pool.openingCents),
    availableCents: sum((pool) => pool.availableCents),
    overrunCents: sum((pool) => pool.overrunCents),
    safetyCents: 250000,
    incomeCents: incomeSum,
  };
}

function incomeCategoriesFor(income: typeof octoberIncome) {
  return Object.entries(
    income.reduce<Record<string, { amountCents: number; count: number }>>((acc, entry) => {
      acc[entry.category] = acc[entry.category] ?? { amountCents: 0, count: 0 };
      acc[entry.category].amountCents += entry.amountCents;
      acc[entry.category].count += 1;
      return acc;
    }, {}),
  ).map(([name, value]) => ({ name, ...value }));
}

const octoberPools = [
  { id: 'operation' as const, name: '运行', ...poolView(-6250, 300000) },
  { id: 'free' as const, name: '随心', ...poolView(0, 150000) },
  { id: 'travel' as const, name: '旅行', ...poolView(0, 100000) },
  { id: 'growth' as const, name: '成长', ...poolView(-6600, 50000) },
];
const septemberPools = [
  { id: 'operation' as const, name: '运行', ...poolView(-3200, 300000) },
  { id: 'free' as const, name: '随心', ...poolView(0, 150000) },
  { id: 'travel' as const, name: '旅行', ...poolView(-12000, 100000) },
  { id: 'growth' as const, name: '成长', ...poolView(0, 50000) },
];

const octoberCategories = Object.entries(
  octoberRecords.reduce<Record<string, { amountCents: number; count: number }>>((acc, entry) => {
    acc[entry.category] = acc[entry.category] ?? { amountCents: 0, count: 0 };
    acc[entry.category].amountCents += entry.amountCents;
    acc[entry.category].count += 1;
    return acc;
  }, {}),
).map(([name, value]) => ({ name, ...value }));

const septemberCategories = Object.entries(
  septemberRecords.reduce<Record<string, { amountCents: number; count: number }>>((acc, entry) => {
    acc[entry.category] = acc[entry.category] ?? { amountCents: 0, count: 0 };
    acc[entry.category].amountCents += entry.amountCents;
    acc[entry.category].count += 1;
    return acc;
  }, {}),
).map(([name, value]) => ({ name, ...value }));

const octoberSummary = summaryFor(octoberRecords, octoberStructural, octoberIncome, octoberPools);
const septemberSummary = summaryFor(septemberRecords, septemberStructural, septemberIncome, septemberPools);

export const sampleSnapshot: AppV2Snapshot = {
  schemaVersion: 3,
  snapshotKind: 'account-ledger-view',
  sourceHash: '0000000000000000000000000000000000000000000000000000000000000000',
  calendarMonth: '2026-10',
  generatedAt: '2026-10-15T09:00:00+08:00',
  sourceLastRecordedAt: '2026-10-15T10:00:00+08:00',
  ledgerCount: octoberRecords.length + septemberRecords.length + octoberStructural.length + septemberStructural.length
    + octoberIncome.length + septemberIncome.length,
  coverage: { kind: 'recent-months', months: ['2026-10', '2026-09'] },
  months: [
    {
      month: '2026-10',
      lastRecordedAt: '2026-10-15T10:00:00+08:00',
      mechanismActive: true,
      summary: octoberSummary,
      pools: octoberPools,
      daily: dailyFor('2026-10', octoberRecords),
      categories: octoberCategories,
      records: octoberRecords,
      structuralRecords: octoberStructural,
      incomeRecords: octoberIncome,
      incomeCategories: incomeCategoriesFor(octoberIncome),
      topIds: ['sample-003', 'sample-004', 'sample-002'],
    },
    {
      month: '2026-09',
      lastRecordedAt: '2026-09-18T16:45:00+08:00',
      mechanismActive: true,
      summary: septemberSummary,
      pools: septemberPools,
      daily: dailyFor('2026-09', septemberRecords),
      categories: septemberCategories,
      records: septemberRecords,
      structuralRecords: septemberStructural,
      incomeRecords: septemberIncome,
      incomeCategories: incomeCategoriesFor(septemberIncome),
      topIds: ['sample-102', 'sample-101'],
    },
  ],
};
