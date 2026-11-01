import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import fixture from '../../tests/fixtures/app-v2-snapshot.json';
import type {DashboardMonth} from './types';

const chartOptions = vi.hoisted(() => [] as any[]);
vi.mock('echarts/core', () => ({use: vi.fn(), init: vi.fn(() => ({
  setOption: (options: unknown) => chartOptions.push(options), on: vi.fn(), resize: vi.fn(), dispose: vi.fn(),
}))}));
import {disposeCharts, renderCharts} from './charts';

function unassignedMonth(): DashboardMonth {
  const month = structuredClone(fixture.months[0]) as unknown as DashboardMonth;
  month.month = '2026-10';
  month.mechanismActive = true;
  Object.assign(month.summary, {personalCents: 1800, unknownCents: 1800, monthlyBudgetCents: 0, openingBudgetCents: 0});
  month.daily = [
    {date: '2026-10-01', amountCents: 2500, count: 1, operationCumulativeCents: 0, referenceCents: 11},
    {date: '2026-10-02', amountCents: -700, count: 1, operationCumulativeCents: 0, referenceCents: 22},
    {date: '2026-10-03', amountCents: 0, count: 0, operationCumulativeCents: 0, referenceCents: 33},
  ];
  return month;
}

describe('cumulative personal net spending chart', () => {
  beforeEach(() => {
    chartOptions.length = 0;
    vi.stubGlobal('document', {querySelector: () => ({})});
    vi.stubGlobal('window', {matchMedia: () => ({matches: true})});
    vi.stubGlobal('ResizeObserver', class {observe() {} disconnect() {}});
  });
  afterEach(() => {disposeCharts(); vi.unstubAllGlobals();});

  it('shows all unassigned consumption and refunds for a historical month without mutating the snapshot', () => {
    const month = unassignedMonth();
    const before = JSON.stringify(month);
    renderCharts(month, '2026-11-01', vi.fn(), vi.fn());
    const line = chartOptions[0];
    expect(line.series[1].name).toBe('累计净消费');
    expect(line.series[1].data).toEqual([25, 18, 18]);
    expect(line.series[0].data).toEqual([null, null, null]);
    expect(line.tooltip.formatter([{dataIndex: 1}])).toContain('累计净消费　¥ 18.00');
    expect(line.tooltip.formatter([{dataIndex: 1}])).toContain('暂无额度');
    expect(line.tooltip.formatter([{dataIndex: 1}])).not.toContain('运行累计');
    expect(JSON.stringify(month)).toBe(before);
  });

  it('hides future actuals while retaining today and historical refunds', () => {
    const month = unassignedMonth();
    renderCharts(month, '2026-10-02', vi.fn(), vi.fn());
    expect(chartOptions[0].series[1].data).toEqual([25, 18, null]);
    expect(chartOptions[0].tooltip.formatter([{dataIndex: 2}])).toContain('未来日期');
  });

  it('shows no actuals for a future month', () => {
    renderCharts(unassignedMonth(), '2026-09-30', vi.fn(), vi.fn());
    expect(chartOptions[0].series[1].data).toEqual([null, null, null]);
  });

  it('prorates the total monthly allowance instead of the operation reference', () => {
    const month = unassignedMonth();
    month.summary.monthlyBudgetCents = 31000;
    month.summary.openingBudgetCents = 31000;
    renderCharts(month, '2026-11-01', vi.fn(), vi.fn());
    expect(chartOptions[0].series[0].data).toEqual([10, 20, 30]);
    expect(chartOptions[0].tooltip.formatter([{dataIndex: 1}])).toContain('总额度参考　¥ 20.00');
  });

  it('does not compare an inactive mechanism even if numeric budgets remain', () => {
    const month = unassignedMonth();
    month.mechanismActive = false;
    month.summary.monthlyBudgetCents = 31000;
    renderCharts(month, '2026-11-01', vi.fn(), vi.fn());
    expect(chartOptions[0].series[0].data).toEqual([null, null, null]);
    expect(chartOptions[0].series[1].data).toEqual([25, 18, 18]);
  });
});
