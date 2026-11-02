import {describe,it,expect} from 'vitest';
import {mobileSyncHtml,type MobileSyncState} from './mobile-sync';
const connected:MobileSyncState={initialized:true,configured:true,connectionKnown:true,hasData:true,source:'cloud',revision:2,checking:false,warning:'',editingConnection:false,editingPairing:false};
describe('mobile sync has progressive connection controls',()=>{
 it('starts with pairing only, without fetch or credential forms',()=>{
  const html=mobileSyncHtml({...connected,initialized:false,configured:false,hasData:false,source:'none',revision:null},'<form id="sync-form"></form>','<button id="scan-pairing">扫码配对</button>');
  expect(html).toContain('id="scan-pairing"');expect(html).not.toContain('id="fetch-button"');expect(html).not.toContain('id="sync-form"');
  expect(html).toContain('连接电脑账本');
 });
 it('resumes credential setup after pairing, with no competing fetch button',()=>{
  const html=mobileSyncHtml({...connected,configured:false,hasData:false,source:'none'},'<form id="sync-form"></form>','<button id="scan-pairing">扫码</button>');
  expect(html).toContain('id="sync-form"');expect(html).not.toContain('id="scan-pairing"');expect(html).not.toContain('id="fetch-button"');
 });
 it('shows actual synced status while hiding connected credential and pairing forms',()=>{
  const html=mobileSyncHtml(connected,'<form id="sync-form"></form>','<button id="scan-pairing">扫码</button>');
  expect(html).toContain('已同步');expect(html).toContain('第 2 版');expect(html).toContain('id="fetch-button"');
  expect(html).not.toContain('id="sync-form"');expect(html).not.toContain('id="scan-pairing"');
  for(const term of ['真实上传','最近发布','待发内容','正式账本状态','已初始化'])expect(html).not.toContain(term);
 });
 it('exposes only missing setup and explicitly requested management controls',()=>{
  const credentials='<form id="sync-form"></form>',pairing='<button id="scan-pairing">扫码</button>';
  expect(mobileSyncHtml({...connected,configured:false},credentials,pairing)).toContain('id="sync-form"');
  const paired=mobileSyncHtml({...connected,initialized:false},credentials,pairing);
  expect(paired).toContain('id="scan-pairing"');expect(paired).not.toContain('id="sync-form"');
  expect(mobileSyncHtml({...connected,editingConnection:true},credentials,pairing)).toContain('id="sync-form"');
 });
 it('keeps cache, no-data and failure states honest and escapes warnings',()=>{
  const cache=mobileSyncHtml({...connected,source:'cache',warning:'<script>坏网络</script>'},'','');
  expect(cache).not.toContain('<script>');expect(cache).toContain('&lt;script&gt;');
  expect(cache).toContain('本地数据可用');expect(cache).not.toContain('<h2>已同步</h2>');
  expect(mobileSyncHtml({...connected,hasData:false,source:'none'},'','')).toContain('等待首次同步');
 });
 it('does not tell an unconfirmed device to pair again',()=>{
  const html=mobileSyncHtml({...connected,initialized:null,hasData:false,source:'none',revision:null},'','');
  expect(html).not.toContain('扫描电脑上的配对二维码');expect(html).toContain('设备状态尚未确认');
 });
});
