import {afterEach,describe,expect,it,vi} from 'vitest';
import {loadMotionPreference,prefersReducedMotion,saveMotionPreference} from './mobile-preferences';
afterEach(()=>vi.unstubAllGlobals());
describe('fixed display motion',()=>{
 it('ignores both stored and system reduction',()=>{
  vi.stubGlobal('localStorage',{getItem:()=> 'reduce',setItem:vi.fn()});
  vi.stubGlobal('window',{matchMedia:()=>({matches:true})});
  expect(loadMotionPreference()).toBe('system');expect(prefersReducedMotion('reduce')).toBe(false);
  saveMotionPreference('reduce');expect(loadMotionPreference()).toBe('system');
 });
 it('works when storage and window are unavailable',()=>{
  vi.stubGlobal('localStorage',undefined);vi.stubGlobal('window',undefined);
  expect(loadMotionPreference()).toBe('system');expect(prefersReducedMotion()).toBe(false);
 });
});
