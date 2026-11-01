/**
 * Full page startup flow tests for the ledger App UI: the module runs its
 * real top-level boot code against a minimal fake DOM and a scripted Tauri
 * `invoke`, asserting that the cached App V2 snapshot is visible before and
 * independently of the network, that labels stay honest (cache is never
 * presented as a cloud-confirmed version, pending is never published), and
 * that the synthetic preview only appears when nothing real exists.
 */
import { describe, expect, it, vi, beforeEach, beforeAll, afterAll } from 'vitest';

// These synthetic scenarios describe an October cache, independent of run date.
beforeAll(()=>{vi.useFakeTimers({toFake:['Date']});vi.setSystemTime(new Date('2026-10-16T12:00:00+08:00'));});
afterAll(()=>vi.useRealTimers());

interface FakeElement {
  textContent: string;
  hidden: boolean;
  disabled: boolean;
  innerHTML: string;
  src: string;
  value: string;
  dataset: Record<string, string>;
  handlers: Record<string, Array<(event?: unknown) => void>>;
  classList: { toggle: () => void };
  append: (...nodes: unknown[]) => void;
  replaceChildren: () => void;
  addEventListener: (type: string, handler: (event?: unknown) => void) => void;
  removeAttribute: (name: string) => void;
  remove: () => void;
  closest: () => null;
}

function makeElement(dataset: Record<string, string> = {}): FakeElement {
  const element: FakeElement = {
    textContent: '',
    hidden: false,
    disabled: false,
    innerHTML: '',
    src: '',
    value: '',
    dataset,
    handlers: {},
    classList: { toggle: () => undefined },
    append: () => undefined,
    replaceChildren: () => undefined,
    remove: () => {element.hidden=true;},
    addEventListener(type, handler) {
      (element.handlers[type] ??= []).push(handler);
    },
    removeAttribute(name) {
      if (name === 'src') element.src = '';
    },
    closest: () => null,
  };
  return element;
}

const elements = new Map<string, FakeElement>();
const navButtons = [
  makeElement({ view: 'home' }),
  makeElement({ view: 'analysis' }),
  makeElement({ view: 'my' }),
];
const documentStub = {
  querySelector(selector: string): FakeElement {
    let element = elements.get(selector);
    if (!element) {
      element = makeElement();
      elements.set(selector, element);
    }
    return element;
  },
  querySelectorAll(selector: string): FakeElement[] {
    return selector === '.bottom-nav button' ? navButtons : [];
  },
  createElement: () => makeElement(),
  addEventListener: () => undefined,
  hidden: false,
};

vi.stubGlobal('document', documentStub);
vi.stubGlobal('window', { addEventListener: () => undefined });

// Scripted invoke: each test installs its own implementation.
const invokeState = vi.hoisted(() => ({
  impl: (_command: string, _args?: unknown): Promise<unknown> =>
    Promise.reject(new Error('invoke not scripted')),
  seen: [] as string[],
  context: {platform:'Android',preview:false},
}));

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (command: string, args?: unknown) => {
    invokeState.seen.push(command);
    if(command === 'app_context') return Promise.resolve(invokeState.context);
    return invokeState.impl(command, args);
  },
  isTauri: () => true,
}));

vi.mock('@tauri-apps/plugin-barcode-scanner', () => ({
  scan: vi.fn(),
  cancel: vi.fn(async () => undefined),
  Format: { QRCode: 'QRCode' },
  checkPermissions: vi.fn(async () => 'granted'),
  requestPermissions: vi.fn(async () => 'granted'),
}));

