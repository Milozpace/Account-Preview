import {uiIcon} from './ui-icons';
import { dashboardHtml, categoriesHtml, dashboardRecordRow } from './view';
import { boundedWidth, calendarOffset, dateState, dateTitle, escapeHtml, money, monthProgress, monthTitle, percent, ratio, shanghaiToday } from './helpers';
import { disposeCharts, highlightPool, poolColors, poolNames, renderCharts } from './charts';
import type { DashboardMonth, DashboardSnapshot, DashboardStatus, LedgerRecord, Pool } from './types';
import { createDrawerMotion, mountMotion, reducedMotion } from './motion';
import {dashboardHeaderHtml} from './header';
import {openExportDialog} from './export-dialog';
import {downloadExport, type SaveExport} from './export-ui';
import {desktopStartupHtml,mountDesktopMonthPicker} from './desktop-ui';


/** One presentation/controller for the browser and the Windows app. */
export type DashboardApi = <T>(path: string, options?: RequestInit) => Promise<T>;
export interface DashboardOptions {
  api: DashboardApi;
  readOnly?: boolean;
  subscribe?: (changed: (snapshotChanged: boolean) => void) => (() => void);
  isDark?: () => boolean;
  subscribeTheme?: (changed: () => void) => (() => void);
  saveExport?: SaveExport;
  openSettings?: ()=>void;
  desktopApp?: boolean;
  showIncome?: boolean;
  retryLocal?: ()=>Promise<void>;
}
function previousMonth(month: string): string {
  const date = new Date(`${month}-15T12:00:00Z`);date.setUTCMonth(date.getUTCMonth()-1);return date.toISOString().slice(0,7);
}
export function mountDashboard(options: DashboardOptions): void {
  const app = document.querySelector<HTMLDivElement>('#app')!;
  app.hidden = false;
  document.title = '生活账本 · 月度观察';
  if (!document.querySelector('.skip-link')) document.body.insertAdjacentHTML('afterbegin', '<a class="skip-link" href="#main">跳至月度概览</a>');
  const api = options.api;
  const escape = escapeHtml;
  const state: {
    snapshot: DashboardSnapshot | null; status: DashboardStatus | null; selectedMonth: string;
    loading: boolean; requesting: boolean; error: string | null; expanded: boolean;
  } = { snapshot: null, status: null, selectedMonth: '', loading: true, requesting: false, error: null, expanded: false };
  let statusRequest: Promise<void> | null = null;
  let snapshotRequest: Promise<void> | null = null;
  let syncTimer: ReturnType<typeof setTimeout> | null = null;
  let drawerRecords: LedgerRecord[] = [];
  let drawerLimit = 60;
  let disposeMotion: () => void = () => {};
  let disposeCategoryMotion: () => void = () => {};
  let detailSource: HTMLElement | null = null;

  const arrow = '<span aria-hidden="true">↗</span>';
  const icon = '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M5 14a7 7 0 0 0 12 3l2-2M19 10A7 7 0 0 0 7 7L5 9" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M14 15h5v5M5 4v5h5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  app.innerHTML = `
    <div class="page-shell">
      ${dashboardHeaderHtml(Boolean(options.openSettings),options.desktopApp)}
      <div id="status-bar" class="status-bar" role="status" aria-live="polite"></div>
      <main id="main" tabindex="-1"><div id="content"></div></main>
      <footer class="page-footer"><span>让数字回到生活里。</span><button id="back-to-top" class="back-to-top" type="button">回到顶部 ↑</button></footer>
    </div>
    <dialog id="detail-drawer" class="detail-drawer" aria-labelledby="detail-title"><div class="drawer-header"><div><p class="eyebrow">DETAILS / 月度明细</p><h2 id="detail-title"></h2></div><button id="close-drawer" class="icon-button" aria-label="关闭明细" type="button">${uiIcon('close')}</button></div><p id="detail-description" class="drawer-description"></p><div id="detail-body"></div></dialog>
  `;

  const content = document.querySelector<HTMLDivElement>('#content')!;
  const selector = document.querySelector<HTMLSelectElement>('#month-selector')!;
  const monthPicker=options.desktopApp?mountDesktopMonthPicker(selector):undefined;
  const syncButton = document.querySelector<HTMLButtonElement>('#sync-button')!;
  const dialog = document.querySelector<HTMLDialogElement>('#detail-drawer')!;
  const drawerMotion = createDrawerMotion(dialog);
  const today = () => state.status?.today || shanghaiToday();
  const selected = () => state.snapshot?.months.find((month) => month.month === state.selectedMonth);
  const budgetValue = (value: number | null | undefined, active: boolean) => active ? money(value) : '—';
  const currency = (value: number | null | undefined) => `<span class="currency">¥</span> ${money(value)}`;


  function renderStatus(): void {
    const status = state.status;
    const busy = state.requesting || Boolean(status?.syncing);
    syncButton.disabled = busy || Boolean(options.readOnly);
    const firstSyncButton = document.querySelector<HTMLButtonElement>('#first-sync-button');
    if (firstSyncButton) firstSyncButton.disabled = busy || Boolean(options.readOnly);
    syncButton.classList.toggle('is-syncing', busy);
    syncButton.querySelector('span')!.textContent = busy ? '正在同步' : '同步展示';
    selector.disabled = !state.snapshot;
    document.querySelector<HTMLButtonElement>('#export-button')!.disabled=!state.snapshot;
    monthPicker?.update();
    syncButton.setAttribute('aria-label',busy?'正在同步':'同步展示');
    const error = state.error || status?.error;
    let message = state.loading ? '正在读取…' : '';
    let tone = '';
    if (busy) { message = '正在同步…'; tone = 'busy'; }
    else if (error) { message = `${error}${state.snapshot ? ' · 继续显示上次成功快照。' : ''}`; tone = 'warning'; }
    else if (status?.monthChanged) { message = '月份已更新 · 请同步'; tone = 'warning'; }
    else if (status?.sourceChanged && state.snapshot) { message = '账本有更新 · 请同步'; tone = 'warning'; }
    else if (!state.loading && !state.snapshot) { message = '尚无数据 · 请同步'; }
    const generated = state.snapshot?.generatedAt;
    const statusBar = document.querySelector<HTMLElement>('#status-bar')!;
    statusBar.hidden = !message;
    statusBar.innerHTML = message ? `<span class="status-message ${tone}"><i aria-hidden="true"></i>${escape(message)}</span>` : '';
    syncButton.title = [generated ? `更新于 ${dateTitle(generated, true)}` : '', state.snapshot?.sourceLastRecordedAt ? `最新记录 ${dateTitle(state.snapshot.sourceLastRecordedAt, true)}` : '', status?.syncDetail || ''].filter(Boolean).join(' · ');
  }

  function renderSelector(): void {
    if (!state.snapshot) return;
    selector.innerHTML = state.snapshot.months.map((month) => `<option value="${escape(month.month)}">${escape(monthTitle(month.month))}${month.month === today().slice(0,7) ? ' · 本月' : month.month === previousMonth(today().slice(0,7)) ? ' · 上月' : ''}</option>`).join('');
    selector.value = state.selectedMonth;
  }

  function render(): void {
    if(options.desktopApp)app.classList.toggle('desktop-no-snapshot',!state.snapshot);
    disposeMotion();disposeCategoryMotion();
    disposeCharts();
    renderSelector();
    renderStatus();
    const month = selected();
    if (!month) {
      if(options.desktopApp){
        content.innerHTML=desktopStartupHtml(state.loading,state.status?.localState==='error',state.status?.error||state.error||'',state.requesting||Boolean(state.status?.syncing));
        document.querySelector('#first-sync-button')?.addEventListener('click',sync);
        document.querySelector('#startup-settings')?.addEventListener('click',()=>options.openSettings?.());
        document.querySelector('#retry-local')?.addEventListener('click',()=>void retryLocal());
        return;
      }
      content.innerHTML = `<section class="first-sync"><span class="first-sync-art" aria-hidden="true">◌</span><p class="eyebrow">A LITTLE CLARITY / 月度观察</p><h1>${state.loading ? '正在打开生活账本' : '从这个月开始，看见生活的流向。'}</h1><p>${state.loading ? '正在读取本地数据。' : '同步账单，开始查看。'}</p>${state.loading ? '' : '<button class="primary-button" id="first-sync-button" type="button">同步展示 <span aria-hidden="true">↗</span></button>'}</section>`;
      document.querySelector('#first-sync-button')?.addEventListener('click', sync);
      return;
    }
    content.innerHTML = dashboardHtml(month, today(), state.expanded, false, options.showIncome ?? true);
    bindContentActions();
    redrawCharts();
    disposeMotion = mountMotion(content);
  }

  function redrawCharts(): void {
    const month = selected();
    if (!month) return;
    const pinned = content.querySelector<HTMLElement>('[data-highlight-pool][aria-pressed="true"]')?.dataset.highlightPool;
    disposeCharts();
    renderCharts(month, today(), (date) => openDate(date), (record) => openRecord(record.id), { dark: options.isDark?.() ?? false, reduceMotion: options.desktopApp ? false : undefined });
    if (pinned) highlightPool(pinned);
  }

  function bindContentActions(): void {
    content.querySelectorAll<HTMLElement>('[data-date], [data-category], [data-record], [data-pool], [data-scope]').forEach((button) => {
      button.setAttribute('aria-haspopup', 'dialog');button.setAttribute('aria-controls', 'detail-drawer');
      button.setAttribute('aria-expanded', 'false');
    });
    content.querySelectorAll<HTMLButtonElement>('[data-date]').forEach((button) => button.addEventListener('click', () => openDate(button.dataset.date!)));
    content.querySelectorAll<HTMLButtonElement>('[data-category]').forEach((button) => button.addEventListener('click', () => openCategory(button.dataset.category!)));
    content.querySelectorAll<HTMLButtonElement>('[data-record]').forEach((button) => button.addEventListener('click', () => openRecord(button.dataset.record!)));
    content.querySelectorAll<HTMLButtonElement>('[data-pool]').forEach((button) => button.addEventListener('click', () => {
      const pool = button.dataset.pool!;
      openDrawer(`${poolNames[pool]}池`, '', selected()!.records.filter((record) => record.pool === pool));
    }));
    content.querySelectorAll<HTMLButtonElement>('[data-scope]').forEach((button) => button.addEventListener('click', () => {
      const month = selected()!;
      if (button.dataset.scope === 'structural') openDrawer('结构性支出', '', month.structuralRecords);
      else if (button.dataset.scope === 'income') openDrawer('本月收入', '仅统计已入账的收入记录；未导入账本的真实收入不计入', month.incomeRecords);
      else openDrawer('待归类消费', '', month.records.filter((record) => record.pool === 'unknown'));
    }));
    bindCategoryToggle();
    let pinned: string | null = null;
    const legends = content.querySelectorAll<HTMLButtonElement>('[data-highlight-pool]');
    legends.forEach((button) => {
      const pool = button.dataset.highlightPool!;
      button.addEventListener('pointerenter', () => highlightPool(pool));
      button.addEventListener('pointerleave', () => highlightPool(pinned));
      button.addEventListener('focus', () => highlightPool(pool));
      button.addEventListener('blur', () => highlightPool(pinned));
      button.addEventListener('click', () => {
        pinned = pinned === pool ? null : pool;
        legends.forEach((legend) => legend.setAttribute('aria-pressed', String(legend.dataset.highlightPool === pinned)));
        highlightPool(pinned);
      });
    });
  }

  function bindCategoryActions(): void {
    content.querySelectorAll<HTMLButtonElement>('[data-category]').forEach((button) => {
      button.setAttribute('aria-haspopup', 'dialog');button.setAttribute('aria-controls', 'detail-drawer');button.setAttribute('aria-expanded', 'false');
      button.addEventListener('click', () => openCategory(button.dataset.category!));
    });
    bindCategoryToggle();
  }

  function bindCategoryToggle(): void {
    document.querySelector('#expand-categories')?.addEventListener('click', () => {
      state.expanded = !state.expanded;
      const section = document.querySelector<HTMLElement>('#categories-body')!;
      const top = section.getBoundingClientRect().top;
      section.innerHTML = categoriesHtml(selected()!, state.expanded);bindCategoryActions();
      // Keep the same control reachable without jumping to the expanded footer.
      const toggle = document.querySelector<HTMLButtonElement>('#expand-categories');
      toggle?.focus({ preventScroll: true });
      if (!state.expanded && top < 0) section.scrollIntoView({ block: 'start', behavior: 'auto' });
      disposeCategoryMotion();disposeCategoryMotion = mountMotion(section);
    });
  }

  function openDate(date: string): void {
    const month = selected();
    if (!month) return;
    const daily = month.daily.find((day) => day.date === date);
    const description = date > today() ? '未来日期' : daily?.count ? '' : '未记录消费';
    openDrawer(dateTitle(date), description, month.records.filter((record) => record.date === date), content.querySelector<HTMLButtonElement>(`[data-date="${date}"]`));
  }

  function openCategory(category: string): void {
    openDrawer(category, '', selected()!.records.filter((record) => record.category === category));
  }

  function openRecord(id: string): void {
    const month = selected();
    const record = month && [...month.records, ...month.structuralRecords, ...month.incomeRecords].find((item) => item.id === id);
    if (!record) return;
    const scopeLabel = record.scope === 'STRUCTURAL' ? '结构性支出' : record.scope === 'INCOME' ? '收入' : poolNames[record.pool] ?? record.scope;
    openDrawer(record.title, `${dateTitle(record.time, true)} · ${record.category} · ${scopeLabel}`, [record]);
  }

  function openDrawer(title: string, description: string, records: LedgerRecord[], source?: HTMLElement | null): void {
    if (detailSource) { detailSource.classList.remove('is-selected');detailSource.setAttribute('aria-expanded', 'false'); }
    const focused = document.activeElement;
    detailSource = source ?? (focused instanceof HTMLButtonElement && content.contains(focused) && focused.hasAttribute('aria-haspopup') ? focused : null);
    detailSource?.classList.add('is-selected');detailSource?.setAttribute('aria-expanded', 'true');
    document.querySelector('#detail-title')!.textContent = title;
    const detailDescription = document.querySelector<HTMLElement>('#detail-description')!;
    detailDescription.textContent = description;
    detailDescription.hidden = !description;
    drawerRecords = [...records].sort((a, b) => b.time.localeCompare(a.time) || b.id.localeCompare(a.id));
    drawerLimit = 60;
    renderDrawer();
    dialog.classList.remove('is-closing');dialog.scrollTop = 0;
    drawerMotion.open();document.body.classList.add('drawer-open');
  }

  async function closeDetails(immediate = false): Promise<void> {
    if (!immediate && dialog.classList.contains('is-closing')) return;
    dialog.classList.add('is-closing');
    await drawerMotion.close(immediate);
    if (!dialog.open) dialog.classList.remove('is-closing');
  }

  function renderDrawer(): void {
    const total = drawerRecords.reduce((sum, record) => sum + record.amountCents, 0);
    const body = document.querySelector('#detail-body')!;
    body.innerHTML = `<div class="drawer-total"><span>${drawerRecords.length} 笔记录</span><strong>净额 ¥ ${money(total)}</strong></div>${drawerRecords.length ? `<div class="drawer-list">${drawerRecords.slice(0, drawerLimit).map((record) => `<article class="drawer-record"><div><h3>${escape(record.title)}</h3><p>${escape(dateTitle(record.time, true))}</p><div class="detail-tags"><span>${escape(record.category)}</span><span>${escape(record.scope === 'INCOME' ? '收入' : record.scope === 'STRUCTURAL' ? '结构性支出' : poolNames[record.pool] ?? record.pool)}</span>${record.amountCents < 0 ? '<span class="negative-tag">负调整</span>' : ''}</div></div><strong class="${record.amountCents < 0 ? 'negative-money' : ''}">¥ ${money(record.amountCents)}</strong></article>`).join('')}</div>` : '<div class="drawer-empty"><span aria-hidden="true">◌</span><p>没有可展示的记录</p></div>'}${drawerRecords.length > drawerLimit ? `<button id="more-details" class="expand-button" type="button">再显示 ${Math.min(60, drawerRecords.length - drawerLimit)} 笔</button>` : ''}`;
    document.querySelector('#more-details')?.addEventListener('click', () => { drawerLimit += 60; renderDrawer(); });
  }

  async function loadSnapshot(): Promise<void> {
    if (snapshotRequest) return snapshotRequest;
    snapshotRequest = (async () => {
      const snapshot = await api<DashboardSnapshot>('/api/dashboard');
      if (snapshot.schemaVersion !== 2 || !Array.isArray(snapshot.months) || !snapshot.months.length) throw new Error('快照格式暂不支持，请重新同步。');
      state.snapshot = snapshot;
      if (!snapshot.months.some((month) => month.month === state.selectedMonth)) state.selectedMonth = snapshot.calendarMonth;
      if (!snapshot.months.some((month) => month.month === state.selectedMonth)) state.selectedMonth = snapshot.months[0].month;
      state.error = null;
      if (dialog.open) await closeDetails(true);
      render();
    })();
    try { await snapshotRequest; } finally { snapshotRequest = null; }
  }

  async function refreshStatus(): Promise<void> {
    if (statusRequest) return statusRequest;
    statusRequest = (async () => {
      try {
        const status = await api<DashboardStatus>('/api/status');
        state.status = status;
        state.error = null;
        if (status.generatedAt && status.generatedAt !== state.snapshot?.generatedAt && !status.syncing && !status.error) await loadSnapshot();
      } catch (error) { state.error = error instanceof Error ? error.message : '暂时无法读取同步状态。'; }
      renderStatus();
      if (state.status?.syncing && !syncTimer) syncTimer = setTimeout(() => { syncTimer = null; void refreshStatus(); }, 1500);
    })();
    try { await statusRequest; } finally { statusRequest = null; }
  }

  async function sync(): Promise<void> {
    if (options.readOnly || state.requesting || state.status?.syncing) return;
    state.requesting = true;
    state.error = null;
    if(options.desktopApp&&!state.snapshot)render();else renderStatus();
    try {
      await api<unknown>('/api/sync', { method: 'POST', headers: { 'X-Dashboard-Request': '1' } });
      await refreshStatus();
    } catch (error) { state.error = error instanceof Error ? error.message : '同步未完成，请重试。'; }
    finally { state.requesting = false;if(options.desktopApp&&!state.snapshot)render();else renderStatus(); }
  }

  async function retryLocal():Promise<void>{
    if(state.loading||!options.retryLocal)return;
    state.loading=true;state.error=null;render();
    try{await options.retryLocal();await loadSnapshot();}
    catch(error){state.error=error instanceof Error?error.message:'本机快照读取失败。';}
    finally{await refreshStatus();state.loading=false;render();}
  }

  selector.addEventListener('change', () => { state.selectedMonth = selector.value; state.expanded = false; render(); });
  syncButton.addEventListener('click', sync);
  document.querySelector('#export-button')!.addEventListener('click',()=>{if(state.snapshot)openExportDialog(state.snapshot,state.selectedMonth,options.saveExport??downloadExport,options.showIncome??true);});
  document.querySelector('#app-settings-button')?.addEventListener('click',()=>options.openSettings?.());
  document.querySelector('#back-to-top')!.addEventListener('click', () => {
    window.scrollTo({ top: 0, behavior: reducedMotion() ? 'instant' : 'smooth' });
    document.querySelector<HTMLElement>('.brand')?.focus({ preventScroll: true });
  });
  document.querySelector('#close-drawer')!.addEventListener('click', () => { void closeDetails(); });
  dialog.addEventListener('cancel', (event) => { event.preventDefault();void closeDetails(); });
  dialog.addEventListener('close', () => {
    // A queued native close event must not reset a newly reopened panel.
    if (dialog.open) return;
    document.body.classList.remove('drawer-open');dialog.classList.remove('is-closing');
    detailSource?.classList.remove('is-selected');detailSource?.setAttribute('aria-expanded', 'false');
    if (detailSource?.isConnected) detailSource.focus({ preventScroll: true });
    detailSource = null;
  });
  dialog.addEventListener('click', (event) => { if (event.target === dialog) { const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) void closeDetails(); } });
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') void refreshStatus(); });
  window.addEventListener('focus', () => { void refreshStatus(); });
  const unsubscribeTheme = options.subscribeTheme?.(redrawCharts);
  window.addEventListener('beforeunload', () => { monthPicker?.destroy();unsubscribeTheme?.();unsubscribe?.();disposeMotion();disposeCategoryMotion();disposeCharts(); if (syncTimer) clearTimeout(syncTimer); });

  const unsubscribe = options.subscribe?.((snapshotChanged) => {
    if (snapshotChanged) void loadSnapshot().then(refreshStatus).catch((error) => { state.error = error instanceof Error ? error.message : '本机快照暂不可用。';renderStatus(); });
    else void refreshStatus();
  });

  render();
  void (async () => {
    const results = await Promise.allSettled([loadSnapshot(), refreshStatus()]);
    const snapshotResult = results[0];
    if (snapshotResult.status === 'rejected' && !state.snapshot && state.status?.localState!=='empty') state.error = snapshotResult.reason instanceof Error ? snapshotResult.reason.message : '本地快照暂不可用。';
    state.loading = false;
    render();
  })();
}
