import '../../dashboard/src/style.css';
import './product.css';
import {uiIcon} from '../../dashboard/src/ui-icons';
import { invoke, isTauri } from '@tauri-apps/api/core';
import { scan, cancel, Format, checkPermissions, requestPermissions } from '@tauri-apps/plugin-barcode-scanner';
import { PairingQrController } from './pairing-qr';
import { PairingScanSession } from './pairing-scan';
import './pairing-scan.css';
import { RefreshController } from './refresh';
import { availableMonths, coverageNote, getMonth, monthLabel, resolveMonth, type AppV2Snapshot } from './bills';
import { sampleSnapshot } from './sample';
import { dateTitle, escapeHtml, money, shanghaiToday } from '../../dashboard/src/helpers';
import { dashboardHtml } from '../../dashboard/src/view';
import { homeHtml as mobileHome, analysisHtml, billsHtml as mobileBills, poolHtml, myHtml, empty } from './mobile';
import { Navigation, emptyQuery, type RootView, type AnalysisTab, type Screen, type BillQuery } from './navigation';
import { selectBills } from './navigation';
import { groupedRecords } from './mobile';
import { sameSnapshotContent } from './snapshot-state';
import type { LedgerRecord } from '../../dashboard/src/types';
import { onboardingHtml, dateInspectionHtml, recordInspectionHtml } from './mobile';
import { mountMobileMotion, type MobileTransition } from './mobile-motion';
import { loadMotionPreference, saveMotionPreference, prefersReducedMotion, type MotionPreference } from './mobile-preferences';
import {mobileSyncHtml} from './mobile-sync';
import {setupPhase} from './mobile-setup';
import {MobilePickers} from './mobile-picker';
import './mobile-picker.css';
import './mobile-setup.css';
import { loadThemePreference, saveThemePreference, type ThemePreference } from './mobile-theme';
import {buildAnalysisExport} from '../../dashboard/src/analysis-export';
import {exportFormHtml, ExportSession, updateExportFeedback, downloadExport} from '../../dashboard/src/export-ui';
import {desktopSettingsHtml,desktopSyncHtml,desktopConnectionHtml,type SettingsSection} from './desktop-settings';
import {budgetSettingsHtml} from './budget-settings';
import {BudgetSession} from './budget-session';
const budgetSession=new BudgetSession(invoke);
import {BillUploadSession,billUploadHtml,type BillUploadItem} from './bill-upload';
import './bill-upload.css';
import {BillUploadRefresh} from './bill-upload-refresh';

interface LedgerStatusSummary { initialized:boolean;publishEnabled:boolean;cachedRevision:number|null;pendingRevision:number|null;lastPublishedRevision:number|null }
interface LedgerCacheOutcome { revision:number;snapshot:AppV2Snapshot }
type LedgerFetchOutcome = {kind:'updated'|'latest';revision:number;snapshot:AppV2Snapshot;warning?:string}|{kind:'cloud-empty';hasCache:boolean};
interface ProbeSyncSummary {configured:boolean;paired:boolean;pendingRevision:number|null}
interface ProbeStatus {platform:string;cached:boolean;revision:number|null;syntheticRecords:number;storage:string;records:{id:string;label:string;amountMinor:number}[]|null}
type SnapshotSource='none'|'cache'|'cloud'|'sample'|'file'|'local';
interface DesktopConfig {projectRoot:string;pythonExecutable:string}
interface DesktopSnapshot {snapshot:AppV2Snapshot|null;sourceChanged:boolean;monthChanged:boolean}
interface DesktopRefresh {snapshot:AppV2Snapshot;sourceChanged:boolean;monthChanged:boolean;warning?:string;appPublish:{ok:boolean|null;published:boolean;skipped?:boolean;error?:string;reason?:string}}

const nav=new Navigation();
const state={
  view:'home' as Screen['view'],snapshot:null as AppV2Snapshot|null,source:'none' as SnapshotSource,revision:null as number|null,
  selectedMonth:null as string|null,detailId:null as string|null,message:'',messageTone:'' as ''|'tone-warn'|'tone-error',
  ledger:null as LedgerStatusSummary|null,probeConfigured:false,probePaired:false,
  android:typeof navigator!=='undefined'&&/Android/i.test(navigator.userAgent),busy:false,version:'',
  analysisTab:'trend' as AnalysisTab,analysisDate:null as string|null,expanded:false,
  desktopConfig:null as DesktopConfig|null,desktopWarning:'',loading:true,preview:false,
  settingsSection:'general' as SettingsSection,configFeedback:'',configError:false,
  chartDate:null as string|null,chartRecord:null as string|null,
  editingConnection:false,editingPairing:false,probeSummaryLoaded:false,checkingCloud:false,exportMonth:'',
  setupError:'',connectionReadError:'',
};
const exportSession=new ExportSession();
const uploadSession=new BillUploadSession();
let uploadPageActive=true;
const uploadRefresh=new BillUploadRefresh({
  state:()=>({enabled:uploadPageActive&&state.view==='upload'&&!document.hidden&&state.android&&isTauri()&&!state.preview,busy:uploadSession.busy,pending:uploadSession.activeItems.length}),
  check:async()=>{await loadBillUploads(true);return !uploadSession.error;},
});
const main=document.querySelector<HTMLDivElement>('#main')!;
const statusBar=document.querySelector<HTMLElement>('#status-bar')!;
const selector=document.querySelector<HTMLSelectElement>('#month-selector')!;
const settingsButton=document.querySelector<HTMLButtonElement>('#settings-button')!;
const mobile=()=>state.android||(!isTauri()&&typeof window.matchMedia==='function'&&window.matchMedia('(max-width: 700px)').matches);
const currentMonth=()=>getMonth(state.snapshot,state.selectedMonth);
const phonePhase=()=>setupPhase(state.ledger?.initialized??null,state.probeConfigured,state.probeSummaryLoaded);
const phoneReady=()=>phonePhase()==='ready';
const scroll=()=>typeof window.scrollY==='number'?window.scrollY:0;
const restoreScroll=(top:number)=>{if(typeof window.scrollTo==='function')window.scrollTo({top,behavior:'instant'});};
const sourceLabel=()=>state.source==='cloud'?`云端已确认 · 第 ${state.revision} 版`:state.source==='cache'?`本地缓存 · 第 ${state.revision} 版（未连接云端确认）`:state.source==='local'?'本机快照 · 云端状态单独确认':state.source==='file'?'本地核验文件 · 未上云':state.source==='sample'?'合成预览数据 · 非真实账单':'';

