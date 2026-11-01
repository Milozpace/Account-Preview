import {escapeHtml} from '../../dashboard/src/helpers';

export type BudgetPool='运行账户'|'随心消费'|'旅行/体验'|'成长/项目/耐用品';
export type BudgetAmounts=Record<BudgetPool,string>;
export interface BudgetStatus {
 sourceHash:string;revision:number;rates:{effective:string;amounts:BudgetAmounts}[];suggestedMonth:string;
}
export interface BudgetPreview {
 effective:string;amounts:BudgetAmounts;previousAmounts:BudgetAmounts|null;affectedThrough:string|null;
 changed:boolean;sourceHash:string;revision:number;
}
export interface BudgetSettingsState {
 status:BudgetStatus|null;draft:{effective:string;amounts:Record<string,string>;reason:string};
 loading:boolean;busy:boolean;preview:BudgetPreview|null;message:string;error:boolean;
}
const pools:[BudgetPool,string][]=[['运行账户','operation'],['随心消费','free'],['旅行/体验','travel'],['成长/项目/耐用品','growth']];

export function budgetSettingsHtml(state:BudgetSettingsState):string {
 const {status,draft,loading,busy,message,error}=state;
 const disabled=busy||loading?' disabled':'';
 const notice=`<p id="budget-status" class="settings-inline-status" role="status" aria-live="polite" data-error="${error}">${escapeHtml(message||(loading?'正在读取预算…':!status?'尚未读取预算':''))}</p>`;
 const reload=`<button type="button" id="budget-reload" class="settings-save"${disabled}>重新读取</button>`;
 if(!status)return `${notice}${reload}`;
 const preview=state.preview?.sourceHash===status.sourceHash&&state.preview.revision===status.revision&&state.preview.effective===draft.effective?state.preview:null;
 return `<div class="desktop-budget-settings"><p class="settings-inline-status">新账本预算默认0，不影响分类和分池；需要额度管理时，在这里设置生效月份和每月额度。</p>
  <form id="budget-form"><fieldset${disabled}>
   <div class="desktop-setting-row"><label for="budget-existing-rate">已有生效月</label><select id="budget-existing-rate"><option value=""${status.rates.some(rate=>rate.effective===draft.effective)?'':' selected'}>新增生效月</option>${status.rates.map(rate=>`<option value="${escapeHtml(rate.effective)}"${rate.effective===draft.effective?' selected':''}>${escapeHtml(rate.effective)}</option>`).join('')}</select></div>
   <div class="desktop-setting-row"><label for="budget-month">生效月份</label><input id="budget-month" type="month" value="${escapeHtml(draft.effective)}" required></div>
   ${pools.map(([pool,id])=>`<div class="desktop-setting-row"><label for="budget-${id}">${pool}</label><div class="settings-amount-input"><input id="budget-${id}" data-budget-pool="${pool}" type="text" inputmode="decimal" value="${escapeHtml(draft.amounts[pool]??'')}" autocomplete="off" required pattern="[0-9]+([.][0-9]{1,2})?" aria-label="${pool}每月额度"><span aria-hidden="true">元 / 月</span></div></div>`).join('')}
   <div class="desktop-setting-row"><label for="budget-reason">修改理由</label><input id="budget-reason" type="text" value="${escapeHtml(draft.reason)}" maxlength="500" autocomplete="off" required></div>
   <div class="settings-save-row"><button class="settings-save" type="submit">预览更改</button></div>
  </fieldset></form>
  ${preview?`<section class="desktop-settings-group budget-preview" aria-labelledby="budget-preview-title"><h3 id="budget-preview-title">变更预览</h3><p class="budget-affected-period">影响月份：${escapeHtml(preview.effective)}${preview.affectedThrough?` 至 ${escapeHtml(preview.affectedThrough)}`:' 起，至下一条生效额度前'}</p>
   <table class="budget-comparison"><thead><tr><th scope="col">资金池</th><th scope="col">原额度</th><th scope="col">新额度</th></tr></thead><tbody>${pools.map(([pool])=>`<tr><th scope="row">${pool}</th><td>${preview.previousAmounts?`${escapeHtml(preview.previousAmounts[pool])} 元`:'此前无生效额度'}</td><td>${escapeHtml(preview.amounts[pool])} 元</td></tr>`).join('')}</tbody></table>
   <p class="settings-inline-status">历史额度调整会重算后续滚存与安全积累，不改变交易记录。</p>
   ${preview.changed?`<div class="settings-save-row"><button class="settings-save" id="budget-confirm-save" type="button"${disabled}>${busy?'正在保存…':'确认保存'}</button></div>`:'<p class="settings-inline-status">额度未变化</p>'}</section>`:''}
  ${notice}${reload}
 </div>`;
}
