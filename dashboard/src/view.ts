import {uiIcon} from './ui-icons';
import { boundedWidth, calendarOffset, dateState, escapeHtml, money, monthProgress, percent, ratio } from './helpers';
import { poolColors, poolNames } from './palette';
import type { DashboardMonth, LedgerRecord, Pool } from './types';
import { hasComparableAllowance, hasPoolAllowance } from './budget-display';
const escape = escapeHtml;
const arrow = `<span aria-hidden="true">${uiIcon('upRight')}</span>`;
const budgetValue = (value: number | null | undefined, active: boolean) => active ? `¥ ${money(value)}` : '暂无额度';
const overviewBudgetValue = (value: number | null | undefined, active: boolean) => active ? `<small>¥</small> ${money(value)}` : '<small>暂无额度</small>';
const currency = (value: number | null | undefined) => '<span class="currency">¥</span> ' + money(value);

export function poolHtml(pool: Pool, active: boolean): string {
  active = hasPoolAllowance(pool, active);
  const poolRatio = active ? pool.usageRatio : null;
  const negative = pool.spentCents < 0;
  const overspent = active && (pool.overrunCents ?? 0) > 0;
  return `<article class="pool" style="--pool-color:${poolColors[pool.id]}">
    <div class="pool-heading"><span class="pool-dot" aria-hidden="true"></span><h3>${escape(pool.name)}</h3></div>
    <button class="pool-amount text-button" data-pool="${escape(pool.id)}" aria-label="查看${escape(pool.name)}消费明细">${currency(pool.spentCents)} ${arrow}</button>
    <div class="pool-spent-label">本月已用 <span>${percent(poolRatio)}${negative ? ' · 负调整净额' : ''}</span></div>
    <div class="pool-track ${overspent ? 'is-overrun' : ''}" aria-hidden="true"><i style="width:${boundedWidth(poolRatio)}%"></i></div>
    <div class="pool-remaining"><span>剩余额度</span><strong>${budgetValue(pool.availableCents, active)}</strong></div>
    ${overspent ? `<p class="overrun">超支 ¥ ${money(pool.overrunCents)}</p>` : ''}
    ${active ? `<div class="pool-budget-details"><span>新增 <b>${budgetValue(pool.budgetCents, active)}</b></span>${pool.id === 'travel' || pool.id === 'growth' ? `<span>承接 <b>${budgetValue(pool.carryCents, active)}</b></span>` : ''}</div>` : ''}
  </article>`;
}

export function calendarHtml(month: DashboardMonth, today: string, selectedDate?: string): string {
  const max = Math.max(1, ...month.daily.map((day) => Math.abs(day.amountCents)));
  const empties = Array.from({ length: calendarOffset(month.month) }, () => '<span class="calendar-empty" aria-hidden="true"></span>').join('');
  return `<div class="calendar-weekdays" aria-hidden="true">${['一', '二', '三', '四', '五', '六', '日'].map((day) => `<span>${day}</span>`).join('')}</div><div class="calendar-grid">${empties}${month.daily.map((day) => {
    const status = dateState(day.date, day.count, today);
    const future = status === 'future';
    const intensity = day.count > 0 ? Math.max(1, Math.ceil(Math.sqrt(Math.abs(day.amountCents) / max) * 4)) : 0;
    const description = future ? '未来日期' : day.count ? `¥ ${money(day.amountCents)} · ${day.count} 笔` : '未记录消费';
    return `<button class="calendar-day ${status} level-${intensity} ${day.amountCents < 0 ? 'negative' : ''} ${day.date === today ? 'today' : ''} ${day.date === selectedDate ? 'is-selected' : ''}" type="button" data-date="${day.date}"${selectedDate!==undefined?` aria-pressed="${day.date===selectedDate}"`:''} title="${escape(day.date)} · ${escape(description)}" aria-label="${escape(day.date)}，${escape(description)}"><span>${Number(day.date.slice(8))}</span><small>${future ? '待到来' : day.count ? money(day.amountCents) : '未记录'}</small></button>`;
  }).join('')}</div><div class="calendar-key"><span><i class="key-today"></i>今天</span><span class="heatmap-key">金额低 <i></i><i></i><i></i><i></i> 高</span></div>`;
}

