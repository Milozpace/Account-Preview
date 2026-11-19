import {escapeHtml as e} from '../../dashboard/src/helpers';
import {uiIcon} from '../../dashboard/src/ui-icons';
import {releaseCardHtml} from './release-notes';

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
const button=(action:UpdateAction,label:string,primary=false)=>`<button type="button" class="${primary?'setup-primary update-primary':'quiet-button'}" data-update-action="${action}">${label}</button>`;
export function aboutPageHtml(status:UpdateStatus|null,version=''):string {
 const identity=`<div class="about-identity"><div class="account-emblem" aria-hidden="true"><i></i><i></i><i></i><i></i></div><h1>生活账本</h1><p class="about-tagline">让数字回到生活里。</p><p class="about-version">版本 ${e(status?.currentVersion||version||'开发预览')}</p></div>`;
 if(!status)return `<section class="about app-update">${identity}<div class="update-status" role="status" aria-live="polite"><p class="update-state">正在读取更新状态…</p></div></section>`;
 if(!status.supported)return `<section class="about app-update">${identity}<p class="update-message">个人版请通过电脑 USB 更新。</p></section>`;
 const phase=status.phase;
 // A manual recheck may retain the previous target in native state until it finishes.
 const target=['available','downloading','ready','error'].includes(phase)?status.target:undefined;
 const busy=phase==='checking'||phase==='downloading';
 const progress=Math.max(0,Math.min(100,Math.round(status.progress||0)));
 const verifying=phase==='downloading'&&Boolean(status.message);
 const installState=phase==='ready'?status.installState:undefined;
 const label=installState==='permission'?'需要安装权限':installState==='opened'?'等待安装完成':{
  idle:'',checking:'正在检查更新…',latest:'已是最新版本',available:`发现新版本${target?' '+target.version:''}`,
  downloading:verifying?status.message:`正在下载 ${progress}%`,ready:'更新已准备好',error:target?'更新未完成':'检查失败',
 }[phase];
 let message=busy?'':status.message;
 if(message.startsWith('无法完成更新检查。')||message.startsWith('更新服务暂不可用'))message='无法连接更新服务，请检查网络后重试。';
 const hint=message||(installState==='permission'?'请开启“允许此来源安装应用”，返回后继续安装。':installState==='opened'?'请在系统界面确认安装；若已取消，可再次打开。':'');
 const icon=phase==='error'||message||installState==='permission'?'warning':phase==='latest'||phase==='ready'?'check':phase==='downloading'?'export':'sync';
 const primary=busy?`<button type="button" class="setup-primary update-primary" disabled>${phase==='checking'?'检查更新':verifying?'正在复核…':'正在下载…'}</button>`:
  phase==='ready'?button('install',installState==='permission'?'继续安装':installState==='opened'?'再次打开安装器':'安装更新',true):
  target?button('download',phase==='error'?'重新下载':'下载更新',true):button('check',phase==='error'?'重试检查':'检查更新',true);
 const secondary=target&&!busy?`<div class="update-secondary">${button('check','重新检查')}${button('ignore','忽略此版本')}</div>`:'';
 return `<section class="about app-update" data-phase="${phase}">${identity}${status.testChannel?'<p class="update-test-channel">临时更新测试通道 · 非正式发布</p>':''}<div class="update-status" role="status" aria-live="polite">${label?`<p class="update-state"><span class="update-symbol" aria-hidden="true">${uiIcon(icon)}</span>${e(label)}</p>`:''}${hint?`<p class="update-message">${e(hint)}</p>`:''}${target?`<p class="update-target">${phase==='available'?'安装包':`新版本 ${e(target.version)} ·`} ${(target.size/1024/1024).toFixed(1)} MB</p>`:''}</div>${phase==='downloading'?`<progress max="100" value="${progress}" aria-label="安装包下载进度">${progress}%</progress>`:''}<div class="setup-actions">${primary}</div>${secondary}${target?releaseCardHtml(target,'available'):''}</section>`;
}
export function updateReminderHtml(status:UpdateStatus|null):string {
 if(!status?.supported||!status.remind||!status.target||!['available','ready'].includes(status.phase))return '';
 return `<aside class="update-reminder" aria-label="程序更新提醒"><strong>新版本 ${e(status.target.version)}${status.phase==='ready'?' 已可安装':''}</strong><div><button type="button" class="quiet-button" data-open="about">查看更新</button>${button('later','稍后提醒')}</div></aside>`;
}
