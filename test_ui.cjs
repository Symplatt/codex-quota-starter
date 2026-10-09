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
function status(){const at=Date.parse('2090-10-09T19:09:00+08:00')/1000;return {service:'quota-starter',version:'1.4.4',paused:false,busy:false,status:'等待重置时间',current:{primary:{usedPercent:20,resetsAt:at},checkedAt:at-3600},timing:{serverResetAt:at,minSendAt:at+1,minRefreshAt:at+18060,maxRefreshAt:at+366*86400,sendAt:at+2,estimatedResetAt:at+18002,schedule:null},nextPoll:at,attempts:[],events:[],autostart:{available:true,enabled:true}};}
test('slow polling keeps data, countdown and serializes requests',async()=>{
 const p=setup();await p.respond(status());const count=p.e('countdown').textContent;
 p.run('refresh();refresh();refresh();');assert.equal(p.requests(),2);assert.equal(p.e('remaining').textContent,'80%');
 p.run('Date.now=()=>Date.parse("2090-10-09T18:09:00+08:00");countdown();');assert.notEqual(p.e('countdown').textContent,count);
 const next=status();next.current.primary.usedPercent=30;await p.respond(next);assert.equal(p.e('remaining').textContent,'70%');
});
test('busy and empty interim snapshots keep complete timing',async()=>{
 const p=setup();await p.respond(status());const send=p.e('send-at').textContent;
 p.run('refresh()');await p.respond({...status(),busy:true,current:null,timing:{sendAt:null}});
 assert.equal(p.e('remaining').textContent,'80%');assert.equal(p.e('send-at').textContent,send);assert.equal(p.e('trigger').disabled,true);
});
test('failure preserves values and recovery restores controls',async()=>{
 const p=setup();await p.respond(status());await p.run('fetch=async()=>{throw Error("offline")};refresh();');
 assert.equal(p.e('remaining').textContent,'80%');assert.equal(p.e('status').textContent,'等待重连');
 p.c.next=status();await p.run('fetch=async()=>({ok:true,json:async()=>next});refresh();');assert.equal(p.e('trigger').disabled,false);
});
test('reload restores session display without enabling stale actions',async()=>{
 const p=setup();await p.respond(status());const q=setup(p.storage);
 assert.equal(q.e('remaining').textContent,'80%');assert.equal(q.e('trigger').disabled,true);await q.respond(status());
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
 p.e('manual-date').value='2091-02-28';p.run('manualDirty=true;applyManualDate();');
 assert.equal(p.run('inputTime(draftRefresh)'), '2091-02-28T01:10:00');
 for(const value of ['2091-02-29','2091-13-01','2090-01-01','2091-02-']){
  p.e('manual-date').value=value;p.run('manualDirty=true;applyManualDate();renderWheels();');
  assert.equal(p.e('save-time').disabled,true);
  assert.equal(p.run('inputTime(draftRefresh)'), '2091-02-28T01:10:00');
 }
 p.run('stepWheel(4,1);');assert.equal(p.e('manual-date').value,'2091-02-28');assert.equal(p.run('manualDirty'),false);
});
