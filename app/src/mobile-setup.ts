/** Device setup is normal application state, independent of network success. */
import {issueView,syncIssue} from './sync-issue';
export type SetupPhase = 'checking'|'pair'|'connect'|'ready';
export function setupPhase(initialized:boolean|null,configured:boolean,connectionKnown=true):SetupPhase {
  if(initialized===null)return 'checking';
  if(!initialized)return 'pair';
  if(!connectionKnown)return 'checking';
  return configured?'ready':'connect';
}

/** Keep raw diagnostics out of ordinary connection guidance. */
export function connectionError(message:string):string {
  return issueView(syncIssue(message)).title;
}
