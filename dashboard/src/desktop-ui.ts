import {escapeHtml} from './helpers';
import {uiIcon} from './ui-icons';

export function desktopStartupHtml(loading:boolean,failed:boolean,message:string,syncing=false):string {
 if(loading||syncing)return `<section class="desktop-startup" role="status" aria-live="polite"><span class="first-sync-art desktop-startup-ring" aria-hidden="true">◌</span><p>${syncing?'正在同步账单':'正在打开生活账本'}</p></section>`;
 return `<section class="desktop-startup desktop-startup-result"><h1>${failed?'暂时无法打开账本':'暂无本机快照'}</h1><p role="status">${escapeHtml(message||(failed?'本机快照读取失败，请重试或检查设置。':'同步账单后即可查看。'))}</p><div class="desktop-startup-actions">${failed?'<button id="retry-local" type="button">重试</button>':'<button id="first-sync-button" type="button">同步展示</button>'}<button id="startup-settings" type="button">打开设置</button></div></section>`;
}

/** Retain the existing select as controller state; only Windows gets the visible listbox. */
export function mountDesktopMonthPicker(select:HTMLSelectElement){
 const root=select.closest<HTMLElement>('.month-select')!;
 select.hidden=true;select.tabIndex=-1;
 for(const el of root.querySelectorAll(':scope > .ui-icon, :scope > span:last-child'))el.remove();
 const trigger=document.createElement('button');trigger.type='button';trigger.id='desktop-month-trigger';trigger.className='desktop-month-trigger';
 trigger.setAttribute('aria-haspopup','listbox');trigger.setAttribute('aria-controls','desktop-month-list');trigger.setAttribute('aria-expanded','false');
 const list=document.createElement('div');list.id='desktop-month-list';list.className='desktop-month-list';list.role='listbox';list.setAttribute('aria-label','查看月份');list.hidden=true;
 root.append(trigger,list);let signature='';
 const buttons=()=>Array.from(list.querySelectorAll<HTMLButtonElement>('[role=option]'));
 const close=(restore=false)=>{list.hidden=true;trigger.setAttribute('aria-expanded','false');if(restore)trigger.focus({preventScroll:true});};
 const update=()=>{
  const options=Array.from(select.options);const selected=options.find(o=>o.value===select.value);
  trigger.disabled=select.disabled;trigger.setAttribute('aria-label',`查看月份${selected?'：'+selected.textContent:''}`);
  trigger.innerHTML=`${uiIcon('calendar')}<span>${escapeHtml(selected?.textContent?.replace(/ · (本月|上月)$/,'')||'选择月份')}</span><span class="desktop-month-chevron">${uiIcon('down')}</span>`;
  const next=options.map(o=>o.value+'|'+o.textContent).join(';')+'|'+select.value;
  if(signature===next)return;signature=next;close(!list.hidden&&root.contains(document.activeElement));
  list.innerHTML=options.map(o=>{const [date,note]=o.textContent?.split(' · ')??['',''];return `<button type="button" role="option" tabindex="-1" data-value="${escapeHtml(o.value)}" aria-selected="${o.value===select.value}"><span>${escapeHtml(date)}</span><span class="desktop-month-note">${escapeHtml(note||'')}</span><span class="desktop-month-check">${o.value===select.value?uiIcon('check'):''}</span></button>`;}).join('');
 };
 const open=()=>{if(trigger.disabled)return;list.hidden=false;trigger.setAttribute('aria-expanded','true');(buttons().find(b=>b.getAttribute('aria-selected')==='true')??buttons()[0])?.focus({preventScroll:true});};
 trigger.addEventListener('click',()=>list.hidden?open():close(true));
 trigger.addEventListener('keydown',e=>{if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();open();}});
 list.addEventListener('click',e=>{const option=(e.target as Element).closest<HTMLButtonElement>('[role=option]');if(!option)return;select.value=option.dataset.value!;close(true);select.dispatchEvent(new Event('change',{bubbles:true}));});
 list.addEventListener('keydown',e=>{
  const options=buttons(),index=options.indexOf(document.activeElement as HTMLButtonElement);
  if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close(true);}
  else if(e.key==='Tab')close();
  else if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?options.length-1:(index+(e.key==='ArrowDown'?1:-1)+options.length)%options.length;options[next]?.focus();}
 });
 const outside=(e:PointerEvent)=>{if(!root.contains(e.target as Node))close();};
 // activeElement can briefly be body while focus moves to another option.
 // Keep that option visible through pointerup/click so selection can complete.
 const focusout=(e:FocusEvent)=>{if(!root.contains(e.relatedTarget as Node|null))close();};
 document.addEventListener('pointerdown',outside);root.addEventListener('focusout',focusout);
 update();return{update,destroy:()=>{document.removeEventListener('pointerdown',outside);root.removeEventListener('focusout',focusout);trigger.remove();list.remove();select.hidden=false;select.removeAttribute('tabindex');}};
}
