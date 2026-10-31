import './header.css';
import {uiIcon} from './ui-icons';

export const exportIcon=uiIcon('export');
const syncIcon=uiIcon('sync');
const settingsIcon=uiIcon('settings');

export function dashboardHeaderHtml(settings=false,desktopApp=false):string {
  return `<header class="site-header app-toolbar-header"><a class="brand" href="#main"><span class="brand-symbol" aria-hidden="true"><i></i><i></i><i></i><i></i></span><span>生活账本</span></a>
    <div class="header-controls"><${desktopApp?'div':'label'} class="month-select${desktopApp?' desktop-month-picker':''}"><span class="sr-only">查看月份</span>${uiIcon('calendar')}<select id="month-selector" aria-label="查看月份" disabled></select><span aria-hidden="true">${uiIcon('down')}</span></${desktopApp?'div':'label'}>
      <div class="header-actions" role="group" aria-label="账本操作"><button id="sync-button" class="header-action sync-button" type="button" aria-label="同步展示">${syncIcon}<span class="sr-only">同步展示</span></button><button id="export-button" class="header-action" type="button" aria-label="导出账单" disabled>${exportIcon}</button>${settings?`<button id="app-settings-button" class="header-action" type="button" aria-label="应用设置">${settingsIcon}</button>`:''}</div>
    </div></header>`;
}
