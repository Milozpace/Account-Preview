import {escapeHtml as e} from '../../dashboard/src/helpers';
import {uiIcon} from '../../dashboard/src/ui-icons';
import {setupPhase} from './mobile-setup';
import {syncIssue,syncIssueHtml,type SyncIssue} from './sync-issue';

export interface MobileSyncState {
  initialized:boolean|null;configured:boolean;connectionKnown:boolean;hasData:boolean;
  source:string;revision:number|null;checking:boolean;warning:string;
  editingConnection:boolean;editingPairing:boolean;
  setupError?:string;
  issue?:SyncIssue|null;showUpdateHelp?:boolean;
}

/** Show one missing step; preserve existing identity and connection on return. */
export function mobileSyncHtml(state:MobileSyncState,credentials:string,pairing:string):string {
  const phase=setupPhase(state.initialized,state.configured,state.connectionKnown);
  const ready=phase==='ready',pair=phase==='pair';
  const issue=state.issue||(state.setupError||state.warning?syncIssue(state.setupError||state.warning):null);
  const feedback=issue?syncIssueHtml(issue,state.hasData,state.showUpdateHelp):'';
  const title=phase==='checking'?'正在确认连接':pair?'连接电脑账本':phase==='connect'?'设置同步连接':state.checking?'正在获取账单':state.source==='cloud'&&!issue?'已同步':state.hasData?'本地数据可用':'等待首次同步';
  const subtitle=phase==='checking'?'设备状态尚未确认，请重试读取。':pair?'在电脑打开“设置 → 手机与同步”，显示配对二维码。':phase==='connect'?'已完成设备配对，接下来连接坚果云。':state.revision!=null?`第 ${state.revision} 版${state.source==='cache'?' · 可离线查看':''}`:'电脑同步账单后，在这里获取。';
  const steps=!ready?`<ol class="setup-progress" aria-label="连接步骤">${['扫码配对','同步连接','获取账单'].map((name,index)=>`<li${(pair&&index===0)||(phase==='connect'&&index===1)?' aria-current="step"':''}><span aria-hidden="true">${index+1}</span>${name}</li>`).join('')}</ol>`:'';
  const showConnection=phase==='connect'||(ready&&state.editingConnection);
  const showPairing=pair||(ready&&state.editingPairing);
  const row=(action:string,label:string,status:string,expanded:boolean)=>`<button type="button" class="menu-row sync-management-row" data-action="${action}" aria-expanded="${expanded}"><span>${label}</span><span class="menu-value">${status}</span><span class="row-chevron" aria-hidden="true">${uiIcon(expanded?'up':'right')}</span></button>`;
  return `<section class="mobile-sync">${steps}<div class="setup-intro"><div class="setup-emblem" aria-hidden="true">${uiIcon(ready&&state.source==='cloud'&&!issue?'check':'sync')}</div><h2>${title}</h2><p class="sync-subtitle">${e(subtitle)}</p>${ready?`<div class="setup-actions"><button class="setup-primary" id="fetch-button" type="button" ${state.checking?'disabled':''}>${state.checking?'正在获取…':state.hasData?'检查更新':'获取账单'}</button></div>`:phase==='checking'?'<button type="button" class="quiet-button" data-action="retry-setup">重新读取状态</button>':''}</div>
    ${showPairing?`<div class="setup-panel">${pairing}<p id="pair-status" role="status" aria-live="polite"></p></div>`:''}
    ${showConnection?credentials:''}${feedback}
    ${ready?`<div class="menu-group">${row('manage-connection','同步连接','已配置',showConnection)}${row('manage-pairing','设备配对','已配对',showPairing)}</div>`:phase==='connect'?'<p class="setup-complete">设备已配对，无需重新扫码。</p>':''}</section>`;
}
