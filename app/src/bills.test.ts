/**
 * Unit tests for the pure bill-view logic: month coverage bounds, record
 * filtering, escaping in rendered rows, and the synthetic preview shape.
 */
import { describe, expect, it } from 'vitest';
import {
  availableMonths, beijingMonth, categoryOptions, coverageNote, emptyFilters, filterRecords,
  homeCards, monthLabel, poolRow, previousOfMonth, recordDetail, recordRow, resolveMonth,
  type AppV2Snapshot,
} from './bills';
import { sampleSnapshot } from './sample';
import sharedFixtureJson from '../../tests/fixtures/app-v2-snapshot.json';
import historyFixtureJson from '../../tests/fixtures/app-v2-history-snapshot.json';

/** The exact file the Python and Rust suites load (three-end shared fixture). */
const sharedFixture = sharedFixtureJson as unknown as AppV2Snapshot;

const october = sampleSnapshot.months[0];

function makeRecord(overrides: Partial<Parameters<typeof recordRow>[0]> = {}) {
  return {
    id: 'r1', time: '09:30', date: '2026-10-02', title: '午餐', category: '餐饮',
    pool: 'operation' as const, scope: 'personal', amountCents: -2500,
    ...overrides,
  };
}

describe('month coverage', () => {
  it('retains September selection in the November tracked snapshot', () => {
    const history=historyFixtureJson as unknown as AppV2Snapshot;
    expect(availableMonths(history)).toEqual(['2026-11','2026-10','2026-09']);
    expect(resolveMonth(history,'2026-09')).toBe('2026-09');
    expect(monthLabel('2026-09',new Date('2026-11-01T12:00:00+08:00'))).toBe('2026 年 9 月');
  });
  it('lists exactly the snapshot coverage months', () => {
    expect(availableMonths(sampleSnapshot)).toEqual(['2026-10', '2026-09']);
  });

  it('defaults to the newest coverage month', () => {
    expect(resolveMonth(sampleSnapshot, null)).toBe('2026-10');
  });

  it('keeps a wanted month that is inside the coverage', () => {
    expect(resolveMonth(sampleSnapshot, '2026-09')).toBe('2026-09');
  });

  it('rejects a month outside the coverage and falls back to the newest', () => {
    expect(resolveMonth(sampleSnapshot, '2026-08')).toBe('2026-10');
    expect(resolveMonth(null, '2026-10')).toBeNull();
  });

  it('labels months by the real Beijing-calendar month, not the snapshot', () => {
    const now = new Date('2026-10-20T12:00:00+08:00');
    expect(monthLabel('2026-10', now)).toBe('2026 年 10 月 · 本月');
    expect(monthLabel('2026-09', now)).toBe('2026 年 9 月 · 上月');
    // A cache frozen at October, viewed in December: October is history now.
    const december = new Date('2026-12-05T12:00:00+08:00');
    expect(monthLabel('2026-10', december)).toBe('2026 年 10 月');
    expect(monthLabel('2026-09', december)).toBe('2026 年 9 月');
  });

  it('computes the Beijing month and its previous month', () => {
    expect(beijingMonth(new Date('2026-10-31T16:30:00-07:00'))).toBe('2026-11');
    expect(beijingMonth(new Date('2026-01-01T00:30:00+08:00'))).toBe('2026-01');
    expect(previousOfMonth('2026-01')).toBe('2025-12');
    expect(previousOfMonth('2026-10')).toBe('2026-09');
  });

  it('warns honestly when the snapshot no longer covers the current month', () => {
    const now = new Date('2026-11-05T12:00:00+08:00');
    expect(coverageNote(sampleSnapshot, now)).toBe(
      '快照数据停留在 2026 年 10 月；等待电脑发布 2026 年 11 月账单后同步。');
    expect(coverageNote(sampleSnapshot, new Date('2026-10-20T12:00:00+08:00'))).toBeNull();
    expect(coverageNote(null, now)).toBeNull();
    // A December cache without November data is stale too.
    const frozen = { ...sampleSnapshot, months: [sampleSnapshot.months[0]] };
    expect(coverageNote(frozen, new Date('2026-12-05T12:00:00+08:00'))).toBe(
      '快照数据停留在 2026 年 10 月；等待电脑发布 2026 年 12 月账单后同步。');
  });
});