export function dashboardRecordRow(record: LedgerRecord, rank?: number): string {
  return `<button class="record-row" type="button" data-record="${escape(record.id)}">${rank != null ? `<span class="record-rank">${String(rank).padStart(2, '0')}</span>` : ''}<span class="record-main"><strong>${escape(record.title)}</strong><small>${escape(record.date.slice(5).replace('-', '.'))} <span class="record-pool" style="--pool-color:${poolColors[record.pool] || '#9b9891'}">${escape(poolNames[record.pool] ?? '结构性')}</span> · ${escape(record.category)}${record.amountCents < 0 ? ' · 负调整' : ''}</small></span><span class="record-money ${record.amountCents < 0 ? 'negative-money' : ''}">¥ ${money(record.amountCents)}</span><span class="record-arrow" aria-hidden="true">↗</span></button>`;
}

export function categoriesHtml(month: DashboardMonth, expanded = false): string {
  const categories = [...month.categories].sort((a, b) => b.amountCents - a.amountCents || a.name.localeCompare(b.name, 'zh-CN'));
  const visible = expanded ? categories : categories.slice(0, 7);
  const max = Math.max(1, ...categories.map((category) => Math.abs(category.amountCents)));
  if (!categories.length) return '<p class="empty-section">暂无消费记录</p>';
  return `<div class="category-list">${visible.map((category) => `<button class="category-row" data-category="${escape(category.name)}" type="button"><span class="category-label">${escape(category.name)}</span><span class="category-track" aria-hidden="true"><i class="${category.amountCents < 0 ? 'negative' : ''}" style="width:${Math.abs(category.amountCents) / max * 100}%"></i></span><span class="category-value">¥ ${money(category.amountCents)}<small>${percent(ratio(category.amountCents, month.summary.personalCents))}</small></span></button>`).join('')}</div>${categories.length > 7 ? `<button class="expand-button" id="expand-categories" type="button" aria-expanded="${expanded}">${expanded ? '收起较小类别 ↑' : `展开全部 ${categories.length} 个类别 ↓`}</button>` : ''}`;
}


