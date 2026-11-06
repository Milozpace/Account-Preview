import type {DashboardApi} from '../../dashboard/src/presentation';
import type {DashboardStatus,DashboardSnapshot} from '../../dashboard/src/types';
import type {AppSnapshot} from './bills';
import {shanghaiToday} from '../../dashboard/src/helpers';
import {sameSnapshotContent} from './snapshot-state';

interface LocalRead {snapshot:AppSnapshot|null;sourceChanged:boolean;monthChanged:boolean}
interface Refresh extends Omit<LocalRead,'snapshot'> {
  snapshot:AppSnapshot;warning?:string;
  appPublish:{ok:boolean|null;published:boolean;skipped?:boolean;error?:string;reason?:string};
}
interface Options {
  invoke:(command:string)=>Promise<unknown>;
  previewSnapshot?:AppSnapshot;
  today?:()=>string;
}
const errorText=(error:unknown,fallback:string)=>typeof error==='string'&&error.length<200?error:fallback;

/** Native validates V3; only the presentation-facing envelope is V2-shaped. */
export function createDesktopData(options:Options){
  let current:AppSnapshot|null=options.previewSnapshot??null;
  let source=options.previewSnapshot?'preview':'none';
  let detail=options.previewSnapshot?'合成预览 · 不读取账本、不连接云端':'';
  let error:string|null=options.previewSnapshot?detail:null;
  let publicationDetail='',publicationError:string|null=null;
  let sourceChanged=false,monthChanged=false,syncing=false,epoch=0;
  let localState:DashboardStatus['localState']=options.previewSnapshot?'ready':'loading';
  let initialization:Promise<void>|undefined,reading:Promise<void>|undefined;
  let lastRead=0;
  const listeners=new Set<(snapshotChanged:boolean)=>void>();
  const today=options.today??shanghaiToday;
  const notify=(changed:boolean)=>{for(const listener of listeners)listener(changed);};
  async function loadLocal(){
    if(options.previewSnapshot||syncing)return;
    if(reading)return reading;
    const turn=++epoch;lastRead=Date.now();
    reading=(async()=>{
      try{
        const loaded=await options.invoke('desktop_load_snapshot') as LocalRead;
        if(turn!==epoch)return;
        sourceChanged=loaded.sourceChanged;monthChanged=loaded.monthChanged;
        let changed=false;
        if(loaded.snapshot){
          changed=!sameSnapshotContent(current,loaded.snapshot);current=loaded.snapshot;source='local';
          if(changed){publicationDetail='';publicationError=null;}
          detail=publicationDetail||'本机快照 · 云端状态请在账本与同步设置中确认';
        }
        localState=current?'ready':'empty';
        error=publicationError;
        notify(changed);
      }catch(reason){if(turn===epoch){localState='error';error=errorText(reason,current?'本机快照读取未能完成，继续显示上次有效数据。':'本机快照读取失败，请重试或检查设置。');notify(false);}}
    })();
    try{await reading;}finally{reading=undefined;}
  }
  async function initialize(){
    if(initialization)return initialization;
    initialization=(async()=>{
      if(options.previewSnapshot)return;
      try{
        const cache=await options.invoke('ledger_cache') as {snapshot:AppSnapshot;revision:number}|null;
        if(cache){current=cache.snapshot;source='cache';detail=`本地缓存 · 第 ${cache.revision} 版（未连接云端确认）`;}
      }catch{ /* Optional encrypted cache is independent of the authoritative local source. */ }
      // Paint a valid cache immediately; otherwise keep startup pending until the local read finishes.
      if(current)setTimeout(()=>void loadLocal(),0);
      else await loadLocal();
    })();
    return initialization;
  }
  const status=():DashboardStatus=>({
    generatedAt:current?.generatedAt??null,sourceChanged,
    monthChanged:monthChanged||Boolean(current&&current.calendarMonth!==today().slice(0,7)),
    syncing,error,today:today(),syncDetail:detail,localState,
  });
  const api:DashboardApi=async<T>(path:string,request?:RequestInit):Promise<T>=>{
    await initialize();
    if(path==='/api/dashboard'){
      if(!current)throw Error(error||'还没有可用快照，请先同步展示。');
      return {...current,schemaVersion:2} as T;
    }
    if(path==='/api/status'){
      if(source!=='preview'&&Date.now()-lastRead>5000&&!syncing&&lastRead)void loadLocal();
      return status() as T;
    }
    if(path==='/api/sync'&&request?.method==='POST'){
      if(options.previewSnapshot)throw Error('界面预览不能执行同步。');
      if(syncing)throw Error('正在同步，请稍候。');
      epoch++;syncing=true;error=null;
      try{
        const refreshed=await options.invoke('desktop_refresh_snapshot') as Refresh;
        current=refreshed.snapshot;source='local';sourceChanged=refreshed.sourceChanged;monthChanged=refreshed.monthChanged;
        localState='ready';
        const warning=[sourceChanged?'账本随后有更新，请再次同步展示。':'',monthChanged?'月份随后已变化，请再次同步展示。':'',refreshed.warning??''].filter(Boolean).join(' ');
        detail=refreshed.appPublish.published?'本机展示已更新 · 云端发布已确认':refreshed.appPublish.skipped?'本机展示已更新 · 云端发布未启用':`本机展示已更新 · ${refreshed.appPublish.error||refreshed.appPublish.reason||'云端发布未确认，请稍后重试。'}`;
        error=warning||(!refreshed.appPublish.published?detail:null);
        if(warning&&!refreshed.appPublish.published)error=detail+' · '+warning;
        publicationDetail=detail;publicationError=error;
        // The current local snapshot remains displayable even if publishing fails.
        notify(true);
        return {ok:true} as T;
      }catch(reason){error=errorText(reason,'本机展示更新失败，已保留上次有效数据。');throw Error(error);}
      finally{syncing=false;lastRead=Date.now();}
    }
    throw Error('不支持的展示数据请求。');
  };
  return {api,retry:async()=>{await initialize();await loadLocal();},subscribe:(listener:(snapshotChanged:boolean)=>void)=>{listeners.add(listener);return()=>{listeners.delete(listener);};}};
}
