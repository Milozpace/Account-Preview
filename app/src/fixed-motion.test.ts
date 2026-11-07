import {describe,it,expect,vi} from 'vitest';
import {loadMotionPreference,prefersReducedMotion} from './mobile-preferences';
describe('fixed motion',()=>{
 it('ignores saved and system reduced motion',()=>{
  vi.stubGlobal('localStorage',{getItem:()=> 'reduce'});
  vi.stubGlobal('window',{matchMedia:()=>({matches:true})});
  expect(loadMotionPreference()).toBe('system');
  expect(prefersReducedMotion()).toBe(false);
  vi.unstubAllGlobals();
 });
});
