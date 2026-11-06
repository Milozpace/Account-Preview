import {describe,it,expect} from 'vitest';
import {desktopSettingsHtml,desktopSyncHtml,desktopConnectionHtml} from './desktop-settings';
describe('desktop settings content',()=>{
 it('shows saved connection status without credential fields until explicitly editing',()=>{
  const html=desktopConnectionHtml({configured:true,known:true,editing:false,busy:false});
  expect(html).toContain('已配置');expect(html).toContain('data-action="manage-connection"');
  expect(html).not.toContain('id="account"');expect(html).not.toContain('id="password"');
  const edit=desktopConnectionHtml({configured:true,known:true,editing:true,busy:false});
  expect(edit).toContain('id="sync-form"');expect(edit).toContain('取消更换');
  expect(desktopConnectionHtml({configured:false,known:false,editing:false,busy:false})).not.toContain('id="sync-form"');
  expect(desktopConnectionHtml({configured:false,known:true,editing:false,busy:false})).toContain('id="sync-form"');
 });
 it('offers retry instead of an endless loading message when connection status fails',()=>{
  const html=desktopConnectionHtml({configured:false,known:false,editing:false,busy:false,readError:'读取失败'});
  expect(html).toContain('data-action="retry-connection-status"');expect(html).toContain('读取失败');
  expect(html).not.toContain('正在读取本机连接状态');expect(html).not.toContain('id="sync-form"');
 });
 it('keeps only the sidebar sync entry and removes the old general shortcut',()=>{
  const html=desktopSettingsHtml({section:'general',theme:'system',config:null,version:''});
  const general=html.match(/<section data-settings-panel="general"[^>]*>([\s\S]*?)<\/section>/)![1];
  expect(general).not.toContain('data-open="sync"');
  expect(html.match(/data-settings-tab="sync"/g)).toHaveLength(1);
  expect(general).not.toContain('id="sync-form"');
 });
 it('keeps the ledger folder reachable without putting it on the general panel',()=>{
  const html=desktopSettingsHtml({section:'general',theme:'system',config:{projectRoot:'C:\\账本',pythonExecutable:'python.exe'},version:'0.3.12'});
  expect(html).toContain('aria-current="page"');expect(html).toContain('data-settings-panel="advanced" hidden');
  expect(html).not.toContain('data-settings-panel="diagnostics"');
  expect(html.match(/data-settings-tab=/g)).toHaveLength(4);
  expect(html).not.toContain('id="project-root"');
  expect(html).toContain('id="choose-project"');
  expect(html).toContain('C:\\账本');
  expect(html).toContain('data-theme-choice="system" aria-pressed="true"');expect(html).toContain('v0.3.12');
  expect(html).not.toContain('字体跟随系统设置');
 });
 it('escapes paths and opens the requested category',()=>{
  const html=desktopSettingsHtml({section:'advanced',theme:'dark',config:{projectRoot:'<script>',pythonExecutable:'"abc'},version:''});
  expect(html).toMatch(/data-settings-panel="advanced"(?! hidden)/);expect(html).toContain('&lt;script&gt;');
 });
 it('provides separate budget and sync categories while preserving inactive form markup',()=>{
  const html=desktopSettingsHtml({section:'budget',theme:'system',config:null,version:'',budgetContent:'<input id="budget-month">',syncContent:'<form id="sync-form"></form>'});
  expect(html).toMatch(/data-settings-tab="budget" aria-current="page">[\s\S]*?<span>四池预算<\/span>/);
  expect(html).toMatch(/data-settings-tab="sync">[\s\S]*?<span>手机与同步<\/span>/);
  expect(html).toContain('data-settings-panel="budget" aria-labelledby="settings-budget-title"');
  expect(html).toContain('data-settings-panel="sync" hidden');
  expect(html).toContain('<input id="budget-month">');
  expect(html).toContain('<form id="sync-form"></form>');
 });
 it('groups sync status, existing credentials and pairing controls without mobile cards',()=>{
  const html=desktopSyncHtml('<dl>本机状态</dl>','<div class="card-block"><form id="sync-form"><input id="account"><input id="password"><button id="save-sync">保存</button></form></div>','<button id="show-pairing">显示二维码</button><button id="hide-qr">隐藏</button>',true,true);
  expect(html).toContain('desktop-sync-settings');
  expect(html).toContain('id="fetch-button" type="button" disabled');
  expect(html).toContain('id="sync-form"');expect(html).toContain('id="show-pairing"');expect(html).toContain('id="hide-qr"');
  expect(html).toContain('id="pair-status"');expect(html).not.toContain('sync-summary');
  expect(desktopSyncHtml('','','',false,false)).toContain('id="fetch-button" type="button" disabled');
 });
});
