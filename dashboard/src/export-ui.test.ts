import {describe, expect, it} from 'vitest';
import fixture from '../../tests/fixtures/app-v2-snapshot.json';
import type {DashboardSnapshot} from './types';
import {exportFormHtml, ExportSession} from './export-ui';
import {dashboardHeaderHtml} from './header';

const snapshot=fixture as unknown as DashboardSnapshot;
describe('export entry and save state',()=>{
  it('offers only available months and exact current detail counts',()=>{
    const html=exportFormHtml(snapshot,'2026-10');
    expect(exportFormHtml(snapshot,'2026-10',false)).not.toContain('笔收入');
    expect(html).toContain('value="2026-10" selected');
    expect(html).toContain('value="2026-09"');
    expect(html).not.toContain('2026-08');
    expect(html).toContain(`${snapshot.months[0].records.length} 笔个人消费`);
    expect(html).toContain(`${snapshot.months[0].structuralRecords.length} 笔结构性支出`);
    expect(html).toContain('Markdown');
    expect(html).not.toContain('sourceHash');
  });
  it('has a disabled save when no data is available',()=>{
    const html=exportFormHtml(null,'');
    expect(html).toContain('尚无可导出的账单');
    expect(html).toMatch(/id="save-analysis-export"[^>]*disabled/);
  });
  it('keeps cancelled saves quiet and blocks a concurrent second save',async()=>{
    const session=new ExportSession();let resolve!:(value:{saved:boolean})=>void;let calls=0;
    const saving=session.save(()=>new Promise(r=>{calls++;resolve=r;}));
    expect(session.busy).toBe(true);
    await session.save(async()=>{calls++;return{saved:true};});
    expect(calls).toBe(1);
    resolve({saved:false});await saving;
    expect(session.busy).toBe(false);expect(session.message).toBe('');
  });
  it('reports success only after confirmation and can retry after failure',async()=>{
    const session=new ExportSession();
    await session.save(async()=>{throw Error('保存失败，请重试。');});
    expect(session.tone).toBe('error');expect(session.message).toContain('保存失败');
    await session.save(async()=>({saved:true}));
    expect(session.tone).toBe('success');expect(session.message).toBe('文件已保存');
  });
  it('offers compact named header actions without placing controls in the content',()=>{
    const html=dashboardHeaderHtml(true);
    for(const id of ['month-selector','sync-button','export-button','app-settings-button'])expect(html).toContain(`id="${id}"`);
    expect(html).toContain('aria-label="导出账单"');expect(html).toContain('aria-label="应用设置"');
    expect(html).toContain('class="header-action');
    expect(dashboardHeaderHtml(false)).not.toContain('id="app-settings-button"');
  });
});
