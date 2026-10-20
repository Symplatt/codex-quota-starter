const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/ui.html','utf8').split('<script>')[1].split('</script>')[0];
function setup(storage={},preferences={}){
  const elements=new Map();
  function element(){return {value:'',textContent:'',disabled:false,children:[],options:[],attributes:{},setAttribute(key,value){this.attributes[key]=value;},querySelector(){return null;},addEventListener(){},append(child){this.children.push(child);this.options.push(child);if(!this.value)this.value=child.value;},replaceChildren(){this.children=[];},showModal(){},close(){}};}
  const intervals=[];let response,requests=0;
  const c=vm.createContext({document:{getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id);},createElement:element,createElementNS:element},Intl,Date,JSON,AbortController,setTimeout,clearTimeout,setInterval(f){intervals.push(f);},sessionStorage:{getItem:k=>storage[k]||null,setItem:(k,v)=>storage[k]=v},localStorage:{getItem:k=>preferences[k]||null,setItem:(k,v)=>preferences[k]=v},fetch:()=>{requests++;return new Promise(resolve=>response=resolve);}});
  vm.runInContext(source,c);
  return {c,e:id=>elements.get(id),run:code=>vm.runInContext(code,c),respond:async s=>{response({ok:true,json:async()=>s});await vm.runInContext('refreshPromise',c);},requests:()=>requests,storage};
}
function status(){const at=Date.parse('2090-10-09T19:09:00+08:00')/1000;return {service:'quota-starter',version:'1.4.5',paused:false,busy:false,status:'等待重置时间',current:{primary:{usedPercent:20,resetsAt:at},checkedAt:at-3600},timing:{serverResetAt:at,minSendAt:at+1,minRefreshAt:at+18060,maxRefreshAt:at+366*86400,sendAt:at+2,estimatedResetAt:at+18002,schedule:null},nextPoll:at,attempts:[],events:[],autostart:{available:true,enabled:true}};}

test('error banner shows current failures safely and disappears after recovery',async()=>{
 const p=setup(),s=status();await p.respond(s);assert.equal(p.e('error-banner').hidden,true);
 s.connection={stopped:true,upgradeRequired:true,error:'Codex CLI 版本过低（0.144.4） <script>test</script>'};
 p.run('refresh()');await p.respond(s);
 assert.equal(p.e('error-banner').hidden,false);assert.equal(p.e('error-banner').textContent,s.connection.error);
 assert.equal(p.e('status').textContent,'Codex CLI 需要升级');
 p.run('refresh()');await p.respond({...status(),events:[{kind:'connection_failure',detail:{error:'old'}}]});
 assert.equal(p.e('error-banner').hidden,true);assert.equal(p.e('error-banner').textContent,'');
 await p.run('fetch=async()=>{throw Error("offline")};refresh();');
 assert.equal(p.e('error-banner').hidden,false);assert.match(p.e('error-banner').textContent,/本机后台/);
});

test('drag snaps off-grid minutes in the movement direction and respects bounds',async()=>{
 const p=setup(),s=status(),base=Math.floor(s.timing.minRefreshAt/600)*600+3600;
 s.timing.minRefreshAt=base-1800;s.timing.maxRefreshAt=base+86400;await p.respond(s);
 p.c.initial=base-18000+3*60;
 assert.equal(p.run('dragSendTarget(initial,-1)'),base-18000);
 assert.equal(p.run('dragSendTarget(initial,1)'),base-18000+600);
 assert.equal(p.run('dragSendTarget(initial,601)'),base-18000+1200);
 assert.equal(p.run('dragSendTarget(initial,-601)'),base-18000-600);
 assert.equal(p.run('dragSendTarget(initial,0)'),p.c.initial);
 assert.equal(p.run('dragSendTarget(initial,1e12)'),Math.floor((s.timing.maxRefreshAt-18000)/600)*600);
 assert.equal(p.run('dragSendTarget(initial,-1e12)'),Math.ceil((s.timing.minRefreshAt-18000)/600)*600);
});

test('timeline handle follows live availability including stale sessions and pending saves',async()=>{
 const p=setup(),s=status();await p.respond(s);assert.equal(p.e('timeline-handle').disabled,false);
 p.run("pendingActions.add('schedule');updateControls()");assert.equal(p.e('timeline-handle').disabled,true);
 p.run("pendingActions.clear();connected=false;updateControls()");assert.equal(p.e('timeline-handle').disabled,true);
});

