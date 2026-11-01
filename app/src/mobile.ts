/** Pure touch-oriented views. Trusted snapshot values are formatted, never recalculated. */
import { boundedWidth, dateTitle, escapeHtml as e, money, percent, shanghaiToday } from '../../dashboard/src/helpers';
import { calendarHtml, categoriesHtml, dashboardRecordRow } from '../../dashboard/src/view';
import { poolColors, poolNames } from '../../dashboard/src/palette';
import { filterRecords, type AppV2Month, type AppV2Snapshot } from './bills';
import { emptyQuery, groupDays, selectBills, type BillQuery, type AnalysisTab } from './navigation';
import type { LedgerRecord } from '../../dashboard/src/types';
import {uiIcon} from '../../dashboard/src/ui-icons';

const arrow = `<span class="row-chevron" aria-hidden="true">${uiIcon('right')}</span>`;
export const empty = (title: string, text = '') => `<div class="empty-state"><span aria-hidden="true">◌</span><h3>${e(title)}</h3>${text?`<p>${e(text)}</p>`:''}</div>`;
const sectionHeading = (title: string, action = '') => `<div class="mobile-section-heading"><h2>${e(title)}</h2>${action}</div>`;
const more = `<button class="quiet-button" type="button" data-action="all-bills">查看全部 ${uiIcon('right')}</button>`;

export function onboardingHtml(paired:boolean,configured:boolean):string {
  return `<section class="onboarding card-block">${empty('尚无账单数据',!paired?'先连接同步，并扫描电脑上的配对二维码。':!configured?'账本已配对，连接同步后即可读取账单。':'账本已连接，检查云端更新以读取已发布的账单。')}<div class="actions"><button type="button" data-open="sync">${paired&&configured?'数据同步':'连接与配对'}</button>${paired&&configured?'<button type="button" data-action="fetch-empty">检查云端更新</button>':''}</div></section>`;
}

export function dateInspectionHtml(month:AppV2Month,date:string|null):string {
  const day=month.daily.find(item=>item.date===date);
  return day?`<div class="chart-selection-card"><div><strong>${e(dateTitle(day.date))}</strong><span>${day.count} 笔 · 净消费 ¥ ${money(day.amountCents)}</span></div><button type="button" class="chart-detail-button" data-action="chart-date-bills" data-inspect-date="${e(day.date)}">查看当天账单 ${uiIcon('right')}</button></div>`:'<p class="hint">点日期查看数据，再打开当天账单。</p>';
}
export function recordInspectionHtml(record:LedgerRecord|null):string {
  return record?`<div class="chart-selection-card"><div><strong>${e(record.title)}</strong><span>${e(dateTitle(record.date))} · ${e(poolNames[record.pool]??record.pool)} · ¥ ${money(record.amountCents)}${record.amountCents<0?' · 负调整':''}</span></div><button type="button" class="chart-detail-button" data-action="chart-record-detail" data-inspect-record="${e(record.id)}">查看账单详情 ${uiIcon('right')}</button></div>`:'<p class="hint">点消费查看数据，再打开账单详情。</p>';
}

export function groupedRecords(records: AppV2Month['records'], structural = false): string {
  return groupDays(records).map(day => `<section class="bill-day"><div class="day-heading"><h3>${e(dateTitle(day.date))}</h3><span>${structural?'合计':'净消费'} ¥ ${money(day.cents)}</span></div>${day.records.map(record => dashboardRecordRow(record)).join('')}</section>`).join('');
}

export function homeHtml(month: AppV2Month): string {
  const summary=month.summary,active=month.mechanismActive;
  const value=(cents:number)=>money(active?cents:null);
  const recent=filterRecords(month.records,emptyQuery()).slice(0,5);
  return `<section class="mobile-overview" aria-label="月度总览">
    <div class="hero-summary"><span class="eyebrow">月度观察</span><button type="button" class="hero-amount" data-action="all-bills"><span>个人净消费</span><strong><small>¥</small> ${money(summary.personalCents)}</strong><span class="hero-detail">查看消费明细 ${arrow}</span></button>
    <div class="hero-secondary"><div><span>本月新增额度</span><strong>¥ ${value(summary.monthlyBudgetCents)}</strong></div><div><span>各池可用合计</span><strong>¥ ${value(summary.availableCents)}</strong></div></div></div>
    ${!active?'<p class="mechanism-note">该月未设预算，仍按用途统计消费；可在电脑版“四池预算”设置额度。</p>':''}
    ${sectionHeading('四个资金池')}<div class="mobile-pools">${month.pools.filter(pool=>['operation','free','travel','growth'].includes(pool.id)).map(pool=>{
      const over=active&&pool.overrunCents>0;
      return `<button type="button" class="mobile-pool ${over?'is-overrun':''}" data-pool="${pool.id}" style="--pool-color:${poolColors[pool.id]}"><div class="pool-row-top"><span class="mobile-pool-name"><i></i>${e(pool.name)}池</span><span class="pool-balance"><small>${over?'超支':'剩余额度'}</small><strong>¥ ${value(over?pool.overrunCents:pool.availableCents)}</strong>${arrow}</span></div><div class="pool-track ${over?'is-overrun':''}" aria-hidden="true"><i style="width:${boundedWidth(active?pool.usageRatio:null)}%"></i></div><div class="pool-row-bottom"><span>已用 ¥ ${money(pool.spentCents)}${pool.spentCents<0?' · 负调整':''}</span><span>${percent(active?pool.usageRatio:null)}</span></div></button>`;
    }).join('')}</div>
    <div class="safety-row"><span><i class="safety-mark" aria-hidden="true">◇</i> 安全积累</span><strong>¥ ${value(summary.safetyCents)}</strong></div>
    <div class="overview-secondary"><button class="scope-item" data-scope="structural" type="button"><span>结构性支出</span><strong>¥ ${money(summary.structuralCents)}</strong>${arrow}</button><button class="scope-item" data-scope="unknown" type="button"><span>待归类消费</span><strong>¥ ${money(summary.unknownCents)}</strong>${arrow}</button></div>
    ${sectionHeading('最近消费',more)}<div class="recent-records">${recent.length?recent.map(record=>dashboardRecordRow(record)).join(''):empty('这个月还没有消费记录','同步后会在这里显示。')}</div>
  </section>`;
}

