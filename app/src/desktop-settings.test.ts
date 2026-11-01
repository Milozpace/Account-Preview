import {describe,it,expect} from 'vitest';
import {desktopSettingsHtml,desktopSyncHtml} from './desktop-settings';
describe('desktop settings content',()=>{
 it('offers a native sync navigation button in the visible general panel',()=>{
  const html=desktopSettingsHtml({section:'general',theme:'system',motion:'system',config:null,version:'',diagnostics:''});
  const general=html.match(/<section data-settings-panel="general"[^>]*>([\s\S]*?)<\/section>/)![1];
  expect(general).toMatch(/<button\b[^>]*type="button"[^>]*data-open="sync"[^>]*>[\s\S]*?手机与同步[\s\S]*?<\/button>/);
  expect(general).not.toContain('id="sync-form"');
 });
 it('keeps advanced and diagnostic controls reachable without putting them on the general panel',()=>{
  const html=desktopSettingsHtml({section:'general',theme:'system',motion:'reduce',config:{projectRoot:'C:\\账本',pythonExecutable:'python.exe'},version:'0.3.12',diagnostics:'<button id="diagnose">诊断</button>'});
  expect(html).toContain('aria-current="page"');expect(html).toContain('data-settings-panel="advanced" hidden');
  expect(html).toContain('data-settings-panel="diagnostics" hidden');expect(html).toContain('id="project-root"');
  expect(html).toContain('data-theme-choice="system" aria-pressed="true"');expect(html).toContain('v0.3.12');
  expect(html).not.toContain('字体跟随系统设置');
 });
 it('escapes paths and opens the requested category',()=>{
  const html=desktopSettingsHtml({section:'advanced',theme:'dark',motion:'system',config:{projectRoot:'<script>',pythonExecutable:'"abc'},version:'',diagnostics:''});
  expect(html).toMatch(/data-settings-panel="advanced"(?! hidden)/);expect(html).toContain('&lt;script&gt;');expect(html).toContain('&quot;abc');
 });
 it('provides separate budget and sync categories while preserving inactive form markup',()=>{
  const html=desktopSettingsHtml({section:'budget',theme:'system',motion:'system',config:null,version:'',diagnostics:'',budgetContent:'<input id="budget-month">',syncContent:'<form id="sync-form"></form>'});
  expect(html).toContain('data-settings-tab="budget" aria-current="page">四池预算');
  expect(html).toContain('data-settings-tab="sync">手机与同步');
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
