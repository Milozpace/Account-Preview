import { describe, expect, it } from 'vitest';
import fixture from '../../tests/fixtures/app-v2-snapshot.json';
import type { DashboardSnapshot } from './types';
import { buildAnalysisExport } from './analysis-export';

const snapshot = () => structuredClone(fixture) as DashboardSnapshot;

describe('buildAnalysisExport', () => {
  it('exports one deterministic month with exact computed totals and separate structural details', () => {
    const data = snapshot();
    const before = structuredClone(data);
    const result = buildAnalysisExport(data, '2026-10');
    expect(buildAnalysisExport(data, '2026-10', false).content).not.toContain('收入');
    expect(result.fileName).toBe('消费复盘_2026-10.md');
    expect(result).toEqual(buildAnalysisExport(data, '2026-10'));
    expect(data).toEqual(before);
    expect(result.content).toContain('2026-10-01 至 2026-10-31');
    expect(result.content).toContain('快照生成时间：2026-10-15T08:30:00+08:00');
    expect(result.content).toContain('来源最新记录时间：2026-10-12T12:00:00+08:00');
    expect(result.content).toContain('本月最新记录时间：2026-10-12T12:00:00+08:00');
    expect(result.content).toContain('| 个人净消费（含项目） | 130.00 |');
    expect(result.content).toContain('| 结构性净额 | 2500.00 |');
    expect(result.content).toContain('| 合计净额 | 2630.00 |');
    expect(result.content).toContain('| 当月收入（已入账） | 8000.00 |');
    expect(result.content).toContain('| 待归类净消费 | 12.00 |');
    expect(result.content).toContain('| 学习提升 | 68.00 | 1 | 52.31% |');
    expect(result.content).toContain('| 随心消费 | 300.00 | 5.00 | 305.00 | 68.00 | 52.31% | 22.30% | 237.00 | 0.00 |');
    expect(result.content).toContain('| 待归类 | — | — | — | 12.00 | 9.23% | — | — | — |');
    expect(result.content).toContain('| 5 | 2026-10-12 | 合成负调整退款 | 餐饮与日常食品 | 运行账户 | -15.00 |');
    expect(result.content.split('## 结构性明细')[0]).not.toContain('合成房租');
    expect(result.content).toContain('| 收入结转 | 8000.00 | 1 |');
    expect(result.content).toContain('| 1 | 2026-10-10 | 合成工资入账 | 收入结转 | 不适用 | 8000.00 |');
    expect(result.content).toContain('未导入账本的真实收入不计入');
    expect(result.content).not.toContain('合成火车票');
    expect(result.content).not.toMatch(/tx_oct|tx_sep|abababab|sourceHash|topIds|ledgerCount|完整账单/);
    expect(result.content).toContain('个人净消费包含 PERSONAL 与 PROJECT');
    expect(result.content).toContain('不作消费价值判断');
  });

  it('uses provided usage and available/overrun rather than recomputing budget business logic', () => {
    const data = snapshot();
    const pool = data.months[0].pools[0];
    pool.usageRatio = 0.12345;
    pool.availableCents = 123;
    pool.overrunCents = 456;
    const content = buildAnalysisExport(data, '2026-10').content;
    expect(content).toContain('| 运行账户 | 600.00 | 0.00 | 600.00 | 50.00 | 38.46% | 12.35% | 1.23 | 4.56 |');
  });

  it.each([0, -100])('shows no shares with a nonpositive personal denominator (%s)', personalCents => {
    const data = snapshot();
    data.months[0].summary.personalCents = personalCents;
    const content = buildAnalysisExport(data, '2026-10').content;
    expect(content).toContain('| 学习提升 | 68.00 | 1 | — |');
    expect(content).toContain('| 运行账户 | 600.00 | 0.00 | 600.00 | 50.00 | — | 8.33% | 550.00 | 0.00 |');
    expect(content).toContain('| 待归类 | — | — | — | 12.00 | — | — | — | — |');
    expect(content).not.toMatch(/NaN|Infinity/);
  });

  it('hides inactive budgets and invalid opening denominators while preserving signed actuals', () => {
    const data = snapshot();
    data.months[1].mechanismActive = false;
    let content = buildAnalysisExport(data, '2026-09').content;
    expect(content).toContain('| 运行账户 | — | — | — | -5.00 | -2.01% | — | — | — |');
    expect(content).toContain('| 期初预算（含滚存） | — |');
    data.months[1].mechanismActive = true;
    for (const opening of [0, -100]) {
      data.months[1].pools[0].openingCents = opening;
      content = buildAnalysisExport(data, '2026-09').content;
      expect(content).toContain('| -5.00 | -2.01% | — | 605.00 | 0.00 |');
    }
  });

  it('keeps PROJECT entries and does not infer a category or a value verdict', () => {
    const data = snapshot();
    data.months[0].records[2].scope = 'PROJECT';
    data.months[0].records[2].category = '已确认项目分类';
    const content = buildAnalysisExport(data, '2026-10').content;
    expect(content).toContain('合成教材 | 已确认项目分类 | 随心消费 | 68.00');
    expect(content).not.toMatch(/值得|不值|浪费|建议减少|建议增加/);
  });

  it('removes embedded secrets, paths and URLs and neutralizes Markdown/HTML/control injection', () => {
    const data = snapshot();
    data.months[0].records[0].title = '午餐|`<script>\n[查看](https://evil.test/a)\u202e 订单123456789012345678 账号6222021234567890123 C:\\private\\ledger.db /private/secret.db alice@example.com';
    const content = buildAnalysisExport(data, '2026-10').content;
    expect(content).toContain('午餐&#124;&#96;&lt;script&gt; &#91;查看&#93;&#40;&#41;');
    expect(content).not.toMatch(/https:|evil|123456789|622202|private|ledger\.db|secret\.db|alice@|\u202e|<script>/);
    const row = content.split('\n').find(line => line.startsWith('| 1 | 2026-10-01 | 午餐'))!;
    expect(row.split('|')).toHaveLength(8);
  });

  it('handles empty records and explicit null times without claiming source completeness', () => {
    const data = snapshot();
    data.sourceLastRecordedAt = null;
    data.months[0].lastRecordedAt = null;
    data.months[0].records = [];
    data.months[0].structuralRecords = [];
    data.months[0].incomeRecords = [];
    data.months[0].incomeCategories = [];
    data.months[0].summary.incomeCents = 0;
    const content = buildAnalysisExport(data, '2026-10').content;
    expect(content).toContain('来源最新记录时间：—');
    expect(content).toContain('本月记录日期范围：—');
    expect(content.match(/暂无记录。/g)).toHaveLength(3);
    expect(content).toContain('时间信息不代表来源完整性');
  });

  it('removes short labeled identifiers and non-HTTP links while retaining descriptive text', () => {
    const data = snapshot();
    data.months[0].records[0].title = '公交月票 订单号:ABC-123 账号：user42 ftp://example.test/private';
    const content = buildAnalysisExport(data, '2026-10').content;
    expect(content).toContain('公交月票');
    expect(content).not.toMatch(/ABC-123|user42|ftp:|example\.test/);
  });

  it('removes labeled account and POSIX paths after Chinese punctuation without damaging pool names', () => {
    const data = snapshot();
    data.months[0].records[0].title = '合成午餐 账户：alice 路径：/private/secret.db';
    const content = buildAnalysisExport(data, '2026-10').content;
    expect(content).toContain('合成午餐');
    expect(content).toContain('旅行/体验');
    expect(content).toContain('成长/项目/耐用品');
    expect(content).not.toMatch(/alice|private|secret\.db/);
  });

  it('rejects null budget cents when the budget mechanism is active', () => {
    for (const key of ['monthlyBudgetCents', 'openingBudgetCents', 'availableCents', 'overrunCents', 'safetyCents'] as const) {
      const data = snapshot();
      data.months[0].summary[key] = null;
      expect(() => buildAnalysisExport(data, '2026-10')).toThrow();
    }
    for (const key of ['budgetCents', 'carryCents', 'openingCents', 'endingCents', 'availableCents', 'overrunCents'] as const) {
      const data = snapshot();
      data.months[0].pools[0][key] = null;
      expect(() => buildAnalysisExport(data, '2026-10')).toThrow();
    }
  });

  it('accepts explicit null budget cents when the budget mechanism is inactive', () => {
    const data = snapshot();
    data.months[0].mechanismActive = false;
    data.months[0].summary.monthlyBudgetCents = null;
    data.months[0].pools[0].openingCents = null;
    expect(buildAnalysisExport(data, '2026-10').content).toContain('| 运行账户 | — | — | — | 50.00 | 38.46% | — | — | — |');
  });

  it('formats safe integer cents exactly at the numeric boundary', () => {
    const data = snapshot();
    data.months[0].records[0].amountCents = Number.MAX_SAFE_INTEGER;
    data.months[0].records[1].amountCents = -Number.MAX_SAFE_INTEGER;
    const content = buildAnalysisExport(data, '2026-10').content;
    expect(content).toContain('| 90071992547409.91 |');
    expect(content).toContain('| -90071992547409.91 |');
  });

  it('rejects unavailable or malformed month choices', () => {
    for (const month of ['2026-08', '2026-13', '../2026-10', '2026-1']) {
      expect(() => buildAnalysisExport(snapshot(), month)).toThrow();
    }
  });

  it.each([undefined, NaN, Infinity, 1.5, Number.MAX_SAFE_INTEGER + 1])('rejects invalid cents rather than fabricating zeros (%s)', value => {
    const data = snapshot();
    data.months[0].summary.personalCents = value as number;
    expect(() => buildAnalysisExport(data, '2026-10')).toThrow();
  });

  it('rejects missing computed fields, invalid ratios and cross-month records', () => {
    const missing = snapshot();
    missing.months[0].pools[0].availableCents = undefined as unknown as number;
    expect(() => buildAnalysisExport(missing, '2026-10')).toThrow();
    const ratio = snapshot();
    ratio.months[0].pools[0].usageRatio = NaN;
    expect(() => buildAnalysisExport(ratio, '2026-10')).toThrow();
    const date = snapshot();
    date.months[0].records[0].date = '2026-09-01';
    expect(() => buildAnalysisExport(date, '2026-10')).toThrow();
  });
});