function renderStatusBar():void {
  const warning=coverageNote(state.snapshot)||state.desktopWarning;
  const parts=[sourceLabel(),state.message,warning].filter(Boolean);
  const recorded=state.snapshot?.sourceLastRecordedAt;
  const date=recorded&&(!mobile()||state.view==='home')?`账单截至 ${dateTitle(recorded)}`:'';
  statusBar.innerHTML=`<span class="status-summary">${escapeHtml(date||(!state.snapshot?'尚无账单数据':''))}</span><span class="status-detail ${state.messageTone|| (warning?'tone-warn':'')}">${escapeHtml(parts.join(' · '))}</span>`;
  statusBar.title=[...parts,state.snapshot?.generatedAt?`快照生成 ${dateTitle(state.snapshot.generatedAt,true)}`:''].filter(Boolean).join(' · ');
  statusBar.classList.toggle('has-warning',Boolean(warning)||Boolean(state.messageTone)||state.source==='sample');
  if(mobile()&&state.source!=='sample'){
    statusBar.innerHTML=`<span class="status-summary">${escapeHtml(date||(!state.snapshot?'尚无账单数据':''))}</span>${state.snapshot?`<button type="button" class="quiet-button status-connection" data-status-sync>${state.messageTone?'同步需处理':state.source==='cloud'?'已同步':'本地账单'}</button>`:''}`;
    statusBar.querySelector?.('[data-status-sync]')?.addEventListener('click',()=>open({view:'sync'}));
  }
  const hidden=mobile()&&(state.view!=='home'||!state.snapshot)&&state.source!=='sample';
  statusBar.hidden=hidden;statusBar.classList.toggle('is-hidden',hidden);
}
function renderSelector():void {
  selector.innerHTML=availableMonths(state.snapshot).map(m=>`<option value="${m}">${escapeHtml(monthLabel(m))}</option>`).join('');
  if(state.selectedMonth)selector.value=state.selectedMonth;
  selector.disabled=!state.snapshot||state.busy;
}
function open(screen:Screen):void {
  if(mobile()&&((['bills','export','detail','pool'].includes(screen.view)&&!state.snapshot)||(screen.view==='upload'&&!phoneReady())))screen={view:'sync'};
  if(screen.view==='export'){state.exportMonth=state.selectedMonth??'';if(!exportSession.busy)exportSession.clear();}
  hideQr();
  searchComposing=false;queuedRender=false;
  rememberHistory();
  nav.open(screen,scroll());state.view=nav.current.view;state.detailId=screen.recordId??null;
  pushHistory();render('forward');restoreScroll(nav.current.scroll??0);focusHeading();
  if(screen.view==='upload')void loadBillUploads(false);
}
function selectRoot(root:RootView):void {
  if(!['home','analysis','my'].includes(root))return;
  hideQr();searchComposing=false;queuedRender=false;rememberHistory();nav.selectRoot(root,scroll());state.view=nav.current.view;state.detailId=nav.current.recordId??null;
  reconcileNavigation();pushHistory();render('tab');restoreScroll(nav.current.scroll??0);
}
function focusHeading():void {document.querySelector<HTMLElement>('#page-title')?.focus?.({preventScroll:true});}
let historyPosition=0,historyKey=0;
const historyPositions=new Map<number,number>();
let pendingParentNavigation:ReturnType<Navigation['snapshot']>|null=null;
function historyState():{accountNav:ReturnType<Navigation['snapshot']>;accountPosition:number} {
  nav.current.historyKey??=++historyKey;historyPositions.set(nav.current.historyKey,historyPosition);
  return{accountNav:nav.snapshot(),accountPosition:historyPosition};
}
function rememberHistory():void {nav.save(scroll());window.history?.replaceState?.(historyState(),'');}
function pushHistory():void {historyPosition++;window.history?.pushState?.(historyState(),'');}
function back():void {
  hideQr();
  if(!mobile()&&state.view==='settings'&&nav.root==='home'){
    location.assign(location.pathname);return;
  }
  const parent=nav.snapshot().stacks[nav.root].at(-2);
  const target=parent?.historyKey==null?undefined:historyPositions.get(parent.historyKey);
  if(nav.canBack&&target!==undefined&&target<historyPosition&&typeof window.history?.go==='function'){
    rememberHistory();nav.back();
    // The parent entry predates later tab activity. Preserve the live tab stacks.
    pendingParentNavigation=nav.snapshot();window.history.go(target-historyPosition);return;
  }
  rememberHistory();if(!nav.back())return;
  state.view=nav.current.view;state.detailId=nav.current.recordId??null;
  window.history?.replaceState?.(historyState(),'');
  render('back');restoreScroll(nav.current.scroll??0);focusHeading();
}
const titles:Record<Screen['view'],string>={home:'总览',analysis:'分析',my:'我的',bills:'账单查询',pool:'资金池',detail:'账单详情',sync:'数据同步',settings:'应用设置',about:'关于',diagnostics:'开发与诊断',export:'导出账单',upload:'上传原始账单'};
function renderNav():void {
  for(const button of document.querySelectorAll<HTMLButtonElement>('.bottom-nav button')){
    const active=button.dataset.view===nav.root;button.classList.toggle('current',active);button.setAttribute?.('aria-current',active?'page':'false');
  }
}
function findRecord(id:string):LedgerRecord|null {return state.snapshot?.months.flatMap(m=>[...m.records,...m.structuralRecords]).find(r=>r.id===id)??null;}
function reconcileNavigation():void {
  const covered=new Set(state.snapshot?.months.flatMap(m=>[...m.records,...m.structuralRecords]).map(r=>r.id)??[]);
  nav.reconcile(currentMonth(),covered);
  state.view=nav.current.view;state.detailId=nav.current.recordId??null;
  if(state.chartDate&&!currentMonth()?.daily.some(day=>day.date===state.chartDate))state.chartDate=null;
  if(state.chartRecord&&!currentMonth()?.records.some(record=>record.id===state.chartRecord))state.chartRecord=null;
}
function detailHtml():string {
  const record=state.detailId?findRecord(state.detailId):null;
  if(!record)return empty('没有找到这条记录','数据已更新，请返回重新选择。');
  const structural=record.scope==='STRUCTURAL';
  return `<section class="record-detail"><div class="detail-hero"><span>${escapeHtml(record.category)}</span><h2>${escapeHtml(record.title)}</h2><strong>¥ ${money(record.amountCents)}</strong><p>${structural?'结构性支出金额':'计入消费金额'}${record.amountCents<0?' · 负调整':''}</p></div><dl class="detail-list"><dt>日期</dt><dd>${escapeHtml(dateTitle(record.date))}</dd><dt>时间</dt><dd>${escapeHtml(dateTitle(record.time,true))}</dd><dt>分类</dt><dd>${escapeHtml(record.category)}</dd><dt>资金池</dt><dd>${structural?'不计入四池':escapeHtml(poolLabel(record.pool))}</dd><dt>范围</dt><dd>${structural?'结构性支出':record.scope==='PROJECT'?'项目个人消费':'个人消费'}</dd></dl></section>`;
}
function poolLabel(id:string):string {return ({operation:'运行',free:'随心',travel:'旅行',growth:'成长',unknown:'待归类'} as Record<string,string>)[id]??id;}
function settingsPageHtml():string {
  const config=state.desktopConfig;
  const preference=loadMotionPreference();
  const themePreference=loadThemePreference();
  if(!mobile())return desktopSettingsHtml({section:state.settingsSection,theme:themePreference,motion:preference,config:state.desktopConfig,version:state.version,diagnostics:diagnosticsHtml(),budgetContent:budgetSettingsHtml(budgetSession),syncContent:desktopSyncHtml(ledgerStatusHtml(),credentialsHtml(),pairingHtml(),state.busy||state.checkingCloud,isTauri()&&!state.preview)});
  const appearance=`<label for="theme-preference">外观</label><select id="theme-preference">${([['system','跟随系统'],['light','浅色'],['dark','深色']] as const).map(([value,label])=>`<option value="${value}" ${themePreference===value?'selected':''}>${label}</option>`).join('')}</select>`;
  return `<section class="settings"><div class="card-block"><h2>显示与操作</h2>${appearance}<label for="motion-preference">动态效果</label><select id="motion-preference"><option value="system" ${preference==='system'?'selected':''}>跟随系统</option><option value="reduce" ${preference==='reduce'?'selected':''}>减少动态效果</option></select><p class="hint">字体跟随系统设置。系统开启减少动态效果时，应用也会减少动效。</p></div>${!mobile()?`<div class="card-block"><h2>电脑账本位置</h2><form id="desktop-config-form"><label for="project-root">账本项目目录</label><input id="project-root" value="${escapeHtml(config?.projectRoot??'')}" autocomplete="off" spellcheck="false"><label for="python-path">Python 程序</label><input id="python-path" value="${escapeHtml(config?.pythonExecutable??'')}" autocomplete="off" spellcheck="false"><div class="actions"><button type="submit">保存本机配置</button></div><p id="desktop-config-status" role="status"></p></form></div><div class="menu-group"><button type="button" class="menu-row" data-open="diagnostics"><span>开发与诊断</span><span class="row-chevron" aria-hidden="true">${uiIcon('right')}</span></button></div>`:''}</section>`;
}
function viewHtml():string {
  const month=currentMonth();
  if(state.view==='upload')return billUploadHtml(uploadSession,state.android&&isTauri()&&!state.preview);
  if(state.view==='export')return `<section class="analysis-export-page">${exportFormHtml(state.snapshot,state.exportMonth)}</section>`;
  if(state.view==='sync')return syncHtml();
  if(state.view==='settings')return settingsPageHtml();
  if(state.view==='diagnostics')return mobile()?settingsPageHtml():diagnosticsHtml();
  if(state.view==='about')return `<section class="about"><div class="account-emblem" aria-hidden="true"><i></i><i></i><i></i><i></i></div><h1>生活账本</h1><p>让数字回到生活里。</p><dl class="detail-list"><dt>版本</dt><dd>${escapeHtml(state.version||'开发预览')}</dd><dt>查看范围</dt><dd>当前快照中的近两个月</dd><dt>数据来源</dt><dd>已校验的 SQLite 账本展示快照</dd></dl></section>`;
  if(state.view==='my')return myHtml(state.snapshot,state.source==='cloud'?'已同步':state.snapshot?'本地数据可用':phonePhase()==='pair'?'未配对':phonePhase()==='connect'?'待设置同步':phoneReady()?'等待账单':'正在确认连接',phoneReady());
  if(!month)return state.loading?empty('正在打开生活账本','正在读取本地数据。'):onboardingHtml(state.ledger?.initialized??null,state.probeConfigured,state.probeSummaryLoaded,state.view==='analysis',state.checkingCloud);
  if(state.view==='bills')return mobileBills(month,nav.current.query??emptyQuery());
  if(state.view==='pool')return poolHtml(month,nav.current.poolId??'');
  if(state.view==='detail')return detailHtml();
  if(state.view==='analysis')return analysisHtml(month,state.analysisTab,state.analysisDate,shanghaiToday(),state.expanded);
  return mobile()?mobileHome(month):dashboardHtml(month,shanghaiToday(),state.expanded,isTauri()&&!state.preview);
}
let charts:typeof import('../../dashboard/src/charts')|undefined;
let renderRevision=0;
let searchComposing=false;
let queuedRender=false;
let disposeMotion:(()=>void)|undefined;
let pickers:MobilePickers|undefined;
let pendingPickerAction:(()=>void)|undefined;
function syncMobilePickers():void {
  if(!mobile()){pickers?.destroy();pickers=undefined;return;}
  const root=document.querySelector<HTMLElement>('#app');if(!root||typeof root.querySelectorAll!=='function')return;
  pickers??=new MobilePickers({
    onOpen:()=>{rememberHistory();window.history.pushState({...window.history.state,accountPicker:true},'');},
    onDismiss:after=>{
      if(window.history.state?.accountPicker){pendingPickerAction=after;window.history.back();}
      else{pickers?.closeFromHistory();after?.();}
    },
  });
  pickers.sync(root);
}
function render(transition:MobileTransition='none'):void {
  if(searchComposing&&state.view==='bills'){queuedRender=true;renderStatusBar();return;}
  const currentScroll=scroll();
  const focused=document.activeElement as HTMLInputElement|null;
  const focusId=focused?.id,selection=focused?.selectionStart;
  // A background sync must not discard an in-progress form. Values remain in this call only.
  const drafts=['account','password','project-root','python-path'].map(id=>{
    const input=document.querySelector<HTMLInputElement>(`#${id}`);
    return [id,input?.value,input?.dataset.dirty==='true'] as const;
  });
  renderRevision++;const revision=renderRevision;charts?.disposeCharts();disposeMotion?.();disposeMotion=undefined;
  document.documentElement?.setAttribute?.('data-layout',mobile()?'mobile':'desktop');
  document.documentElement?.setAttribute?.('data-motion-preference',mobile()?(prefersReducedMotion()?'reduce':'system'):loadMotionPreference());
  document.documentElement?.classList?.toggle('desktop-settings-surface',!mobile()&&state.view==='settings');
  renderSelector();renderStatusBar();renderNav();
  document.querySelector<HTMLElement>('.app-header')?.classList.toggle('is-hidden',mobile()&&!['home','analysis'].includes(state.view));
  document.querySelector<HTMLElement>('.app-header .brand')?.classList.toggle('is-hidden',mobile()&&state.view!=='home');
  const rootTitle=document.querySelector<HTMLElement>('#mobile-root-title');if(rootTitle){rootTitle.hidden=!mobile()||state.view!=='analysis';rootTitle.classList.toggle('is-hidden',rootTitle.hidden);}
  if(!mobile()){settingsButton.innerHTML=uiIcon('settings');settingsButton.ariaLabel='应用设置';settingsButton.title='应用设置';}
  const root=!nav.canBack;
  const sampleNote=state.source==='sample'?'<p class="sample-note">合成预览数据，仅用于检查界面。真实数据将在同步后显示。</p>':'';
  const desktopSettings=!mobile()&&state.view==='settings';
  main.innerHTML=`${root?'':`<div class="page-title-row"><button type="button" class="back-button" data-action="back" aria-label="${desktopSettings?'返回账本':'返回上一页'}">${uiIcon('left')}<span>${desktopSettings?'返回账本':'返回'}</span></button><h1 id="page-title" tabindex="-1">${desktopSettings?'设置':titles[state.view]}</h1></div>`}${sampleNote}<div id="view-content">${viewHtml()}</div>`;
  for(const [id,value,dirty] of drafts){
    const input=document.querySelector<HTMLInputElement>(`#${id}`);
    if(input&&value!==undefined&&(!['project-root','python-path'].includes(id)||dirty)){
      input.value=value;if(dirty)input.dataset.dirty='true';
    }
  }
  bindView();updateBusy();
  if(desktopSettings){const feedback=document.querySelector<HTMLElement>('#desktop-config-status');if(feedback){feedback.textContent=state.configFeedback;feedback.dataset.error=String(state.configError);}}
  const sync=document.querySelector<HTMLButtonElement>('#desktop-sync');if(sync)sync.hidden=mobile();
  selector.closest?.('.month-select')?.classList.toggle('is-hidden',!state.snapshot||['my','settings','sync','diagnostics','about','detail','export'].includes(state.view));
  if(state.view==='export')updateExportFeedback(document,exportSession);
  syncMobilePickers();
  if(focusId){const next=document.querySelector<HTMLInputElement>(`#${focusId}`);next?.focus?.({preventScroll:true});if(next?.type==='search'&&selection!=null)next.setSelectionRange?.(selection,selection);}
  restoreScroll(currentScroll);
  if(mobile()&&typeof main.querySelectorAll==='function')disposeMotion=mountMobileMotion(main,{transition});
  uploadRefresh.sync();
  renderChartSelection();
  renderCurrentCharts(revision);
}
function renderCurrentCharts(revision=renderRevision):void {
  if(currentMonth()&&((!mobile()&&state.view==='home')||(state.view==='analysis'&&state.analysisTab==='trend'))&&typeof document.getElementById==='function'){
    void import('../../dashboard/src/charts').then(module=>{
      if(revision!==renderRevision)return;charts=module;
      module.renderCharts(currentMonth()!,shanghaiToday(),date=>{if(mobile()){state.chartDate=date;renderChartSelection();}else openBills({date});},record=>{if(mobile()){state.chartRecord=record.id;renderChartSelection();}else open({view:'detail',recordId:record.id});},{touch:mobile(),reduceMotion:mobile()?prefersReducedMotion():undefined,dark:document.documentElement.dataset.theme==='dark'});
      const highlighted=document.querySelector<HTMLElement>('.scatter-legend button[aria-pressed="true"]')?.dataset.highlightPool;
      if(highlighted)module.highlightPool(highlighted);
    }).catch(()=>{if(revision===renderRevision){state.message='图表暂时未能显示，可重新打开页面。';state.messageTone='tone-warn';renderStatusBar();}});
  }
}
window.addEventListener('account-theme-change',()=>{
  charts?.disposeCharts();
  renderCurrentCharts(++renderRevision);
});

