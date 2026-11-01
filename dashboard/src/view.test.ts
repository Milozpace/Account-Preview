import { describe, it, expect } from 'vitest';
import { dashboardHtml, calendarHtml, poolHtml } from './view';
import fixture from '../../tests/fixtures/app-v2-snapshot.json';
import type { DashboardMonth } from './types';

describe('shared dashboard presentation without HTTP or native dependencies', () => {
  const month = fixture.months[0] as unknown as DashboardMonth;
  it('renders the existing desktop dashboard sections and exact cents without changing data', () => {
    const before = JSON.stringify(month);
    const html = dashboardHtml(month, '2026-10-16');
    for (const label of ['个人净消费','本月新增额度','各池可用合计','安全积累','累计净消费','消费日历','类别分布','Top 10']) expect(html).toContain(label);
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
    expect(html).toContain('当前没有可比较额度');
    expect(html).toContain('消费尚未分配到资金池');
    expect(html).toContain('data-scope="unknown"');
    expect(html).toContain('暂无额度');
    expect(html).not.toContain('超支 ¥');
    expect(html).not.toContain('overrun-text');
    expect(html).not.toMatch(/本月新增额度<\/span><strong[^>]*><small>¥<\/small> 0\.00/);
    expect(JSON.stringify(first)).toBe(before);
  });
  it('retains legitimate zero monthly additions and balances when an opening allowance exists', () => {
    const funded = structuredClone(month);
    funded.mechanismActive = true;
    Object.assign(funded.summary, {monthlyBudgetCents: 0, openingBudgetCents: 10000, availableCents: 0, safetyCents: 0});
    const html = dashboardHtml(funded, '2026-11-01');
    expect(html).not.toContain('当前没有可比较额度');
    expect(html).toContain('本月新增额度</span><strong class="overview-amount"><small>¥</small> 0.00');
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
