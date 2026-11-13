import {describe,it,expect} from 'vitest';
import {syncIssue,issueView,syncIssueHtml} from './sync-issue';

describe('sync diagnostics preserve cause and recovery',()=>{
  it.each([
    [401,'auth-unauthorized'],[403,'access-forbidden'],[409,'cloud-path-conflict'],
    [429,'rate-limited'],[503,'service-unavailable'],
  ])('classifies legacy status %s without matching the contract-stage label', (status,code)=>{
    const issue=syncIssue(`【认证/契约/版本检查】【槽位下载 · GET · ${status}】操作未完成`);
    expect(issue.code).toBe(code);expect(issue.httpStatus).toBe(status);
  });
  it('does not infer an upgrade or password failure from an unknown contract error',()=>{
    const issue=syncIssue('【认证/契约/版本检查】账单快照不是受支持的 App V3 契约，已拒绝。');
    expect(issueView(issue).title).toContain('校验');
    expect(issueView(issue).action).toBeUndefined();
    expect(issueView(syncIssue('【认证/契约/版本检查】操作未完成')).title).not.toContain('密码');
  });
  it('does not turn a combined legacy 401/403 message into a definite password rejection',()=>{
    const issue=syncIssue('云端拒绝访问（401/403），请检查坚果云应用密码是否有效。');
    expect(issue.code).toBe('access-rejected');expect(issue.httpStatus).toBeUndefined();
    expect(issueView(issue).title).not.toContain('密码未通过');
  });
  it('uses a definite HTTP status ahead of a combined legacy description',()=>{
    const issue=syncIssue('【槽位下载 · GET · 403】云端拒绝访问（401/403），请检查坚果云应用密码是否有效。');
    expect(issue.code).toBe('access-forbidden');expect(issue.httpStatus).toBe(403);
  });
  it('treats a missing service root during credential validation as a connection failure',()=>{
    const view=issueView({code:'cloud-path-missing',stage:'credential-check',httpStatus:404,message:'missing service root',retryable:false});
    expect(view.severity).toBe('error');expect(view.action).toBe('connection');expect(view.next).not.toContain('首次发布');
  });
  it('distinguishes permissions from credentials and storage from connectivity',()=>{
    const forbidden=issueView({code:'access-forbidden',stage:'download',message:'403',retryable:false});
    expect(forbidden.next).toContain('不能认定密码错误');
    expect(issueView({code:'local-state-unavailable',stage:'local-cache',message:'storage',retryable:false}).title).toContain('本机');
  });
  it('keeps unknown native failures actionable and escapes all native fields',()=>{
    const issue=syncIssue({code:'<b>new-error</b>',stage:'<img>',message:'<script>bad</script>',retryable:true});
    const html=syncIssueHtml(issue,false);
    expect(html).toContain('问题详情');expect(html).not.toContain('<script>');expect(html).not.toContain('<img>');
    expect(html).not.toContain('已有账单');
  });
});