// A small but contract-shaped snapshot for display assertions.
const CACHED_SNAPSHOT = {
  schemaVersion: 2,
  sourceHash: 'a'.repeat(64),
  calendarMonth: '2026-10',
  generatedAt: '2026-10-15T09:00:00+08:00',
  sourceLastRecordedAt: '2026-10-15T10:00:00+08:00',
  ledgerCount: 3,
  months: [{
    month: '2026-10',
    lastRecordedAt: '2026-10-15T10:00:00+08:00',
    mechanismActive: true,
    summary: {
      personalCents: -25650, structuralCents: 1200000, totalCents: 1174350, unknownCents: -12800,
      monthlyBudgetCents: 600000, openingBudgetCents: 600000, availableCents: 574350,
      overrunCents: 0, safetyCents: 250000,
    },
    pools: [
      { id: 'operation', name: '运行', budgetCents: 300000, carryCents: null, openingCents: 300000, spentCents: -6250, endingCents: 306250, availableCents: 306250, overrunCents: null, usageRatio: 0.02 },
      { id: 'free', name: '随心', budgetCents: 150000, carryCents: null, openingCents: 150000, spentCents: 0, endingCents: 150000, availableCents: 150000, overrunCents: null, usageRatio: 0 },
      { id: 'travel', name: '旅行', budgetCents: 100000, carryCents: 0, openingCents: 100000, spentCents: 0, endingCents: 100000, availableCents: 100000, overrunCents: null, usageRatio: 0 },
      { id: 'growth', name: '成长', budgetCents: 50000, carryCents: 0, openingCents: 50000, spentCents: -6600, endingCents: 56600, availableCents: 56600, overrunCents: null, usageRatio: 0.13 },
      { id: 'unknown', name: '待归类', budgetCents: null, carryCents: null, openingCents: null, spentCents: -12800, endingCents: null, availableCents: null, overrunCents: null, usageRatio: null },
    ],
    daily: [{ date: '2026-10-02', amountCents: -2450, count: 1, operationCumulativeCents: -2450, referenceCents: -20000 }],
    categories: [{ name: '餐饮', amountCents: -2450, count: 1 }],
    records: [{
      id: 'led-001', time: '08:31', date: '2026-10-02', title: '真实早餐记录', category: '餐饮',
      pool: 'operation', scope: 'personal', amountCents: -2450,
    }],
    structuralRecords: [],
    topIds: [],
  }],
};

const LEDGER_READY = {
  initialized: true,
  publishEnabled: false,
  cachedRevision: 2,
  pendingRevision: null,
  lastPublishedRevision: 2,
};

async function flush(rounds = 10): Promise<void> {
  for (let i = 0; i < rounds; i += 1) await Promise.resolve();
}

function statusBarHtml(): string {
  return elements.get('#status-bar')!.innerHTML;
}

function mainHtml(): string {
  return elements.get('#main')!.innerHTML;
}

function click(id: string): void {
  for (const handler of elements.get(id)!.handlers.click ?? []) handler();
}

/** Use only an actual rendered native button, then bubble its click to the real router. */
function activateRenderedButton(attribute:string,value:string,detail=1):void {
  const button=[...mainHtml().matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)]
    .find(match=>match[1].includes(`${attribute}="${value}"`));
  expect(button,`rendered button ${attribute}=${value}`).toBeDefined();
  expect(button![1]).toContain('type="button"');
  expect(button![1]).not.toMatch(/\b(?:disabled|hidden)\b|tabindex="-1"/);
  const dataset=Object.fromEntries([...button![1].matchAll(/data-([\w-]+)="([^"]*)"/g)]
    .map(([,key,item])=>[key.replace(/-([a-z])/g,(_,letter:string)=>letter.toUpperCase()),item]));
  const target={closest:()=>({dataset})};
  for(const handler of elements.get('#main')!.handlers.click??[])handler({target,detail});
}

/** Navigate through the bottom nav (same handlers main.ts bound at import). */
function goto(view: 'home' | 'bills' | 'sync'): void {
  if(view !== 'home') {
    const dataset=view==='bills'?{action:'all-bills'}:{open:'sync'};
    for(const handler of elements.get('#main')!.handlers.click??[])handler({target:{closest:()=>({dataset})}});
    return;
  }
  const button = navButtons.find((candidate) => candidate.dataset.view === view)!;
  for (const handler of button.handlers.click ?? []) handler();
}

