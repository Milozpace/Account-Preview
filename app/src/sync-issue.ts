import {escapeHtml as e} from '../../dashboard/src/helpers';

/** Native diagnostics carry facts; the view supplies recovery instructions. */
export interface SyncIssue {code:string;stage:string;message:string;httpStatus?:number;retryable:boolean}
type Severity='info'|'warning'|'error';
interface IssueView {title:string;next:string;severity:Severity;action?:'connection'|'pairing'|'update'}

export function syncIssue(error:unknown):SyncIssue {
  if(error&&typeof error==='object'&&'code' in error&&'message' in error){
    const value=error as Partial<SyncIssue>;
    if(typeof value.code==='string'&&typeof value.message==='string')return {
      code:value.code,stage:typeof value.stage==='string'?value.stage:'sync',message:value.message,
      ...(typeof value.httpStatus==='number'?{httpStatus:value.httpStatus}:{}),retryable:value.retryable===true,
    };
  }
  const message=typeof error==='string'?error:error instanceof Error?error.message:'本次操作未完成。';
  // Legacy hosts return strings. Only specific evidence is classified; stage names
  // such as “认证/契约/版本检查” are never credential evidence.
  const status=message.match(/(?:HTTP\s*|·\s*)(401|403|409|429|5\d\d)(?!\d)/)?.[1]
    ||message.match(/[（(](401|403|409|429|5\d\d)[）)]/)?.[1];
  let code='sync-unknown',retryable=false;
  if(status==='401'||/应用密码被拒绝/.test(message))code='auth-unauthorized';
  else if(status==='403')code='access-forbidden';
  else if(status==='409')code='cloud-path-conflict';
  else if(status==='429'){code='rate-limited';retryable=true;}
  else if(status?.startsWith('5')){code='service-unavailable';retryable=true;}
  else if(/401\s*\/\s*403|403\s*\/\s*401/.test(message))code='access-rejected';
  else if(/超时/.test(message)){code='request-timeout';retryable=true;}
  else if(/网络|连接失败|坏网络/.test(message)){code='network-unavailable';retryable=true;}
  else if(/相机/.test(message))code='camera-permission';
  else if(/已有另一组/.test(message))code='pairing-conflict';
  else if(/安全存储/.test(message))code='secure-storage';
  else if(/解密|本配对认证/.test(message))code='snapshot-authentication';
  else if(/契约|schema|数据格式/.test(message.replace(/【[^】]*】/g,'')))code='snapshot-invalid';
  return {code,stage:'sync',message,retryable,...(status?{httpStatus:Number(status)}:{})};
}