test('minute chart keeps gaps unknown and reports consumption separately from recovery',async()=>{
 const p=setup(),s=status();await p.respond(s);
 p.c.points=[{minute:0,primaryRemaining:90},{minute:60,primaryRemaining:88},{minute:180,primaryRemaining:85},{minute:240,primaryRemaining:100}];
 const values=JSON.parse(p.run("JSON.stringify(chartData(points,0,240,'primaryRemaining'))"));
 assert.deepEqual(values.map(v=>v.delta),[null,-2,null,15]);
 assert.equal(p.run("chartData(points,0,240,'secondaryRemaining').length"),0);
 s.quotaHistory=[{minute:Math.floor(Date.now()/60000)*60,primaryRemaining:60,secondaryRemaining:null}];
 p.run('refresh()');await p.respond(s);
 assert.equal(p.e('chart-empty').hidden,true);assert.match(p.e('quota-chart-svg').attributes['aria-label'],/已记录 1 分钟/);
 assert.ok(p.e('quota-chart-svg').children.some(c=>c.attributes.fill==='#315c43'));
 p.e('chart-quota').value='secondaryRemaining';p.run('renderChart()');assert.equal(p.e('chart-empty').hidden,false);
});

test('smooth line preserves steep drops, extrema, and disconnect gaps',async()=>{
 const p=setup();await p.respond(status());
 p.c.points=[{minute:0,value:90,delta:null},{minute:60,value:89,delta:-1},{minute:120,value:60,delta:-29},{minute:180,value:61,delta:1},{minute:300,value:40,delta:null}];
 const paths=JSON.parse(p.run('JSON.stringify(chartSegments(points,v=>v,v=>v))'));
 assert.equal(paths.length,3);
 assert.match(paths[1].d,/M60 89 C/);assert.ok(paths[1].d.endsWith(',120 60'));
 // Every control stays between the true endpoints; there is no invented peak/trough.
 for(let i=0;i<paths.length;i++){
  const numbers=paths[i].d.match(/-?[\d.]+/g).map(Number),min=Math.min(p.c.points[i].value,p.c.points[i+1].value),max=Math.max(p.c.points[i].value,p.c.points[i+1].value);
  assert.ok(numbers[3]>=min&&numbers[3]<=max);assert.ok(numbers[5]>=min&&numbers[5]<=max);
 }
 const scale=JSON.parse(p.run('JSON.stringify(chartScale(points))'));assert.ok(scale.low<=40&&scale.high>=90);
 p.c.points=[{value:99},{value:98}];const close=JSON.parse(p.run('JSON.stringify(chartScale(points))'));
 assert.ok(close.high-close.low>=20);assert.equal(close.high,100);assert.ok(close.step<=5);
});

test('consolidated log omits routine reads and preserves small request evidence',async()=>{
 const p=setup(),s=status();s.activity=[{time:s.nextPoll,kind:'check',detail:{}},{time:s.nextPoll-1,kind:'trigger',detail:{status:'completed',evidence:'周期已核验'}},{time:s.nextPoll-2,kind:'trigger',detail:{status:'reserved'}}];
 await p.respond(s);const rows=p.e('activity').children.map(r=>r.children.map(c=>c.textContent));
 assert.equal(rows.length,2);assert.equal(rows[0][1],'发送小额请求');assert.match(rows[0][2],/已完成\n周期已核验/);assert.match(rows[1][2],/待核实/);
 assert.equal(p.e('history'),undefined);
});

test('paused sampling setting reflects server state and locks offline controls',async()=>{
 const p=setup(),s=status();await p.respond(s);assert.equal(p.e('poll-while-paused').checked,false);
 s.pollWhilePaused=true;p.run('refresh()');await p.respond(s);assert.equal(p.e('poll-while-paused').checked,true);
 p.run('showOffline()');assert.equal(p.e('poll-while-paused').disabled,true);
});

test('paused badge never claims background running even when still connected',async()=>{
 const p=setup(),s=status();s.linked=true;s.paused=true;await p.respond(s);
 assert.equal(p.e('badge').textContent,'停止运行');
 s.busy=true;p.run('refresh()');await p.respond(s);assert.equal(p.e('badge').textContent,'停止运行');
 s.paused=false;s.busy=false;p.run('refresh()');await p.respond(s);assert.equal(p.e('badge').textContent,'后台运行');
 s.connection={recoveryAt:Date.now()/1000+1200};p.run('refresh()');await p.respond(s);assert.equal(p.e('badge').textContent,'等待重连');
 s.connection={stopped:true};p.run('refresh()');await p.respond(s);assert.equal(p.e('badge').textContent,'已断开');
});