// The startup auto-fetch hangs until a test releases it; shared handle so the
// later scenarios can drain the queue deterministically.
let releaseStartupFetch: ((outcome: unknown) => void) | undefined;

async function importMainOnce(): Promise<void> {
  await import('./main');
}

describe('page startup flow (ledger cache-first display)', () => {
  beforeEach(() => {
    invokeState.seen = [];
    documentStub.hidden = false;
  });

  it('shows the cached ledger snapshot during startup before the network answers', async () => {
    invokeState.impl = (command) => {
      if (command === 'ledger_status_command') return Promise.resolve(LEDGER_READY);
      if (command === 'ledger_cache') {
        return Promise.resolve({ revision: 2, snapshot: CACHED_SNAPSHOT });
      }
      if (command === 'load_probe') {
        return Promise.resolve({ platform: 'Windows', cached: false, revision: null, syntheticRecords: 0, storage: 'Windows DPAPI', records: null });
      }
      if (command === 'sync_status') return Promise.resolve({ configured: true, paired: true });
      if (command === 'ledger_fetch') {
        return new Promise((resolve) => {
          releaseStartupFetch = resolve;
        });
      }
      if (command === 'app_version') return Promise.resolve('0.2.0-test');
      return Promise.reject(new Error(`unexpected command ${command}`));
    };
    await importMainOnce();
    await flush();
    // Startup display: ledger_cache already resolved, ledger_fetch still hanging.
    expect(statusBarHtml()).toContain('本地缓存 · 第 2 版');
    expect(statusBarHtml()).toContain('未连接云端确认');
    expect(statusBarHtml()).not.toContain('云端已确认');
    expect(mainHtml()).toContain('个人净消费');
    // The record list lives in the bills view; navigate there to confirm
    // the cached records themselves are rendered.
    goto('bills');
    expect(mainHtml()).toContain('真实早餐记录');
    // While the network hangs, the snapshot stays visible.
    expect(releaseStartupFetch).toBeDefined();
    goto('home');
    expect(mainHtml()).toContain('个人净消费');
  });

  it('drains the hanging fetch and switches to the cloud-confirmed view', async () => {
    releaseStartupFetch?.({ kind: 'updated', revision: 3, snapshot: CACHED_SNAPSHOT });
    await flush();
    expect(statusBarHtml()).toContain('云端已确认 · 第 3 版');
    expect(statusBarHtml()).toContain('自动检查：云端版本 3。');
    expect(mainHtml()).toContain('个人净消费');
  });

  it('keeps the snapshot visible when the network fails', async () => {
    invokeState.impl = (command) => {
      if (command === 'ledger_fetch') {
        return Promise.reject('云端拒绝访问（401/403），请检查坚果云应用密码是否有效。');
      }
      return Promise.reject(new Error(`unexpected command ${command}`));
    };
    click('#fetch-button');
    await flush();
    expect(statusBarHtml()).toContain('已保留当前数据');
    expect(mainHtml()).toContain('个人净消费');
  });

  it('reports a cloud without publications and keeps showing the cache', async () => {
    invokeState.impl = (command) => {
      if (command === 'ledger_fetch') return Promise.resolve({ kind: 'cloud-empty', hasCache: true });
      return Promise.reject(new Error(`unexpected command ${command}`));
    };
    click('#fetch-button');
    await flush();
    expect(statusBarHtml()).toContain('云端还没有发布文件，已保留本地缓存');
    // The retained cache is the last cloud-confirmed version, so the label stays honest.
    expect(statusBarHtml()).toContain('云端已确认 · 第 3 版');
    expect(mainHtml()).toContain('个人净消费');
  });

  it('shows a partially verified download as cache, never as a cloud-confirmed version', async () => {
    invokeState.impl = (command) => command === 'ledger_fetch'
      ? Promise.resolve({ kind: 'updated', revision: 4, warning: '部分云端文件检查未完成，可重试。', snapshot: CACHED_SNAPSHOT })
      : Promise.reject(new Error(`unexpected command ${command}`));
    click('#fetch-button');
    await flush();
    expect(statusBarHtml()).toContain('部分云端文件检查未完成');
    expect(statusBarHtml()).toContain('本地缓存 · 第 4 版');
    expect(statusBarHtml()).not.toContain('云端已确认');
    expect(mainHtml()).toContain('个人净消费');
  });

  it('places the recorded-through date only on home and inside My',()=>{
    for(const handler of navButtons.find(b=>b.dataset.view==='analysis')!.handlers.click??[])handler();
    expect(statusBarHtml()).not.toContain('账单截至');
    for(const handler of navButtons.find(b=>b.dataset.view==='my')!.handlers.click??[])handler();
    expect(statusBarHtml()).not.toContain('账单截至');
    expect((mainHtml().match(/账单截至/g)??[]).length).toBe(1);
    goto('home');expect(statusBarHtml()).toContain('账单截至');
  });

  it('keeps desktop publication controls out of the read-only phone sync view', async () => {
    invokeState.impl = (command) => {
      if (command === 'ledger_status_command') {
        return Promise.resolve({ ...LEDGER_READY, pendingRevision: 5 });
      }
      return Promise.reject(new Error(`unexpected command ${command}`));
    };
    // Navigate to the sync view through the bottom nav.
    goto('sync');
    await flush();
    const html = mainHtml();
    expect(html).not.toContain('待发内容');
    expect(html).not.toContain('真实上传');
    expect(html).toContain('本地数据可用');
    expect(html).toContain('data-action="manage-connection"');
  });
});

