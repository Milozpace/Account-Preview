import {describe,it,expect} from 'vitest';
import {desktopStartupHtml} from './desktop-ui';
describe('desktop startup surfaces',()=>{
 it('shows only the ring and one status while reading',()=>{
  const html=desktopStartupHtml(true,false,'optional-cache-error');
  expect(html).toContain('正在打开生活账本');expect(html).toContain('◌');
  for(const unwanted of ['optional-cache-error','同步展示','打开设置','A LITTLE','从这个月'])expect(html).not.toContain(unwanted);
 });
 it('offers read-only retry for an escaped real read failure',()=>{
  const html=desktopStartupHtml(false,true,'路径 <missing>');
  expect(html).toContain('id="retry-local"');expect(html).toContain('打开设置');expect(html).toContain('&lt;missing&gt;');expect(html).not.toContain('id="first-sync-button"');
 });
 it('offers explicit sync only after a confirmed empty read',()=>{
  expect(desktopStartupHtml(false,false,'')).toContain('id="first-sync-button"');
 });
 it('keeps sync progress and failure visible on the first-run surface',()=>{
  expect(desktopStartupHtml(false,false,'',true)).toContain('正在同步账单');
  const failed=desktopStartupHtml(false,false,'同步失败，请重试');
  expect(failed).toContain('同步失败，请重试');expect(failed).toContain('id="first-sync-button"');
 });
});
