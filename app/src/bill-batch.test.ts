import {describe,it,expect} from 'vitest';
import {BillUploadSession,billUploadHtml} from './bill-upload';
const entry={id:'a',fileName:'a.csv',byteCount:10,status:'uploaded' as const};
describe('bill batches',()=>{
 it('clears old batch failures after a confirmed retry or a cancelled new selection',async()=>{
  const s=new BillUploadSession();
  await s.choose(async()=>({items:[{...entry,status:'pending' as const}],completed:0,failed:1,failures:[{fileName:'a.csv',reason:'上传未确认'}]}));
  await s.retry(async()=>entry);
  expect(s.failures).toEqual([]);expect(s.items[0].status).toBe('uploaded');
  await s.choose(async()=>({items:[],completed:0,failed:1,failures:[{fileName:'bad.csv',reason:'读取失败'}]}));
  await s.choose(async()=>null);expect(s.failures).toEqual([]);
 });
 it('retains successes and reports each failure safely',async()=>{
  const s=new BillUploadSession();
  await s.choose(async()=>({items:[entry],completed:1,failed:1,failures:[{fileName:'<bad>.xlsx',reason:'读取失败'}]}));
  expect(s.items).toHaveLength(1);expect(s.error).toBe(true);expect(s.message).toContain('1');
  expect(billUploadHtml(s,true)).toContain('&lt;bad&gt;.xlsx');
  expect(billUploadHtml(s,true)).toContain('读取失败');
 });
 it('retains earlier successful items after a later cancellation',async()=>{
  const s=new BillUploadSession();await s.choose(async()=>({items:[entry],completed:1,failed:0}));
  await s.choose(async()=>null);expect(s.items).toHaveLength(1);expect(s.message).toBe('已取消选择');
 });
 it('does not duplicate same content across a batch',async()=>{
  const s=new BillUploadSession();await s.choose(async()=>({items:[entry,entry],completed:2,failed:0}));
  expect(s.items).toHaveLength(1);
 });
});
