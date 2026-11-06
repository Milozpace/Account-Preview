import {describe,it,expect,vi,afterEach} from 'vitest';
import fixture from '../../tests/fixtures/app-v2-snapshot.json';
import {createDesktopData} from './desktop-data';
import type {AppSnapshot} from './bills';

const fresh=()=>structuredClone(fixture) as AppSnapshot;
const deferred=<T>()=>{let resolve!:(value:T)=>void;const promise=new Promise<T>(r=>resolve=r);return{promise,resolve};};
afterEach(()=>vi.useRealTimers());
describe('native data behind the unchanged web presentation',()=>{
 it('waits for the local source when an optional cache is unavailable',async()=>{
  const local=deferred<unknown>();const calls:string[]=[];
  const data=createDesktopData({invoke:async command=>{calls.push(command);if(command==='ledger_cache')throw '正式账本尚未初始化';return local.promise;}});
  let settled=false;const snapshot=data.api<any>('/api/dashboard').finally(()=>{settled=true;});
  await Promise.resolve();await Promise.resolve();await Promise.resolve();
  expect(settled).toBe(false);expect(calls).toContain('desktop_load_snapshot');
  local.resolve({snapshot:fresh(),sourceChanged:false,monthChanged:false});
  expect((await snapshot).months).toEqual(fixture.months);
  expect((await data.api<any>('/api/status')).error).toBeNull();
 });
 it('distinguishes an empty source from an actual read failure',async()=>{
  const empty=createDesktopData({invoke:async command=>command==='ledger_cache'?null:{snapshot:null,sourceChanged:false,monthChanged:false}});
  const status=await empty.api<any>('/api/status');expect(status.localState).toBe('empty');expect(status.error).toBeNull();
  const failed=createDesktopData({invoke:async command=>{throw command==='ledger_cache'?'可选缓存错误':'本机路径不可用';}});
  expect((await failed.api<any>('/api/status')).error).toBe('本机路径不可用');
  expect((await failed.api<any>('/api/status')).localState).toBe('error');
 });
 it('retries a failed local read without refreshing or publishing',async()=>{
  let recovered=false;const calls:string[]=[];
  const data=createDesktopData({invoke:async command=>{calls.push(command);if(command==='ledger_cache')return null;if(!recovered)throw '读取失败';return{snapshot:fresh(),sourceChanged:false,monthChanged:false};}});
  await data.api('/api/status');recovered=true;
  await data.retry();expect((await data.api<any>('/api/dashboard')).months).toEqual(fixture.months);
  expect(calls).toEqual(['ledger_cache','desktop_load_snapshot','desktop_load_snapshot']);
 });
 it('shows the verified cache before a slow local read and never fetches cloud automatically',async()=>{
  vi.useFakeTimers();const local=deferred<unknown>();const calls:string[]=[];
  const data=createDesktopData({invoke:async command=>{calls.push(command);if(command==='ledger_cache')return{revision:7,snapshot:fresh()};if(command==='desktop_load_snapshot')return local.promise;throw Error('unexpected');},today:()=> '2026-10-17'});
  const snapshot=await data.api<any>('/api/dashboard');expect(snapshot.schemaVersion).toBe(2);expect(snapshot.months).toEqual(fixture.months);
  await vi.runOnlyPendingTimersAsync();expect(calls).toEqual(['ledger_cache','desktop_load_snapshot']);
  expect((await data.api<any>('/api/status')).syncDetail).toContain('本地缓存');
  local.resolve({snapshot:fresh(),sourceChanged:false,monthChanged:false});await Promise.resolve();await Promise.resolve();
  expect((await data.api<any>('/api/status')).syncDetail).toContain('本机快照');
  expect(calls).not.toContain('ledger_fetch');
 });
 it('preserves cache and real month labels when the local source is stale',async()=>{
  vi.useFakeTimers();const data=createDesktopData({invoke:async command=>command==='ledger_cache'?{revision:7,snapshot:fresh()}:{snapshot:null,sourceChanged:true,monthChanged:true},today:()=> '2026-11-01'});
  await data.api('/api/dashboard');await vi.runOnlyPendingTimersAsync();
  const status=await data.api<any>('/api/status');expect(status.sourceChanged).toBe(true);expect(status.monthChanged).toBe(true);expect(status.today).toBe('2026-11-01');
  expect((await data.api<any>('/api/dashboard')).calendarMonth).toBe('2026-10');
 });
 it('renders a refreshed local snapshot even if cloud publication fails',async()=>{
  vi.useFakeTimers();const updated=fresh();updated.generatedAt='2026-10-17T10:00:00+08:00';const changes:boolean[]=[];
  const data=createDesktopData({invoke:async command=>command==='ledger_cache'?{revision:1,snapshot:fresh()}:command==='desktop_load_snapshot'?{snapshot:fresh(),sourceChanged:false,monthChanged:false}:{snapshot:updated,sourceChanged:true,monthChanged:false,appPublish:{ok:false,published:false,error:'云端确认失败'}},today:()=> '2026-10-17'});
  data.subscribe(change=>changes.push(change));await data.api('/api/dashboard');await vi.runOnlyPendingTimersAsync();await data.api('/api/sync',{method:'POST'});
  expect((await data.api<any>('/api/dashboard')).generatedAt).toBe(updated.generatedAt);
  const status=await data.api<any>('/api/status');expect(status.error).toContain('本机展示已更新');expect(status.error).toContain('云端确认失败');expect(status.sourceChanged).toBe(true);expect(changes).toContain(true);
 });
 it('cannot let an older pending local read replace a manual refresh',async()=>{
  vi.useFakeTimers();const oldRead=deferred<unknown>();const updated=fresh();updated.generatedAt='2026-10-17T10:00:00+08:00';
  const data=createDesktopData({invoke:async command=>command==='ledger_cache'?{revision:1,snapshot:fresh()}:command==='desktop_load_snapshot'?oldRead.promise:{snapshot:updated,sourceChanged:false,monthChanged:false,appPublish:{ok:true,published:true}},today:()=> '2026-10-17'});
  await data.api('/api/dashboard');await vi.runOnlyPendingTimersAsync();await data.api('/api/sync',{method:'POST'});oldRead.resolve({snapshot:fresh(),sourceChanged:false,monthChanged:false});await Promise.resolve();await Promise.resolve();
  expect((await data.api<any>('/api/dashboard')).generatedAt).toBe(updated.generatedAt);expect((await data.api<any>('/api/status')).error).toBeNull();
 });
 it('safe preview performs no native reads or writes',async()=>{
  vi.useFakeTimers();const invoke=vi.fn();const data=createDesktopData({invoke,previewSnapshot:fresh(),today:()=> '2026-10-17'});
  expect((await data.api<any>('/api/dashboard')).months).toEqual(fixture.months);await vi.runOnlyPendingTimersAsync();await expect(data.api('/api/sync',{method:'POST'})).rejects.toThrow('预览');expect(invoke).not.toHaveBeenCalled();
 });
 it('a later source check preserves the independently known cloud failure',async()=>{
  vi.useFakeTimers();const data=createDesktopData({invoke:async command=>command==='ledger_cache'?{revision:1,snapshot:fresh()}:command==='desktop_load_snapshot'?{snapshot:fresh(),sourceChanged:false,monthChanged:false}:{snapshot:fresh(),sourceChanged:false,monthChanged:false,appPublish:{ok:false,published:false,error:'云端确认失败'}},today:()=> '2026-10-17'});
  await data.api('/api/dashboard');await vi.runOnlyPendingTimersAsync();await data.api('/api/sync',{method:'POST'});await vi.advanceTimersByTimeAsync(6000);await data.api('/api/status');await Promise.resolve();await Promise.resolve();
  expect((await data.api<any>('/api/status')).error).toContain('云端确认失败');
 });
});
