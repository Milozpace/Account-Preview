import {buildAnalysisExport} from './analysis-export';
import {uiIcon} from './ui-icons';
import {exportFormHtml, ExportSession, updateExportFeedback, type ExportSnapshot, type SaveExport} from './export-ui';

/** Export one frozen, validated snapshot. Background updates never mix report versions. */
export function openExportDialog(snapshot:ExportSnapshot,wanted:string,save:SaveExport,showIncome=true):void {
  if(document.querySelector('#analysis-export-dialog'))return;
  const previous=document.activeElement as HTMLElement|null;
  const dialog=document.createElement('dialog');
  dialog.id='analysis-export-dialog';dialog.className='analysis-export-dialog';
  dialog.setAttribute('aria-labelledby','analysis-export-title');
  const session=new ExportSession();
  let month=snapshot.months.some(item=>item.month===wanted)?wanted:snapshot.months[0]?.month??'';
  const render=()=>{
    dialog.innerHTML=`<div class="export-dialog-heading"><h2 id="analysis-export-title">导出账单</h2><button class="export-close" type="button" aria-label="关闭导出">${uiIcon('close')}</button></div>${exportFormHtml(snapshot,month,showIncome)}`;
    dialog.querySelector('.export-close')?.addEventListener('click',()=>dialog.close());
    dialog.querySelector<HTMLSelectElement>('#export-month')?.addEventListener('change',event=>{month=(event.target as HTMLSelectElement).value;session.clear();render();dialog.querySelector<HTMLSelectElement>('#export-month')?.focus();});
    dialog.querySelector('#save-analysis-export')?.addEventListener('click',async()=>{
      if(session.busy)return;
      const saving=session.save(()=>save(buildAnalysisExport(snapshot,month,showIncome)));
      updateExportFeedback(dialog,session);
      await saving;
      if(dialog.isConnected)updateExportFeedback(dialog,session);
    });
    updateExportFeedback(dialog,session);
  };
  dialog.addEventListener('close',()=>{dialog.remove();if(previous?.isConnected)previous.focus({preventScroll:true});},{once:true});
  dialog.addEventListener('click',event=>{if(event.target===dialog){const box=dialog.getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)dialog.close();}});
  render();document.body.append(dialog);dialog.showModal();
}