describe('record filtering', () => {
  const records = [
    makeRecord({ id: 'a', date: '2026-10-02', title: '午餐 · 面馆', category: '餐饮', pool: 'operation' }),
    makeRecord({ id: 'b', date: '2026-10-05', title: '地铁', category: '交通', pool: 'operation', amountCents: -500 }),
    makeRecord({ id: 'c', date: '2026-10-05', title: '图书', category: '学习', pool: 'growth' }),
    makeRecord({ id: 'd', date: '2026-10-01', title: '待归类票务', category: '其他', pool: 'unknown' }),
  ];

  it('returns records sorted newest first with no filters', () => {
    expect(filterRecords(records, emptyFilters()).map((record) => record.id)).toEqual(['b', 'c', 'a', 'd']);
  });

  it('breaks same-day ties by time descending then id', () => {
    expect(filterRecords(records, emptyFilters()).slice(0, 2).map((record) => record.id)).toEqual(['b', 'c']);
  });

  it('matches a case-insensitive title substring', () => {
    expect(filterRecords(records, { ...emptyFilters(), query: '图书' }).map((record) => record.id)).toEqual(['c']);
    expect(filterRecords(records, { ...emptyFilters(), query: '  图书 ' }).length).toBe(1);
  });

  it('filters by exact category and pool', () => {
    expect(filterRecords(records, { ...emptyFilters(), category: '交通' }).map((record) => record.id)).toEqual(['b']);
    expect(filterRecords(records, { ...emptyFilters(), pool: 'growth' }).map((record) => record.id)).toEqual(['c']);
  });

  it('combines filters with AND semantics', () => {
    expect(filterRecords(records, { query: '', category: '餐饮', pool: 'growth' })).toEqual([]);
    expect(filterRecords(records, { query: '面', category: '餐饮', pool: 'operation' }).map((record) => record.id)).toEqual(['a']);
  });

  it('lists category options sorted for the dropdown', () => {
    expect(categoryOptions(october)).toEqual(['餐饮', '交通', '其他', '修正', '学习']);
  });

  it('never mutates the input order', () => {
    const copy = [...records];
    filterRecords(records, emptyFilters());
    expect(records).toEqual(copy);
  });
});

describe('row and detail rendering', () => {
  it('escapes hostile titles and categories in the row HTML', () => {
    const row = recordRow(makeRecord({ title: '<img src=x onerror=alert(1)>', category: '"餐饮"&' }));
    expect(row).not.toContain('<img');
    expect(row).toContain('&lt;img src=x onerror=alert(1)&gt;');
    expect(row).toContain('&quot;餐饮&quot;&amp;');
  });

  it('marks negative amounts and shows the negative-adjustment tag', () => {
    const negative = recordRow(makeRecord({ amountCents: -2500 }));
    const positive = recordRow(makeRecord({ amountCents: 2500 }));
    expect(negative).toContain('negative-money');
    expect(negative).toContain('负调整');
    expect(positive).not.toContain('negative-money');
    expect(positive).not.toContain('负调整');
  });

  it('falls back to the raw pool id when the pool name is unknown', () => {
    const row = recordRow({ ...makeRecord(), pool: 'mystery' } as unknown as Parameters<typeof recordRow>[0]);
    expect(row).toContain('mystery');
  });

  it('exposes every record field in the detail view', () => {
    const detail = recordDetail(makeRecord());
    const labels = detail.map(([label]) => label);
    expect(labels).toEqual(['日期', '时间', '金额', '分类', '池', '范围', '记录编号']);
    expect(Object.fromEntries(detail)).toMatchObject({ 金额: '¥ −25.00（负调整）', 池: '运行' });
  });
});

describe('home cards and pool rows', () => {
  it('shows budget-derived values only while the mechanism is active', () => {
    const active = homeCards(october);
    expect(active.personal).toContain('256.50');
    expect(active.budget).not.toBe('—');
    const inactive = homeCards({ ...sampleSnapshot.months[1], mechanismActive: false });
    expect(inactive.budget).toBe('—');
    expect(inactive.available).toBe('—');
    expect(inactive.safety).toBe('—');
  });

  it('escapes pool names and flags an overrun pool', () => {
    const pool = october.pools[0];
    expect(poolRow(pool)).toContain('运行');
    const overrunPool = { ...pool, overrunCents: 1200, usageRatio: 1.2 };
    expect(poolRow(overrunPool)).toContain('超支 ¥ 12.00');
    expect(poolRow(overrunPool)).toContain('is-overrun');
  });
});

describe('shared three-end fixture', () => {
  it('is the exact file the Python and Rust suites load and renders correctly', () => {
    expect(sharedFixture.schemaVersion).toBe(2);
    expect(sharedFixture.coverage.months).toEqual(['2026-10', '2026-09']);
    const fixtureOctober = sharedFixture.months[0];
    const cards = homeCards(fixtureOctober);
    expect(cards.personal).toBe('130.00');
    expect(cards.budget).toBe('1,500.00');
    expect(filterRecords(fixtureOctober.records, { ...emptyFilters(), query: '地铁' }).length).toBe(1);
    expect(categoryOptions(fixtureOctober)).toContain('餐饮与日常食品');
    expect(poolRow(fixtureOctober.pools[0])).toContain('运行账户');
  });
});

describe('synthetic preview snapshot', () => {
  it('stays inside the App V2 contract shape', () => {
    expect(sampleSnapshot.schemaVersion).toBe(2);
    expect(sampleSnapshot.months).toHaveLength(2);
    for (const month of sampleSnapshot.months) {
      expect(month.records.every((record) => Number.isSafeInteger(record.amountCents))).toBe(true);
      expect(month.records.every((record) => /^2026-\d{2}-\d{2}$/.test(record.date))).toBe(true);
      expect(month.records.every((record) => record.date.startsWith(month.month))).toBe(true);
    }
  });

  it('never collides with real ledger ids', () => {
    for (const month of sampleSnapshot.months) {
      for (const record of [...month.records, ...month.structuralRecords]) {
        expect(record.id.startsWith('sample-')).toBe(true);
      }
    }
  });
});
