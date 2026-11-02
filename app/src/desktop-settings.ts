import {escapeHtml} from '../../dashboard/src/helpers';
import {uiIcon} from '../../dashboard/src/ui-icons';
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
  <aside class="settings-sidebar"><nav aria-label="设置分类">${([['general','一般','settings'],['budget','四池预算','calendar'],['sync','手机与同步','sync'],['advanced','高级','document'],['diagnostics','开发与诊断','info']] as const).map(([id,label,icon])=>`<button type="button" data-settings-tab="${id}"${section===id?' aria-current="page"':''}>${uiIcon(icon)}<span>${label}</span></button>`).join('')}</nav><span class="settings-version">${version?`v${escapeHtml(version)}`:''}</span></aside>
  <div class="settings-workspace">
   <section data-settings-panel="general"${hidden('general')} aria-labelledby="settings-general-title"><h2 id="settings-general-title">一般</h2>
    <div class="settings-surface settings-appearance"><div class="setting-heading">${uiIcon('settings')}<div><h3 id="appearance-label">外观</h3><p>选择账本的显示方式</p></div></div><div id="theme-preference" class="settings-segments" role="group" aria-labelledby="appearance-label">${([['system','跟随系统'],['light','浅色'],['dark','深色']] as const).map(([value,label])=>`<button type="button" data-theme-choice="${value}" aria-pressed="${theme===value}"><span class="theme-thumbnail theme-thumbnail-${value}" aria-hidden="true"><i></i><i></i><i></i></span><span>${label}</span>${uiIcon('check')}</button>`).join('')}</div></div>
    <div class="settings-surface desktop-setting-row"><div class="setting-copy"><label for="motion-preference">动态效果</label><p>跟随系统偏好，或减少界面动画</p></div><select id="motion-preference"><option value="system"${motion==='system'?' selected':''}>跟随系统</option><option value="reduce"${motion==='reduce'?' selected':''}>减少动态效果</option></select></div>
   </section>
   <section data-settings-panel="budget"${hidden('budget')} aria-labelledby="settings-budget-title"><h2 id="settings-budget-title">四池预算</h2>${budgetContent}</section>
   <section data-settings-panel="sync"${hidden('sync')} aria-labelledby="settings-sync-title"><h2 id="settings-sync-title">手机与同步</h2>${syncContent}</section>
   <section data-settings-panel="advanced"${hidden('advanced')} aria-labelledby="settings-advanced-title"><h2 id="settings-advanced-title">高级</h2><form id="desktop-config-form" class="settings-surface"><div class="setting-heading">${uiIcon('document')}<div><h3>本机账本位置</h3><p>用于读取这台电脑上的账本</p></div></div>
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
  <section class="desktop-settings-group" aria-labelledby="desktop-sync-status-title"><div class="setting-heading">${uiIcon('document')}<h3 id="desktop-sync-status-title">账本状态</h3></div>${statusHtml}<div class="settings-save-row"><button class="settings-save" id="fetch-button" type="button"${busy||!nativeAllowed?' disabled':''}>${uiIcon('sync')}${busy?'正在检查…':'检查云端更新'}</button></div></section>
  <section class="desktop-settings-group desktop-sync-connection" aria-label="同步连接">${credentialsHtml}</section>
  <section class="desktop-settings-group desktop-sync-pairing" aria-labelledby="desktop-pairing-title"><div class="setting-heading">${uiIcon('sync')}<div><h3 id="desktop-pairing-title">设备配对</h3><p>让自己的手机查看同一本账本</p></div></div>${pairingHtml}<p id="pair-status" class="hint" role="status" aria-live="polite"></p></section>
 </div>`;
}

export function desktopConnectionHtml(options:{configured:boolean;known:boolean;editing:boolean;busy:boolean;error?:string;readError?:string}):string {
 const {configured,known,editing,busy,error='',readError=''}=options;
 const form=known&&(!configured||editing);
 return `<div class="desktop-connection"><div class="setting-heading">${uiIcon('sync')}<div><h3>同步连接</h3><p>${readError?'连接状态暂时无法确认':known?'坚果云 · 凭据保存在本机系统安全存储':'正在读取本机连接状态…'}</p></div>${known?`<span class="connection-badge${configured&&!readError?' is-configured':''}">${readError?'待确认':configured?`${uiIcon('check')}已配置`:'未配置'}</span>`:''}</div>
 ${readError?`<button type="button" class="settings-save" data-action="retry-connection-status"${busy?' disabled':''}>重新读取连接状态</button>`:''}
 ${known&&configured?`<div class="connection-summary"><p>已保存同步凭据，云端连通性以检查结果为准。</p><button type="button" class="settings-save" data-action="manage-connection" aria-expanded="${editing}"${busy?' disabled':''}>${editing?'取消更换':'更换凭据'}</button></div>`:''}
 ${form?`<form id="sync-form"><label for="account">坚果云账号</label><input id="account" name="account" autocomplete="off" inputmode="email" autocapitalize="none" spellcheck="false" placeholder="账号邮箱" required${busy?' disabled':''}><label for="password">应用密码</label><input id="password" name="password" type="password" autocomplete="new-password" placeholder="此电脑的应用密码" required${busy?' disabled':''}><div class="actions"><button id="save-sync" type="submit"${busy?' disabled':''}>${busy?'正在保存…':'保存同步凭据'}</button></div></form>`:''}
 <p id="sync-status" class="settings-inline-status" role="status" aria-live="polite" data-error="${Boolean(error||readError)}">${escapeHtml(error||readError)}</p></div>`;
}