function renderChartSelection():void {
  if(state.view!=='analysis'||state.analysisTab!=='trend')return;
  const month=currentMonth();if(!month)return;
  const date=document.querySelector<HTMLElement>('#date-inspection');if(date)date.innerHTML=dateInspectionHtml(month,state.chartDate);
  const record=document.querySelector<HTMLElement>('#record-inspection');if(record)record.innerHTML=recordInspectionHtml(state.chartRecord?month.records.find(r=>r.id===state.chartRecord)??null:null);
}
function openBills(query:Partial<BillQuery>={}):void {open({view:'bills',query:{...emptyQuery(),...query}});}
function updateQuery(patch:Partial<BillQuery>):void {nav.current.query={...(nav.current.query??emptyQuery()),...patch};rememberHistory();render();}
function searchBills(input:HTMLInputElement):void {
  const month=currentMonth();if(!month)return;
  const query={...(nav.current.query??emptyQuery()),query:input.value};nav.current.query=query;rememberHistory();
  const records=selectBills(month,query);
  const list=document.querySelector<HTMLElement>('.record-list');
  const caption=document.querySelector<HTMLElement>('.bill-count');
  if(list)list.innerHTML=records.length?groupedRecords(records,query.scope==='structural'):empty('没有符合条件的记录','试试其他关键词，或重置筛选。');
  if(caption)caption.textContent=`${records.length} 笔 · ${query.scope==='structural'?'合计':'净消费'} ¥ ${money(records.reduce((sum,record)=>sum+record.amountCents,0))}`;
  const clear=document.querySelector<HTMLButtonElement>('[data-action="clear-search"]');if(clear)clear.hidden=!query.query;
}
function selectSettingsSection(section:SettingsSection):void {
  if(!['general','budget','sync','advanced','diagnostics'].includes(section))return;
  if(section!=='sync')hideQr();
  state.settingsSection=section;
  restoreScroll(0);
  document.querySelectorAll<HTMLElement>('[data-settings-panel]').forEach(panel=>{panel.hidden=panel.dataset.settingsPanel!==section;});
  document.querySelectorAll<HTMLButtonElement>('[data-settings-tab]').forEach(tab=>{if(tab.dataset.settingsTab===section)tab.setAttribute('aria-current','page');else tab.removeAttribute('aria-current');});
  if(section==='budget'&&!budgetSession.status)void loadBudget();
  if(section==='sync')void refreshDesktopSettings();
}
async function loadBudget(preserve=false):Promise<void> {
  if(!isTauri()||state.preview){budgetSession.message='预算编辑需要配置本地账本后使用。';render();return;}
  const loading=budgetSession.load(preserve);render();await loading;
  if(state.view==='settings')render();
}
async function previewBudget():Promise<void> {
  if(!isTauri()||state.preview)return;
  const pending=budgetSession.preflight();render();await pending;if(state.view==='settings')render();
}
async function saveBudget():Promise<void> {
  if(!isTauri()||state.preview)return;
  const pending=budgetSession.save();render();const saved=await pending;
  if(saved)await loadDesktop(true);
  if(state.view==='settings')render();
}
function bindView():void {
  document.querySelector<HTMLButtonElement>('#bill-upload-choose')?.addEventListener('click',()=>void chooseBillUpload());
  document.querySelector<HTMLButtonElement>('#bill-upload-check')?.addEventListener('click',()=>void loadBillUploads(true));
  document.querySelectorAll<HTMLButtonElement>('[data-upload-retry]').forEach(button=>button.addEventListener('click',()=>void retryBillUpload(button.dataset.uploadRetry!)));
  document.querySelector<HTMLFormElement>('#budget-form')?.addEventListener('submit',event=>{event.preventDefault();void previewBudget();});
  document.querySelector<HTMLButtonElement>('#budget-confirm-save')?.addEventListener('click',()=>void saveBudget());
  document.querySelector<HTMLButtonElement>('#budget-reload')?.addEventListener('click',()=>void loadBudget(true));
  document.querySelector<HTMLSelectElement>('#budget-existing-rate')?.addEventListener('change',event=>{const month=(event.target as HTMLSelectElement).value;if(month){budgetSession.selectMonth(month);render();}});
  for(const [id,field] of [['budget-month','effective'],['budget-reason','reason'],['budget-operation','运行账户'],['budget-free','随心消费'],['budget-travel','旅行/体验'],['budget-growth','成长/项目/耐用品']]) {
    document.querySelector<HTMLInputElement>(`#${id}`)?.addEventListener('input',event=>{budgetSession.change(field,(event.target as HTMLInputElement).value);document.querySelector<HTMLElement>('.budget-preview')?.remove();});
  }
  document.querySelectorAll<HTMLButtonElement>('[data-theme-choice]').forEach(button=>button.addEventListener('click',()=>{
    saveThemePreference(button.dataset.themeChoice as ThemePreference);
    document.querySelectorAll<HTMLButtonElement>('[data-theme-choice]').forEach(choice=>choice.setAttribute('aria-pressed',String(choice===button)));
  }));
  document.querySelector<HTMLSelectElement>('#export-month')?.addEventListener('change',event=>{state.exportMonth=(event.target as HTMLSelectElement).value;exportSession.clear();render();});
  document.querySelector<HTMLButtonElement>('#save-analysis-export')?.addEventListener('click',()=>void saveAnalysisExport());
  document.querySelector<HTMLSelectElement>('#theme-preference')?.addEventListener('change',event=>{saveThemePreference((event.target as HTMLSelectElement).value as ThemePreference);});
  document.querySelector<HTMLSelectElement>('#motion-preference')?.addEventListener('change',event=>{saveMotionPreference((event.target as HTMLSelectElement).value as MotionPreference);document.documentElement?.setAttribute?.('data-motion-preference',loadMotionPreference());if(mobile())render();});
  for(const id of ['project-root','python-path'])document.querySelector<HTMLInputElement>(`#${id}`)?.addEventListener('input',event=>{(event.target as HTMLInputElement).dataset.dirty='true';});
  const search=document.querySelector<HTMLInputElement>('#bill-search');
  search?.addEventListener('compositionstart',()=>{searchComposing=true;});
  search?.addEventListener('input',event=>{if((event as InputEvent).isComposing){searchComposing=true;return;}searchBills(event.target as HTMLInputElement);});
  search?.addEventListener('compositionend',event=>{searchComposing=false;searchBills(event.target as HTMLInputElement);if(queuedRender){queuedRender=false;render();}});
  document.querySelector<HTMLSelectElement>('#bill-category')?.addEventListener('change',event=>updateQuery({category:(event.target as HTMLSelectElement).value}));
  document.querySelector<HTMLSelectElement>('#bill-pool')?.addEventListener('change',event=>updateQuery({pool:(event.target as HTMLSelectElement).value}));
  document.querySelector<HTMLSelectElement>('#bill-scope')?.addEventListener('change',event=>updateQuery({scope:(event.target as HTMLSelectElement).value as BillQuery['scope'],category:'',pool:''}));
  document.querySelector<HTMLFormElement>('#sync-form')?.addEventListener('submit',onSyncSubmit);
  document.querySelector<HTMLFormElement>('#inspect-form')?.addEventListener('submit',onInspectSubmit);
  document.querySelector<HTMLFormElement>('#desktop-config-form')?.addEventListener('submit',onDesktopConfigure);
  document.querySelector<HTMLButtonElement>('#fetch-button')?.addEventListener('click',()=>refreshController.requestManual());
  document.querySelector<HTMLButtonElement>('#show-pairing')?.addEventListener('click',onShowPairing);
  document.querySelector<HTMLButtonElement>('#hide-qr')?.addEventListener('click',hideQr);
  document.querySelector<HTMLButtonElement>('#scan-pairing')?.addEventListener('click',onScanPairing);
  document.querySelector<HTMLButtonElement>('#diagnose')?.addEventListener('click',onDiagnose);
  document.querySelector<HTMLButtonElement>('#probe-status')?.addEventListener('click',onProbeStatus);
}

