interface ScanResult {content:string}
interface ActiveScan {stopped:(value:null)=>void;cancelled:boolean;closing:Promise<void>|null}

/** Native Android cancel can leave scan() pending; release this session separately. */
export class PairingScanSession {
  private current:ActiveScan|undefined;
  get active():boolean{return Boolean(this.current);}

  constructor(
    private readonly scan:()=>Promise<ScanResult>,
    private readonly cancelNative:()=>Promise<void>,
    private readonly show:(active:boolean)=>void,
  ){}

  async run():Promise<ScanResult|null>{
    if(this.current)throw new Error('Scanner is already active');
    let stopped!:(value:null)=>void;
    const cancelled=new Promise<null>(resolve=>{stopped=resolve;});
    const session:ActiveScan={stopped,cancelled:false,closing:null};
    this.current=session;this.show(true);
    try{
      const native=this.scan().then(result=>{
        if(session.cancelled)result.content='';
        return result;
      });
      const result=await Promise.race([native,cancelled]);
      if(session.cancelled){await session.closing;return null;}
      return result;
    }catch(error){
      if(session.cancelled){await session.closing;return null;}
      await this.cancelNative().catch(()=>undefined);
      throw error;
    }finally{
      this.current=undefined;this.show(false);
    }
  }

  async cancel():Promise<void>{
    const session=this.current;if(!session)return;
    if(session.closing)return session.closing;
    session.cancelled=true;
    session.closing=this.cancelNative().then(()=>{session.stopped(null);}).catch(error=>{
      session.cancelled=false;session.closing=null;throw error;
    });
    return session.closing;
  }
}
