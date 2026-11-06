import { filterRecords, type AppMonth } from './bills';
import type { LedgerRecord } from '../../dashboard/src/types';

export type RootView = 'home' | 'analysis' | 'my';
export type AnalysisTab = 'trend' | 'composition' | 'calendar';
export interface BillQuery { query: string; category: string; pool: string; date: string; scope: 'personal' | 'structural' | 'income' }
export const emptyQuery = (): BillQuery => ({ query: '', category: '', pool: '', date: '', scope: 'personal' });
export type View = RootView | 'bills' | 'pool' | 'detail' | 'sync' | 'settings' | 'about' | 'diagnostics' | 'export' | 'upload';
export interface Screen { view: View; query?: BillQuery; recordId?: string; poolId?: string; scroll?: number; historyKey?:number }

/** Each tab owns its stack; no navigation state contains ledger amounts. */
export class Navigation {
  root: RootView = 'home';
  private stacks: Record<RootView, Screen[]> = { home: [{view:'home',scroll:0}], analysis:[{view:'analysis',scroll:0}], my:[{view:'my',scroll:0}] };
  get current(): Screen { return this.stacks[this.root].at(-1)!; }
  get canBack(): boolean { return this.stacks[this.root].length > 1; }
  save(scroll: number): void { this.current.scroll = scroll; }
  selectRoot(root: RootView, scroll = 0): void {
    this.save(scroll);
    if(root===this.root&&this.canBack)this.stacks[root]=[this.stacks[root][0]];
    this.root = root;
  }
  resetRoot(root: RootView): void { this.root=root;this.stacks[root]=[{view:root,scroll:0}]; }
  open(screen: Screen, scroll = 0): void { this.save(scroll); this.stacks[this.root].push({...screen,scroll:screen.scroll ?? 0}); }
  back(): boolean { if (!this.canBack) return false; this.stacks[this.root].pop(); return true; }
  resetDates(): void { for (const stack of Object.values(this.stacks)) for (const screen of stack) { if(screen.query) screen.query.date=''; } }
  restrictMonth(month:string|null):void {for(const stack of Object.values(this.stacks))for(const screen of stack)if(screen.query?.date&&!screen.query.date.startsWith(`${month}-`))screen.query.date='';}
  /** Reconcile every saved tab against the shared month, including history restores. */
  reconcile(month:AppMonth|null, coveredIds:ReadonlySet<string>):void {
    for(const root of ['home','analysis','my'] as const){
      this.stacks[root]=this.stacks[root].filter(screen=>screen.view!=='detail'||Boolean(screen.recordId&&coveredIds.has(screen.recordId)));
      for(const screen of this.stacks[root]){
        const query=screen.query;if(!query)continue;
        const records=query.scope==='structural'?month?.structuralRecords:query.scope==='income'?month?.incomeRecords:month?.records;
        if(query.category&&!records?.some(record=>record.category===query.category))query.category='';
        if(query.date&&!month?.daily.some(day=>day.date===query.date))query.date='';
        if(query.scope!=='personal')query.pool='';
      }
    }
  }
  snapshot(): {root:RootView;stacks:Record<RootView,Screen[]>} { return structuredClone({root:this.root,stacks:this.stacks}); }
  restore(value:unknown):boolean {
    if(!value||typeof value!=='object')return false;
    const state=value as ReturnType<Navigation['snapshot']>;
    const roots:RootView[]=['home','analysis','my'];
    if(!roots.includes(state.root)||!state.stacks)return false;
    const views:View[]=[...roots,'bills','pool','detail','sync','settings','about','diagnostics','export','upload'];
    for(const root of roots){const stack=state.stacks[root];if(!Array.isArray(stack)||!stack.length||stack[0].view!==root||stack.some(screen=>!screen||!views.includes(screen.view)))return false;}
    this.root=state.root;this.stacks=structuredClone(state.stacks);return true;
  }
}

export function selectBills(month: AppMonth, query: BillQuery): LedgerRecord[] {
  const records = query.scope === 'structural' ? month.structuralRecords
    : query.scope === 'income' ? month.incomeRecords
    : month.records;
  return filterRecords(records, query).filter(record => !query.date || record.date === query.date);
}
export function groupDays(records: LedgerRecord[]): Array<{date:string;cents:number;records:LedgerRecord[]}> {
  const groups: Array<{date:string;cents:number;records:LedgerRecord[]}> = [];
  for(const record of records) {
    let last=groups.at(-1);
    if(!last || last.date!==record.date) { last={date:record.date,cents:0,records:[]};groups.push(last); }
    last.cents+=record.amountCents;last.records.push(record);
  }
  return groups;
}
