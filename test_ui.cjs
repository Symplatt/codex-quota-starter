const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/ui.html','utf8').split('<script>')[1].split('</script>')[0];
function setup(storage={}){
  const elements=new Map();
  function element(){return {value:'',textContent:'',disabled:false,children:[],options:[],setAttribute(){},querySelector(){return null;},addEventListener(){},append(child){this.children.push(child);this.options.push(child);if(!this.value)this.value=child.value;},replaceChildren(){this.children=[];},showModal(){},close(){}};}
  const intervals=[];let response,requests=0;
  const c=vm.createContext({document:{getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id);},createElement:element},Intl,Date,JSON,AbortController,setTimeout,clearTimeout,setInterval(f){intervals.push(f);},sessionStorage:{getItem:k=>storage[k]||null,setItem:(k,v)=>storage[k]=v},fetch:()=>{requests++;return new Promise(resolve=>response=resolve);}});
  vm.runInContext(source,c);
  return {c,e:id=>elements.get(id),run:code=>vm.runInContext(code,c),respond:async s=>{response({ok:true,json:async()=>s});await vm.runInContext('refreshPromise',c);},requests:()=>requests,storage};
}
function status(){const at=Date.parse('2090-10-09T19:09:00+08:00')/1000;return {service:'quota-starter',version:'1.4.5',paused:false,busy:false,status:'等待重置时间',current:{primary:{usedPercent:20,resetsAt:at},checkedAt:at-3600},timing:{serverResetAt:at,minSendAt:at+1,minRefreshAt:at+18060,maxRefreshAt:at+366*86400,sendAt:at+2,estimatedResetAt:at+18002,schedule:null},nextPoll:at,attempts:[],events:[],autostart:{available:true,enabled:true}};}
test('slow polling keeps data, countdown and serializes requests',async()=>{
 const p=setup();await p.respond(status());const count=p.e('countdown').textContent;
 p.run('refresh();refresh();refresh();');assert.equal(p.requests(),2);assert.equal(p.e('remaining').textContent,'80%');
 p.run('Date.now=()=>Date.parse("2090-10-09T18:09:00+08:00");countdown();');assert.notEqual(p.e('countdown').textContent,count);
 const next=status();next.current.primary.usedPercent=30;await p.respond(next);assert.equal(p.e('remaining').textContent,'70%');
});
test('busy and empty interim snapshots keep complete timing',async()=>{
 const p=setup();await p.respond(status());const send=p.e('send-at').textContent;
 p.run('refresh()');await p.respond({...status(),busy:true,current:null,timing:{sendAt:null}});
 assert.equal(p.e('remaining').textContent,'80%');assert.equal(p.e('send-at').textContent,send);assert.equal(p.e('recheck').disabled,true);
});
test('failure preserves values and recovery restores controls',async()=>{
 const p=setup();await p.respond(status());await p.run('fetch=async()=>{throw Error("offline")};refresh();');
 assert.equal(p.e('remaining').textContent,'80%');assert.equal(p.e('status').textContent,'等待重连');
 p.c.next=status();await p.run('fetch=async()=>({ok:true,json:async()=>next});refresh();');assert.equal(p.e('recheck').disabled,false);
});
test('reload restores session display without enabling stale actions',async()=>{
 const p=setup();await p.respond(status());const q=setup(p.storage);
 assert.equal(q.e('remaining').textContent,'80%');assert.equal(q.e('recheck').disabled,true);await q.respond(status());
});
test('refresh picker disables invalid minutes and converts cross-day time',async()=>{
 const p=setup();await p.respond(status());
 p.run('draftRefresh=snapshot.timing.minRefreshAt;renderWheels();');
 assert.equal(p.run('inputTime(draftRefresh)'), '2090-10-10T00:10:00');
 assert.equal(p.run('candidates(4).filter(x=>x.disabled).length'),10);
 p.run('stepWheel(4,-1)');assert.equal(p.run('parts(draftRefresh)[4]'),10);
 p.run('stepWheel(4,1)');assert.equal(p.run('parts(draftRefresh)[4]'),11);
});
test('changing months clamps month-end and handles year rollover',async()=>{
 const p=setup();await p.respond(status());
 p.run("draftRefresh=stamp([2091,0,31,12,0]);selectPart(1,candidates(1).find(x=>x.value===1));");
 assert.equal(p.run('inputTime(draftRefresh)'), '2091-02-28T12:00:00');
});
test('open draft survives status polling and upper bound disables later values',async()=>{
 const p=setup();await p.respond(status());
 p.run('draftRefresh=snapshot.timing.minRefreshAt+3600;refreshDialog.open=true;renderWheels();');
 const draft=p.run('draftRefresh');p.run('refresh()');await p.respond(status());assert.equal(p.run('draftRefresh'),draft);
 p.run('draftRefresh=snapshot.timing.maxRefreshAt;renderWheels();');
 assert.ok(p.run('candidates(4).some(x=>x.disabled)'));
});