const tabs: Array<[AnalysisTab,string]>=[['trend','趋势'],['composition','构成'],['calendar','日历']];
export function analysisHtml(month: AppV2Month, tab: AnalysisTab, date: string|null, today=shanghaiToday(), expanded=false): string {
  const navigation=`<div class="analysis-tabs" role="tablist" aria-label="分析主题">${tabs.map(([id,label])=>`<button id="tab-${id}" type="button" role="tab" data-analysis="${id}" aria-controls="analysis-panel" aria-selected="${id===tab}">${label}</button>`).join('')}</div>`;
  let body='';
  if(tab==='trend') body=`<section class="mobile-chart-panel"><div class="panel-header"><h2>累计净消费</h2><span class="small-unit">元</span></div><div class="line-legend"><span><i></i>实际</span><span><i></i>参考</span></div><div id="operation-chart" class="chart line-chart" role="img" aria-label="累计净消费与总额度参考，点击日期查看数据"></div><div id="date-inspection" class="chart-inspection" aria-live="polite">${dateInspectionHtml(month,null)}</div></section><section class="mobile-chart-panel"><div class="panel-header"><h2>每一笔消费的落点</h2><span class="small-unit">元 / 笔</span></div><div class="scatter-legend">${Object.entries(poolNames).map(([id,name])=>`<button type="button" data-highlight-pool="${id}" aria-pressed="false"><i style="background:${poolColors[id]}"></i>${name}</button>`).join('')}</div><div id="scatter-chart" class="chart scatter-chart" role="img" aria-label="消费日期与金额散点，点击消费点查看数据"></div><div id="record-inspection" class="chart-inspection" aria-live="polite">${recordInspectionHtml(null)}</div>${month.records.length?'':empty('暂无消费记录')}</section>`;
  if(tab==='composition') {
    const top=month.topIds.slice(0,10).map(id=>month.records.find(r=>r.id===id)).filter((r):r is AppV2Month['records'][number]=>Boolean(r));
    body=`<section class="mobile-chart-panel"><div class="panel-header"><h2>类别分布</h2><span class="small-unit">元 / 占比</span></div><div id="categories-body">${categoriesHtml(month,expanded)}</div></section><section class="mobile-chart-panel"><div class="panel-header"><h2>Top 10 大额消费</h2><span class="small-unit">元 / 笔</span></div>${top.length?top.map((r,i)=>dashboardRecordRow(r,i+1)).join(''):empty('暂无大额消费')}</section>`;
  }
  if(tab==='calendar') {
    const selected=date&&date.startsWith(month.month)?date:null;
    const records=month.records.filter(r=>r.date===selected);
    const day=month.daily.find(d=>d.date===selected);
    body=`<section class="mobile-chart-panel mobile-calendar"><div class="panel-header"><h2>${Number(month.month.slice(5))} 月消费日历</h2><span class="small-unit">元 / 日</span></div>${calendarHtml(month,today,selected??'')}</section><section class="calendar-records"><div class="mobile-section-heading"><h2>${selected?e(dateTitle(selected)):'选择一个日期'}</h2>${day?`<span>${day.count} 笔 · ¥ ${money(day.amountCents)}</span>`:''}</div>${selected?records.length?records.map(r=>dashboardRecordRow(r)).join(''):empty(selected>today?'未来日期':'未记录消费'):empty('点日期，查看当天消费')}</section>`;
  }
  return `<section class="analysis">${navigation}<div role="tabpanel" id="analysis-panel" aria-labelledby="tab-${tab}">${body}</div></section>`;
}

