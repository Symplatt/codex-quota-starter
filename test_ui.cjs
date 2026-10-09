const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/ui.html','utf8').split('<script>')[1].split('</script>')[0];
function setup(storage={}){
  const elements=new Map();
  function element(){return {value:'',textContent:'',disabled:false,children:[],options:[],addEventListener(){},append(child){this.children.push(child);this.options.push(child);if(!this.value)this.value=child.value;},replaceChildren(){this.children=[];},showModal(){},close(){}};}
  const intervals=[];let response,requests=0;
  const c=vm.createContext({document:{getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id);},createElement:element},Intl,Date,JSON,AbortController,setTimeout,clearTimeout,setInterval(f){intervals.push(f);},sessionStorage:{getItem:k=>storage[k]||null,setItem:(k,v)=>storage[k]=v},fetch:()=>{requests++;return new Promise(resolve=>response=resolve);}});
  vm.runInContext(source,c);
  return {c,e:id=>elements.get(id),run:code=>vm.runInContext(code,c),respond:async s=>{response({ok:true,json:async()=>s});await vm.runInContext('refreshPromise',c);},requests:()=>requests,storage};
}
function status(){const at=Date.parse('2090-10-09T19:09:00+08:00')/1000;return {service:'quota-starter',version:'1.3.0',paused:false,busy:false,status:'等待重置时间',current:{primary:{usedPercent:20,resetsAt:at},checkedAt:at-3600},timing:{serverResetAt:at,minSendAt:at+1,sendAt:at+2,estimatedResetAt:at+18002,schedule:null},nextPoll:at,attempts:[],events:[],autostart:{available:true,enabled:true}};}
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
test('picker disables earlier hours, minutes, seconds and dates',async()=>{
 const p=setup();await p.respond(status());
 assert.equal(p.e('schedule-day').min,'2090-10-09');
 assert.equal(p.e('schedule-hour').options.filter(o=>o.disabled).length,19);
 assert.equal(p.e('schedule-minute').options.filter(o=>o.disabled).length,9);
 assert.equal(p.e('schedule-second').options.filter(o=>o.disabled).length,1);
 assert.equal(p.e('schedule-time').value,'2090-10-09T19:09:01');
 p.e('schedule-day').value='2090-10-10';p.run('syncPicker(true)');assert.equal(p.e('schedule-hour').options.filter(o=>o.disabled).length,0);
});
test('typed draft survives refresh and tomorrow boundary rolls over',async()=>{
 const p=setup();await p.respond(status());p.e('schedule-day').value='2090-10-10';p.e('schedule-hour').value='20';p.run('timeDirty=true;syncPicker(true)');
 const draft=p.e('schedule-time').value;p.run('refresh()');await p.respond(status());assert.equal(p.e('schedule-time').value,draft);
 p.c.next=status();p.c.next.timing.serverResetAt=Date.parse('2090-10-10T23:59:59+08:00')/1000;p.run('timeDirty=false;lastScheduleId=undefined;renderStatus(next)');assert.equal(p.e('schedule-day').min,'2090-10-11');
});