test('chart selections survive reload and a new browser session',async()=>{
 const preferences={},p=setup({},preferences);await p.respond(status());
 p.e('chart-window').value='1440';p.e('chart-quota').value='secondaryRemaining';p.run('rememberChartPreferences()');
 const reopened=setup({},preferences);await reopened.respond(status());
 assert.equal(reopened.e('chart-window').value,'1440');assert.equal(reopened.e('chart-quota').value,'secondaryRemaining');
 reopened.e('chart-window').value='60';reopened.run('rememberChartPreferences()');
 const again=setup({},preferences);await again.respond(status());assert.equal(again.e('chart-window').value,'60');assert.equal(again.e('chart-quota').value,'secondaryRemaining');
});

test('invalid or unavailable chart storage never breaks the dashboard',async()=>{
 const preferences={'quota-starter:chart-preferences:v1':'{"window":"invalid","quota":"invalid"}'},p=setup({},preferences);await p.respond(status());
 assert.notEqual(p.e('chart-window').value,'invalid');assert.notEqual(p.e('chart-quota').value,'invalid');
 p.run('localStorage.setItem=()=>{throw Error("blocked")};rememberChartPreferences()');assert.ok(p.e('quota-chart-svg').children.length>0);
 const corrupt=setup({},{'quota-starter:chart-preferences:v1':'{broken'});await corrupt.respond(status());assert.equal(corrupt.e('version').textContent,'v1.4.5');
});

test('startup and operation errors use banner without showing normal pause as error',async()=>{
 const p=setup(),s=status();s.paused=true;await p.respond(s);assert.equal(p.e('error-banner').hidden,true);
 s.autostart.error='无法设置自启';p.run('refresh()');await p.respond(s);
 assert.equal(p.e('error-banner').textContent,'无法设置自启');
 p.run("operationError='操作失败';refresh()");await p.respond(status());
 assert.equal(p.e('error-banner').textContent,'操作失败');
 p.run("operationError='';refresh()");await p.respond(status());assert.equal(p.e('error-banner').hidden,true);
});
test('timeline places near-immediate sends visibly and keeps exact cross-day times',async()=>{
 const p=setup(),s=status();await p.respond(s);
 assert.equal(p.e('timeline-reset-at').textContent,'19:09:00');
 assert.equal(p.e('send-at').textContent,'19:09:02');
 assert.equal(p.e('estimated-reset').textContent,'00:09:02');
 assert.equal(p.e('reset-date').textContent,'2090/10/10');
 assert.match(p.e('schedule-timeline').attributes.style,/--send-position:14%/);
 assert.equal(p.e('timeline-break').hidden,true);
});
test('timeline compresses long waits without changing timestamps and removes break after edits',async()=>{
 const p=setup(),s=status(),at=s.timing.serverResetAt;
 s.timing.sendAt=at+300*86400;s.timing.estimatedResetAt=s.timing.sendAt+18000;
 await p.respond(s);
 assert.equal(p.e('timeline-break').hidden,false);
 assert.match(p.e('schedule-timeline').attributes.style,/--send-position:50%/);
 assert.equal(p.e('send-date').textContent,p.run(`time(${s.timing.sendAt}).split(' ')[0]`));
 assert.ok(p.e('timeline-break').title.includes(p.run(`time(${s.timing.sendAt})`)));
 s.timing.sendAt=at+3600;s.timing.estimatedResetAt=at+21600;
 p.run('refresh()');await p.respond(s);
 assert.equal(p.e('timeline-break').hidden,true);
 const position=Number(p.e('schedule-timeline').attributes.style.match(/position:([\d.]+)/)[1]);
 assert.ok(Math.abs(position-(10+.8*100/6))<0.001);
 s.timing.sendAt=at+18000;s.timing.estimatedResetAt=at+36000;
 p.run('refresh()');await p.respond(s);assert.equal(p.e('timeline-break').hidden,true);
});
test('timeline tolerates missing times and retains all three events offline',async()=>{
 const p=setup(),s=status();s.current=null;s.timing={sendAt:null,serverResetAt:null,estimatedResetAt:null};
 await p.respond(s);assert.equal(p.e('timeline-reset-at').textContent,'—');assert.equal(p.e('timeline-break').hidden,true);
 p.run('refresh()');await p.respond(status());
 await p.run('fetch=async()=>{throw Error("offline")};refresh();');
 assert.equal(p.e('timeline-reset-at').textContent,'19:09:00');assert.equal(p.e('send-at').textContent,'19:09:02');
});
test('hour labels follow Beijing time across midnight and become sparser in narrow cards',async()=>{
 const p=setup(),s=status(),at=Date.parse('2090-10-09T20:00:00+08:00')/1000;
 s.timing={...s.timing,serverResetAt:at,sendAt:at+2,estimatedResetAt:at+18002};
 await p.respond(s);
 const hours=()=>p.e('timeline-ticks').children.flatMap(t=>t.children.map(l=>l.textContent));
 assert.deepEqual(hours(),['20:00','21:00','22:00','23:00','00:00','01:00']);
 p.e('timeline-rail').clientWidth=160;p.run('renderTimeline(snapshot.timing)');
 assert.ok(hours().length<4);assert.ok(hours().every(h=>h.endsWith(':00')));
 p.e('timeline-rail').clientWidth=560;s.timing.sendAt=at+5*86400;s.timing.estimatedResetAt=s.timing.sendAt+18000;
 p.run('refresh()');await p.respond(s);
 assert.equal(p.e('timeline-break').hidden,false);
 assert.ok(p.e('timeline-ticks').children.length<80);
 assert.ok(hours().includes('00:00'));
});
test('restore automatic is disabled without a pending manual schedule',async()=>{
 const p=setup(),s=status();await p.respond(s);assert.equal(p.e('auto-time').disabled,true);
 s.timing.mode='manual';s.timing.schedule={status:'pending',sendAt:s.timing.sendAt};
 p.run('refresh()');await p.respond(s);assert.equal(p.e('auto-time').disabled,false);
 s.timing.mode='auto';s.timing.schedule.status='cancelled';
 p.run('refresh()');await p.respond(s);assert.equal(p.e('auto-time').disabled,true);
});
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