export function billsHtml(month: AppV2Month, query: BillQuery): string {
  const records=selectBills(month,query);
  const source=query.scope==='structural'?month.structuralRecords:month.records;
  const categories=[...new Set(source.map(r=>r.category))].sort((a,b)=>a.localeCompare(b,'zh-CN'));
  return `<section class="bills"><div class="bill-toolbar"><label class="search-field"><span aria-hidden="true">${uiIcon('search')}</span><input id="bill-search" type="search" placeholder="搜索账单标题" value="${e(query.query)}" aria-label="搜索账单标题"><button class="search-clear" type="button" data-action="clear-search" aria-label="清除搜索" ${query.query?'':'hidden'}>${uiIcon('close')}</button></label>
    <div class="bill-filter-row"><label><span class="sr-only">记录类型</span><select id="bill-scope"><option value="personal" ${query.scope==='personal'?'selected':''}>个人消费</option><option value="structural" ${query.scope==='structural'?'selected':''}>结构性支出</option></select></label><label><span class="sr-only">分类</span><select id="bill-category"><option value="">全部分类</option>${categories.map(c=>`<option value="${e(c)}" ${query.category===c?'selected':''}>${e(c)}</option>`).join('')}</select></label><label><span class="sr-only">资金池</span><select id="bill-pool" ${query.scope==='structural'?'disabled':''}><option value="">全部资金池</option>${Object.entries(poolNames).map(([id,name])=>`<option value="${id}" ${query.pool===id?'selected':''}>${name}</option>`).join('')}</select></label></div>
    ${query.date?`<div class="active-filters"><span>${e(dateTitle(query.date))}</span><button type="button" class="quiet-button" data-action="clear-date">取消日期 ×</button></div>`:''}
    <div class="bill-result-heading"><p class="bill-count">${records.length} 笔 · ${query.scope==='structural'?'合计':'净消费'} ¥ ${money(records.reduce((s,r)=>s+r.amountCents,0))}</p><button type="button" class="quiet-button" data-action="clear-filters">重置筛选</button></div></div>
    <div class="record-list">${records.length?groupedRecords(records,query.scope==='structural'):empty('没有符合条件的记录','试试其他关键词，或重置筛选。')}</div></section>`;
}

export function poolHtml(month: AppV2Month, id: string): string {
  const pool=month.pools.find(p=>p.id===id);
  if(!pool) return empty('没有找到这个资金池');
  const active=month.mechanismActive,value=(v:number)=>money(active?v:null);
  const records=selectBills(month,{...emptyQuery(),pool:id});
  return `<section class="pool-detail" style="--pool-color:${poolColors[id]}"><div class="pool-detail-hero"><span class="eyebrow">${e(pool.name)}池</span><h2>剩余额度</h2><strong>¥ ${value(pool.availableCents)}</strong>${active&&pool.overrunCents>0?`<p class="overrun">超支 ¥ ${money(pool.overrunCents)}</p>`:''}<div class="pool-track"><i style="width:${boundedWidth(active?pool.usageRatio:null)}%"></i></div></div><dl class="pool-facts"><div><dt>新增额度</dt><dd>¥ ${value(pool.budgetCents)}</dd></div>${id==='travel'||id==='growth'?`<div><dt>历史承接</dt><dd>¥ ${value(pool.carryCents)}</dd></div>`:''}<div><dt>已用${pool.spentCents<0?' · 负调整':''}</dt><dd>¥ ${money(pool.spentCents)}</dd></div><div><dt>使用进度</dt><dd>${percent(active?pool.usageRatio:null)}</dd></div></dl>${!active?'<p class="mechanism-note">该月未设预算，仍按用途统计消费；可在电脑版“四池预算”设置额度。</p>':''}${sectionHeading('对应消费',`<span class="small-unit">${records.length} 笔</span>`)}${records.length?groupedRecords(records):empty('这个月还没有对应消费')}</section>`;
}

export function myHtml(snapshot: AppV2Snapshot|null, status: string): string {
  const row=(view:string,label:string,mark:string,sub='')=>`<button type="button" class="menu-row" data-open="${view}"><span class="menu-icon" aria-hidden="true">${mark}</span><span><strong>${label}</strong>${sub?`<small>${e(sub)}</small>`:''}</span>${arrow}</button>`;
  return `<section class="my"><div class="my-heading"><div class="account-emblem" aria-hidden="true"><i></i><i></i><i></i><i></i></div><div><h1>生活账本</h1>${snapshot?.sourceLastRecordedAt?`<p>账单截至 ${e(dateTitle(snapshot.sourceLastRecordedAt))}</p>`:''}</div></div><div class="menu-group">${row('bills','账单查询',uiIcon('search'))}${row('export','导出账单',uiIcon('export'))}${row('upload','上传原始账单',uiIcon('upload'))}</div><div class="menu-group">${row('sync','数据同步',uiIcon('sync'),status)}${row('settings','应用设置',uiIcon('settings'))}${row('about','关于',uiIcon('info'))}</div></section>`;
}
