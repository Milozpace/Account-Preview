import {escapeHtml as e} from '../../dashboard/src/helpers';

export interface ReleaseNotes {
  version:string;
  notes:string;
}

function userNotes(value:string):string {
  const notes=value.replace(/\r\n?/g,'\n').trim();
  // Legacy Android catalogs return the full Release body.
  const section=/^##[ \t]+本次更新[ \t]*#*[ \t]*$/m.exec(notes);
  const content=section?notes.slice(section.index+section[0].length):notes;
  return content.split(section?/^#{1,2}[ \t]+/m:/^#{1,2}[ \t]+(?:升级说明|给\s*Agent\s*的更新指引|流程变更)[ \t]*#*[ \t]*$/mi,1)[0].trim();
}

function inlineNotes(value:string):string {
  return e(value).replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>').replace(/`([^`\n]+)`/g,'<code>$1</code>');
}

function notesHtml(notes:string):string {
  let html='';
  let list=false;
  for(const line of notes.split('\n').map(value=>value.trim())) {
    const item=/^[-*+]\s+(.+)|^\d+[.)]\s+(.+)/.exec(line);
    if(item) {
      if(!list)html+='<ul>';
      html+=`<li>${inlineNotes(item[1]??item[2])}</li>`;
      list=true;
      continue;
    }
    if(list)html+='</ul>';
    list=false;
    if(!line)continue;
    const heading=/^#{1,6}\s+(.+)/.exec(line);
    html+=heading?`<h3>${inlineNotes(heading[1])}</h3>`:`<p>${inlineNotes(line)}</p>`;
  }
  return list?html+'</ul>':html;
}

export function releaseCardHtml(release:ReleaseNotes,mode:'installed'|'available'):string {
  const installed=mode==='installed';
  const notes=userNotes(release.notes)||'暂无更新说明。';
  const content=`<div class="release-notes">${notesHtml(notes)}</div>`;
  const collapsible=!installed&&(notes.length>400||notes.split('\n').filter(Boolean).length>8);
  return `<article class="release-card">
    <header class="release-card-header">
      <div><p class="release-version">版本 ${e(release.version)}</p><h2 id="release-card-title">${installed?'已更新到新版':'这版有什么新变化'}</h2></div>
      <div class="release-illustration" aria-hidden="true"><span><i></i><i></i><i></i><i></i></span></div>
    </header>
    <div class="release-card-body">${collapsible?`<details class="update-notes-details"><summary>查看更新说明</summary>${content}</details>`:content}</div>
  </article>`;
}

function showDesktopRelease(dialog:HTMLDialogElement,release:ReleaseNotes):void {
  if(dialog.open)return;
  dialog.innerHTML=`${releaseCardHtml(release,'installed')}<form method="dialog" class="release-card-actions"><button type="submit" autofocus>继续使用</button></form>`;
  dialog.showModal();
}

export async function mountDesktopRelease(settings:boolean,load:()=>Promise<ReleaseNotes|null>):Promise<void> {
  const dialog=document.querySelector<HTMLDialogElement>('#desktop-release');
  if(!dialog?.showModal)return;
  const release=await load().catch(()=>null);
  if(!release?.notes)return;
  if(settings) {
    const main=document.querySelector('#main');
    if(!main)return;
    document.documentElement.setAttribute('data-release-notes','');
    main.addEventListener('click',event=>{
      if(event.target instanceof Element&&event.target.closest('[data-open-release]'))showDesktopRelease(dialog,release);
    });
    return;
  }
  const key=`account.release-seen.${release.version}`;
  try {
    if(localStorage.getItem(key))return;
    showDesktopRelease(dialog,release);
    localStorage.setItem(key,'1');
  }catch { /* An unavailable WebView store must never interrupt app startup. */ }
}