describe('native first run without pairing', () => {
  beforeEach(() => {
    vi.resetModules();
    invokeState.seen = [];
  });

  it('shows real onboarding when no cache and no pairing exist', async () => {
    invokeState.impl = (command) => {
      if (command === 'ledger_status_command') {
        return Promise.resolve({ initialized: false, publishEnabled: false, cachedRevision: null, pendingRevision: null, lastPublishedRevision: null });
      }
      if (command === 'ledger_cache') return Promise.resolve(null);
      if (command === 'load_probe') return Promise.reject(new Error('not paired'));
      if (command === 'sync_status') return Promise.resolve({ configured: false, paired: false });
      if (command === 'app_version') return Promise.resolve('0.2.0-test');
      return Promise.reject(new Error(`unexpected command ${command}`));
    };
    await import('./main');
    await flush();
    expect(statusBarHtml()).toContain('尚无账单数据');
    expect(mainHtml()).not.toContain('合成预览数据');
    expect(mainHtml()).not.toContain('个人净消费');
    expect(mainHtml()).toContain('data-open="sync"');
    // No network attempt without credentials and an initialized ledger.
    expect(invokeState.seen).not.toContain('ledger_fetch');
  });

  it('keeps an empty state with an honest reason when the cache read errors', async () => {
    invokeState.impl = (command) => {
      if (command === 'ledger_status_command') {
        return Promise.resolve({ initialized: false, publishEnabled: false, cachedRevision: null, pendingRevision: null, lastPublishedRevision: null });
      }
      // A fresh device fails the cache read (no identity configured yet).
      if (command === 'ledger_cache') return Promise.reject('【配置/安全存储】正式账本尚未初始化。');
      if (command === 'load_probe') return Promise.reject(new Error('not paired'));
      if (command === 'sync_status') return Promise.resolve({ configured: false, paired: false });
      if (command === 'app_version') return Promise.resolve('0.2.0-test');
      return Promise.reject(new Error(`unexpected command ${command}`));
    };
    await import('./main');
    await flush();
    expect(statusBarHtml()).toContain('尚无账单数据');
    expect(statusBarHtml()).toContain('正式账本尚未初始化');
    expect(mainHtml()).not.toContain('个人净消费');
    expect(invokeState.seen).not.toContain('ledger_fetch');
  });
});

