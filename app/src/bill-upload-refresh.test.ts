import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {BillUploadRefresh,type UploadRefreshState} from './bill-upload-refresh';
describe('upload receipt auto checks',()=>{
 beforeEach(()=>{vi.useFakeTimers();vi.setSystemTime(0);});
 afterEach(()=>{vi.useRealTimers();});
 function fixture(){
  const state:UploadRefreshState={enabled:true,busy:false,pending:2};
  const check=vi.fn(async()=>true);
  const controller=new BillUploadRefresh({state:()=>state,check});
  return{state,check,controller};
 }
 it('checks on entry then at a low frequency and stops when all files complete',async()=>{
  const f=fixture();f.controller.sync(true);await vi.advanceTimersByTimeAsync(0);expect(f.check).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(59_999);expect(f.check).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);expect(f.check).toHaveBeenCalledTimes(2);
  f.state.pending=0;f.controller.sync();await vi.advanceTimersByTimeAsync(180_000);expect(f.check).toHaveBeenCalledTimes(2);
 });
 it('never checks in preview, background, another page, or an empty queue',async()=>{
  const f=fixture();f.state.enabled=false;f.controller.sync(true);await vi.advanceTimersByTimeAsync(120_000);expect(f.check).not.toHaveBeenCalled();
  f.state.enabled=true;f.state.pending=0;f.controller.sync(true);await vi.advanceTimersByTimeAsync(120_000);expect(f.check).not.toHaveBeenCalled();
 });
 it('does not overlap a picker or network request and debounces foreground switches',async()=>{
  const f=fixture();f.state.busy=true;f.controller.sync(true);expect(f.check).not.toHaveBeenCalled();
  f.state.busy=false;let finish!:(result:boolean)=>void;f.check.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  f.controller.sync(true);f.controller.sync(true);await vi.advanceTimersByTimeAsync(90_000);expect(f.check).toHaveBeenCalledTimes(1);
  finish(true);await vi.advanceTimersByTimeAsync(0);f.controller.sync(true);expect(f.check).toHaveBeenCalledTimes(1);
 });
 it('cancels scheduled checks when leaving the page',async()=>{
  const f=fixture();f.controller.sync();f.state.enabled=false;f.controller.sync();
  await vi.advanceTimersByTimeAsync(180_000);expect(f.check).not.toHaveBeenCalled();
 });
 it('backs off after failure, including a rejected native request',async()=>{
  const f=fixture();f.check.mockResolvedValueOnce(false).mockRejectedValueOnce(Error('offline'));
  f.controller.sync(true);await vi.advanceTimersByTimeAsync(0);
  await vi.advanceTimersByTimeAsync(119_999);expect(f.check).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);expect(f.check).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(239_999);expect(f.check).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(1);expect(f.check).toHaveBeenCalledTimes(3);
 });
 it('reduces request frequency for a larger queue and stops on disposal',async()=>{
  const f=fixture();f.state.pending=100;f.controller.sync(true);await vi.advanceTimersByTimeAsync(0);
  await vi.advanceTimersByTimeAsync(599_999);expect(f.check).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);expect(f.check).toHaveBeenCalledTimes(2);
  f.controller.stop();await vi.advanceTimersByTimeAsync(1_000_000);expect(f.check).toHaveBeenCalledTimes(2);
 });
 it('budgets all non-recorded native reads, including failed uploads',async()=>{
  const f=fixture();f.state.pending=1;f.state.requests=21;
  f.controller.sync(true);await vi.advanceTimersByTimeAsync(0);
  await vi.advanceTimersByTimeAsync(125_999);expect(f.check).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);expect(f.check).toHaveBeenCalledTimes(2);
 });
});
