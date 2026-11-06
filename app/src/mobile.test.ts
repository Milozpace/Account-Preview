import { describe, expect, it } from 'vitest';
import { homeHtml, analysisHtml, billsHtml, poolHtml, myHtml, onboardingHtml, dateInspectionHtml, recordInspectionHtml } from './mobile';
import { emptyQuery } from './navigation';
import fixture from '../../tests/fixtures/app-v2-snapshot.json';
import type { AppMonth } from './bills';

const month = fixture.months[0] as unknown as AppMonth;
describe('mobile presentation', () => {
  it('keeps export in My and preserves the overview layout',()=>{
    expect(myHtml(fixture as never,'已同步')).toContain('data-open="export"');
    expect(homeHtml(month)).not.toContain('data-open="export"');
    expect(homeHtml(month)).not.toContain('导出账单');
  });
  it('offers real first-run setup without showing synthetic amounts or transactions',()=>{
    const html=onboardingHtml(false,false);
    expect(html).toContain('连接电脑账本');expect(html).toContain('data-open="sync"');
    expect(html).not.toContain('◌');
    expect(html).not.toContain('个人净消费');expect(html).not.toContain('data-record=');
    expect(onboardingHtml(true,true)).toContain('data-action="fetch-empty"');
  });
  it('hides unavailable bill tools but keeps preferences and connection available',()=>{
    const html=myHtml(null,'未连接');
    for(const view of ['bills','export','upload'])expect(html).not.toContain(`data-open="${view}"`);
    for(const view of ['sync','settings','about'])expect(html).toContain(`data-open="${view}"`);
  });
  it('touch chart selections have explicit detail actions and escaped snapshot values',()=>{
    const record={...month.records[0],title:'<img onerror=bad>'};
    const html=recordInspectionHtml(record);
    expect(html).toContain('&lt;img');expect(html).not.toContain('<img');
    expect(html).toContain('data-action="chart-record-detail"');
    expect(dateInspectionHtml(month,month.daily[0].date)).toContain('data-action="chart-date-bills"');
    const trend=analysisHtml(month,'trend',null);
    expect(trend).toContain('id="date-inspection"');expect(trend).toContain('id="record-inspection"');
  });
  it('shows one headline, four ordered pool rows and at most five recent consumption records', () => {
    const html = homeHtml(month);
    expect(html).toContain('个人净消费');
    expect((html.match(/class="mobile-pool /g) ?? []).length).toBe(4);
    expect(html.indexOf('运行')).toBeLessThan(html.indexOf('随心'));
    expect(html).toContain('最近消费');
    expect((html.match(/data-record=/g) ?? []).length).toBe(Math.min(5, month.records.length));
    expect(html).toContain('安全积累');
  });
  it('has three analysis themes and only mounts charts on the trend theme', () => {
    const trend = analysisHtml(month, 'trend', null, '2026-10-16');
    expect(trend).toContain('id="operation-chart"');
    expect(trend).toContain('id="scatter-chart"');
    for (const tab of ['composition','calendar'] as const) {
      const html = analysisHtml(month, tab, null, '2026-10-16');
      expect(html).not.toContain('id="operation-chart"');
      expect(html).toContain('aria-selected="true"');
    }
    expect(analysisHtml(month,'calendar',month.records[0].date,'2026-10-16')).toContain(month.records[0].title);
  });
  it('has an honest empty search and structural query results', () => {
    expect(billsHtml(month, {...emptyQuery(), query:'no-such-entry'})).toContain('没有符合条件的记录');
    const html = billsHtml(month,{...emptyQuery(), scope:'structural'});
    expect(html).toContain(month.structuralRecords[0].title);
    expect(html).toContain('结构性支出');
  });
  it('lists income as a third bill scope without a pool chip or pool filter', () => {
    const income = month.incomeRecords[0];
    expect(income).toBeDefined();
    const html = billsHtml(month,{...emptyQuery(), scope:'income'});
    expect(html).toContain('<option value="income" selected>收入</option>');
    expect(html).toContain(income.title);
    expect(html).toContain('id="bill-pool" disabled');
    expect(html).toContain('· 合计 ¥');
    expect(html).not.toContain('record-pool');
  });
  it('never treats inactive budget as zero or masks overrun and negative adjustment', () => {
    const inactive = structuredClone(month);
    inactive.mechanismActive = false;
    expect(homeHtml(inactive)).toContain('该月未设预算，仍按用途统计消费');
    const pool = structuredClone(month.pools[0]);
    pool.availableCents = -1234; pool.overrunCents = 1234;
    pool.spentCents = -100; pool.usageRatio = -0.01;
    const html = poolHtml({...month,pools:[pool,...month.pools.slice(1)]},pool.id);
    expect(html).toContain('超支');
    expect(html).toContain('12.34');
    expect(html).toContain('负调整');
  });
  it('keeps auxiliary entrances on My rather than introducing an account service', () => {
    const html = myHtml(fixture as never,'');
    for (const text of ['账单查询','数据同步','应用设置','关于']) expect(html).toContain(text);
    expect(html).not.toContain('登录');
  });
  it('keeps one ledger identity and date while removing redundant menu descriptions and slogans',()=>{
    const html=myHtml(fixture as never,'已同步');
    expect((html.match(/账单截至/g)??[]).length).toBe(1);
    expect((html.match(/生活账本/g)??[]).length).toBe(1);
    expect(html).not.toContain('查看近两个月的消费与明细');
    expect(html).not.toContain('让数字回到生活里');
  });
});
