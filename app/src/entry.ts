import {invoke,isTauri} from '@tauri-apps/api/core';
import { mountMobileTheme, mountDesktopTheme, mountDesktopWindowTheme } from './mobile-theme';
import {loadMotionPreference} from './mobile-preferences';

async function start(){
  const native=isTauri();
  let context={platform:/Android/i.test(navigator.userAgent)?'Android':'Windows',preview:false};
  if(native)context=await invoke<typeof context>('app_context').catch(()=>context);
  const desktop=['Windows','macOS'].includes(context.platform)&&(native||innerWidth>700);
  let nativeTheme: Awaited<ReturnType<typeof mountDesktopWindowTheme>> | undefined;
  if(native&&desktop){
    try { const {getCurrentWindow}=await import('@tauri-apps/api/window');nativeTheme=await mountDesktopWindowTheme(getCurrentWindow()); }
    catch { /* Older native hosts retain browser theme handling. */ }
  }
  const changed=()=>window.dispatchEvent(new Event('account-theme-change'));
  const stopTheme=desktop?mountDesktopTheme(changed,nativeTheme?.readTheme):mountMobileTheme(true,changed);
  window.addEventListener('pagehide',event=>{if(!event.persisted){stopTheme();nativeTheme?.dispose();}});
  if(desktop){await import('./desktop-theme.css');await import('./desktop-settings.css');await import('./desktop-ui.css');document.documentElement?.setAttribute?.('data-motion-preference',loadMotionPreference());}
  else await import('./mobile-theme.css');
  const settings=desktop&&new URLSearchParams(location.search).get('panel')==='settings';
  if(desktop&&!settings){const module=await import('./desktop');await import('./visual-system.css');module.startDesktop(context.preview);}
  else{const module=await import('./main');await import('./visual-system.css');if(settings)module.openAppSettings();document.querySelector<HTMLElement>('#app')!.hidden=false;}
  if(native&&desktop&&!context.preview){
    const goHome=()=>location.assign(location.pathname);
    const goSettings=()=>location.assign(location.pathname+'?panel=settings');
    window.addEventListener('keydown',event=>{
      if(event.ctrlKey&&!event.altKey&&!event.shiftKey&&(event.key===','||event.key==='1')){
        event.preventDefault();if(event.key===',')goSettings();else goHome();
      }
    });
  }
  if(!native&&!settings){const wide=innerWidth>700;window.addEventListener('resize',()=>{if((innerWidth>700)!==wide)location.reload();});}
}
export const entryReady=start().catch(error=>{const app=document.querySelector<HTMLElement>('#app')!;app.hidden=false;app.textContent=error instanceof Error?error.message:'页面暂时无法打开，请重新启动。';});
