import {describe,it,expect} from 'vitest';
import {MobileUpdateSession,aboutPageHtml,updateReminderHtml,type UpdateStatus} from './mobile-update';

const status=(overrides:Partial<UpdateStatus>={}):UpdateStatus=>({supported:true,currentVersion:'0.4.0',currentCode:4007,phase:'idle',message:'',progress:0,remind:false,...overrides});
const available=status({phase:'available',remind:true,target:{version:'0.4.1',nativeVersion:'0.4.8',code:4008,notes:'修复 <img src=x onerror=alert(1)>',size:8200000}});

describe('手机程序更新',()=>{
 it('以更新卡片呈现用户亮点，隐藏旧发布稿中的维护指引',()=>{
  const notes='## 本次更新\n### 查账更顺手\n- 按 **月份** 查看\n\n## 升级说明\n兼容步骤\n## 给 Agent 的更新指引\n执行命令';
  const html=aboutPageHtml({...available,target:{...available.target!,notes}});
  expect(html).toContain('class="release-card"');
  expect(html).toContain('查账更顺手');
  expect(html).toContain('<strong>月份</strong>');
  expect(html).not.toContain('兼容步骤');
  expect(html).not.toContain('执行命令');
 });
 it('关于保留原品牌介绍并合并版本和更新，读取状态前也显示已知版本',()=>{
  const html=aboutPageHtml(null,'0.4.0');
  expect(html).toContain('class="about app-update"');expect(html).toContain('account-emblem');
  expect(html).toContain('生活账本');expect(html).toContain('让数字回到生活里。');
  expect(html).toContain('版本 0.4.0');expect(html).not.toContain('update-summary');
  expect(aboutPageHtml(status({currentVersion:''}),'0.4.0')).toContain('版本 0.4.0');
 });
 it('单独显示更新说明、大小和主动下载按钮，并转义远端文案',()=>{
  const html=aboutPageHtml(available);
  expect(html).toContain('0.4.1');expect(html).toContain('下载更新');expect(html).toContain('7.8 MB');
  expect(html).not.toContain('<img');expect(html).toContain('&lt;img');
  expect(html).not.toContain('安装序号');expect(html).not.toContain('网络要求与更新原理');
 });
 it('准备完毕才显示安装，权限往返后可继续安装',()=>{
  expect(aboutPageHtml(status({phase:'downloading',progress:42}))).toContain('42%');
  expect(aboutPageHtml(status({phase:'downloading'}))).not.toContain('data-update-action="install"');
  const html=aboutPageHtml(status({phase:'ready',installState:'permission'}));
  expect(html).toContain('继续安装');expect(html).toContain('允许此来源');
 });
 it('个人版只解释 USB 更新，禁止出现公开包下载动作',()=>{
  const html=aboutPageHtml(status({supported:false}));expect(html).toContain('USB');expect(html).not.toContain('data-update-action="download"');
 });
 it('测试构建明确标出临时通道，不伪装成正式发布',()=>{
  expect(aboutPageHtml(status({testChannel:true}))).toContain('临时更新测试通道');
  expect(aboutPageHtml(status())).not.toContain('临时更新测试通道');
 });
 it('手动发现预发布版时标明版本类型，首页不提示该版本',()=>{
  const prerelease={...available,target:{...available.target!,version:'0.4.2-rc.1',prerelease:true}};
  expect(aboutPageHtml(prerelease)).toContain('发现预发布版本 0.4.2-rc.1');
  expect(aboutPageHtml(prerelease)).toContain('下载更新');
  expect(updateReminderHtml(prerelease)).toBe('');
  expect(updateReminderHtml({...prerelease,phase:'ready'})).toBe('');
 });
 it('读取、默认、最新和检查中各有明确状态，检查中隐藏旧候选及可执行动作',()=>{
  expect(aboutPageHtml(null)).toContain('正在读取更新状态');
  expect(aboutPageHtml(status())).toContain('生活账本');
  expect(aboutPageHtml(status({phase:'latest'}))).toContain('已是最新版本');
  const stableOnly={...status({phase:'latest'}),stableOnly:true};
  expect(aboutPageHtml(stableOnly)).toContain('未发现新版正式版');
  const checking=aboutPageHtml({...available,phase:'checking'});
  expect(checking).toContain('正在检查更新');expect(checking).toContain('disabled');
  expect(checking).not.toContain('data-update-action="download"');expect(checking).not.toContain('0.4.1');
 });
 it('下载中只显示进度，安装包复核时不误报为仍在下载',()=>{
  const downloading=aboutPageHtml({...available,phase:'downloading',progress:42.4});
  expect(downloading).toContain('42%');expect(downloading).toContain('value="42"');
  expect(downloading).not.toContain('data-update-action=');
  const verifying=aboutPageHtml({...available,phase:'downloading',progress:100,message:'正在复核安装包…'});
  expect(verifying).toContain('正在复核安装包');expect(verifying).not.toContain('正在下载');
 });
 it('失败时提供对应重试；已就绪的安装错误仍保留安装入口',()=>{
  const failed=aboutPageHtml(status({phase:'error',message:'网络失败 <test>'}));
  expect(failed).toContain('检查失败');expect(failed).toContain('重试');expect(failed).toContain('&lt;test&gt;');
  expect(failed).not.toContain('data-update-action="download"');
  const retry=aboutPageHtml({...available,phase:'error',message:'下载失败'});
  expect(retry).toContain('重新下载');expect(retry).toContain('data-update-action="check"');
  const ready=aboutPageHtml({...available,phase:'ready',message:'未能打开系统安装界面，请重试或使用 USB 安装。'});
  expect(ready).toContain('安装更新');expect(ready).toContain('未能打开');
  expect(ready).not.toContain('data-update-action="download"');
  const recheck=aboutPageHtml({...available,phase:'ready',message:'无法完成更新检查。请稍后重试。'});
  expect(recheck).toContain('data-update-action="check"');expect(recheck).toContain('安装更新');
 });
 it('安装器已打开与权限缺失分别显示，不把安装器打开说成已更新',()=>{
  const opened=aboutPageHtml({...available,phase:'ready',installState:'opened'});
  expect(opened).toContain('等待安装完成');expect(opened).toContain('再次打开安装器');
  expect(opened).not.toContain('已是最新版本');
  const permission=aboutPageHtml({...available,phase:'ready',installState:'permission'});
  expect(permission).toContain('需要安装权限');expect(permission).toContain('允许此来源安装应用');
 });
 it('长更新说明默认收起，保留完整内容且不把下载按钮推到长文后',()=>{
  const notes='较长的更新说明。'.repeat(100);
  const html=aboutPageHtml({...available,target:{...available.target!,notes}});
  expect(html).toContain('<details class="update-notes-details">');
  expect(html).not.toContain('<details class="update-notes-details" open');
  expect(html).toContain(notes);expect(html).toContain('下载更新');
 });
 it('已忽略/稍后不自动提醒，更新失败不覆盖账本状态',()=>{
  expect(updateReminderHtml({...available,remind:false})).toBe('');
  expect(updateReminderHtml(available)).toContain('稍后');
  expect(updateReminderHtml(available)).toContain('data-open="about"');
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