async function loadBillUploads(refresh:boolean):Promise<void>{
  if(state.preview||!state.android||!isTauri()||uploadSession.busy)return;
  const operation=uploadSession.load(()=>invoke<BillUploadItem[]>('bill_upload_status',{refresh}),refresh,
    refresh?()=>invoke<BillUploadItem[]>('bill_upload_status',{refresh:false}):undefined);
  if(state.view==='upload')render();
  await operation;if(state.view==='upload')render();
  if(!refresh)uploadRefresh.sync(true);
}
async function chooseBillUpload():Promise<void>{
  if(state.preview||!state.android||!isTauri()||uploadSession.busy)return;
  const operation=uploadSession.choose(()=>invoke<BillUploadItem|null>('bill_upload_choose'));
  render();await operation;
  // Reload persisted pending metadata after network failure; it enables retry
  // without retaining the document URI or reselecting a raw file in WebView.
  if(uploadSession.error){
    const message=uploadSession.message;
    await uploadSession.load(()=>invoke<BillUploadItem[]>('bill_upload_status',{refresh:false}));
    uploadSession.message=message;uploadSession.error=true;
  }
  if(state.view==='upload')render();
}
async function retryBillUpload(id:string):Promise<void>{
  if(state.preview||!state.android||!isTauri()||uploadSession.busy)return;
  const operation=uploadSession.retry(()=>invoke<BillUploadItem>('bill_upload_retry',{id}));
  render();await operation;if(state.view==='upload')render();
}

