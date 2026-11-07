import { describe, it, expect } from 'vitest';
import { dashboardHtml, calendarHtml, poolHtml, dashboardRecordRow } from './view';
import fixture from '../../tests/fixtures/app-v2-snapshot.json';
import type { DashboardMonth } from './types';

describe('shared dashboard presentation without HTTP or native dependencies', () => {
  const month = fixture.months[0] as unknown as DashboardMonth;
  it('shows the personal budget card and hides income leads',()=>{
    const html=dashboardHtml(month,'2026-10-16',false,false,false);
    expect(html).toContain('本月新增额度');expect(html).not.toContain('本月收入');
  });
  it('renders the existing desktop dashboard sections and exact cents without changing data', () => {
    const before = JSON.stringify(month);
    const html = dashboardHtml(month, '2026-10-16');
    for (const label of ['个人净消费','本月收入','各池可用','安全积累','累计净消费','消费日历','类别分布','Top 10']) expect(html).toContain(label);
    expect(html).toContain((month.summary.personalCents / 100).toFixed(2));
    expect(JSON.stringify(month)).toBe(before);
  });
  it('keeps no-record and future dates distinct and never says zero consumption', () => {
    const html = calendarHtml(month, `${month.month}-01`);
    expect(html).toContain('未来日期');
    expect(html).not.toContain('零消费');
  });
  it('escapes imported category and record text', () => {
    const hostile = structuredClone(month);
    hostile.categories[0].name = '<img src=x onerror=alert(1)>';
    hostile.records[0].title = '<script>oops</script>';
    hostile.topIds = [hostile.records[0].id];
    const html = dashboardHtml(hostile, '2026-10-16', true);
    expect(html).not.toContain('<script>oops');
    expect(html).not.toContain('<img src=x');
    expect(html).toContain('&lt;script&gt;');
  });
  it('announces the selected calendar day for the touch calendar',()=>{
    const date=month.daily[0].date;
    expect(calendarHtml(month,'2026-10-16',date)).toContain(`data-date="${date}" aria-pressed="true"`);
  });
  it.each([true, false])('keeps unassigned first-month spending visible with unavailable budget states (active=%s)', (mechanismActive) => {
    const first = structuredClone(month);
    first.mechanismActive = mechanismActive;
    Object.assign(first.summary, {personalCents: 12345, unknownCents: 12345, monthlyBudgetCents: 0, openingBudgetCents: 0, availableCents: 0, overrunCents: 12345, safetyCents: 0});
    first.pools.forEach(pool => Object.assign(pool, {budgetCents: 0, carryCents: 0, openingCents: 0, spentCents: 0, availableCents: 0, overrunCents: 12345, usageRatio: 5}));
    const before = JSON.stringify(first);
    const html = dashboardHtml(first, '2026-11-01');
    expect(html).toContain('123.45');
    expect(html).toContain(mechanismActive?'当前没有可比较额度':'未设预算');
    expect(html).toContain('剩余额度尚未完整');
    expect(html).toContain('data-scope="unknown"');
    expect(html).toContain('暂无额度');
    expect(html).not.toContain('超支 ¥');
    expect(html).not.toContain('overrun-text');
    expect(html).not.toContain('本月新增额度');
    expect(JSON.stringify(first)).toBe(before);
  });
  it('offers optional desktop budget settings and warns about pending pools even when they net to zero',()=>{
    const first=structuredClone(month);first.mechanismActive=false;
    first.records=[{...first.records[0],pool:'unknown',amountCents:100},{...first.records[0],id:'refund',pool:'unknown',amountCents:-100}];
    first.summary.unknownCents=0;
    const html=dashboardHtml(first,'2026-11-01',false,true);
    expect(html).toContain('data-action="edit-budget"');expect(html).toContain('默认额度为0');
    expect(html).toContain('2 笔消费待分池');expect(html).toContain('剩余额度尚未完整');
    expect(html).toContain('各池可用（未含待分池）');
    expect(dashboardHtml(first,'2026-11-01')).not.toContain('data-action="edit-budget"');
  });
  it('shows this month income in the second headline card instead of the added allowance', () => {
    const html = dashboardHtml(month, '2026-11-01');
    expect(html).toContain('本月收入</span><strong class="overview-amount"><small>¥</small> 8,000.00');
    expect(html).not.toContain('本月新增额度');
  });
  it('hides zero-valued scope leads and keeps the non-zero ones', () => {
    const zeroed = structuredClone(month);
    Object.assign(zeroed.summary, {structuralCents: 0, incomeCents: 0, unknownCents: 500});
    const html = dashboardHtml(zeroed, '2026-11-01');
    expect(html).toContain('data-scope="unknown"');
    expect(html).toContain('待归类消费');
    expect(html).not.toContain('data-scope="structural"');
    expect(html).not.toContain('data-scope="income"');
  });
  it('omits the scope row entirely when every lead is zero', () => {
    const zeroed = structuredClone(month);
    Object.assign(zeroed.summary, {structuralCents: 0, incomeCents: 0, unknownCents: 0});
    const html = dashboardHtml(zeroed, '2026-11-01');
    expect(html).not.toContain('overview-secondary');
  });
  it('keeps the income lead clickable while the headline card is not a control', () => {
    const html = dashboardHtml(month, '2026-11-01');
    expect(html).toContain('<button class="scope-item" data-scope="income" type="button">');
    expect(html).toContain('<div class="overview-card"><span>本月收入</span>');
  });
  it('renders income rows without a pool chip', () => {
    const record = {id: 'tx_income', date: '2026-11-05', time: '10:00', title: '工资', category: '工资薪酬', pool: 'unknown', scope: 'INCOME', amountCents: 200000} as const;
    const html = dashboardRecordRow(record);
    expect(html).toContain('工资薪酬');
    expect(html).not.toContain('record-pool');
  });
  it('keeps income visible when an opening allowance exists and the mechanism is active', () => {
    const funded = structuredClone(month);
    funded.mechanismActive = true;
    Object.assign(funded.summary, {monthlyBudgetCents: 0, openingBudgetCents: 10000, availableCents: 0, safetyCents: 0});
    const html = dashboardHtml(funded, '2026-11-01');
    expect(html).not.toContain('当前没有可比较额度');
    expect(html).toContain('本月收入</span><strong class="overview-amount"><small>¥</small> 8,000.00');
  });
  it('shows real spending without an overrun for a pool with no allowance', () => {
    const pool = {...month.pools[0], openingCents: 0, budgetCents: 0, carryCents: 0, spentCents: 2345, availableCents: -2345, overrunCents: 2345, usageRatio: null};
    const html = poolHtml(pool, true);
    expect(html).toContain('23.45');
    expect(html).toContain('暂无额度');
    expect(html).not.toContain('超支 ¥');
    expect(html).not.toContain('¥ −23.45');
  });
});
