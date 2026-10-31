import {describe,it,expect} from 'vitest';
import {desktopSettingsHtml} from './desktop-settings';
describe('desktop settings content',()=>{
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
});
