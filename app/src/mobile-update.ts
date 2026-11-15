import {escapeHtml as e} from '../../dashboard/src/helpers';
import {uiIcon} from '../../dashboard/src/ui-icons';

export type UpdateAction='check'|'auto'|'download'|'install'|'later'|'ignore';
export interface UpdateStatus {
 supported:boolean;currentVersion:string;currentCode:number;
 phase:'idle'|'checking'|'latest'|'available'|'downloading'|'ready'|'error';
 message:string;progress:number;remind:boolean;
 testChannel?:boolean;
 installState?:'permission'|'opened';
 target?:{version:string;nativeVersion:string;code:number;notes:string;size:number};
}
type Invoke=<T>(command:string,args?:Record<string,unknown>)=>Promise<T>;
export class MobileUpdateSession {
 status:UpdateStatus|null=null;
 private pending:Promise<void>|null=null;
 private actionPending:{action:UpdateAction;promise:Promise<void>}|null=null;
 constructor(private invoke:Invoke){}
 get active():boolean{return this.status?.phase==='checking'||this.status?.phase==='downloading';}
 refresh():Promise<void>{return this.actionPending?.promise??this.request('mobile_update_status');}
 action(action:UpdateAction):Promise<void>{
  if(this.actionPending?.action===action)return this.actionPending.promise;
  const read=this.actionPending?.promise??this.pending;
  const promise=(async()=>{if(read)await read;await this.request('mobile_update_action',{action});})().finally(()=>{if(this.actionPending?.promise===promise)this.actionPending=null;});
  this.actionPending={action,promise};
  return promise;
 }
 private request(command:string,args?:Record<string,unknown>):Promise<void>{
  if(this.pending)return this.pending;
  this.pending=this.invoke<UpdateStatus>(command,args).then(value=>{this.status=value;}).catch(()=>{
   this.status={supported:true,currentVersion:this.status?.currentVersion??'',currentCode:this.status?.currentCode??0,phase:'error',message:'更新服务暂不可用，请稍后重试；也可以使用 USB 安装。',progress:0,remind:false};
  }).finally(()=>{this.pending=null;});
  return this.pending;
 }
}
const button=(action:UpdateAction,label:string,primary=false)=>`<button type="button" class="${primary?'update-primary':'quiet-button'}" data-update-action="${action}">${label}</button>`;
function updateNotesHtml(value:string):string {
 const notes=value?.trim()||'暂无更新说明';
 const content=`<pre class="update-notes">${e(notes)}</pre>`;
 return notes.length>400||notes.split('\n').length>8?`<details class="update-notes-details"><summary>查看更新说明</summary>${content}</details>`:content;
}
export function updatePageHtml(status:UpdateStatus|null):string {
 if(!status)return `<section class="settings app-update"><div class="card-block"><div class="update-status" role="status" aria-live="polite"><span class="update-symbol">${uiIcon('sync')}</span><h2>正在读取更新状态…</h2></div></div></section>`;
 if(!status.supported)return '<section class="settings app-update"><div class="card-block"><h2>通过电脑更新</h2><p>个人版请使用 USB 安装对应的更新包。</p></div></section>';
 const phase=status.phase;
 // A manual recheck may retain the previous target in native state until it finishes.
 const target=['available','downloading','ready','error'].includes(phase)?status.target:undefined;
 const busy=phase==='checking'||phase==='downloading';
 const progress=Math.max(0,Math.min(100,Math.round(status.progress||0)));
 const verifying=phase==='downloading'&&Boolean(status.message);
 const installState=phase==='ready'?status.installState:undefined;
 const label=installState==='permission'?'需要安装权限':installState==='opened'?'等待安装完成':{
  idle:'检查应用更新',checking:'正在检查更新…',latest:'已是最新版本',available:'发现新版本',
  downloading:verifying?status.message:`正在下载 ${progress}%`,ready:'更新已准备好',error:target?'更新未完成':'检查失败',
 }[phase];
 let message=busy?'':status.message;
 if(message.startsWith('无法完成更新检查。')||message.startsWith('更新服务暂不可用'))message='无法连接更新服务，请检查网络后重试。';
 const hint=message||(installState==='permission'?'请开启“允许此来源安装应用”，返回后继续安装。':installState==='opened'?'请在系统界面确认安装；若已取消，可再次打开。':'');
 const icon=phase==='error'||message||installState==='permission'?'warning':phase==='latest'||phase==='ready'?'check':phase==='downloading'?'export':'sync';
 const primary=busy?`<button type="button" class="update-primary" disabled>${phase==='checking'?'正在检查…':verifying?'正在复核…':'正在下载…'}</button>`:
  phase==='ready'?button('install',installState==='permission'?'继续安装':installState==='opened'?'再次打开安装器':'安装更新',true):
  target?button('download',phase==='error'?'重新下载':'下载更新',true):button('check',phase==='error'?'重试检查':'检查更新',true);
 const secondary=target&&!busy?`<div class="update-secondary">${button('check','重新检查')}${button('ignore','忽略此版本')}</div>`:'';
 return `<section class="settings app-update" data-phase="${phase}">${status.testChannel?'<p class="update-test-channel">临时更新测试通道 · 非正式发布</p>':''}<div class="card-block update-summary"><div class="update-status" role="status" aria-live="polite"><span class="update-symbol" aria-hidden="true">${uiIcon(icon)}</span><div><h2>${e(label)}</h2>${hint?`<p class="update-message">${e(hint)}</p>`:''}</div></div>${phase==='downloading'?`<progress max="100" value="${progress}" aria-label="安装包下载进度">${progress}%</progress>`:''}<dl class="update-version"><dt>当前版本</dt><dd>${e(status.currentVersion||'未知')}</dd></dl></div>${target?`<div class="card-block update-release"><div class="update-release-heading"><h3>新版本 ${e(target.version)}</h3><span>${(target.size/1024/1024).toFixed(1)} MB</span></div>${updateNotesHtml(target.notes)}</div>`:''}<div class="update-controls">${primary}${secondary}</div></section>`;
}
export function updateReminderHtml(status:UpdateStatus|null):string {
 if(!status?.supported||!status.remind||!status.target||!['available','ready'].includes(status.phase))return '';
 return `<aside class="update-reminder" aria-label="程序更新提醒"><strong>新版本 ${e(status.target.version)}${status.phase==='ready'?' 已可安装':''}</strong><div><button type="button" class="quiet-button" data-open="update">查看更新</button>${button('later','稍后提醒')}</div></aside>`;
}
