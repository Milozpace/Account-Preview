import {escapeHtml as e} from '../../dashboard/src/helpers';
import {uiIcon} from '../../dashboard/src/ui-icons';

export interface BillUploadItem {id:string;fileName:string;byteCount:number;status:'pending'|'uploaded'|'received'|'recorded'}
export interface BillUploadBatch {items:BillUploadItem[];failed:number;completed?:number;failures?:{fileName:string;reason:string}[]}
const labels:Record<BillUploadItem['status'],string>={pending:'待重试',uploaded:'待记账',received:'电脑已收取',recorded:'已记账'};
const rank={pending:0,uploaded:1,received:2,recorded:3};

/** Only native summaries enter this session. File bytes and URIs never enter the page. */
export class BillUploadSession {
 items:BillUploadItem[]=[];
 busy=false;
 checking=false;
 message='';
 error=false;
 failures:{fileName:string;reason:string}[]=[];
 clearFailures():void {this.failures=[];}
 get activeItems():BillUploadItem[]{return this.items.filter(item=>item.status!=='recorded');}
 private merge(entries:BillUploadItem[]):void {
  const map=new Map(this.items.map(item=>[item.id,item]));
  for(const item of entries){const old=map.get(item.id);map.set(item.id,old&&rank[old.status]>rank[item.status]?old:item);}
  this.items=[...map.values()].slice(-128);
 }
 private async run(action:()=>Promise<void>,failure:string,checking=false):Promise<void>{
  if(this.busy)return;
  this.busy=true;this.checking=checking;this.message='';this.error=false;
  try{await action();}catch{this.error=true;this.message=failure;}finally{this.busy=false;this.checking=false;}
 }
 async choose(action:()=>Promise<BillUploadBatch|BillUploadItem|null>):Promise<void>{
  await this.run(async()=>{
   const result=await action();if(!result){this.message='已取消选择';return;}
   if ('items' in result) {
    this.merge(result.items);this.failures.push(...(result.failures??[]));this.error=result.failed>0;
    this.message=result.failed?`已处理 ${result.completed??result.items.length} 份，${result.failed} 份未完成；可重试待处理文件，未列出的文件请重新选择。`:`已处理 ${result.completed??result.items.length} 份账单`;
   } else {
    this.merge([result]);const status=this.items.find(item=>item.id===result.id)!.status;
    this.message=status==='recorded'?'这份文件已记账':status==='received'?'电脑已收取这份文件':'已上传，等待电脑记账';
   }
  },'上传未确认。请检查网络、配对与文件格式后重试。');
 }
 async load(action:()=>Promise<BillUploadItem[]>,refresh=false,recover?:()=>Promise<BillUploadItem[]>):Promise<void>{
  await this.run(async()=>{
   const before=this.activeItems.length;
   try{this.merge(await action());}
   catch(error){
    // Native refresh saves each authenticated receipt before reading the next.
    // Recover that confirmed progress without turning a partial failure into success.
    if(recover)try{this.merge(await recover());}catch{ /* Keep the last verified state. */ }
    throw error;
   }
   if(refresh&&before>0&&!this.activeItems.length)this.message='账单已记账';
  },refresh?'暂时无法更新状态，待处理账单已保留。':'暂时无法读取上传记录，请检查配对与同步配置。',true);
 }
 async retry(action:()=>Promise<BillUploadItem>):Promise<void>{
  await this.run(async()=>{const entry=await action();this.merge([entry]);this.message=entry.status==='recorded'?'这份文件已记账':entry.status==='pending'?'上传未确认，文件仍待重试':this.failures.length?'上传已确认；下方保留上次选择的失败记录。':'上传已确认';},'重试未确认，文件仍保留在手机。请稍后再试。');
 }
}

export function billUploadHtml(session:BillUploadSession,nativeAndroid:boolean):string {
 const failures=session.failures.map(item=>`<li><strong>${e(item.fileName)}</strong> · ${e(item.reason)}</li>`).join('');
 const disabled=session.busy||!nativeAndroid?' disabled':'';
 const items=[...session.activeItems].reverse().map(item=>`<li class="bill-upload-item"><span class="bill-upload-file-icon" aria-hidden="true">${uiIcon('document')}</span><div><strong>${e(item.fileName)}</strong><small>${labels[item.status]}</small></div>${item.status==='pending'?`<button type="button" data-upload-retry="${e(item.id)}"${disabled}>重试</button>`:''}</li>`).join('');
 return `<section class="bill-upload-page"><button type="button" id="bill-upload-choose" class="bill-upload-dropzone" data-checking="${session.checking}"${disabled} aria-describedby="bill-upload-formats"><span class="bill-upload-symbol" aria-hidden="true">${uiIcon('upload')}</span><strong>${session.busy&&!session.checking?'正在处理…':'点击上传账单'}</strong><span id="bill-upload-formats">微信 XLSX / 支付宝 CSV · 最大 4 MiB</span></button><p class="bill-upload-note">上传后，由电脑审核记账</p>${!nativeAndroid?'<p class="hint">请在手机 App 中上传</p>':''}<p id="bill-upload-feedback" role="status" aria-live="polite" class="${session.error?'tone-error':''}"${session.message?'':' hidden'}>${e(session.message)}</p>${failures?`<div><p>上次选择的失败记录（请核对后清除，关闭 App 后不保留）</p><button type="button" data-action="clear-upload-failures">已核对，清除记录</button><ul aria-label="上次选择的失败记录">${failures}</ul></div>`:''}${items?`<div class="bill-upload-heading"><h2>待处理</h2><button type="button" id="bill-upload-check" class="bill-upload-refresh" aria-label="检查处理结果" title="检查处理结果" aria-busy="${session.checking}"${disabled}>${uiIcon('sync')}</button></div><ul class="bill-upload-list" aria-label="待处理账单">${items}</ul>`:'<p class="bill-upload-empty">暂无待处理账单</p>'}</section>`;
}