describe('Windows local presentation and isolated preview',()=>{
  beforeEach(()=>{
    vi.resetModules();elements.clear();navButtons.forEach(button=>button.handlers={});
    invokeState.seen=[];invokeState.context={platform:'Windows',preview:false};
  });
  const common=(command:string):Promise<unknown>=>{
    if(command==='ledger_status_command')return Promise.resolve(LEDGER_READY);
    if(command==='ledger_cache')return Promise.resolve({revision:2,snapshot:CACHED_SNAPSHOT});
    if(command==='desktop_project_config')return Promise.resolve({projectRoot:'C:\\test-project',pythonExecutable:'C:\\python.exe'});
    if(command==='sync_status')return Promise.resolve({configured:true,paired:true});
    if(command==='app_version')return Promise.resolve('0.3.0-test');
    return Promise.reject(new Error(`unexpected command ${command}`));
  };
  it('keeps budget changes in the local API and rereads after a pre-save load finishes late',async()=>{
    let releaseOld:((result:unknown)=>void)|undefined;let loads=0;
    const amounts={'运行账户':'100.00','随心消费':'20.00','旅行/体验':'10.00','成长/项目/耐用品':'0.00'};
    const fresh=structuredClone(CACHED_SNAPSHOT);fresh.sourceHash='b'.repeat(64);
    invokeState.impl=(command,args:any)=>{
      if(command==='desktop_load_snapshot'){
        loads++;return loads===1?new Promise(resolve=>{releaseOld=resolve;}):Promise.resolve({snapshot:fresh,sourceChanged:false,monthChanged:false});
      }
      if(command==='desktop_budget_status')return Promise.resolve({sourceHash:'a'.repeat(64),revision:2,suggestedMonth:'2026-10',rates:[{effective:'2026-10',amounts}]});
      if(command==='desktop_budget_preview')return Promise.resolve({...args.request,previousAmounts:amounts,affectedThrough:null,changed:true,sourceHash:'a'.repeat(64),revision:2});
      if(command==='desktop_budget_save')return Promise.resolve({...args.request,previousAmounts:amounts,affectedThrough:null,changed:true,applied:true,sourceHash:'b'.repeat(64),revision:3,snapshotUpdated:true});
      return common(command);
    };
    await import('./main');await flush(20);click('#settings-button');activateRenderedButton('data-settings-tab','budget');await flush(20);
    expect(mainHtml()).toContain('id="budget-form"');
    const input=elements.get('#budget-operation')!;input.value='120';
    input.handlers.input.at(-1)!({target:input});
    elements.get('#budget-form')!.handlers.submit.at(-1)!({preventDefault(){}});await flush(15);
    expect(mainHtml()).toContain('id="budget-confirm-save"');
    expect(invokeState.seen).not.toContain('desktop_budget_save');
    elements.get('#budget-confirm-save')!.handlers.click.at(-1)!();await flush(10);
    releaseOld?.({snapshot:CACHED_SNAPSHOT,sourceChanged:false,monthChanged:false});await flush(40);
    expect(loads).toBe(2);expect(mainHtml()).toContain('额度已保存');
    for(const command of ['desktop_refresh_snapshot','ledger_fetch','sync_configure','ledger_publish'])expect(invokeState.seen).not.toContain(command);
  });
  it('opens optional budget settings from the observation overview without writing or publishing',async()=>{
    const observation=structuredClone(CACHED_SNAPSHOT);observation.months[0].mechanismActive=false;
    const zero={'运行账户':'0.00','随心消费':'0.00','旅行/体验':'0.00','成长/项目/耐用品':'0.00'};
    invokeState.impl=command=>command==='desktop_load_snapshot'?Promise.resolve({snapshot:observation,sourceChanged:false,monthChanged:false})
      :command==='desktop_budget_status'?Promise.resolve({sourceHash:'a'.repeat(64),revision:0,suggestedMonth:'2026-10',rates:[{effective:'2026-10',amounts:zero}]}):common(command);
    await import('./main');await flush(30);
    expect(mainHtml()).toContain('未设预算，默认额度为0');
    activateRenderedButton('data-action','edit-budget');await flush(20);
    expect(mainHtml()).toContain('id="budget-form"');expect(mainHtml()).toContain('data-settings-panel="budget" aria-labelledby="settings-budget-title"');
    for(const command of ['desktop_budget_save','desktop_refresh_snapshot','ledger_fetch'])expect(invokeState.seen).not.toContain(command);
  });
  it.each([{hasSnapshot:false,detail:1},{hasSnapshot:true,detail:0}])('opens sync from settings and returns with hasSnapshot=$hasSnapshot (click detail=$detail)',async({hasSnapshot,detail})=>{
    const initialStatus=hasSnapshot?LEDGER_READY:{initialized:false,publishEnabled:false,cachedRevision:null,pendingRevision:null,lastPublishedRevision:null};
    let currentStatus={...initialStatus,pendingRevision:initialStatus.pendingRevision as number|null};
    invokeState.impl=command=>command==='desktop_load_snapshot'
      ?Promise.resolve({snapshot:hasSnapshot?CACHED_SNAPSHOT:null,sourceChanged:false,monthChanged:false})
      :command==='ledger_cache'?Promise.resolve(hasSnapshot?{revision:2,snapshot:CACHED_SNAPSHOT}:null)
      :command==='ledger_status_command'?Promise.resolve(currentStatus):common(command);
    const assign=vi.fn();vi.stubGlobal('location',{pathname:'/',assign});
    await import('./main');await flush(30);click('#settings-button');
    expect(mainHtml()).toContain('data-settings-panel="general"');
    const readsBefore=invokeState.seen.filter(command=>command==='ledger_status_command').length;
    if(hasSnapshot)currentStatus={...initialStatus,pendingRevision:5};
    // Native buttons emit the same delegated click for pointer and keyboard activation.
    activateRenderedButton('data-settings-tab','sync',detail);await flush(25);
    expect(mainHtml()).toContain('data-settings-panel="sync" aria-labelledby="settings-sync-title"');
    expect(mainHtml()).toContain('id="sync-form"');
    expect(mainHtml()).toContain('id="show-pairing"');
    expect(mainHtml()).not.toContain('id="scan-pairing"');
    expect(mainHtml()).toContain(hasSnapshot?'第 5 版（尚未发布成功）':'未初始化（需先在电脑初始化或扫码配对）');
    expect(invokeState.seen.filter(command=>command==='ledger_status_command')).toHaveLength(readsBefore+1);
    activateRenderedButton('data-settings-tab','general');
    expect(mainHtml()).toContain('data-settings-panel="general"');
    expect(mainHtml()).not.toContain('class="view sync"');
    activateRenderedButton('data-action','back');
    expect(assign).toHaveBeenCalledWith('/');
    for(const command of ['ledger_fetch','sync_configure','ledger_create_pairing','ledger_import_pairing','desktop_refresh_snapshot'])expect(invokeState.seen).not.toContain(command);
  });
  it('opens the application settings directly and returns to the shared desktop overview',async()=>{
    invokeState.impl=command=>command==='desktop_load_snapshot'?Promise.resolve({snapshot:CACHED_SNAPSHOT,sourceChanged:false,monthChanged:false}):common(command);
    const assign=vi.fn();vi.stubGlobal('location',{pathname:'/',assign});
    const module=await import('./main');await flush(30);module.openAppSettings();
    expect(mainHtml()).toContain('id="theme-preference"');
    expect(mainHtml()).toContain('id="project-root"');
    expect(mainHtml()).not.toContain('class="my"');
    for(const handler of elements.get('#main')!.handlers.click??[])handler({target:{closest:()=>({dataset:{action:'back'}})}});
    expect(assign).toHaveBeenCalledWith('/');
  });
  it('keeps the secondary desktop header control out of the mobile My menu',async()=>{
    invokeState.impl=command=>command==='desktop_load_snapshot'?Promise.resolve({snapshot:CACHED_SNAPSHOT,sourceChanged:false,monthChanged:false}):common(command);
    await import('./main');await flush(30);click('#settings-button');
    expect(mainHtml()).toContain('id="theme-preference"');
    expect(mainHtml()).not.toContain('class="my"');
  });
  it('clears an optional encrypted-cache error after successfully loading the authoritative local snapshot',async()=>{
    invokeState.impl=command=>command==='ledger_cache'?Promise.reject('【配置/安全存储】正式账本尚未初始化。'):command==='desktop_load_snapshot'?Promise.resolve({snapshot:CACHED_SNAPSHOT,sourceChanged:false,monthChanged:false}):common(command);
    const module=await import('./main');module.openAppSettings();await flush(30);
    expect(statusBarHtml()).toContain('本机快照');
    expect(statusBarHtml()).not.toContain('正式账本尚未初始化');
    expect(mainHtml()).toContain('id="theme-preference"');
    expect(invokeState.seen).not.toContain('ledger_fetch');
  });
  it('retains the cache failure when the authoritative local source also cannot be loaded',async()=>{
    invokeState.impl=command=>command==='ledger_cache'?Promise.reject('【配置/安全存储】正式账本尚未初始化。'):command==='desktop_load_snapshot'?Promise.reject('本机账本读取失败。'):common(command);
    const module=await import('./main');module.openAppSettings();await flush(30);
    expect(statusBarHtml()).toContain('正式账本尚未初始化');
    expect(statusBarHtml()).toContain('本机账本读取失败');
  });
  it('paints cached data while the independent local source check is still pending',async()=>{
    let release:((value:unknown)=>void)|undefined;
    invokeState.impl=command=>command==='desktop_load_snapshot'?new Promise(resolve=>release=resolve):common(command);
    await import('./main');await flush(25);
    expect(mainHtml()).toContain('个人净消费');
    expect(statusBarHtml()).toContain('本地缓存 · 第 2 版');
    release?.({snapshot:null,sourceChanged:true,monthChanged:false});await flush(25);
    expect(statusBarHtml()).toContain('账本有更新');
    expect(mainHtml()).toContain('个人净消费');
    expect(invokeState.seen).not.toContain('ledger_fetch');
  });
  it('shows a validated local snapshot as local, without claiming cloud confirmation',async()=>{
    invokeState.impl=command=>command==='desktop_load_snapshot'?Promise.resolve({snapshot:CACHED_SNAPSHOT,sourceChanged:false,monthChanged:false}):common(command);
    await import('./main');await flush(30);
    expect(statusBarHtml()).toContain('本机快照');
    expect(statusBarHtml()).not.toContain('云端已确认');
    expect(invokeState.seen).not.toContain('ledger_fetch');
  });
  it('cloud checking never replaces an authoritative local view even with the same workbook hash',async()=>{
    const cloud={...CACHED_SNAPSHOT,generatedAt:'2026-09-15T09:00:00+08:00',calendarMonth:'2026-09'};
    invokeState.impl=command=>command==='desktop_load_snapshot'?Promise.resolve({snapshot:CACHED_SNAPSHOT,sourceChanged:false,monthChanged:false}):command==='ledger_fetch'?Promise.resolve({kind:'updated',revision:3,snapshot:cloud}):common(command);
    await import('./main');await flush(30);click('#fetch-button');await flush(25);
    expect(statusBarHtml()).toContain('继续显示本机快照');
    expect(statusBarHtml()).toContain('本机快照');
    expect(statusBarHtml()).not.toContain('云端已确认 · 第 3 版');
  });
  it('credential-save failures remain visible after rendering',async()=>{
    invokeState.impl=command=>command==='desktop_load_snapshot'?Promise.resolve({snapshot:CACHED_SNAPSHOT,sourceChanged:false,monthChanged:false}):command==='sync_configure'?Promise.reject('应用密码被拒绝，请重新输入。'):common(command);
    await import('./main');await flush(30);goto('sync');await flush(25);
    elements.get('#account')!.value='test@example.invalid';elements.get('#password')!.value='test-password';
    const handler=elements.get('#sync-form')!.handlers.submit.at(-1)!;
    await handler({preventDefault:()=>undefined});await flush(25);
    expect(statusBarHtml()).toContain('应用密码被拒绝');
    expect(elements.get('#password')!.value).toBe('');
  });
  it('defers an asynchronous repaint until Chinese composition is committed',async()=>{
    invokeState.context={platform:'Android',preview:false};
    let finish:((value:unknown)=>void)|undefined;
    invokeState.impl=command=>command==='ledger_fetch'?new Promise(resolve=>finish=resolve):common(command);
    await import('./main');await flush(30);goto('bills');
    const search=elements.get('#bill-search')!;search.value='午';
    for(const handler of search.handlers.compositionstart??[])handler({target:search});
    const before=mainHtml();
    const incoming=structuredClone(CACHED_SNAPSHOT);incoming.months[0].records[0].title='合成云端新记录';
    finish?.({kind:'updated',revision:3,snapshot:incoming});await flush(25);
    expect(mainHtml()).toBe(before);
    search.value='午餐';
    for(const handler of search.handlers.compositionend??[])handler({target:search});
    await flush(25);
    expect(mainHtml()).toContain('value="午餐"');
  });
  it('keeps a known publication result while warning that the source changed afterwards',async()=>{
    invokeState.impl=command=>command==='desktop_load_snapshot'?Promise.resolve({snapshot:CACHED_SNAPSHOT,sourceChanged:false,monthChanged:false}):command==='desktop_refresh_snapshot'?Promise.resolve({snapshot:CACHED_SNAPSHOT,sourceChanged:true,monthChanged:false,appPublish:{ok:true,published:true}}):common(command);
    await import('./main');await flush(30);click('#desktop-sync');await flush(30);
    expect(statusBarHtml()).toContain('云端发布已确认');
    expect(statusBarHtml()).toContain('账本随后有更新');
    expect(mainHtml()).toContain('个人净消费');
  });
  it('reports post-publication freshness uncertainty without hiding the local snapshot',async()=>{
    invokeState.impl=command=>command==='desktop_load_snapshot'?Promise.resolve({snapshot:CACHED_SNAPSHOT,sourceChanged:false,monthChanged:false}):command==='desktop_refresh_snapshot'?Promise.resolve({snapshot:CACHED_SNAPSHOT,sourceChanged:false,monthChanged:false,warning:'发布后未能确认来源，展示可能已过期。',appPublish:{ok:false,published:false,error:'云端确认失败'}}):common(command);
    await import('./main');await flush(30);click('#desktop-sync');await flush(30);
    expect(statusBarHtml()).toContain('云端确认失败');
    expect(statusBarHtml()).toContain('展示可能已过期');
    expect(mainHtml()).toContain('个人净消费');
  });
  it('safe preview does not touch identity, cache, project config or the network',async()=>{
    invokeState.context={platform:'Windows',preview:true};
    invokeState.impl=command=>command==='app_version'?Promise.resolve('0.3.0-test'):Promise.reject(new Error('preview must not invoke this'));
    await import('./main');await flush(25);
    expect(statusBarHtml()).toContain('合成预览');
    expect(invokeState.seen).toEqual(['app_context','app_version']);
    click('#desktop-sync');click('#fetch-button');goto('sync');await flush(25);
    expect(invokeState.seen).toEqual(['app_context','app_version']);
  });
});
