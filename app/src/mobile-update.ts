import {escapeHtml as e} from '../../dashboard/src/helpers';

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
const button=(action:UpdateAction,label:string)=>`<button type="button" class="quiet-button" data-update-action="${action}">${label}</button>`;
export function updatePageHtml(status:UpdateStatus|null):string {
 if(!status)return '<section class="app-update"><p role="status">正在读取更新状态…</p></section>';
 if(!status.supported)return '<section class="app-update"><h2>程序更新</h2><p>个人版使用本机交付包，请通过 USB 覆盖安装对应身份的 APK。</p><p>个人包不在公开仓库分发。</p></section>';
 const target=status.target;
 const busy=status.phase==='checking'||status.phase==='downloading';
 const progress=Math.max(0,Math.min(100,Math.round(status.progress||0)));
 const label={idle:'检查是否有可用新版本',checking:'正在检查更新…',latest:'当前已是最新可用正式版',available:'发现新版本',downloading:`正在下载 ${progress}%`,ready:'安装包已校验，可以安装',error:'更新未能完成'}[status.phase];
 const install=status.installState==='permission'?'请开启“允许此来源安装应用”，返回后点“继续安装”。':status.installState==='opened'?'已打开系统安装器。若取消了安装，可再次点击；更新结果将在重新打开应用后核对。':'';
 const action=status.phase==='ready'?button('install',status.installState?'继续安装':'安装更新'):target&&!busy?button('download',status.phase==='error'?'重新下载':'下载更新'):'';
 return `<section class="app-update"><h2>程序更新</h2>${status.testChannel?'<p class="update-message">临时更新测试通道 · 非正式发布，测试结束后恢复正式源</p>':''}<p>当前版本 ${e(status.currentVersion||'未知')} <small>· 安装序号 ${status.currentCode}</small></p><p role="status" aria-live="polite">${label}</p>${status.message?`<p class="update-message">${e(status.message)}</p>`:''}${target?`<div class="card-block"><h3>新版本 ${e(target.version)}</h3><p>安装包约 ${(target.size/1024/1024).toFixed(1)} MB</p><pre class="update-notes">${e(target.notes||'本版未提供更新说明。')}</pre></div>`:''}${status.phase==='downloading'?`<progress max="100" value="${progress}" aria-label="安装包下载进度">${progress}%</progress>`:''}${install?`<p>${install}</p>`:''}<div class="actions">${action}${!busy?button('check','检查更新'):''}${target&&!busy?button('ignore','忽略此版本'):''}</div><details class="update-network"><summary>网络要求与更新原理</summary><p>检查和下载需要手机能访问 GitHub 的接口与发布资产，建议使用 Wi-Fi。仅点击下载后才获取安装包，不自动使用流量下载。</p><p>应用读取公开版本信息，下载组件包并校验哈希、应用身份和签名，再调起 Android 系统安装器；安装由你确认，沿用原应用数据。</p><p>如果当前网络无法访问 GitHub，可换可用网络，或使用电脑下载后通过 USB 覆盖安装。账本同步仍通过坚果云，与程序更新互相独立。</p></details></section>`;
}
export function updateReminderHtml(status:UpdateStatus|null):string {
 if(!status?.supported||!status.remind||!status.target||!['available','ready'].includes(status.phase))return '';
 return `<aside class="update-reminder" aria-label="程序更新提醒"><strong>新版本 ${e(status.target.version)}${status.phase==='ready'?' 已可安装':''}</strong><div><button type="button" class="quiet-button" data-open="update">查看更新</button>${button('later','稍后提醒')}</div></aside>`;
}