async function saveAnalysisExport():Promise<void>{
  const snapshot=state.snapshot;
  const month=document.querySelector<HTMLSelectElement>('#export-month')?.value;
  if(!snapshot||!month||exportSession.busy)return;
  const saving=exportSession.save(async()=>{
    const report=buildAnalysisExport(snapshot,month);
    if(state.source==='sample')report.content=report.content.replace('\n','\n\n> 合成预览数据，不是真实账本。\n');
    return isTauri()?invoke<{saved:boolean}>('analysis_export_save',report):downloadExport(report);
  });
  updateExportFeedback(document,exportSession);syncMobilePickers();
  await saving;
  if(state.view==='export'){updateExportFeedback(document,exportSession);syncMobilePickers();}
}
main.addEventListener('click',event=>{
  const target=event.target as HTMLElement|null;
  if(!target?.closest)return;
  const button=target.closest<HTMLElement>('[data-record],[data-pool],[data-category],[data-date],[data-scope],[data-open],[data-action],[data-analysis],[data-highlight-pool],[data-settings-tab],#expand-categories');
  if(!button)return;
  if(button.id==='expand-categories'){state.expanded=!state.expanded;render();return;}
  const data=button.dataset;
  if(data.action==='retry-setup'){void retryPhoneSetup();return;}
  if(data.settingsTab){selectSettingsSection(data.settingsTab as SettingsSection);return;}
  if(data.action==='retry-connection-status'){void refreshDesktopSettings();return;}
  if(data.record){open({view:'detail',recordId:data.record});return;}
  if(data.pool){open({view:'pool',poolId:data.pool});return;}
  if(data.category){openBills({category:data.category});return;}
  if(data.date){if(state.view==='analysis'&&state.analysisTab==='calendar'){state.analysisDate=data.date;render();}else openBills({date:data.date});return;}
  if(data.scope){openBills(data.scope==='structural'?{scope:'structural'}:{pool:'unknown'});return;}
  if(data.open==='sync'&&!mobile()){state.settingsSection='sync';if(state.view==='settings')render();else open({view:'settings'});void refreshDesktopSettings();return;}
  if(data.open){open(data.open==='bills'?{view:'bills',query:emptyQuery()}:{view:data.open as Screen['view']});if(data.open==='sync')void refreshLedgerStatus().then(()=>{if(state.view==='sync')render();});return;}
  if(data.analysis){state.analysisTab=data.analysis as AnalysisTab;state.expanded=false;render('tab');return;}
  if(data.highlightPool){const pinned=button.getAttribute('aria-pressed')==='true';document.querySelectorAll<HTMLElement>('[data-highlight-pool]').forEach(el=>el.setAttribute('aria-pressed','false'));button.setAttribute('aria-pressed',String(!pinned));charts?.highlightPool(pinned?null:data.highlightPool);return;}
  switch(data.action){case 'edit-budget':if(!mobile()){state.settingsSection='budget';if(state.view==='settings')render();else open({view:'settings'});if(!budgetSession.status)void loadBudget();}break;case 'manage-connection':state.editingConnection=!state.editingConnection;state.setupError='';render();break;case 'manage-pairing':state.editingPairing=!state.editingPairing;render();break;case 'chart-date-bills':if(data.inspectDate)openBills({date:data.inspectDate});break;case 'chart-record-detail':if(data.inspectRecord)open({view:'detail',recordId:data.inspectRecord});break;case 'fetch-empty':refreshController.requestManual();break;case 'back':case 'back-to-bills':back();break;case 'all-bills':openBills();break;case 'clear-filters':updateQuery(emptyQuery());break;case 'clear-search':updateQuery({query:''});break;case 'clear-date':updateQuery({date:''});break;}
});
function updateBusy():void {
  for(const button of document.querySelectorAll<HTMLButtonElement>('.actions button, .settings-save'))button.disabled=state.busy||!isTauri()||state.preview;
  if(mobile())for(const button of document.querySelectorAll<HTMLButtonElement>('.setup-primary, [data-action="retry-setup"]'))button.disabled=state.busy||state.checkingCloud||!isTauri()||state.preview;
  for(const button of document.querySelectorAll<HTMLButtonElement>('#bill-upload-choose, #bill-upload-check, [data-upload-retry]'))button.disabled=uploadSession.busy||!state.android||!isTauri()||state.preview;
  if(!mobile())for(const id of ['project-root','python-path']){const input=document.querySelector<HTMLInputElement>(`#${id}`);if(input)input.disabled=state.busy;}
  selector.disabled=!availableMonths(state.snapshot).length||state.busy;
  const fetch=document.querySelector<HTMLButtonElement>('#fetch-button');if(fetch)fetch.disabled=state.busy||state.checkingCloud||!isTauri()||state.preview||(mobile()&&(!state.ledger?.initialized||!state.probeConfigured));
  const button=document.querySelector<HTMLButtonElement>('#desktop-sync');if(button){button.disabled=state.busy||!isTauri()||state.preview;const label=state.busy?'正在同步…':'同步展示';button.innerHTML=`${uiIcon('sync')}<span class="sr-only">${label}</span>`;button.ariaLabel=label;button.title=label;}
  syncMobilePickers();
}
async function onDesktopConfigure(event:Event):Promise<void>{
  event.preventDefault();if(state.busy)return;const root=document.querySelector<HTMLInputElement>('#project-root')?.value??'',python=document.querySelector<HTMLInputElement>('#python-path')?.value??'';
  await withBusy(async()=>{state.desktopConfig=await invoke<DesktopConfig>('desktop_configure_project',{projectRoot:root,pythonExecutable:python});state.configFeedback='本机账本位置已保存。';state.configError=false;for(const id of ['project-root','python-path']){const input=document.querySelector<HTMLInputElement>(`#${id}`);if(input)input.dataset.dirty='false';}}).catch(error=>{state.configFeedback=shortError(error,'本机配置保存失败。');state.configError=true;});render();
}
let localEpoch=0;
let localLoad:Promise<void>|null=null;
async function loadDesktop(force=false):Promise<void>{
  if(force){localEpoch++;const prior=localLoad;if(prior)await prior;}
  if(state.busy||state.preview)return;
  if(localLoad)return localLoad;
  const epoch=++localEpoch;
  localLoad=(async()=>{
  try{const outcome=await invoke<DesktopSnapshot>('desktop_load_snapshot');
    if(epoch!==localEpoch)return;
    state.desktopWarning='';
    if(outcome.snapshot){applySnapshot(outcome.snapshot,'local',null);state.desktopWarning='';}
    if(outcome.sourceChanged)state.desktopWarning='账本有更新，请同步展示。';
    if(outcome.monthChanged)state.desktopWarning='月份已变化，请同步展示。';
    if(!outcome.snapshot&&!outcome.sourceChanged&&!outcome.monthChanged)state.desktopWarning='本机尚无展示快照，请同步展示。';
  }catch(error){if(epoch===localEpoch)state.desktopWarning=shortError(error,'本机快照读取未能完成，继续显示上次数据。');}
  try{state.desktopConfig=await invoke<DesktopConfig>('desktop_project_config');}catch{ /* Configuration page can repair an unavailable setup. */ }
  })();
  try{await localLoad;}finally{localLoad=null;}
}
async function refreshDesktop():Promise<void>{
  await withBusy(async()=>{
    localEpoch++;
    state.message='正在更新本机展示…';state.messageTone='';renderStatusBar();
    const result=await invoke<DesktopRefresh>('desktop_refresh_snapshot');
    applySnapshot(result.snapshot,'local',null);
    state.desktopWarning=[result.sourceChanged?'账本随后有更新，请再次同步展示。':'',result.monthChanged?'月份随后已变化，请再次同步展示。':'',result.warning??''].filter(Boolean).join(' ');
    state.message=result.appPublish.published?'本机展示已更新 · 云端发布已确认':result.appPublish.skipped?'本机展示已更新 · 云端发布未启用':`本机展示已更新 · 云端发布未完成：${result.appPublish.error||result.appPublish.reason||'可稍后重试'}`;
    state.messageTone=result.appPublish.published||result.appPublish.skipped?'':'tone-warn';
  }).catch(error=>{state.message=`${shortError(error,'本机展示更新失败。')} 已保留上次有效数据。`;state.messageTone='tone-error';});render();
}