test('manual date preserves time and rejects invalid or out-of-range dates',async()=>{
 const p=setup();await p.respond(status());
 p.run('draftRefresh=snapshot.timing.minRefreshAt+3600;syncManualDate();');
 ['2091','02','28','01','10'].forEach((v,i)=>p.e('manual-'+['year','month','day','hour','minute'][i]).value=v);p.run('manualDirty=true;applyManualDate();');
 assert.equal(p.run('inputTime(draftRefresh)'), '2091-02-28T01:10:00');
 for(const value of ['2091-02-29','2091-13-01','2090-01-01','2091-02-']){
  value.split('-').forEach((v,i)=>p.e('manual-'+['year','month','day'][i]).value=v);p.run('manualDirty=true;applyManualDate();renderWheels();');
  assert.equal(p.e('save-time').disabled,true);
  assert.equal(p.run('inputTime(draftRefresh)'), '2091-02-28T01:10:00');
 }
 p.run('stepWheel(4,1);');assert.equal(p.e('manual-day').value,'28');assert.equal(p.run('manualDirty'),false);
});

test('manual hour and minute synchronize and reject overflow',async()=>{
 const p=setup();await p.respond(status());p.run('draftRefresh=snapshot.timing.minRefreshAt;syncManualDate();');
 p.e('manual-hour').value='13';p.e('manual-minute').value='45';p.run('manualDirty=true;applyManualDate();');
 assert.equal(p.run('inputTime(draftRefresh)'), '2090-10-10T13:45:00');
 for(const [key,value] of [['hour','24'],['minute','60']]){p.run('syncManualDate();');p.e('manual-'+key).value=value;p.run('manualDirty=true;applyManualDate();');assert.equal(p.e('save-time').disabled,true);}
});

test('quota recheck stays available while paused and never calls the trigger endpoint',async()=>{
 const p=setup();const state={...status(),paused:true};await p.respond(state);
 assert.equal(p.e('recheck').disabled,false);
 const calls=[];p.c.calls=calls;p.c.next=state;
 await p.run("fetch=async (url,options)=>{calls.push([url,options?.method||'GET']);return {ok:true,json:async()=>options?.method==='POST'?{message:'当前额度已更新'}:next}};recheckQuota();");
 assert.deepEqual(calls.filter(([url,method])=>method==='POST').map(([url])=>url),['/api/check']);
 assert.equal(p.e('recheck').disabled,false);
 await p.run("fetch=async()=>{throw Error('offline')};recheckQuota();");
 assert.equal(p.e('recheck').disabled,true);
});

test('stopped connection without quota still exposes reconnect',async()=>{
 const p=setup();const s=status();s.current=null;s.connection={stopped:true};s.nextPoll=null;s.timing.sendAt=null;s.timing.estimatedResetAt=null;await p.respond(s);
 assert.equal(p.e('badge').textContent,'已断开');assert.equal(p.e('reconnect').disabled,false);assert.equal(p.e('recheck').disabled,true);
 assert.equal(p.e('countdown').textContent,'已停止，等待重新连接');
});
test('reconnect uses its own endpoint',async()=>{
 const p=setup();await p.respond(status());const calls=[];p.c.calls=calls;p.c.next=status();
 await p.run("fetch=async(url,options)=>{calls.push(url);return {ok:true,json:async()=>options?.method==='POST'?{message:'正在重新连接'}:next}};action('reconnect');");
 assert.equal(calls[0],'/api/reconnect');
});

test('attempt counter includes initial try in five-attempt limit',async()=>{
 const p=setup();await p.respond({...status(),connection:{failures:4}});
 assert.equal(p.e('status').textContent,'等待第 5/5 次尝试');
});

test('expired session refreshes token and retries same draft once',async()=>{
 const p=setup();await p.respond(status());const calls=[];p.c.calls=calls;p.c.next=status();
 await p.run(`let posts=0;fetch=async(url,options)=>{
 calls.push({url,body:options?.body,token:options?.headers?.['X-Quota-Token']});
 if(url==='/api/session')return {ok:true,json:async()=>({token:'new-token'})};
 if(options?.method==='POST'){posts++;return posts===1?{ok:false,status:403,json:async()=>({code:'stale_session'})}:{ok:true,json:async()=>({message:'已保存时间'})};}
 return {ok:true,json:async()=>next};};action('schedule','refresh-error',{refreshAt:12345});`);
 const posts=calls.filter(c=>c.url==='/api/schedule');assert.equal(posts.length,2);assert.equal(posts[0].body,posts[1].body);assert.equal(posts[1].token,'new-token');
 assert.equal(p.e('refresh-error').textContent,'已保存时间');
});
test('origin rejection is not retried',async()=>{
 const p=setup();await p.respond(status());const calls=[];p.c.calls=calls;p.c.next=status();
 await p.run(`fetch=async(url,options)=>{calls.push(url);return options?.method==='POST'?{ok:false,status:403,json:async()=>({error:'请求来源校验失败'})}:{ok:true,json:async()=>next}};action('schedule');`);
 assert.equal(calls.filter(u=>u==='/api/schedule').length,1);assert.ok(!calls.includes('/api/session'));
});
