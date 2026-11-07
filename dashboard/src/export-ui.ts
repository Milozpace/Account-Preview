import {escapeHtml as e, dateTitle, monthTitle} from './helpers';
import type {DashboardSnapshot} from './types';
import './export-ui.css';
import {uiIcon} from './ui-icons';

export type ExportSnapshot=Pick<DashboardSnapshot,'months'|'generatedAt'|'sourceLastRecordedAt'>;
export interface ExportDocument {fileName:string;content:string}
export type SaveExport=(document:ExportDocument)=>Promise<{saved:boolean}>;

export class ExportSession {
  busy=false;
  message='';
  tone:''|'success'|'error'='';
  clear():void {this.message='';this.tone='';}
  async save(action:()=>Promise<{saved:boolean}>):Promise<void>{
    if(this.busy)return;
    this.busy=true;this.clear();
    try{if((await action()).saved){this.message='文件已保存';this.tone='success';}}
    catch{this.message='保存失败，请重试。';this.tone='error';}
    finally{this.busy=false;}
  }
}

export function exportFormHtml(snapshot:ExportSnapshot|null,wanted:string,showIncome=true):string {
  const month=snapshot?.months.find(item=>item.month===wanted)??snapshot?.months[0];
  const noData=!month;
  return `<div class="analysis-export-form"><div class="export-intro"><span class="export-document-mark" aria-hidden="true">${uiIcon('document')}</span>${noData?'<h2>尚无可导出的账单</h2>':''}<p>${noData?'同步账单后即可导出。':'保存当前缓存中的月度汇总与明细。'}</p></div>
    <label class="export-month-label" for="export-month">导出月份</label><select id="export-month" ${noData?'disabled':''}>${snapshot?.months.map(item=>`<option value="${e(item.month)}" ${item.month===month?.month?'selected':''}>${e(monthTitle(item.month))}</option>`).join('')??''}</select>
    <div class="export-contents"><span>消费概览</span><span>分类汇总</span><span>四个资金池</span><span>消费明细</span></div>
    <p class="export-record-count">${month?`${month.records.length} 笔个人消费 · ${month.structuralRecords.length} 笔结构性支出${showIncome ? ` · ${month.incomeRecords.length} 笔收入` : ''}`:''}</p>
    <div class="export-file-meta"><span>Markdown · .md</span><span>${snapshot?`数据更新 ${e(dateTitle(snapshot.generatedAt,true))}`:''}</span></div>
    <button type="button" id="save-analysis-export" class="export-save-button" ${noData?'disabled':''}>保存文件</button><p id="export-feedback" class="export-feedback" role="status" aria-live="polite"></p></div>`;
}

/** Browser-only saving for the local web view and explicitly synthetic UI previews. */
export async function downloadExport(document:ExportDocument):Promise<{saved:boolean}>{
  const blob=new Blob([document.content],{type:'text/markdown;charset=utf-8'});
  const url=URL.createObjectURL(blob),link=window.document.createElement('a');
  link.href=url;link.download=document.fileName;link.hidden=true;window.document.body.append(link);
  try{link.click();}finally{link.remove();setTimeout(()=>URL.revokeObjectURL(url),30_000);}
  // Browser download initiation cannot confirm a filesystem write.
  return {saved:false};
}

export function updateExportFeedback(root:ParentNode,session:ExportSession):void {
  const button=root.querySelector<HTMLButtonElement>('#save-analysis-export');
  if(button){button.disabled=session.busy||!root.querySelector<HTMLSelectElement>('#export-month')?.value;button.textContent=session.busy?'正在保存…':'保存文件';}
  const select=root.querySelector<HTMLSelectElement>('#export-month');if(select)select.disabled=session.busy||!select.options.length;
  const feedback=root.querySelector<HTMLElement>('#export-feedback');if(feedback){feedback.textContent=session.message;feedback.dataset.tone=session.tone;}
}