test('network cooldown displays automatic recovery and retains schedule',async()=>{
 const p=setup();const s={...status(),linked:false,connection:{stopped:false,failures:5,recoveryAt:12345}};
 await p.respond(s);
 assert.equal(p.e('status').textContent,'等待自动重连');
 assert.equal(p.e('badge').textContent,'等待重连');
 assert.equal(p.e('countdown').textContent,'等待自动重连，计划保留');
 assert.notEqual(p.e('send-at').textContent,'—');
 assert.equal(p.e('reconnect').disabled,false);
 p.run('refresh()');await p.respond({...status(),linked:true});
 assert.equal(p.e('badge').textContent,'后台运行');
 assert.match(p.e('countdown').textContent,/倒计时/);
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

test('sending status updates live quota while showing connected state',async()=>{
 const p=setup();await p.respond(status());
 p.run('refresh()');const s={...status(),linked:true,busy:true,phase:'sending'};
 s.current.primary.usedPercent=40;await p.respond(s);
 assert.equal(p.e('remaining').textContent,'60%');assert.equal(p.e('badge').textContent,'后台运行');
 assert.equal(p.e('status').textContent,'正在发送小额请求');assert.equal(p.e('reconnect').disabled,true);
 assert.equal(p.e('pause').disabled,false);
});

test('pending schedule cannot be resubmitted or reenabled by polling',async()=>{
 const p=setup();await p.respond(status());
 p.run("draftRefresh=snapshot.timing.minRefreshAt;refreshDialog.open=true;globalThis.firstAction=action('schedule','refresh-error',{refreshAt:draftRefresh});");
 assert.equal(p.e('save-time').disabled,true);
 await p.run("action('schedule','refresh-error',{refreshAt:draftRefresh})");assert.equal(p.requests(),2);
 p.c.next=status();p.run('renderStatus(next)');assert.equal(p.e('save-time').disabled,true);
 // Complete the POST, then the follow-up status poll.
 await p.respond({message:'saved'});await new Promise(resolve=>setImmediate(resolve));
 await p.respond(status());await p.run('firstAction');
 assert.equal(p.e('save-time').disabled,false);
});

test('invalid status disables stale controls without erasing data',async()=>{
 const p=setup();await p.respond(status());p.run('refresh()');await p.respond({service:'unexpected'});
 assert.equal(p.e('remaining').textContent,'80%');assert.equal(p.e('reconnect').disabled,true);
 assert.equal(p.e('pause').disabled,true);
});

test('rejected replacement token is never retried a third time',async()=>{
 const p=setup();await p.respond(status());p.c.next=status();
 await p.run("globalThis.postCount=0;fetch=async(url,options)=>{if(url==='/api/session')return {ok:true,json:async()=>({token:'another'})};if(options?.method==='POST'){postCount++;return {ok:false,status:403,json:async()=>({code:'stale_session',error:'expired'})};}return {ok:true,json:async()=>next};};action('schedule');");
 assert.equal(p.run('postCount'),2);
});

test('operation toasts expire and old timers cannot erase a replacement',async()=>{
 const p=setup();await p.respond(status());
 p.run("globalThis.feedbackClock=new Map();globalThis.timerId=0;setTimeout=(f,ms)=>{feedbackClock.set(++timerId,{f,ms});return timerId;};clearTimeout=id=>feedbackClock.delete(id);");
 p.run("showFeedback('toast','正在重新连接',true)");
 assert.equal(p.e('toast').textContent,'正在重新连接');
 assert.equal(p.run('[...feedbackClock.values()][0].ms'),3500);
 p.run("showFeedback('toast','检测失败',false)");
 assert.equal(p.run('feedbackClock.size'),1);
 assert.equal(p.run('[...feedbackClock.values()][0].ms'),7000);
 p.run('[...feedbackClock.values()][0].f()');assert.equal(p.e('toast').textContent,'');
 p.run("showFeedback('refresh-error','请选择有效时间')");
 assert.equal(p.e('refresh-error').textContent,'请选择有效时间');
 assert.equal(p.run('feedbackTimers.size'),0);
});

test('persistent activity shows schedule changes and connection events alongside trigger records',async()=>{
 const p=setup(),s=status(),at=s.current.checkedAt;
 s.attempts=[{started:at,status:'completed',detail:{evidence:'周期已核验'}}];
 s.events=[{time:at,kind:'check',detail:{primary:s.current.primary}}];
 s.activity=[
  {time:at,kind:'schedule',detail:{action:'update',previous:{sendAt:at+1800},sendAt:at+3600,refreshAt:at+21600}},
  {time:at-1,kind:'schedule',detail:{action:'clear',previous:{sendAt:at+1800}}},
  {time:at-2,kind:'control',detail:{action:'check'}},
  {time:at-3,kind:'control',detail:{action:'autostart/enable',result:{error:'无法写入设置'}}},
  {time:at-4,kind:'connection_failure',detail:{error:'读取超时',failures:1,nextAt:at+10}},
  {time:at-5,kind:'connection_recovered',detail:{durationSeconds:12}},
 ];
 await p.respond(s);
 const rows=p.e('activity').children.map(row=>row.children.map(cell=>cell.textContent));
 assert.equal(rows.length,6);assert.equal(rows[0][1],'修改定时请求');
 assert.match(rows[0][2],/原发送时间：.*\n发送时间：.*\n预计刷新：/);
 assert.ok(rows[0][2].includes(p.run(`time(${at+21600})`)));
 assert.equal(rows[1][1],'取消定时请求，恢复自动');assert.match(rows[1][2],/原发送时间/);
 assert.equal(rows[2][1],'重新检测');assert.match(rows[3][2],/操作失败：无法写入设置/);
 assert.match(rows[4][2],/错误：读取超时\n失败次数：1\n下次尝试：/);
 assert.equal(rows[5][1],'连接已恢复');assert.equal(rows[5][2],'耗时：12 秒');
 assert.equal(p.e('history'),undefined);
 assert.equal(p.e('logs'),undefined);
});

test('activity reads old schedule records and excludes routine checks',async()=>{
 const p=setup(),s=status(),at=s.current.checkedAt;
 s.events=[{time:at,kind:'check',detail:{}},{time:at-1,kind:'schedule',detail:null},{time:at-2,kind:'schedule',detail:{sendAt:at+3600}}];
 await p.respond(s);
 const rows=p.e('activity').children.map(row=>row.children.map(cell=>cell.textContent));
 assert.equal(rows.length,2);assert.equal(rows[0][1],'取消定时请求，恢复自动');
 assert.equal(rows[1][1],'设置定时请求');assert.match(rows[1][2],/发送时间：.*\n预计刷新：/);
});

test('quota rings show live values, clamp percentages and handle missing weekly data',async()=>{
 const p=setup(),s=status();s.current.secondary={usedPercent:68,windowDurationMins:10080};await p.respond(s);
 assert.equal(p.e('weekly').textContent,'32%');
 assert.equal(p.e('weekly-ring').attributes.style,'--progress:32');
 assert.equal(p.e('weekly-copy').textContent,'近 7 天剩余额度占比');
 p.run("renderQuota('weekly',null)");assert.equal(p.e('weekly').textContent,'—');
 assert.equal(p.e('weekly-ring').attributes.style,'--progress:0');
 p.run("renderQuota('remaining',{usedPercent:150})");assert.equal(p.e('remaining').textContent,'0%');
 assert.equal(p.e('remaining-copy').textContent,'额度已用尽');
});
