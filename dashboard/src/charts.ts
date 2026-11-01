import { init, use, type EChartsType } from 'echarts/core';
import { LineChart, ScatterChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { LabelLayout } from 'echarts/features';
import { escapeHtml, money, monthProgress } from './helpers';
import type { DashboardMonth, LedgerRecord } from './types';
import { hasComparableAllowance } from './budget-display';

use([LineChart, ScatterChart, GridComponent, TooltipComponent, LabelLayout, CanvasRenderer]);

import { poolColors, poolNames } from './palette';
export { poolColors, poolNames } from './palette';

const instances: { chart: EChartsType; observer: ResizeObserver }[] = [];
let scatterChart: EChartsType | null = null;
const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const fontFamily = '"Segoe UI", "Microsoft YaHei", sans-serif';


function createChart(element: HTMLElement): EChartsType {
  const chart = init(element, undefined, { renderer: 'canvas' });
  const observer = new ResizeObserver(() => chart.resize());
  observer.observe(element);
  instances.push({ chart, observer });
  return chart;
}

export function disposeCharts(): void {
  scatterChart = null;
  for (const { chart, observer } of instances.splice(0)) { observer.disconnect(); chart.dispose(); }
}

export function highlightPool(pool: string | null): void {
  if (!scatterChart) return;
  scatterChart.dispatchAction({ type: 'downplay' });
  if (pool) scatterChart.dispatchAction({ type: 'highlight', seriesName: poolNames[pool] });
}

export function renderCharts(month: DashboardMonth, today: string, onDate: (date: string) => void, onRecord: (record: LedgerRecord) => void, options?:{touch?:boolean;reduceMotion?:boolean;dark?:boolean}): void {
  const lineElement = document.querySelector<HTMLElement>('#operation-chart');
  const scatterElement = document.querySelector<HTMLElement>('#scatter-chart');
  if (!lineElement || !scatterElement) return;
  const colors = options?.dark ? {
    muted:'#a6a9b0',surface:'#202124',border:'#37393e',ink:'#f0f1f3',grid:'#37393e',reference:'#858991',area:'#303339',label:'#c9cdd4',
    pools:{operation:'#9eb8aa',free:'#cfaa98',travel:'#a3b9d1',growth:'#c8bc97',unknown:'#b0b2b8'} as Record<string,string>,
  } : {muted:'#858780',surface:'#fffefa',border:'#e6e4dc',ink:'#333b35',grid:'#ecece6',reference:'#c5c6b9',area:'#eef2ed',label:'#60675f',pools:poolColors};
  const axisLabel = { color: colors.muted, fontFamily, fontSize: 11 };
  const daily = month.daily;
  const dates = daily.map((day) => day.date);
  let netCents = 0;
  const cumulativeCents = daily.map(day => {
    netCents += day.amountCents;
    return day.date <= today ? netCents : null;
  });
  const totalDays = monthProgress(month.month, today).total;
  const monthlyBudget = month.summary.monthlyBudgetCents ?? 0;
  const referenceCents = daily.map(day => hasComparableAllowance(month) && monthlyBudget > 0
    ? Math.round(monthlyBudget * Number(day.date.slice(8)) / totalDays) : null);
  const formatY = (value: number) => Math.abs(value) >= 1000 ? `${value / 1000}k` : `${value}`;
  const tooltipStyle = {
    backgroundColor: colors.surface, borderColor: colors.border, borderWidth: 1,
    textStyle: { color: colors.ink, fontFamily, fontSize: 12 },
    extraCssText: 'border-radius:12px;padding:12px 16px;box-shadow:0 8px 24px #2e352415;max-width:280px;white-space:normal;overflow-wrap:anywhere;line-height:1.7;',
    transitionDuration: .12,
    confine: true,
  };

  const line = createChart(lineElement);
  line.setOption({
    animation: !(options?.reduceMotion||reducedMotion()), animationDuration: 520, animationEasing: 'cubicOut',
    grid: { left: 45, right: 12, top: 18, bottom: 28 },
    tooltip: {
      ...tooltipStyle, trigger: 'axis', ...(options?.touch?{triggerOn:'click'}:{}),
      formatter: (parameters: unknown) => {
        const params = parameters as { dataIndex: number }[];
        const index = params[0]?.dataIndex;
        const day = daily[index];
        if (!day) return '';
        const recorded = day.date > today ? '未来日期' : day.count > 0 ? `${day.count} 笔个人/项目消费` : '未记录消费';
        const cumulative = day.date > today ? '未来日期' : `累计净消费　¥ ${money(cumulativeCents[index])}`;
        const reference = referenceCents[index] == null ? '暂无额度' : `¥ ${money(referenceCents[index])}`;
        return `<strong>${escapeHtml(day.date)}</strong><br>${escapeHtml(cumulative)}<br>总额度参考　${reference}<br><span style="color:${colors.muted}">${escapeHtml(recorded)}</span>`;
      },
    },
    xAxis: { type: 'category', data: dates, boundaryGap: false, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { ...axisLabel, formatter: (value: string) => `${Number(value.slice(8))}日`, interval: 5 } },
    yAxis: { type: 'value', axisLine: { show: false }, axisTick: { show: false }, axisLabel: { ...axisLabel, formatter: formatY }, splitLine: { lineStyle: { color: colors.grid, type: 'dashed' } } },
    series: [
      { name: '总额度参考', type: 'line', data: referenceCents.map(cents => cents == null ? null : cents / 100), symbol: 'none', lineStyle: { color: colors.reference, width: 1.5, type: 'dashed' }, emphasis: { disabled: true } },
      { name: '累计净消费', type: 'line', data: cumulativeCents.map(cents => cents == null ? null : cents / 100), showSymbol: false, symbol: 'circle', symbolSize: 5, lineStyle: { color: colors.pools.operation, width: 2.5 }, itemStyle: { color: colors.pools.operation }, areaStyle: { color: colors.area, opacity: 0.65 }, connectNulls: false },
    ],
  });
  if(options?.touch){
    // Axis-wide hit testing makes the first tap work even where no symbol is painted.
    line.getZr().on('click',event=>{
      const point=[event.offsetX,event.offsetY];if(!line.containPixel({gridIndex:0},point))return;
      const index=Math.round(Number(line.convertFromPixel({xAxisIndex:0},event.offsetX)));
      if(!dates[index])return;
      line.dispatchAction({type:'showTip',seriesIndex:1,dataIndex:index});onDate(dates[index]);
    });
  }else line.on('click', (event) => { if (typeof event.dataIndex === 'number' && dates[event.dataIndex]) onDate(dates[event.dataIndex]); });

  const scatter = createChart(scatterElement);
  scatterChart = scatter;
  const largest = new Set(month.topIds.slice(0, 3));
  const ids = ['operation', 'free', 'travel', 'growth', 'unknown'];
  scatter.setOption({
    animation: !(options?.reduceMotion||reducedMotion()), animationDuration: 420, animationEasing: 'cubicOut',
    grid: { left: 48, right: 18, top: 24, bottom: 28 },
    tooltip: {
      ...tooltipStyle, trigger: 'item', ...(options?.touch?{triggerOn:'click'}:{}),
      formatter: (parameter: unknown) => {
        const datum = (parameter as { data: { record: LedgerRecord } }).data.record;
        return `<strong>${escapeHtml(datum.title)}</strong><br>${escapeHtml(datum.date)} · ${escapeHtml(poolNames[datum.pool] ?? datum.pool)}<br>¥ ${money(datum.amountCents)}${datum.amountCents < 0 ? ' · 负调整' : ''}`;
      },
    },
    xAxis: { type: 'category', data: dates, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { ...axisLabel, formatter: (value: string) => `${Number(value.slice(8))}日`, interval: 5 } },
    yAxis: { type: 'value', min: (value: { min: number }) => value.min < 0 ? null : 0, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { ...axisLabel, formatter: formatY }, splitLine: { lineStyle: { color: colors.grid, type: 'dashed' } } },
    series: ids.map((id) => ({
      name: poolNames[id], type: 'scatter',
      data: month.records.filter((record) => record.pool === id).map((record) => ({
        value: [record.date, record.amountCents / 100], record,
        symbol: record.amountCents < 0 ? 'diamond' : 'circle',
        symbolSize: largest.has(record.id) ? 11 : 7,
        label: {
          show: largest.has(record.id), formatter: `¥${money(record.amountCents)}`,
          position: 'top', fontSize: 10, color: colors.label, distance: 5,
        },
      })),
      itemStyle: { color: colors.pools[id], opacity: 0.7 },
      labelLayout: { hideOverlap: true },
      emphasis: { focus: 'series', blurScope: 'coordinateSystem', itemStyle: { opacity: 1, borderColor: options?.dark ? colors.surface : '#fff', borderWidth: 2 }, scale: 1.4 },
    })),
  });
  scatter.on('click', (event) => { const datum = event.data as { record?: LedgerRecord } | undefined; if (datum?.record) onRecord(datum.record); });
}
