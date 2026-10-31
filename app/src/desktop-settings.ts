import {escapeHtml} from '../../dashboard/src/helpers';
export type SettingsSection='general'|'advanced'|'diagnostics';
interface SettingsOptions {
 section:SettingsSection;theme:'system'|'light'|'dark';motion:'system'|'reduce';
 config:{projectRoot:string;pythonExecutable:string}|null;version:string;diagnostics:string;
}
export function desktopSettingsHtml(options:SettingsOptions):string {
 const {section,theme,motion,config,version,diagnostics}=options;
 const hidden=(id:SettingsSection)=>section===id?'':' hidden';
 return `<section class="desktop-settings" aria-label="设置">
  <aside class="settings-sidebar"><nav aria-label="设置分类">${([['general','一般'],['advanced','高级'],['diagnostics','开发与诊断']] as const).map(([id,label])=>`<button type="button" data-settings-tab="${id}"${section===id?' aria-current="page"':''}>${label}</button>`).join('')}</nav><span class="settings-version">${version?`v${escapeHtml(version)}`:''}</span></aside>
  <div class="settings-workspace">
   <section data-settings-panel="general"${hidden('general')} aria-labelledby="settings-general-title"><h2 id="settings-general-title">一般</h2>
    <div class="desktop-setting-row"><label id="appearance-label">外观</label><div id="theme-preference" class="settings-segments" role="group" aria-labelledby="appearance-label">${([['system','跟随系统'],['light','浅色'],['dark','深色']] as const).map(([value,label])=>`<button type="button" data-theme-choice="${value}" aria-pressed="${theme===value}">${label}</button>`).join('')}</div></div>
    <div class="desktop-setting-row"><label for="motion-preference">动态效果</label><select id="motion-preference"><option value="system"${motion==='system'?' selected':''}>跟随系统</option><option value="reduce"${motion==='reduce'?' selected':''}>减少动态效果</option></select></div>
   </section>
   <section data-settings-panel="advanced"${hidden('advanced')} aria-labelledby="settings-advanced-title"><h2 id="settings-advanced-title">高级</h2><form id="desktop-config-form">
    <div class="desktop-path-row"><label for="project-root">账本项目目录</label><input id="project-root" value="${escapeHtml(config?.projectRoot??'')}" autocomplete="off" spellcheck="false"></div>
    <div class="desktop-path-row"><label for="python-path">Python 程序</label><input id="python-path" value="${escapeHtml(config?.pythonExecutable??'')}" autocomplete="off" spellcheck="false"></div>
    <div class="settings-save-row"><button type="submit" class="settings-save">保存本机配置</button><p id="desktop-config-status" role="status" aria-live="polite"></p></div>
   </form></section>
   <section data-settings-panel="diagnostics"${hidden('diagnostics')} aria-labelledby="settings-diagnostics-title"><h2 id="settings-diagnostics-title">开发与诊断</h2>${diagnostics}</section>
  </div></section>`;
}
