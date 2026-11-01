import {describe,it,expect,vi} from 'vitest';
import {desktopStartupHtml,mountDesktopMonthPicker} from './desktop-ui';
describe('desktop startup surfaces',()=>{
 it('shows only the ring and one status while reading',()=>{
  const html=desktopStartupHtml(true,false,'optional-cache-error');
  expect(html).toContain('正在打开生活账本');expect(html).toContain('◌');
  for(const unwanted of ['optional-cache-error','同步展示','打开设置','A LITTLE','从这个月'])expect(html).not.toContain(unwanted);
 });
 it('offers read-only retry for an escaped real read failure',()=>{
  const html=desktopStartupHtml(false,true,'路径 <missing>');
  expect(html).toContain('id="retry-local"');expect(html).toContain('打开设置');expect(html).toContain('&lt;missing&gt;');expect(html).not.toContain('id="first-sync-button"');
 });
 it('offers explicit sync only after a confirmed empty read',()=>{
  expect(desktopStartupHtml(false,false,'')).toContain('id="first-sync-button"');
 });
 it('keeps sync progress and failure visible on the first-run surface',()=>{
  expect(desktopStartupHtml(false,false,'',true)).toContain('正在同步账单');
  const failed=desktopStartupHtml(false,false,'同步失败，请重试');
  expect(failed).toContain('同步失败，请重试');expect(failed).toContain('id="first-sync-button"');
 });
});

it('keeps the list open during internal focus transfer until an old month is selected',async()=>{
 // Native focusout can precede activeElement settling on its relatedTarget.
 class ElementStub {
  hidden=false;disabled=false;tabIndex=0;value='2026-11';textContent='';innerHTML='';
  options=[{value:'2026-11',textContent:'2026 年 11 月'},{value:'2026-10',textContent:'2026 年 10 月'},{value:'2026-09',textContent:'2026 年 9 月'}];
  listeners=new Map<string,(event:any)=>void>();attributes=new Map<string,string>();children:ElementStub[]=[];
  dataset:Record<string,string>={};
  setAttribute(key:string,value:string){this.attributes.set(key,value);}
  getAttribute(key:string){return this.attributes.get(key);}
  removeAttribute(key:string){this.attributes.delete(key);}
  addEventListener(type:string,callback:(event:any)=>void){this.listeners.set(type,callback);}
  removeEventListener(type:string){this.listeners.delete(type);}
  append(...elements:ElementStub[]){this.children.push(...elements);}
  contains(element:ElementStub|null):boolean{return element===this||this.children.some(child=>child.contains(element));}
  querySelectorAll(){return this.children;}
  focus(){}
  remove(){}
  closest(){return root;}
  dispatchEvent(event:Event){this.listeners.get(event.type)?.(event);return true;}
 }
 const root=new ElementStub(), select=new ElementStub(), oldOption=new ElementStub();
 oldOption.dataset.value='2026-09';oldOption.closest=()=>oldOption;
 const documentStub={activeElement:new ElementStub(),createElement:()=>new ElementStub(),addEventListener:vi.fn(),removeEventListener:vi.fn()};
 vi.stubGlobal('document',documentStub);
 try {
  const picker=mountDesktopMonthPicker(select as unknown as HTMLSelectElement);
  const [trigger,list]=root.children;list.children=[oldOption];
  trigger.listeners.get('click')!({});expect(list.hidden).toBe(false);
  root.listeners.get('focusout')!({relatedTarget:oldOption});
  await Promise.resolve();expect(list.hidden).toBe(false);
  const changed=vi.fn();select.addEventListener('change',changed);
  list.listeners.get('click')!({target:oldOption});
  expect(select.value).toBe('2026-09');expect(changed).toHaveBeenCalledOnce();expect(list.hidden).toBe(true);
  trigger.listeners.get('click')!({});root.listeners.get('focusout')!({relatedTarget:null});
  await Promise.resolve();expect(list.hidden).toBe(true);
  picker.destroy();
 } finally {vi.unstubAllGlobals();}
});
