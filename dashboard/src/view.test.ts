import { describe, it, expect } from 'vitest';
import { dashboardHtml, calendarHtml } from './view';
import fixture from '../../tests/fixtures/app-v2-snapshot.json';
import type { DashboardMonth } from './types';

describe('shared dashboard presentation without HTTP or native dependencies', () => {
  const month = fixture.months[0] as unknown as DashboardMonth;
  it('renders the existing desktop dashboard sections and exact cents without changing data', () => {
    const before = JSON.stringify(month);
    const html = dashboardHtml(month, '2026-10-16');
    for (const label of ['个人净消费','本月新增额度','各池可用合计','安全积累','运行池 · 累计消费','消费日历','类别分布','Top 10']) expect(html).toContain(label);
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
});
