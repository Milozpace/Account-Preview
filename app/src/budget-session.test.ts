import {describe,it,expect,vi} from 'vitest';
import {BudgetSession} from './budget-session';
const amounts={'运行账户':'100.00','随心消费':'20.00','旅行/体验':'10.00','成长/项目/耐用品':'0.00'};
const status={sourceHash:'a'.repeat(64),revision:2,suggestedMonth:'2026-10',rates:[{effective:'2026-10',amounts}]};
describe('local budget editing session',()=>{
 it('stays locked until editing starts and discards changes on cancel',async()=>{
  const transport=vi.fn(async()=>structuredClone(status));
  const session=new BudgetSession(transport);await session.load();
  expect(session.editing).toBe(false);
  session.change('运行账户','999');await session.preflight();
  expect(session.draft.amounts['运行账户']).toBe('100.00');expect(transport).toHaveBeenCalledTimes(1);
  session.beginEdit();session.change('运行账户','999');session.change('effective','2026-11');
  session.cancelEdit();expect(session.editing).toBe(false);expect(session.draft.effective).toBe('2026-10');
  expect(session.draft.amounts['运行账户']).toBe('100.00');expect(session.preview).toBeNull();
  expect(await session.save()).toBe(false);expect(transport).toHaveBeenCalledTimes(1);
 });
 it('reads a selected month, previews normalized amounts then explicitly saves without cloud commands',async()=>{
  let updated=false;
  const transport=vi.fn(async(command:string,args?:any)=>{
   if(command==='desktop_budget_status')return updated?{...status,sourceHash:'b'.repeat(64),revision:3}:status;
   if(command==='desktop_budget_preview')return {...args.request,previousAmounts:amounts,affectedThrough:null,changed:true,sourceHash:status.sourceHash,revision:2};
   if(command==='desktop_budget_save'){updated=true;return {...args.request,previousAmounts:amounts,affectedThrough:null,changed:true,applied:true,sourceHash:'b'.repeat(64),revision:3,snapshotUpdated:true};}
   throw new Error('unexpected command');
  });
  const session=new BudgetSession(transport);await session.load();session.beginEdit();
  session.change('运行账户','123.4');await session.preflight();
  expect(session.preview?.amounts['运行账户']).toBe('123.40');
  expect(transport.mock.calls.map(c=>c[0])).not.toContain('desktop_budget_save');
  expect(await session.save()).toBe(true);
  expect(session.editing).toBe(false);expect(session.draft.amounts['运行账户']).toBe('123.40');
  expect(transport.mock.calls.find(c=>c[0]==='desktop_budget_save')?.[1].request.reason).toBe('用户在电脑设置中确认调整四池额度');
  expect(session.message).toContain('已保存');expect(session.status?.revision).toBe(3);
  expect(transport.mock.calls.every(c=>c[0].startsWith('desktop_budget_'))).toBe(true);
 });
 it('does not save unpreviewed or changed drafts and retains inputs on rejected stale save',async()=>{
  const transport=vi.fn(async(command:string,args?:any)=>{
   if(command==='desktop_budget_status')return status;
   if(command==='desktop_budget_preview')return {...args.request,previousAmounts:amounts,affectedThrough:null,changed:true,sourceHash:status.sourceHash,revision:2};
   throw '账本已变化，请重新读取';
  });
  const session=new BudgetSession(transport);await session.load();session.beginEdit();
  expect(await session.save()).toBe(false);session.change('运行账户','125');await session.preflight();
  session.change('运行账户','130');expect(session.preview).toBeNull();expect(await session.save()).toBe(false);
  await session.preflight();expect(await session.save()).toBe(false);
  expect(session.draft.amounts['运行账户']).toBe('130');expect(session.error).toBe(true);
 });
 it('rejects invalid decimal inputs and discards preview that arrives after a draft change',async()=>{
  let release:((value:unknown)=>void)|undefined;
  const transport=vi.fn(async(command:string,args?:any)=>command==='desktop_budget_status'?status:new Promise(resolve=>{release=()=>resolve({...args.request,changed:true,sourceHash:status.sourceHash,revision:2});}));
  const session=new BudgetSession(transport);await session.load();session.beginEdit();
  for(const invalid of ['-1','1.234','NaN','1e3']){session.change('运行账户',invalid);await session.preflight();expect(session.error).toBe(true);}
  expect(transport).toHaveBeenCalledTimes(1);
  session.change('运行账户','120');const pending=session.preflight();session.change('运行账户','121');release?.({});await pending;
  expect(session.preview).toBeNull();expect(session.draft.amounts['运行账户']).toBe('121');
 });
});
