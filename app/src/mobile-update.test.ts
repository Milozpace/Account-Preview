import {describe,it,expect} from 'vitest';
import {MobileUpdateSession,updatePageHtml,updateReminderHtml,type UpdateStatus} from './mobile-update';

const status=(overrides:Partial<UpdateStatus>={}):UpdateStatus=>({supported:true,currentVersion:'0.4.0',currentCode:4007,phase:'idle',message:'',progress:0,remind:false,...overrides});
const available=status({phase:'available',remind:true,target:{version:'0.4.1',nativeVersion:'0.4.8',code:4008,notes:'修复 <img src=x onerror=alert(1)>',size:8200000}});

describe('手机程序更新',()=>{
 it('单独显示更新说明、大小和主动下载按钮，并转义远端文案',()=>{
  const html=updatePageHtml(available);
  expect(html).toContain('0.4.1');expect(html).toContain('下载更新');expect(html).toContain('7.8 MB');
  expect(html).not.toContain('<img');expect(html).toContain('&lt;img');expect(html).toContain('GitHub');
 });
 it('准备完毕才显示安装，权限往返后可继续安装',()=>{
  expect(updatePageHtml(status({phase:'downloading',progress:42}))).toContain('42%');
  expect(updatePageHtml(status({phase:'downloading'}))).not.toContain('data-update-action="install"');
  const html=updatePageHtml(status({phase:'ready',installState:'permission'}));
  expect(html).toContain('继续安装');expect(html).toContain('允许此来源');
 });
 it('个人版只解释 USB 更新，禁止出现公开包下载动作',()=>{
  const html=updatePageHtml(status({supported:false}));expect(html).toContain('USB');expect(html).not.toContain('data-update-action="download"');
 });
 it('已忽略/稍后不自动提醒，更新失败不覆盖账本状态',()=>{
  expect(updateReminderHtml({...available,remind:false})).toBe('');
  expect(updateReminderHtml(available)).toContain('稍后');
  expect(updateReminderHtml(status({phase:'error',message:'网络失败'}))).toBe('');
 });
 it('只通过固定原生命令获取状态和执行动作，复用进行中的请求',async()=>{
  const calls:Array<[string,unknown]>=[];let finish!:(s:UpdateStatus)=>void;
  const session=new MobileUpdateSession(async<T>(command:string,args?:Record<string,unknown>)=>{calls.push([command,args]);return await new Promise<UpdateStatus>(resolve=>{finish=resolve;}) as T;});
  const first=session.action('check');const second=session.action('check');
  expect(calls).toEqual([['mobile_update_action',{action:'check'}]]);
  finish(available);await Promise.all([first,second]);expect(session.status?.target?.code).toBe(4008);
 });
 it('桥接失败给出可操作提示，不把失败说成最新版本',async()=>{
  const session=new MobileUpdateSession(async()=>{throw new Error('native unavailable');});
  await session.refresh();expect(session.status?.phase).toBe('error');expect(session.status?.message).toContain('重试');
 });
 it('状态读取进行中点击检查仍会执行，不丢弃用户操作',async()=>{
  const calls:string[]=[];let finish!:(s:UpdateStatus)=>void;
  const session=new MobileUpdateSession(async<T>(command:string)=>{calls.push(command);if(command==='mobile_update_status')return await new Promise<UpdateStatus>(resolve=>{finish=resolve;}) as T;return available as T;});
  const read=session.refresh();const action=session.action('check');finish(status());await Promise.all([read,action]);
  expect(calls).toEqual(['mobile_update_status','mobile_update_action']);expect(session.status?.target?.code).toBe(4008);
 });
 it('自动检查正在返回时，随后点击的手动检查仍会执行',async()=>{
  const calls:unknown[]=[];let finish!:(s:UpdateStatus)=>void;
  const session=new MobileUpdateSession(async<T>(_command:string,args?:Record<string,unknown>)=>{calls.push(args?.action);if(args?.action==='auto')return await new Promise<UpdateStatus>(resolve=>{finish=resolve;}) as T;return available as T;});
  const auto=session.action('auto');const manual=session.action('check');finish(status());await Promise.all([auto,manual]);
  expect(calls).toEqual(['auto','check']);
 });
});
