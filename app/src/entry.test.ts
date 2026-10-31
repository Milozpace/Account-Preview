import {beforeEach,describe,expect,it,vi} from 'vitest';
const mocks=vi.hoisted(()=>({context:{platform:'Windows',preview:false},native:true,desktop:vi.fn(),settings:vi.fn(),menu:vi.fn(async(_options:unknown)=>({setAsWindowMenu:vi.fn()})),invoke:vi.fn(),assign:vi.fn(),app:{hidden:true,textContent:''},search:''}));
const theme = vi.hoisted(() => ({ mount: vi.fn(() => () => undefined),desktop:vi.fn(()=>()=>undefined) }));
vi.mock('./mobile-theme',()=>({mountMobileTheme:theme.mount,mountDesktopTheme:theme.desktop,mountDesktopWindowTheme:async()=>({readTheme:()=>undefined,dispose:()=>undefined})}));
vi.mock('@tauri-apps/api/window',()=>({getCurrentWindow:()=>({setTheme:vi.fn(async()=>undefined)})}));
vi.mock('@tauri-apps/api/core',()=>({isTauri:()=>mocks.native,invoke:async(command:string)=>{mocks.invoke(command);return mocks.context;}}));
vi.mock('@tauri-apps/api/menu',()=>({Menu:{new:(options:unknown)=>mocks.menu(options)}}));
vi.mock('./desktop',()=>({startDesktop:(preview:boolean)=>mocks.desktop(preview)}));
vi.mock('./main',()=>({openAppSettings:()=>mocks.settings()}));
beforeEach(()=>{
 vi.resetModules();vi.clearAllMocks();mocks.native=true;mocks.context={platform:'Windows',preview:false};mocks.search='';mocks.app.hidden=true;
 vi.stubGlobal('navigator',{userAgent:'Windows'});vi.stubGlobal('innerWidth',1180);
 vi.stubGlobal('location',{get search(){return mocks.search;},pathname:'/',assign:mocks.assign});
 vi.stubGlobal('window',{addEventListener:vi.fn()});vi.stubGlobal('document',{querySelector:()=>mocks.app});
});
describe('platform entry isolation',()=>{
 it('macOS opens the desktop dashboard and desktop theme',async()=>{
  mocks.context={platform:'macOS',preview:false};
  await(await import('./entry')).entryReady;
  expect(mocks.desktop).toHaveBeenCalledWith(false);
  expect(theme.desktop).toHaveBeenCalledOnce();expect(theme.mount).not.toHaveBeenCalled();
 });
 it('macOS settings window enters desktop settings',async()=>{
  mocks.context={platform:'macOS',preview:false};mocks.search='?panel=settings';
  await(await import('./entry')).entryReady;
  expect(mocks.settings).toHaveBeenCalledOnce();expect(mocks.desktop).not.toHaveBeenCalled();
  expect(theme.desktop).toHaveBeenCalledOnce();
 });
 it('Windows loads the desktop without an extra native menu and keeps both shortcuts',async()=>{
  await(await import('./entry')).entryReady;
  expect(mocks.desktop).toHaveBeenCalledWith(false);expect(mocks.settings).not.toHaveBeenCalled();expect(mocks.menu).not.toHaveBeenCalled();
  const keydown=(window.addEventListener as ReturnType<typeof vi.fn>).mock.calls.find(call=>call[0]==='keydown')![1];
  keydown({ctrlKey:true,key:',',preventDefault:vi.fn()});expect(mocks.assign).toHaveBeenLastCalledWith('/?panel=settings');
  keydown({ctrlKey:true,key:'1',preventDefault:vi.fn()});expect(mocks.assign).toHaveBeenLastCalledWith('/');
 });
 it('Android keeps the existing phone entry and no desktop menu',async()=>{mocks.context={platform:'Android',preview:false};await(await import('./entry')).entryReady;expect(mocks.desktop).not.toHaveBeenCalled();expect(mocks.menu).not.toHaveBeenCalled();expect(mocks.app.hidden).toBe(false);});
 it('safe desktop preview does not create menus or touch native state',async()=>{mocks.context.preview=true;await(await import('./entry')).entryReady;expect(mocks.desktop).toHaveBeenCalledWith(true);expect(mocks.invoke.mock.calls).toEqual([['app_context']]);expect(mocks.menu).not.toHaveBeenCalled();});
 it('secondary settings remain available without styling the desktop home',async()=>{mocks.search='?panel=settings';await(await import('./entry')).entryReady;expect(mocks.settings).toHaveBeenCalledOnce();expect(mocks.desktop).not.toHaveBeenCalled();expect(mocks.app.hidden).toBe(false);});
 it('narrow browser preview enters the existing mobile UI',async()=>{mocks.native=false;vi.stubGlobal('innerWidth',390);await(await import('./entry')).entryReady;expect(mocks.desktop).not.toHaveBeenCalled();expect(mocks.invoke).not.toHaveBeenCalled();expect(mocks.app.hidden).toBe(false);});
});
