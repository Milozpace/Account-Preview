import type {BudgetSettingsState,BudgetStatus,BudgetPreview,BudgetAmounts} from './budget-settings';
type Transport=(command:string,args?:any)=>Promise<any>;
const pools=['运行账户','随心消费','旅行/体验','成长/项目/耐用品'] as const;
function amount(value:string):string {
 const raw=value.trim();if(!/^(0|[1-9]\d*)(\.\d{1,2})?$/.test(raw))throw '金额须为非负数，最多两位小数。';
 const [whole,part='']=raw.split('.');return `${whole}.${part.padEnd(2,'0')}`;
}
export class BudgetSession implements BudgetSettingsState {
 status:BudgetStatus|null=null;preview:BudgetPreview|null=null;loading=false;busy=false;editing=false;message='';error=false;
 draft={effective:'',amounts:{} as Record<string,string>,reason:'用户在电脑设置中确认调整四池额度'};
 private viewingMonth='';
 private generation=0;private approvedRequest:any=null;
 constructor(private transport:Transport){}
 async load(preserveDraft=false):Promise<void> {
  if(this.loading||this.busy)return;this.loading=true;this.error=false;this.generation++;
  try {
   this.status=await this.transport('desktop_budget_status');
   if(!preserveDraft||!this.editing||!this.draft.effective)this.selectMonth(this.status!.suggestedMonth);
   this.preview=null;this.approvedRequest=null;
   this.message=preserveDraft&&this.editing?'已重新读取，修改内容已保留，请继续。':'';
  }catch(error){this.error=true;this.message=typeof error==='string'?error:'预算读取失败，请核对本地项目工具版本后重试。';}
  finally{this.loading=false;}
 }
 beginEdit():void {
  if(!this.status||this.loading||this.busy||this.editing)return;
  this.viewingMonth=this.draft.effective;this.editing=true;this.invalidate();
 }
 cancelEdit():void {
  if(this.busy)return;
  this.editing=false;this.selectMonth(this.viewingMonth||this.draft.effective);
 }
 revise():void {if(this.editing&&!this.busy)this.invalidate();}
 selectMonth(month:string):void {
  this.draft.effective=month;
  const rate=this.status?.rates.filter(rate=>rate.effective<=month).at(-1);
  this.draft.amounts={...(rate?.amounts??Object.fromEntries(pools.map(pool=>[pool,'0.00'])))};
  this.invalidate();
 }
 change(field:string,value:string):void {
  if(!this.editing||this.loading)return;
  if(field==='effective'||field==='reason')this.draft[field]=value;
  else this.draft.amounts[field]=value;
  this.invalidate();
 }
 private invalidate():void{this.generation++;this.preview=null;this.approvedRequest=null;this.message='';this.error=false;}
 private request():any {
  if(!this.status)throw '请先读取本地预算。';
  if(!/^\d{4}-(0[1-9]|1[0-2])$/.test(this.draft.effective))throw '请选择有效的生效月份。';
  if(!this.draft.reason.trim())throw '请填写修改理由。';
  return {effective:this.draft.effective,amounts:Object.fromEntries(pools.map(pool=>[pool,amount(this.draft.amounts[pool]??'')])) as BudgetAmounts,expectedSourceHash:this.status.sourceHash,reason:this.draft.reason.trim()};
 }
 async preflight():Promise<void> {
  if(!this.editing||this.loading||this.busy)return;this.busy=true;this.preview=null;this.approvedRequest=null;this.error=false;
  const generation=this.generation;
  try {const request=this.request();const preview=await this.transport('desktop_budget_preview',{request});
   if(generation===this.generation){this.preview=preview;this.approvedRequest=request;this.message=preview.changed?'请核对额度与影响月份，再确认保存。':'额度未变化。';}
  }catch(error){this.error=true;this.message=typeof error==='string'?error:'预算预览未完成，请重新读取后重试。';}
  finally{this.busy=false;}
 }
 async save():Promise<boolean> {
  if(!this.editing||this.loading||this.busy||!this.preview?.changed||!this.approvedRequest)return false;
  this.busy=true;this.error=false;
  try {
   const request=this.request();if(JSON.stringify(request)!==JSON.stringify(this.approvedRequest))throw '内容已变化，请重新预览。';
   const result=await this.transport('desktop_budget_save',{request});
   if(result.applied!==true)throw '预算保存未确认，请重新读取后核对。';
   // A committed ledger is distinct from the subsequent local snapshot result.
   this.preview=null;this.approvedRequest=null;
   this.status={...this.status!,sourceHash:result.sourceHash,revision:result.revision};
   this.status.rates=this.status.rates.filter(rate=>rate.effective!==result.effective).concat({effective:result.effective,amounts:result.amounts}).sort((a,b)=>a.effective.localeCompare(b.effective));
   this.editing=false;this.draft.effective=result.effective;this.draft.amounts={...result.amounts};
   this.message=result.snapshotUpdated===false?'额度已保存；本地展示未更新，请稍后同步展示。':'额度已保存，本地展示已更新。';
   return true;
  }catch(error){this.preview=null;this.approvedRequest=null;this.error=true;this.message=typeof error==='string'?error:'保存未确认，请重新读取预算后核对。';return false;}
  finally{this.busy=false;}
 }
}
