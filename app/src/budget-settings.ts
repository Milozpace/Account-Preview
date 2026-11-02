import {escapeHtml} from '../../dashboard/src/helpers';
import {uiIcon} from '../../dashboard/src/ui-icons';

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
 editing:boolean;loading:boolean;busy:boolean;preview:BudgetPreview|null;message:string;error:boolean;
}
const pools:[BudgetPool,string][]=[['运行账户','operation'],['随心消费','free'],['旅行/体验','travel'],['成长/项目/耐用品','growth']];

export function budgetSettingsHtml(state:BudgetSettingsState):string {
 const {status,draft,editing,loading,busy,message,error}=state;
 const disabled=busy||loading?' disabled':'';
 const notice=`<p id="budget-status" class="settings-inline-status" role="status" aria-live="polite" data-error="${error}">${escapeHtml(message||(loading?'正在读取预算…':!status?'尚未读取预算':''))}</p>`;
 const reload=`<button type="button" id="budget-reload" class="settings-save"${disabled}>重新读取</button>`;
 if(!status)return `${notice}${reload}`;
 const preview=editing&&state.preview?.sourceHash===status.sourceHash&&state.preview.revision===status.revision&&state.preview.effective===draft.effective?state.preview:null;
 const monthLabel=(month:string)=>/^\d{4}-\d{2}$/.test(month)?`${month.slice(0,4)} 年 ${Number(month.slice(5))} 月`:month;
 const cancel=`<button type="button" id="budget-cancel-edit" class="settings-save"${disabled}>取消</button>`;
 const rate=status.rates.filter(rate=>rate.effective<=draft.effective).at(-1);
 const displayAmounts=editing?draft.amounts:rate?.amounts;
 const displayMonth=rate?.effective??draft.effective;
 const poolGrid=`<div class="budget-pool-grid${editing?' is-editing':''}">${pools.map(([pool,id])=>`<div class="budget-pool-editor" data-budget-color="${id}"><${editing?'label':'div'}${editing?` for="budget-${id}"`:''} class="budget-pool-label"><i aria-hidden="true"></i>${pool}</${editing?'label':'div'}>${editing?`<div class="settings-amount-input"><input id="budget-${id}" data-budget-pool="${pool}" type="text" inputmode="decimal" value="${escapeHtml(displayAmounts?.[pool]??'')}" autocomplete="off" required pattern="[0-9]+([.][0-9]{1,2})?" aria-label="${pool}每月额度"><span aria-hidden="true">元 / 月</span></div>`:`<p class="budget-amount-text"><strong>${escapeHtml(displayAmounts?.[pool]??'0.00')}</strong><span>元 / 月</span></p>`}</div>`).join('')}</div>`;
 return `<div class="desktop-budget-settings" data-budget-mode="${editing?preview?'review':'edit':'locked'}">
  <p class="settings-description">${editing?'调整每月额度，确认后生效。':'查看每月额度，需要调整时再开始修改。'}</p>
  ${!editing?`<div class="budget-view-period">${uiIcon('calendar')}<span>生效月份</span>${status.rates.length>1?`<select id="budget-view-month" aria-label="查看生效月份">${status.rates.map(rate=>`<option value="${escapeHtml(rate.effective)}"${rate.effective===displayMonth?' selected':''}>${escapeHtml(monthLabel(rate.effective))}</option>`).join('')}</select>`:`<strong>${escapeHtml(monthLabel(displayMonth))}</strong>`}</div>${poolGrid}<div class="budget-view-footer"><p>额度沿用至下一次调整</p><button type="button" id="budget-start-edit" class="settings-save settings-primary"${disabled}>修改额度${uiIcon('right')}</button></div>`:preview?`
  <section class="desktop-settings-group budget-preview" aria-labelledby="budget-preview-title"><div class="setting-heading">${uiIcon('check')}<h3 id="budget-preview-title">确认额度调整</h3></div><p class="budget-affected-period">影响月份：${escapeHtml(preview.effective)}${preview.affectedThrough?` 至 ${escapeHtml(preview.affectedThrough)}`:' 起，至下一条生效额度前'}</p>
   <table class="budget-comparison"><thead><tr><th scope="col">资金池</th><th scope="col">原额度</th><th scope="col">新额度</th></tr></thead><tbody>${pools.map(([pool])=>`<tr><th scope="row">${pool}</th><td>${preview.previousAmounts?`${escapeHtml(preview.previousAmounts[pool])} 元`:'此前无生效额度'}</td><td>${escapeHtml(preview.amounts[pool])} 元</td></tr>`).join('')}</tbody></table>
   <p class="settings-inline-status">历史额度调整会重算后续滚存与安全积累，不改变交易记录。</p>
   ${!preview.changed?'<p class="settings-inline-status">额度未变化</p>':''}
   <div class="settings-save-row budget-actions">${preview.changed?`<button class="settings-save settings-primary" id="budget-confirm-save" type="button"${disabled}>${busy?'正在保存…':'确认保存'}</button>`:''}<button type="button" id="budget-revise" class="settings-save"${disabled}>继续修改</button>${cancel}</div></section>`:`
  <form id="budget-form"><fieldset${disabled}><div class="settings-surface budget-period"><div class="desktop-setting-row"><div class="setting-copy"><label for="budget-month">生效月份</label><p>从这个月开始采用新额度</p></div><input id="budget-month" type="month" value="${escapeHtml(draft.effective)}" required></div></div>${poolGrid}<div class="settings-save-row budget-actions"><button class="settings-save settings-primary" type="submit">${busy?'正在核对…':'下一步'}${uiIcon('right')}</button>${cancel}${error?reload:''}</div></fieldset></form>`}
  ${notice}
 </div>`;
}
