/** Device setup is normal application state, independent of network success. */
export type SetupPhase = 'checking'|'pair'|'connect'|'ready';
export function setupPhase(initialized:boolean|null,configured:boolean,connectionKnown=true):SetupPhase {
  if(initialized===null)return 'checking';
  if(!initialized)return 'pair';
  if(!connectionKnown)return 'checking';
  return configured?'ready':'connect';
}

/** Keep raw diagnostics out of ordinary connection guidance. */
export function connectionError(message:string):string {
  if(/401|403|拒绝访问|应用密码|认证/.test(message))return '连接未成功，请检查账号和应用密码。';
  if(/安全存储|密钥|身份|校验|解密/.test(message))return '暂时无法读取或验证账本，请查看问题详情。';
  return '暂时无法连接，请检查网络后重试。';
}