function ledgerStatusHtml(): string {
  const summary = state.ledger;
  if (!summary) return '<p class="empty-note">正在读取正式账本状态…</p>';
  if(!mobile()){
    const rows=[['本机账本',state.snapshot?'本机快照可用':state.desktopConfig?'已绑定项目 · 暂无快照':'尚未绑定项目'],['云端发布',summary.publishEnabled?'已启用':'未启用'],['云端缓存',summary.cachedRevision!=null?`第 ${summary.cachedRevision} 版`:'暂无缓存'],['待发内容',summary.pendingRevision!=null?`第 ${summary.pendingRevision} 版 · 尚未发布成功`:'无待发内容'],['最近发布',summary.lastPublishedRevision!=null?`第 ${summary.lastPublishedRevision} 版`:'暂无发布记录']];
    return `<dl class="info-list">${rows.map(([label,value])=>`<dt>${label}</dt><dd>${escapeHtml(value)}</dd>`).join('')}</dl><p class="settings-inline-status" role="status" aria-live="polite" data-error="${Boolean(state.messageTone)}">${escapeHtml([state.message,state.desktopWarning].filter(Boolean).join(' '))}</p>`;
  }
  const rows: Array<[string, string]> = [
    ['正式账本', summary.initialized ? '已初始化' : '未初始化（需先在电脑初始化或扫码配对）'],
    ['真实上传', summary.publishEnabled ? '已启用' : '未启用（可在电脑管理发布设置）'],
    ['本地缓存', summary.cachedRevision != null ? `第 ${summary.cachedRevision} 版` : '无'],
    ['待发内容', summary.pendingRevision != null ? `第 ${summary.pendingRevision} 版（尚未发布成功）` : '无'],
    ['最近发布', summary.lastPublishedRevision ? `第 ${summary.lastPublishedRevision} 版` : '无'],
  ];
  return `<dl class="info-list">${rows.map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`).join('')}</dl>`;
}

function pairingHtml(): string {
  return state.android
    ? `<div class="setup-actions"><button class="setup-primary" id="scan-pairing" type="button" ${state.busy || !isTauri() ? 'disabled' : ''}>${state.busy?'正在配对…':'扫码配对'}</button></div>
       <details class="setup-help"><summary>电脑上还没有配对二维码？</summary><p>请让电脑上的 Agent 按项目“手机与同步”说明启用同步，然后在电脑版“设置 → 手机与同步”显示二维码。只扫描本人账本的二维码。</p></details>`
    : `<div class="actions"><button id="show-pairing" type="button" ${state.busy || !isTauri() ? 'disabled' : ''}>显示配对二维码</button></div>
       <div id="qr-panel" hidden><img id="pairing-qr" alt="正式账本设备配对二维码"><p class="hint">二维码含账本密钥，只给自己的设备扫描。显示两分钟后自动隐藏。</p><button id="hide-qr" class="back-button" type="button">立即隐藏</button></div>`;
}
function syncHtml(): string {
  const pairing=pairingHtml();
  if(!mobile()){state.settingsSection='sync';return settingsPageHtml();}
  if(mobile())return mobileSyncHtml({initialized:state.ledger?.initialized??null,configured:state.probeConfigured,connectionKnown:state.probeSummaryLoaded,hasData:Boolean(state.snapshot),source:state.source,revision:state.revision,checking:state.checkingCloud,warning:[state.messageTone?state.message:'',coverageNote(state.snapshot)].filter(Boolean).join(' '),editingConnection:state.editingConnection,editingPairing:state.editingPairing,setupError:state.setupError},credentialsHtml(),pairing);
  return '';
}

function diagnosticsHtml(): string {
  return `<section class="view settings">
    <div class="card-block"><h3 style="margin:0 0 8px">本地快照核验（开发）</h3>
      <p>读取一个 App V2 快照导出文件，经与加密通道相同的契约校验后在本机显示。不使用账本身份、不写入缓存、不连接云端。</p>
      <form id="inspect-form">
        <label for="inspect-path">快照导出文件路径</label>
        <input id="inspect-path" name="inspect-path" autocomplete="off" spellcheck="false" placeholder="…\\snapshot-v2.json">
        <div class="actions"><button id="inspect-button" type="submit" ${state.busy || !isTauri() ? 'disabled' : ''}>核验并显示</button></div>
      </form>
      <p id="inspect-status" role="status" aria-live="polite"></p>
    </div>
    <div class="card-block"><h3 style="margin:0 0 8px">开发与诊断</h3>
      <p>技术验证（probe）通道保留在设置里，用于同步诊断与连通性检查，与正式账本完全隔离。</p>
      <div class="actions">
        <button id="diagnose" type="button" ${state.busy || !isTauri() ? 'disabled' : ''}>运行同步诊断</button>
        <button id="probe-status" type="button" ${state.busy || !isTauri() ? 'disabled' : ''}>读取验证缓存</button>
      </div>
      <pre id="diagnose-output" class="diagnose-output" hidden></pre>
      <dl id="probe-details" class="info-list" hidden></dl>
    </div>
    ${state.version ? `<p class="hint">界面版本 v${escapeHtml(state.version)}</p>` : ''}
  </section>`;
}


function applySnapshot(snapshot: AppV2Snapshot, source: SnapshotSource, revision: number | null): void {
  const previousMonth=state.selectedMonth;
  state.snapshot = snapshot;
  state.source = source;
  state.revision = revision;
  state.selectedMonth = resolveMonth(snapshot, state.selectedMonth);
  if(previousMonth!==state.selectedMonth){nav.restrictMonth(state.selectedMonth);state.analysisDate=null;}
  reconcileNavigation();
}

function shortError(error: unknown, fallback: string): string {
  return typeof error === 'string' && error.length < 200 ? error : fallback;
}

async function doFetch(operation: string): Promise<void> {
  if(state.preview)return;
  if(state.android&&!phoneReady()){if(state.view==='sync')render();return;}
  state.setupError='';
  state.checkingCloud=true;if(state.view==='sync'||state.view==='settings'||state.android)render();
  try {
    const outcome = await invoke<LedgerFetchOutcome>('ledger_fetch');
    if (outcome.kind === 'cloud-empty') {
      if(state.android&&state.snapshot)state.source='cache';
      state.message = outcome.hasCache
        ? `${operation}：云端还没有发布文件，已保留本地缓存。`
        : `${operation}：云端还没有发布文件。请先在电脑上发布，或在手机完成配对。`;
      state.messageTone = state.android?'':'tone-warn';
    } else if (state.source === 'local') {
      // A cloud check never replaces the desktop's authoritative local view.
      const matches=sameSnapshotContent(state.snapshot,outcome.snapshot);
      state.message=outcome.warning|| (matches?`云端已确认相同内容 · 第 ${outcome.revision} 版。`:'云端内容与本机不同，继续显示本机快照。');
      state.messageTone=outcome.warning||!matches?'tone-warn':'';
    } else if (outcome.warning) {
      // A partially verified download is displayed as cache, never as a
      // cloud-confirmed version.
      applySnapshot(outcome.snapshot, 'cache', outcome.revision);
      state.message = `${operation}：已显示通过校验的版本 ${outcome.revision}。${outcome.warning}`;
      state.messageTone = 'tone-warn';
    } else {
      applySnapshot(outcome.snapshot, 'cloud', outcome.revision);
      state.message = `${operation}：云端版本 ${outcome.revision}。`;
      state.messageTone = '';
    }
  } catch (error) {
    state.message = `${shortError(error, `${operation}未能完成。`)} ${state.snapshot?'已保留当前数据':'尚未下载账单'}，可重试。`;
    state.messageTone = 'tone-error';
  } finally {
    state.checkingCloud=false;
    if(!state.android)await Promise.all([refreshLedgerStatus(),refreshProbeSummary()]);
    render();
  }
}

async function refreshLedgerStatus(): Promise<void> {
  if(state.preview)return;
  try {
    state.ledger = await invoke<LedgerStatusSummary>('ledger_status_command');
  } catch (error) {
    if(state.android){state.setupError=shortError(error,'设备状态读取失败，请重试。');return;}
    state.message = shortError(error, '正式账本状态检查未能完成。');
    state.messageTone = 'tone-error';
  }
}

async function refreshProbeSummary(): Promise<void> {
  try {
    const summary = await invoke<ProbeSyncSummary>('sync_status');
    state.probeConfigured = summary.configured;
    state.probePaired = summary.paired;
    state.probeSummaryLoaded=true;
    state.connectionReadError='';
  } catch {
    if(state.android)state.setupError='同步设置读取失败，请重试。已有配对和数据保持不变。';
    else state.connectionReadError='同步设置读取失败，请重新读取。已有凭据和配对保持不变。';
  }
}

async function refreshDesktopSettings():Promise<void>{
  if(!isTauri()||state.preview)return;
  await Promise.all([refreshLedgerStatus(),refreshProbeSummary()]);
  if(state.view==='settings')render();
}

async function retryPhoneSetup():Promise<void>{
  if(state.busy)return;
  state.setupError='';
  await withBusy(async()=>{
    await refreshLedgerStatus();await refreshProbeSummary();
    if(state.ledger?.initialized&&!state.snapshot){
      try{
        const cached=await invoke<LedgerCacheOutcome|null>('ledger_cache');
        if(cached)applySnapshot(cached.snapshot,'cache',cached.revision);
      }catch(error){state.setupError=shortError(error,'本地账单暂时无法读取，请重试。');}
    }
  });
  render();
}

const refreshController = new RefreshController({
  minAutoIntervalMs: 15_000,
  trigger: () => doFetch('自动检查'),
});

// Pairing QR display (Windows side).
let qrTimer: ReturnType<typeof setTimeout> | undefined;
const qrController = new PairingQrController(
  () => !document.hidden,
  (dataUri) => {
    const image = document.querySelector<HTMLImageElement>('#pairing-qr');
    const panel = document.querySelector<HTMLElement>('#qr-panel');
    if (image && panel) {
      image.src = dataUri;
      panel.hidden = false;
    }
  },
  () => {
    const image = document.querySelector<HTMLImageElement>('#pairing-qr');
    const panel = document.querySelector<HTMLElement>('#qr-panel');
    image?.removeAttribute('src');
    if (panel) panel.hidden = true;
  },
);

function hideQr(): void {
  if (qrTimer) clearTimeout(qrTimer);
  qrTimer = undefined;
  qrController.hide();
}

async function withBusy(action: () => Promise<void>): Promise<void> {
  if (state.busy || !isTauri() || state.preview) return;
  state.busy = true;
  updateBusy();
  try {
    await action();
  } finally {
    state.busy = false;
    updateBusy();
  }
}

function pairStatus(message: string): void {
  const target = document.querySelector<HTMLElement>('#pair-status');
  if (target) target.textContent = message;
}

const scanView=document.querySelector<HTMLElement>('#pairing-scan');
if(scanView)scanView.hidden=true;
const pairingScanner=new PairingScanSession(
  ()=>scan({cameraDirection:'back',formats:[Format.QRCode],windowed:true}),
  cancel,
  active=>{
    if(scanView)scanView.hidden=!active;
    document.documentElement?.classList?.toggle('account-scanning',active);
    document.querySelector<HTMLElement>('#app')?.toggleAttribute?.('inert',active);
    if(active){
      rememberHistory();
      window.history?.pushState?.({...window.history.state,accountScanner:true},'');
      const feedback=document.querySelector<HTMLElement>('#pairing-scan-status');if(feedback)feedback.textContent='';
      document.querySelector<HTMLButtonElement>('#cancel-pairing-scan')?.focus?.();
    }else if(window.history?.state?.accountScanner)window.history.back();
  },
);
async function cancelPairingScan():Promise<void>{
  try{await pairingScanner.cancel();}
  catch{
    const feedback=document.querySelector<HTMLElement>('#pairing-scan-status');
    if(feedback)feedback.textContent='相机未能关闭，请再次点击返回。';
    if(pairingScanner.active&&window.history&&!window.history.state?.accountScanner)
      window.history.pushState({...window.history.state,accountScanner:true},'');
  }
}
document.querySelector<HTMLButtonElement>('#cancel-pairing-scan')?.addEventListener('click',()=>void cancelPairingScan());

async function onShowPairing(): Promise<void> {
  await withBusy(async () => {
    const displayed = await qrController.display(() => invoke<string>('ledger_create_pairing'));
    if (!displayed) {
      pairStatus('二维码未显示。请回到当前页面后重新点击显示。');
      return;
    }
    qrTimer = setTimeout(hideQr, 120_000);
    pairStatus('原生二维码已显示。用手机扫描后，手机会验证身份与样例再保存。');
  }).catch((error) => {
    hideQr();
    pairStatus(shortError(error, '配对二维码生成未能完成。'));
  });
}

async function onScanPairing(): Promise<void> {
  if(state.busy||state.preview||!state.android||!isTauri())return;
  state.setupError='';
  let paired = false;
  await withBusy(async () => {
    let payload = '';
    try {
      let permission = await checkPermissions();
      if (permission !== 'granted') permission = await requestPermissions();
      if (permission !== 'granted') throw new Error('camera unavailable');
      const scanned = await pairingScanner.run();
      if(!scanned)return;
      payload = scanned.content;
      scanned.content = '';
    } catch {
      throw '扫码未完成。请允许相机访问，或取消后重试。';
    }
    try {
      state.ledger = await invoke<LedgerStatusSummary>('ledger_import_pairing', { payload });
      paired = true;
    } finally {
      payload = '';
    }
  }).catch((error) => {
    state.setupError=shortError(error,'扫码配对未能完成。');
    const target = document.querySelector<HTMLElement>('#pair-status');
    if (target) target.textContent = shortError(error, '扫码配对未能完成。');
  });
  if (paired) {
    state.message='';state.messageTone='';
    pairStatus('配对完成。正式账本状态已更新，配置坚果云凭据后即可同步。');
    state.editingPairing=false;
    await refreshProbeSummary();
    if(phoneReady())refreshController.requestAuto();
  }
  render();
}

async function onSyncSubmit(event: Event): Promise<void> {
  event.preventDefault();
  if(state.busy||state.preview||(state.android&&!state.ledger?.initialized))return;
  state.setupError='';
  const accountInput = document.querySelector<HTMLInputElement>('#account');
  const passwordInput = document.querySelector<HTMLInputElement>('#password');
  const statusLine = document.querySelector<HTMLElement>('#sync-status');
  if (!accountInput || !passwordInput || !statusLine) return;
  const account = accountInput.value.trim();
  const appPassword = passwordInput.value;
  passwordInput.value = '';
  if (!account || !appPassword) {
    statusLine.textContent = '请输入坚果云账号和应用密码。';
    return;
  }
  let configured = false;
  await withBusy(async () => {
    const summary = await invoke<ProbeSyncSummary>('sync_configure', { account, appPassword });
    state.probeConfigured = summary.configured;
    state.probePaired = summary.paired;
    state.probeSummaryLoaded=true;
    accountInput.value = '';
    state.connectionReadError='';
    configured = true;
  }).catch((error) => {
    if(state.android){state.setupError=shortError(error,'同步凭据保存未能完成。');statusLine.textContent=state.setupError;}
    else{state.setupError=shortError(error,'同步凭据保存未能完成。');state.message=state.setupError;state.messageTone='tone-error';statusLine.textContent=state.message;}
  });
  if (configured) {
    state.message='';state.messageTone='';
    statusLine.textContent = '已配置。凭据保存在本机系统安全存储中，可随时重新输入更换。';
    const saveButton = document.querySelector<HTMLButtonElement>('#save-sync');
    if (saveButton) saveButton.textContent = '更换同步凭据';
    if (state.android&&state.ledger?.initialized) refreshController.requestAuto();
    state.editingConnection=false;render();
  } else {
    render();
  }
}

async function onInspectSubmit(event: Event): Promise<void> {
  event.preventDefault();
  const pathInput = document.querySelector<HTMLInputElement>('#inspect-path');
  const statusLine = document.querySelector<HTMLElement>('#inspect-status');
  if (!pathInput || !statusLine) return;
  const path = pathInput.value.trim();
  if (!path) {
    statusLine.textContent = '请输入快照导出文件路径。';
    return;
  }
  let inspected = false;
  await withBusy(async () => {
    const snapshot = await invoke<AppV2Snapshot>('ledger_inspect_snapshot', { path });
    applySnapshot(snapshot, 'file', null);
    state.message = '本地快照核验通过：契约校验完成，按未上云数据显示。';
    state.messageTone = '';
    inspected = true;
  }).catch((error) => {
    statusLine.textContent = shortError(error, '快照核验未能完成。');
  });
  if (inspected) {
    nav.resetRoot('home');state.view = 'home';
    render();
  }
}

async function onDiagnose(): Promise<void> {
  const output = document.querySelector<HTMLElement>('#diagnose-output');
  const statusLine = document.querySelector<HTMLElement>('#sync-status');
  await withBusy(async () => {
    const lines = await invoke<string[]>('sync_diagnose');
    if (output) {
      output.textContent = lines.join('\n');
      output.hidden = false;
    }
    if (statusLine) statusLine.textContent = '诊断完成。以下输出可整段全选复制，供反馈使用。';
  }).catch((error) => {
    state.message=shortError(error,'诊断未能完成。');state.messageTone='tone-error';renderStatusBar();
    if(output){output.hidden=false;output.textContent=state.message;}
  });
}

async function onProbeStatus(): Promise<void> {
  const details = document.querySelector<HTMLDListElement>('#probe-details');
  await withBusy(async () => {
    const status = await invoke<ProbeStatus>('load_probe');
    if (!details) return;
    details.replaceChildren();
    for (const [label, value] of [
      ['设备', status.platform],
      ['验证缓存', status.cached ? `第 ${status.revision} 版 · ${status.syntheticRecords} 笔虚构记录` : '无'],
      ['安全存储', status.storage],
    ] as Array<[string, string]>) {
      const term = document.createElement('dt');
      const description = document.createElement('dd');
      term.textContent = label;
      description.textContent = value;
      details.append(term, description);
    }
    details.hidden = false;
  }).catch((error) => {
    state.message=shortError(error,'验证缓存读取未能完成。');state.messageTone='tone-error';renderStatusBar();
    if(details){details.hidden=false;details.textContent=state.message;}
  });
}

// ---------------------------------------------------------------------------

function credentialsHtml():string {
  if(mobile())return `<div class="setup-panel"><form id="sync-form"><label for="account">坚果云账号</label><input id="account" name="account" autocomplete="off" inputmode="email" autocapitalize="none" spellcheck="false" placeholder="账号邮箱"><label for="password">应用密码</label><input id="password" name="password" type="password" autocomplete="new-password" placeholder="当前手机的应用密码"><div class="setup-actions"><button class="setup-primary" id="save-sync" type="submit">${state.busy?'正在保存…':'保存并连接'}</button></div></form><p id="sync-status" role="status" aria-live="polite"></p><details class="setup-help"><summary>如何获取应用密码？</summary><p>在坚果云的“账户信息 → 安全选项 → 第三方应用管理”中添加本手机的应用密码。它不是登录密码，直接填在这里即可，无需发给 Agent。</p></details></div>`;
  return desktopConnectionHtml({configured:state.probeConfigured,known:state.probeSummaryLoaded,editing:state.editingConnection,busy:state.busy,error:state.setupError,readError:state.connectionReadError});
}
selector.addEventListener('change',()=>{
  state.selectedMonth=selector.value;state.expanded=false;state.analysisDate=null;state.chartDate=null;state.chartRecord=null;nav.resetDates();reconcileNavigation();rememberHistory();render('tab');
});
settingsButton.addEventListener('click',()=>{if(mobile())selectRoot('my');else openAppSettings();});
for(const button of document.querySelectorAll<HTMLButtonElement>('.bottom-nav button'))button.addEventListener('click',()=>selectRoot(button.dataset.view as RootView));
document.querySelector<HTMLButtonElement>('#desktop-sync')?.addEventListener('click',()=>void refreshDesktop());
document.querySelector<HTMLButtonElement>('#back-to-top')?.addEventListener('click',()=>restoreScroll(0));
document.addEventListener('visibilitychange',()=>{
  if(state.preview)return;
  if(state.view==='upload'){if(document.hidden)uploadRefresh.stop();else uploadRefresh.sync(true);}
  if(document.hidden){hideQr();void cancelPairingScan();}
  else if(!state.android){void resumeDesktop();}
  else if(state.probeConfigured&&state.ledger?.initialized)refreshController.requestAuto();
});
async function resumeDesktop():Promise<void>{
  if(state.android||state.preview||!isTauri()||document.hidden)return;
  await Promise.all([loadDesktop(),refreshLedgerStatus(),refreshProbeSummary()]);
  render();
}
window.addEventListener('focus',()=>{if(!state.loading)void resumeDesktop();});
window.addEventListener('pagehide',()=>{uploadPageActive=false;uploadRefresh.stop();hideQr();charts?.disposeCharts();disposeMotion?.();pickers?.destroy();pickers=undefined;void cancelPairingScan();});
window.addEventListener('pageshow',()=>{uploadPageActive=true;uploadRefresh.sync(true);});
window.history?.replaceState?.(historyState(),'');
if(window.history&&'scrollRestoration'in window.history)window.history.scrollRestoration='manual';
window.addEventListener('popstate',(event:PopStateEvent)=>{
  if(pairingScanner.active&&!event.state?.accountScanner){void cancelPairingScan();return;}
  if(event.state?.accountScanner){window.history?.back();return;}
  if(pickers?.isOpen&&!event.state?.accountPicker){pickers.closeFromHistory();const choose=pendingPickerAction;pendingPickerAction=undefined;choose?.();return;}
  hideQr();searchComposing=false;queuedRender=false;
  const restored=pendingParentNavigation??event.state?.accountNav;pendingParentNavigation=null;
  if(nav.restore(restored)){historyPosition=event.state?.accountPosition??historyPosition;reconcileNavigation();window.history?.replaceState?.(historyState(),'');render('back');restoreScroll(nav.current.scroll??0);focusHeading();}
});
window.addEventListener('keydown',(event:KeyboardEvent)=>{if(event.defaultPrevented||pickers?.isOpen)return;if(event.key==='Escape'&&pairingScanner.active){event.preventDefault();void cancelPairingScan();}else if(event.key==='Escape'&&nav.canBack){event.preventDefault();back();}});
let previousMobile=mobile();
window.addEventListener('resize',()=>{const next=mobile();if(next!==previousMobile){previousMobile=next;render();}});
if(typeof window.matchMedia==='function')window.matchMedia('(prefers-reduced-motion: reduce)').addEventListener?.('change',()=>{if(mobile())render();});

async function boot():Promise<void>{
  if(!isTauri()){
    applySnapshot(sampleSnapshot,'sample',null);state.loading=false;state.message='当前是浏览器预览，原生环境不可用。';state.messageTone='tone-warn';render();return;
  }
  state.message='正在读取…';render();
  try{
    const context=await invoke<{platform:string;preview:boolean}>('app_context');
    state.android=context.platform==='Android';
    if(context.preview){
      state.preview=true;
      applySnapshot(sampleSnapshot,'sample',null);state.loading=false;state.message='界面预览 · 不读取账本、不连接云端';state.messageTone='tone-warn';
      state.version=await invoke<string>('app_version').catch(()=> '');render();return;
    }
  }catch{ /* Older native host: the browser user agent remains a safe layout fallback. */ }
  const statusTask=refreshLedgerStatus();
  if(state.android)await statusTask;
  let cacheWarning:string|null=null;
  try{
    const cached=state.android&&state.ledger?.initialized!==true?null:await invoke<LedgerCacheOutcome|null>('ledger_cache');
    if(cached){applySnapshot(cached.snapshot,'cache',cached.revision);state.message='';state.messageTone='';state.loading=false;render();}
    else{state.message=state.android?'':'尚未同步到任何账单版本。';state.messageTone=state.android?'':'tone-warn';}
  }catch(error){cacheWarning=shortError(error,'本地缓存读取未能完成。');state.message=cacheWarning;state.messageTone='tone-warn';}
  await statusTask;
  if(!state.android){
    await loadDesktop();
    // A successful authoritative local read supersedes only the optional cache-read warning.
    if(state.source==='local'&&((cacheWarning!==null&&state.message===cacheWarning)||state.message==='尚未同步到任何账单版本。')){state.message='';state.messageTone='';}
    render();
  }
  state.loading=false;render();
  await refreshProbeSummary();
  if(!state.snapshot||state.view==='sync'||state.view==='settings')render();
  void invoke<string>('app_version').then(version=>{state.version=version;if(['settings','about'].includes(state.view))render();}).catch(()=>undefined);
  if(state.android&&state.probeConfigured&&state.ledger?.initialized)refreshController.requestAuto();
}
void boot();

/** Secondary native-menu destination; the desktop home uses the web controller. */
export function openAppSettings():void {
  if(mobile()){selectRoot('my');return;}
  nav.resetRoot('home');nav.open({view:'settings'});state.view='settings';
  // This is the initial document destination, not another step through the phone tab stack.
  rememberHistory();render('none');focusHeading();
}
