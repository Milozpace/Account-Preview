import {describe,it,expect} from 'vitest';
import {budgetSettingsHtml,type BudgetSettingsState,type BudgetAmounts} from './budget-settings';

const amounts:BudgetAmounts={'运行账户':'1200.00','随心消费':'300.00','旅行/体验':'400.00','成长/项目/耐用品':'500.00'};
function state(overrides:Partial<BudgetSettingsState>={}):BudgetSettingsState {
 return {status:{sourceHash:'source-a',revision:4,rates:[{effective:'2026-10',amounts}],suggestedMonth:'2026-10'},draft:{effective:'2026-10',amounts:{...amounts},reason:''},editing:true,loading:false,busy:false,preview:null,message:'',error:false,...overrides};
}

describe('desktop budget settings',()=>{
 it('shows plain amounts with a single edit entry while locked, without input boxes or routine controls',()=>{
  const html=budgetSettingsHtml(state({editing:false}));
  expect(html).toContain('修改额度');expect(html).toContain('1200.00');
  expect(html).not.toContain('<input');expect(html).not.toContain('id="budget-form"');
  expect(html).not.toContain('id="budget-reason"');expect(html).not.toContain('id="budget-reload"');
  expect(html.match(/<button\b/g)).toHaveLength(1);
 });
 it('does not mark budget cards as fund-pool navigation targets',()=>{
  for(const editing of [false,true])expect(budgetSettingsHtml(state({editing}))).not.toMatch(/\bdata-pool=/);
 });
 it('does not offer editable controls before a successful budget read',()=>{
  const html=budgetSettingsHtml(state({status:null,loading:true}));
  expect(html).toContain('正在读取预算');expect(html).not.toContain('id="budget-form"');expect(html).not.toContain('id="budget-confirm-save"');
  const failed=budgetSettingsHtml(state({status:null,error:true,message:'读取失败 <detail>'}));
  expect(failed).toContain('role="status"');expect(failed).toContain('读取失败 &lt;detail&gt;');expect(failed).not.toContain('id="budget-form"');
 });
 it('offers historical effective months and exact decimal drafts in labelled desktop rows',()=>{
  const html=budgetSettingsHtml(state({draft:{effective:'2026-10',amounts:{...amounts,'随心消费':'12.50'},reason:'<理由>"'}}));
  expect(html).toContain('id="budget-form"');expect(html).toContain('从这个月开始采用新额度');
  expect(html).toContain('id="budget-month" type="month"');
  expect(html).toContain('id="budget-operation"');expect(html).toContain('id="budget-free"');expect(html).toContain('id="budget-travel"');expect(html).toContain('id="budget-growth"');
  expect(html).toContain('data-budget-pool="随心消费"');expect(html).toContain('value="12.50"');
  expect(html).not.toContain('id="budget-reason"');expect(html).not.toContain('id="budget-confirm-save"');
 });
 it('shows the applicable previous amounts and inclusive affected period before confirmation',()=>{
  const html=budgetSettingsHtml(state({preview:{effective:'2026-10',amounts:{...amounts,'运行账户':'1500.00'},previousAmounts:amounts,affectedThrough:'2026-12',changed:true,sourceHash:'source-a',revision:4}}));
  expect(html).toContain('2026-10 至 2026-12');expect(html).toContain('<th scope="col">原额度</th>');expect(html).toContain('<th scope="col">新额度</th>');
  expect(html).toContain('1200.00 元');expect(html).toContain('1500.00 元');
  expect(html).toContain('id="budget-confirm-save" type="button"');
 });
 it('shows a new starting rate and continuing future effect without inventing a previous amount',()=>{
  const html=budgetSettingsHtml(state({preview:{effective:'2026-10',amounts,previousAmounts:null,affectedThrough:null,changed:true,sourceHash:'source-a',revision:4}}));
  expect(html).toContain('2026-10 起，至下一条生效额度前');expect(html).toContain('此前无生效额度');expect(html).not.toContain('null');
 });
 it('does not confirm unchanged or stale previews and locks controls during saving',()=>{
  const preview={effective:'2026-10',amounts,previousAmounts:amounts,affectedThrough:null,changed:false,sourceHash:'source-a',revision:4};
  expect(budgetSettingsHtml(state({preview}))).toContain('额度未变化');
  expect(budgetSettingsHtml(state({preview}))).not.toContain('id="budget-confirm-save"');
  expect(budgetSettingsHtml(state({preview:{...preview,changed:true,sourceHash:'old-source'}}))).not.toContain('id="budget-confirm-save"');
  const busy=budgetSettingsHtml(state({busy:true,preview:{...preview,changed:true}}));
  expect(busy).not.toContain('id="budget-form"');expect(busy).toContain('id="budget-confirm-save" type="button" disabled');
 });
});
