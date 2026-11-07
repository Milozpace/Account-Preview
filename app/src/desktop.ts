import '../../dashboard/src/style.css';
import {mountDashboard} from '../../dashboard/src/presentation';
import {invoke,isTauri} from '@tauri-apps/api/core';
import {createDesktopData} from './desktop-data';
import {sampleSnapshot} from './sample';
import {downloadExport} from '../../dashboard/src/export-ui';
import {showIncome} from './delivery-profile';

export function startDesktop(preview:boolean){
  const data=createDesktopData({invoke,previewSnapshot:preview||!isTauri()?sampleSnapshot:undefined});
  mountDashboard({api:data.api,subscribe:data.subscribe,readOnly:preview||!isTauri(),desktopApp:true,retryLocal:data.retry,showIncome,
    isDark:()=>document.documentElement.dataset.theme==='dark',
    subscribeTheme:changed=>{window.addEventListener('account-theme-change',changed);return()=>window.removeEventListener('account-theme-change',changed);},
    openSettings:()=>location.assign(location.pathname+'?panel=settings'),
    saveExport:async report=>{
      if(preview||!isTauri())report={...report,content:report.content.replace('\n','\n\n> 合成预览数据，不是真实账本。\n')};
      return isTauri()?invoke<{saved:boolean}>('analysis_export_save',{...report}):downloadExport(report);
    },
  });
}
