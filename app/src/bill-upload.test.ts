import {describe,it,expect} from 'vitest';
import {BillUploadSession,billUploadHtml,type BillUploadItem} from './bill-upload';
import {myHtml} from './mobile';
import {Navigation} from './navigation';
const entry=(status:BillUploadItem['status']='uploaded'):BillUploadItem=>({id:'a'.repeat(64),fileName:'微信账单.xlsx',byteCount:4096,status});
describe('raw bill upload session',()=>{
 it('keeps the upload entry available in My without ledger data and restores its navigation',()=>{
  expect(myHtml(null,'')).toContain('data-open="upload"');expect(myHtml(null,'')).toContain('上传原始账单');
  const navigation=new Navigation();navigation.selectRoot('my');
  expect(navigation.restore({root:'my',stacks:{home:[{view:'home'}],analysis:[{view:'analysis'}],my:[{view:'my'},{view:'upload'}]}})).toBe(true);
  expect(navigation.back()).toBe(true);expect(navigation.current.view).toBe('my');
 });
 it('keeps cancellation separate from upload success',async()=>{
  const state=new BillUploadSession();await state.choose(async()=>null);
  expect(state.items).toEqual([]);expect(state.message).toBe('已取消选择');expect(state.error).toBe(false);
 });
 it('deduplicates the same file and preserves recorded status',async()=>{
  const state=new BillUploadSession();await state.load(async()=>[entry('recorded')]);
  await state.choose(async()=>entry());expect(state.items).toHaveLength(1);expect(state.items[0].status).toBe('recorded');
  expect(billUploadHtml(state,true)).not.toContain('bill-upload-item');
  expect(billUploadHtml(state,true)).toContain('暂无待处理账单');
 });
 it('removes only confirmed completed files and preserves pending work on failure',async()=>{
  const state=new BillUploadSession();
  await state.load(async()=>[entry(),{...entry('pending'),id:'b'.repeat(64),fileName:'待重试.csv'}]);
  await state.load(async()=>[entry('recorded')],true);
  expect(state.activeItems.map(item=>item.id)).toEqual(['b'.repeat(64)]);
  await state.load(async()=>{throw Error('offline');},true);
  expect(state.activeItems).toHaveLength(1);expect(billUploadHtml(state,true)).toContain('待重试.csv');
 });
 it('uses authenticated local progress if a later receipt request fails',async()=>{
  const state=new BillUploadSession();const second={...entry(),id:'b'.repeat(64)};
  await state.load(async()=>[entry(),second]);
  await state.load(async()=>{throw Error('second request failed');},true,async()=>[entry('recorded'),second]);
  expect(state.error).toBe(true);expect(state.activeItems.map(item=>item.id)).toEqual([second.id]);
 });
 it('makes the whole upload area the primary button and refresh an icon only when needed',async()=>{
  const state=new BillUploadSession();
  let html=billUploadHtml(state,true);
  expect(html).toMatch(/<button[^>]*id="bill-upload-choose"[^>]*class="bill-upload-dropzone"/);
  expect(html).toContain('点击上传账单');expect(html).not.toContain('id="bill-upload-check"');
  expect(html).not.toContain('文件加密上传后');
  await state.load(async()=>[entry()]);html=billUploadHtml(state,true);
  expect(html).toContain('class="bill-upload-refresh"');expect(html).toContain('aria-label="检查处理结果"');
  expect(html).not.toContain('>检查处理结果</button>');
 });
 it('does not clear existing status on network errors or reveal native exception details',async()=>{
  const state=new BillUploadSession();await state.load(async()=>[entry()]);
  await state.load(async()=>{throw Error('secret/path/credential');},true);
  expect(state.items).toHaveLength(1);expect(state.error).toBe(true);expect(state.message).not.toContain('secret');
 });
 it('blocks concurrent selections and releases after failure',async()=>{
  const state=new BillUploadSession();let finish!:(v:BillUploadItem|null)=>void;let calls=0;
  const pending=state.choose(()=>new Promise(resolve=>{finish=resolve;}));
  await state.choose(async()=>{calls++;return entry();});expect(calls).toBe(0);expect(state.busy).toBe(true);
  finish(entry());await pending;expect(state.busy).toBe(false);expect(state.items).toHaveLength(1);
 });
 it('keeps the upload label stable while the secondary status check runs',async()=>{
  const state=new BillUploadSession();await state.load(async()=>[entry()]);
  let finish!:(items:BillUploadItem[])=>void;
  const request=state.load(()=>new Promise(resolve=>{finish=resolve;}),true);
  const html=billUploadHtml(state,true);
  expect(html).toContain('点击上传账单');expect(html).toContain('aria-busy="true"');
  expect(html).not.toContain('>正在处理…</strong>');
  finish([entry()]);await request;
 });
 it('shows resumable pending uploads without claiming success',async()=>{
  const state=new BillUploadSession();await state.load(async()=>[entry('pending')]);
  const html=billUploadHtml(state,true);expect(html).toContain('待重试');expect(html).toContain('data-upload-retry');expect(html).not.toContain('已记账');
 });
 it('renders escaped names and explicit processing states with no raw file input',async()=>{
  const state=new BillUploadSession();await state.load(async()=>[{...entry('received'),fileName:'<script>.csv'}]);
  const html=billUploadHtml(state,true);expect(html).toContain('&lt;script&gt;.csv');expect(html).toContain('电脑已收取');expect(html).not.toContain('type="file"');
 });
 it('offers upload even with an empty ledger and disables browser upload',()=>{
  const state=new BillUploadSession();expect(billUploadHtml(state,true)).toContain('点击上传账单');
  expect(billUploadHtml(state,false)).toContain('请在手机 App 中上传');
 });
});