export function issueView(issue:SyncIssue):IssueView {
  const code=issue.code;
  if(code==='cloud-empty')return {title:'等待电脑首次同步',next:'请让电脑 Agent 完成记账并同步展示，再在这里获取账单。若电脑已发布，请核对两端使用的坚果云账号。',severity:'info'};
  if(code==='auth-unauthorized')return {title:'坚果云账号或应用密码未通过验证',next:'请核对账号和坚果云第三方应用密码；这里使用应用密码，而非网页登录密码。',severity:'error',action:'connection'};
  if(code==='access-rejected')return {title:'坚果云拒绝了访问请求',next:'旧版诊断未区分认证失败和权限不足。请检查账号、应用密码及第三方应用权限，并核对问题详情。',severity:'error',action:'connection'};
  if(code==='access-forbidden')return {title:'坚果云拒绝访问同步位置',next:'请检查账号、第三方应用权限及服务状态。仅凭此结果不能认定密码错误。',severity:'error',action:'connection'};
  if(code==='cloud-path-conflict')return {title:'无法读取云端同步位置',next:'请让电脑 Agent 核对首次发布结果、同步目录和两端账号。当前结果不能确认是否已经发布。',severity:'warning'};
  if(code==='cloud-path-missing')return issue.stage==='credential-check'?{title:'无法验证坚果云服务地址',next:'服务根目录没有找到，候选凭据未保存。请检查连接和服务地址，并让电脑 Agent 核对问题详情。',severity:'error',action:'connection'}:{title:'云端同步目录尚未就绪',next:'请让电脑 Agent 检查同步账号及首次发布是否完成，再获取账单。',severity:'info'};
  if(code==='snapshot-newer-schema')return {title:'请更新手机版 Account',next:'电脑发布了当前手机不支持的数据格式。请覆盖更新手机应用后重新获取账单。',severity:'error',action:'update'};
  if(code==='snapshot-older-schema')return {title:'请在电脑更新并重新同步账单',next:'云端账单采用旧数据格式。请让电脑 Agent 核对程序及同步组件，重新生成并发布账单。',severity:'error'};
  if(code==='request-timeout')return {title:'同步连接超时',next:'请检查网络，稍后重新获取账单。',severity:'warning'};
  if(code==='network-unavailable')return {title:'暂时无法连接坚果云',next:'请检查网络连接后重试。',severity:'warning'};
  if(code==='rate-limited')return {title:'坚果云请求过于频繁',next:'请稍候再试，避免连续点击获取账单。',severity:'warning'};
  if(code==='service-unavailable')return {title:'云端服务暂时不可用',next:'请稍后重试。若持续发生，可将问题详情交给电脑 Agent 检查。',severity:'warning'};
  if(code==='camera-permission')return {title:'扫码未完成',next:'请在系统设置中允许 Account 使用相机，再返回扫码。',severity:'warning',action:'pairing'};
  if(code==='camera-scan-failed')return {title:'相机扫码未完成',next:'请关闭扫码界面后重试，并确保二维码完整、清晰且未被遮挡。',severity:'warning',action:'pairing'};
  if(code==='sync-not-configured')return {title:'请先完成同步连接',next:'设备配对后，请填写坚果云账号及当前设备的应用密码。',severity:'info',action:'connection'};
  if(code==='configuration-invalid')return {title:'同步连接配置不完整',next:'请填写坚果云账号和当前设备的应用密码，再验证连接。',severity:'error',action:'connection'};
  if(code==='configuration-foreign-account')return {title:'当前设备已配置另一坚果云账号',next:'原配置和账单已保留。请核对账号；若确需换账号，请先让电脑 Agent 核对迁移方案。',severity:'error',action:'connection'};
  if(code==='cloud-directory-invalid')return {title:'云端返回的同步目录无法确认',next:'请检查坚果云服务和同步位置，并将问题详情交给电脑 Agent。',severity:'error'};
  if(code==='pairing-conflict')return {title:'手机已有另一组账本配对',next:'已保留原配对。请确认电脑打开的是同一本账，不要卸载应用或清除数据。',severity:'error',action:'pairing'};
  if(code==='pairing-foreign-identity')return {title:'配对码属于另一组账本',next:'已保留原配对。请确认电脑打开的是同一本账；不要清除手机应用数据。',severity:'error',action:'pairing'};
  if(code.startsWith('pairing-')){const unsupported=code.includes('version')||code==='pairing-unsupported';return {title:'无法完成设备配对',next:unsupported?'配对码版本不受支持，请核对两端版本后重试。':'请在对应电脑重新显示账本配对二维码并扫描；原配对保持不变。',severity:'error',action:unsupported?'update':'pairing'};}
  if(code.includes('storage')||code==='local-save-failed'||code==='local-state-unavailable')return {title:'本机安全存储或保存失败',next:'请检查系统授权和可用空间，重启应用后重试。请保留现有应用数据。',severity:'error'};
  if(code.includes('snapshot')||code.includes('revision')||code.includes('integrity'))return {title:'账单未通过安全或数据校验',next:'未接受有问题的数据。请让电脑 Agent 核对配对、版本和发布结果，并提供下方问题详情。',severity:'error'};
  if(code.includes('redirect')||code.includes('tls'))return {title:'无法建立安全的同步连接',next:'请检查网络、系统时间和服务状态；不要关闭证书校验。',severity:'error'};
  return {title:'本次同步检查未完成',next:'请查看问题详情。若重试仍失败，可将错误代码和阶段交给电脑 Agent 检查。',severity:'warning'};
}

export function syncIssueHtml(issue:SyncIssue,hasData:boolean,showUpdateHelp=false):string {
  const view=issueView(issue),label={info:'待完成',warning:'需要留意',error:'需要处理'}[view.severity];
  const action=view.action?`<button type="button" class="quiet-button issue-action" data-action="sync-${view.action==='connection'?'edit-connection':view.action==='pairing'?'edit-pairing':'update-help'}">${view.action==='connection'?'检查同步连接':view.action==='pairing'?'查看设备配对':'如何更新手机'}</button>`:'';
  return `<div class="connection-feedback" data-severity="${view.severity}" role="${view.severity==='error'?'alert':'status'}"><div class="issue-heading"><span class="issue-severity"><span aria-hidden="true">${view.severity==='info'?'ⓘ':'!'}</span> ${label}</span><h3>${e(view.title)}</h3></div><p>${e(view.next)}</p>${hasData?'<p class="issue-cache">已有账单仍可查看；本次未确认云端最新状态。</p>':''}${action}${showUpdateHelp?'<div class="issue-update-help"><p>请让电脑 Agent 从 Account-Preview 发布页取得兼容当前电脑数据格式的 Android 安装包，校验后用同一安装身份和签名的 APK 覆盖安装。无需卸载或重新配对。</p><p class="issue-release-address">https://github.com/Milozpace/Account-Preview/releases</p></div>':''}<details><summary>问题详情</summary><dl><dt>错误代码</dt><dd>${e(issue.code)}</dd><dt>发生阶段</dt><dd>${e(issue.stage)}</dd>${issue.httpStatus?`<dt>HTTP 状态</dt><dd>${issue.httpStatus}</dd>`:''}</dl><p>${e(issue.message)}</p></details></div>`;
}
