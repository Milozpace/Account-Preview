import {escapeHtml} from '../../dashboard/src/helpers';
export type SettingsSection='general'|'budget'|'sync'|'advanced'|'diagnostics';
interface SettingsOptions {
 section:SettingsSection;theme:'system'|'light'|'dark';motion:'system'|'reduce';
 config:{projectRoot:string;pythonExecutable:string}|null;version:string;diagnostics:string;
 budgetContent?:string;syncContent?:string;
}
export function desktopSettingsHtml(options:SettingsOptions):string {
 const {section,theme,motion,config,version,diagnostics,budgetContent='',syncContent=''}=options;
 const hidden=(id:SettingsSection)=>section===id?'':' hidden';
 return `<section class="desktop-settings" aria-label="设置">
  <aside class="settings-sidebar"><nav aria-label="设置分类">${([['general','一般'],['budget','四池预算'],['sync','手机与同步'],['advanced','高级'],['diagnostics','开发与诊断']] as const).map(([id,label])=>`<button type="button" data-settings-tab="${id}"${section===id?' aria-current="page"':''}>${label}</button>`).join('')}</nav><span class="settings-version">${version?`v${escapeHtml(version)}`:''}</span></aside>
  <div class="settings-workspace">
   <section data-settings-panel="general"${hidden('general')} aria-labelledby="settings-general-title"><h2 id="settings-general-title">一般</h2>
    <div class="desktop-setting-row"><label id="appearance-label">外观</label><div id="theme-preference" class="settings-segments" role="group" aria-labelledby="appearance-label">${([['system','跟随系统'],['light','浅色'],['dark','深色']] as const).map(([value,label])=>`<button type="button" data-theme-choice="${value}" aria-pressed="${theme===value}">${label}</button>`).join('')}</div></div>
    <div class="desktop-setting-row"><label for="motion-preference">动态效果</label><select id="motion-preference"><option value="system"${motion==='system'?' selected':''}>跟随系统</option><option value="reduce"${motion==='reduce'?' selected':''}>减少动态效果</option></select></div>
   </section>
   <section data-settings-panel="budget"${hidden('budget')} aria-labelledby="settings-budget-title"><h2 id="settings-budget-title">四池预算</h2>${budgetContent}</section>
   <section data-settings-panel="sync"${hidden('sync')} aria-labelledby="settings-sync-title"><h2 id="settings-sync-title">手机与同步</h2>${syncContent}</section>
   <section data-settings-panel="advanced"${hidden('advanced')} aria-labelledby="settings-advanced-title"><h2 id="settings-advanced-title">高级</h2><form id="desktop-config-form">
    <div class="desktop-path-row"><label for="project-root">账本项目目录</label><input id="project-root" value="${escapeHtml(config?.projectRoot??'')}" autocomplete="off" spellcheck="false"></div>
    <div class="desktop-path-row"><label for="python-path">Python 程序</label><input id="python-path" value="${escapeHtml(config?.pythonExecutable??'')}" autocomplete="off" spellcheck="false"></div>
    <div class="settings-save-row"><button type="submit" class="settings-save">保存本机配置</button><p id="desktop-config-status" role="status" aria-live="polite"></p></div>
   </form></section>
   <section data-settings-panel="diagnostics"${hidden('diagnostics')} aria-labelledby="settings-diagnostics-title"><h2 id="settings-diagnostics-title">开发与诊断</h2>${diagnostics}</section>
  </div></section>`;
}

/** The slots contain trusted application markup; credentials retain their native form IDs. */
export function desktopSyncHtml(statusHtml:string,credentialsHtml:string,pairingHtml:string,busy=false,nativeAllowed=true):string {
 return `<div class="desktop-sync-settings">
  <section class="desktop-settings-group" aria-labelledby="desktop-sync-status-title"><h3 id="desktop-sync-status-title">账本状态</h3>${statusHtml}<div class="settings-save-row"><button class="settings-save" id="fetch-button" type="button"${busy||!nativeAllowed?' disabled':''}>检查云端更新</button></div></section>
  <section class="desktop-settings-group desktop-sync-connection" aria-label="同步连接">${credentialsHtml}</section>
  <section class="desktop-settings-group desktop-sync-pairing" aria-labelledby="desktop-pairing-title"><h3 id="desktop-pairing-title">设备配对</h3>${pairingHtml}<p id="pair-status" class="hint" role="status" aria-live="polite"></p></section>
 </div>`;
}
