import {escapeHtml as e} from '../../dashboard/src/helpers';
import {uiIcon} from '../../dashboard/src/ui-icons';

export interface MobileSyncState {
  initialized:boolean|null;configured:boolean;connectionKnown:boolean;hasData:boolean;
  source:string;revision:number|null;checking:boolean;warning:string;
  editingConnection:boolean;editingPairing:boolean;
}

/** Phone connection details appear only for a missing step or explicit management. */
export function mobileSyncHtml(state:MobileSyncState,credentials:string,pairing:string):string {
  const title=state.checking?'正在检查更新':state.source==='cloud'&&!state.warning?'已同步':state.hasData?'本地数据可用':state.initialized&&state.configured?'等待首次同步':'连接账本';
  const subtitle=state.revision!=null?`第 ${state.revision} 版${state.source==='cache'?' · 尚未确认云端更新':''}`:state.initialized===null?'设备状态尚未确认':state.initialized&&state.configured?'电脑发布后即可获取账单':state.initialized===false?'扫描电脑上的配对二维码':'';
  const showConnection=state.editingConnection||(state.connectionKnown&&!state.configured);
  const showPairing=state.editingPairing||state.initialized===false;
  const row=(action:string,label:string,status:string,expanded:boolean)=>`<button type="button" class="menu-row sync-management-row" data-action="${action}" aria-expanded="${expanded}"><span>${label}</span><span class="menu-value">${status}</span><span class="row-chevron" aria-hidden="true">${uiIcon(expanded?'up':'right')}</span></button>`;
  return `<section class="mobile-sync"><div class="sync-summary card-block"><div class="sync-symbol ${state.warning?'has-warning':''}" aria-hidden="true">${uiIcon(state.warning?'warning':state.source==='cloud'?'check':'sync')}</div><h2>${title}</h2>${subtitle?`<p class="sync-subtitle">${e(subtitle)}</p>`:''}${state.warning?`<p class="sync-warning" role="status">${e(state.warning)}</p>`:''}<div class="actions"><button id="fetch-button" type="button" ${state.checking?'disabled':''}>${state.checking?'正在检查…':'检查更新'}</button></div></div>
    <div class="menu-group">${row('manage-connection','同步连接',state.configured?'已连接':state.connectionKnown?'未连接':'连接设置',showConnection)}${row('manage-pairing','设备配对',state.initialized?'已配对':state.initialized===false?'未配对':'未确认',showPairing)}</div>
    ${showConnection?credentials:''}${showPairing?`<div class="card-block"><h2>设备配对</h2>${pairing}<p id="pair-status" role="status" aria-live="polite"></p></div>`:''}</section>`;
}
