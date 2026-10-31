export interface UploadRefreshState {enabled:boolean;busy:boolean;pending:number;requests?:number}

/** One foreground-only timer. Completed history never earns another request. */
export class BillUploadRefresh {
 private timer:ReturnType<typeof setTimeout>|undefined;
 private running=false;
 private paused=false;
 private nextAt=0;
 private failures=0;
 constructor(private readonly options:{state:()=>UploadRefreshState;check:()=>Promise<boolean>}){}
 sync(immediate=false):void {this.paused=false;this.schedule(immediate);}
 stop():void {this.paused=true;this.clear();}
 private clear():void {if(this.timer!==undefined){clearTimeout(this.timer);this.timer=undefined;}}
 private eligible():boolean {const state=this.options.state();return state.enabled&&!state.busy&&state.pending>0;}
 private interval():number {
  // Space automatic checks for an average of ten receipt reads per minute, leaving account quota
  // for snapshot sync and user actions. Native code skips recorded history.
  const state=this.options.state();
  const base=Math.max(60_000,(state.requests??state.pending)*6_000);
  return Math.max(base,Math.min(600_000,base*2**this.failures));
 }
 private schedule(immediate:boolean):void {
  this.clear();if(this.paused||this.running||!this.eligible())return;
  if(immediate&&this.nextAt<=Date.now()){void this.run();return;}
  const delay=this.nextAt>Date.now()?this.nextAt-Date.now():this.interval();
  this.timer=setTimeout(()=>{this.timer=undefined;void this.run();},delay);
 }
 private async run():Promise<void> {
  if(this.paused||this.running||!this.eligible())return;
  this.running=true;
  try{this.failures=await this.options.check()?0:Math.min(4,this.failures+1);}
  catch{this.failures=Math.min(4,this.failures+1);}
  finally{this.running=false;this.nextAt=Date.now()+this.interval();this.schedule(false);}
 }
}