export function dashboardHtml(month: DashboardMonth, today: string, expanded = false, canEditBudget = false): string {
  const summary = month.summary;
  const active = hasComparableAllowance(month);
  const progress = monthProgress(month.month, today);
  const budgetRatio = active ? ratio(summary.personalCents, (summary.monthlyBudgetCents ?? 0) > 0 ? summary.monthlyBudgetCents! : summary.openingBudgetCents ?? 0) : null;
  const pendingCount = month.records.filter(record => record.pool === 'unknown').length;
  const top = month.topIds.slice(0, 10).map((id) => month.records.find((record) => record.id === id)).filter((record): record is LedgerRecord => Boolean(record));
  return `
    <section class="overview" aria-label="月度概览">
      <div class="overview-grid">
        <div class="overview-card"><span>个人净消费</span><strong class="overview-amount"><small>¥</small> ${money(summary.personalCents)}</strong></div>
        <div class="overview-card"><span>本月新增额度</span><strong class="overview-amount">${overviewBudgetValue(summary.monthlyBudgetCents, active)}</strong></div>
        <div class="overview-card"><span>${pendingCount?'各池可用（未含待分池）':'各池可用合计'}</span><strong class="overview-amount">${overviewBudgetValue(summary.availableCents, active)}</strong></div>
        <div class="overview-card"><span>安全积累</span><strong class="overview-amount">${overviewBudgetValue(summary.safetyCents, active)}</strong></div>
      </div>
      <div class="overview-secondary"><button class="scope-item" data-scope="structural" type="button"><span>结构性支出</span><strong>¥ ${money(summary.structuralCents)}</strong>${arrow}</button><button class="scope-item" data-scope="unknown" type="button"><span>待归类消费</span><strong>¥ ${money(summary.unknownCents)}</strong>${arrow}</button></div>
    </section>
    ${!active ? `<div class="mechanism-note" role="note">${month.mechanismActive?'当前没有可比较额度':'未设预算，默认额度为0，仍按用途统计消费。'} ${canEditBudget?'<button type="button" class="back-button" data-action="edit-budget">设置四池预算</button>':'可在电脑版“设置 → 四池预算”修改。'}</div>` : ''}
    <section class="rhythm-row" aria-labelledby="rhythm-title"><div class="rhythm-summary"><p class="eyebrow">01 / 月度节奏</p><h2 id="rhythm-title">时间走到了哪里</h2><div class="progress-block"><div class="progress-label"><span>月度时间</span><strong>${percent(progress.ratio)} <small>${progress.elapsed} / ${progress.total} 天</small></strong></div><div class="progress-track" aria-hidden="true"><i style="width:${boundedWidth(progress.ratio)}%"></i></div></div><div class="progress-block budget-progress"><div class="progress-label"><span>额度参考进度 </span><strong class="${(budgetRatio ?? 0) > 1 ? 'overrun-text' : ''}">${active ? percent(budgetRatio) : '暂无额度'}</strong></div><div class="progress-track ${(budgetRatio ?? 0) > 1 ? 'is-overrun' : ''}" aria-hidden="true"><i style="width:${boundedWidth(budgetRatio)}%"></i></div></div></div><div class="operation-panel"><div class="panel-header"><div><h3>累计净消费</h3></div><div class="line-legend"><span><i></i>实际</span>${active && (summary.monthlyBudgetCents ?? 0) > 0 ? '<span><i></i>总额度参考</span>' : ''}</div></div><div id="operation-chart" class="chart line-chart" role="img" aria-label="个人与项目每日累计净消费图，可在下方日历查看对应日期明细"></div></div></section>
    <section class="pools-section" aria-labelledby="pools-title"><div class="section-heading"><div><p class="eyebrow">02 / 四个资金池</p><h2 id="pools-title">给每一种生活留些空间</h2></div></div>${pendingCount ? `<p class="hint" role="note">${pendingCount} 笔消费待分池，剩余额度尚未完整；请由 Agent 按用途审核。</p>` : ''}<div class="pool-band">${month.pools.map((pool) => poolHtml(pool, active)).join('')}</div></section>
    <section class="distribution-section" aria-labelledby="distribution-title"><div class="section-heading"><div><p class="eyebrow">03 / 日常的形状</p><h2 id="distribution-title">从一天，到一笔</h2></div></div><div class="distribution-grid"><article class="surface calendar-panel"><div class="panel-header"><div><h3>${Number(month.month.slice(5))} 月消费日历</h3></div><span class="small-unit">元 / 日</span></div>${calendarHtml(month, today)}</article><article class="surface scatter-panel"><div class="panel-header"><div><h3>每一笔消费的落点</h3></div><span class="small-unit">元 / 笔</span></div><div class="scatter-legend">${Object.entries(poolNames).map(([id, name]) => `<button type="button" data-highlight-pool="${id}" aria-pressed="false" aria-label="突出${name}池消费"><i style="background:${poolColors[id]}"></i>${name}</button>`).join('')}</div><div id="scatter-chart" class="chart scatter-chart" role="img" aria-label="按日期和金额呈现的个人消费散点图；键盘用户可以通过消费日历或下方 Top 10 打开明细"></div>${month.records.length ? '' : '<p class="chart-empty">暂无消费记录</p>'}</article></div></section>
    <section class="details-section" aria-labelledby="details-title"><div class="section-heading"><div><p class="eyebrow">04 / 消费构成</p><h2 id="details-title">哪些事，占据了这个月</h2></div></div><div class="details-grid"><article class="surface categories-panel"><div class="panel-header"><div><h3>类别分布</h3></div><span class="small-unit">元 / 占比</span></div><div id="categories-body">${categoriesHtml(month, expanded)}</div></article><article class="surface top-panel"><div class="panel-header"><div><h3>Top 10 大额消费</h3></div><span class="small-unit">元 / 笔</span></div><div class="top-list">${top.length ? top.map((record, index) => dashboardRecordRow(record, index + 1)).join('') : '<p class="empty-section">暂无大额消费</p>'}</div></article></div></section>
  `;
}
