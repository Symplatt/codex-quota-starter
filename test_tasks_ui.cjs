const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/tasks.js','utf8');

async function setup(initial={tasks:[],settings:{directory:'E:\\tasks',models:['gpt-6-astra','gpt-6.1-sol']}}, hook){
  const elements=new Map(),calls=[],intervals=[],listeners={};let scroll=0;
  const tops={'schedule-panel':140,'quota-chart-panel':550,'tasks-panel':1000,'activity-panel':1700,'settings-panel':3000};
  function element(id=''){
    return {id,value:'',textContent:'',children:[],style:{},dataset:{},attributes:{},events:{},open:false,
      classList:{toggle(){}},append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=nodes;},
      addEventListener(name,callback){this.events[name]=callback;},setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];},
      getBoundingClientRect(){return {top:(tops[id]||0)-scroll,left:100,right:700,bottom:600};},
      showModal(){this.open=true;},close(){this.open=false;},reset(){for(const [k,v] of Object.entries({'task-mode':'time','task-source':'new','task-effort':'medium','task-path':'','task-content':'','task-cwd':'','task-title':''}))get(k).value=v;}};
  }
  const get=id=>{if(!elements.has(id))elements.set(id,element(id));return elements.get(id);};
  const nodes=Object.keys(tops).map(id=>{const n=element();n.dataset.target=id;return n;});
  let data=initial;
  const window={innerHeight:900,get scrollY(){return scroll;},matchMedia:()=>({matches:false}),
    scrollTo(options){scroll=options.top;window.lastScroll=options;listeners.scroll?.();},addEventListener(n,fn){listeners[n]=fn;}};
  const context=vm.createContext({token:'old',document:{getElementById:get,createElement:element,createElementNS:(_, tag)=>element(tag),
    body:element(),documentElement:{scrollHeight:3800},querySelectorAll(selector){return selector==='.page-nodes button'?nodes:[];}},
    window,Intl,Date,JSON,AbortController,setTimeout,clearTimeout,crypto:{randomUUID:()=> '12345678-1234-1234-1234-123456789abc'},
    setInterval(fn,ms){intervals.push([fn,ms]);},getComputedStyle:()=>({getPropertyValue:()=> '20px'}),
    fetch:async(url,options)=>{calls.push({url,options});if(hook){const result=await hook(url,options);if(result)return result;}
      return {ok:true,json:async()=>url==='/api/tasks'?data:{}};}});
  vm.runInContext(source,context);await new Promise(setImmediate);
  return {get,calls,context,nodes,intervals,window,setData(d){data=d;},
    async event(id,type='click'){return get(id).events[type]({preventDefault(){}});},
    async poll(){await intervals.find(([,ms])=>ms===2000)[0]();}};
}
const sample=(state='waiting')=>({id:'a'.repeat(32),title:'<img src=x onerror=alert(1)>',status:state,
  model:'gpt-6-astra',effort:'ultra',mode:'time',runAt:1792701000,path:'E:\\tasks\\x.md',progress:'等待开始时间'});

test('new tasks require an explicit execution directory before posting',async()=>{
 const p=await setup();await p.event('task-new');
 p.get('task-content').value='更新 C:\\project';await p.event('task-form','submit');
 assert.match(p.get('task-error').textContent,/请选择执行目录/);
 assert.equal(p.calls.filter(c=>c.url.endsWith('/create')).length,0);
 assert.equal(p.get('task-dialog').open,true);
});

test('detail copies full text and keeps unchanged or selected text nodes across polling',async()=>{
 let task={...sample('running'),content:'任务内容\n第二行',result:'执行结果\n第二行',cwd:'C:\\project'};
 const p=await setup({tasks:[task]},async(url)=>url.endsWith('/detail')?{ok:true,json:async()=>({task})}:null);
 await p.get('task-list').children[0].children[4].children.find(b=>b.textContent==='详情').events.click();
 const node=p.get('task-detail-result');let value=node.textContent,writes=0;
 Object.defineProperty(node,'textContent',{get:()=>value,set:v=>{writes++;value=v;}});
 await p.poll();await p.poll();assert.equal(writes,0);
 p.context.document.getSelection=()=>({isCollapsed:false,rangeCount:1,getRangeAt:()=>({intersectsNode:n=>n===node})});
 task={...task,result:'新增执行结果'};await p.poll();assert.equal(writes,0);
 p.context.document.getSelection=()=>({isCollapsed:true});await p.poll();assert.equal(value,'新增执行结果');
 let copied;p.context.navigator={clipboard:{writeText:async text=>{copied=text;}}};
 await p.event('task-copy-content');assert.equal(copied,task.content);
 assert.equal(p.get('task-copy-content-feedback').textContent,'已复制');
 await p.event('task-copy-result');assert.equal(copied,task.result);
 p.context.navigator.clipboard.writeText=async()=>{throw Error('无法访问剪贴板');};
 await p.event('task-copy-result');assert.match(p.get('task-copy-result-feedback').textContent,/复制失败/);
 task={...task,result:''};await p.poll();assert.equal(p.get('task-copy-result').disabled,true);
});

test('task polling uses only its endpoint, renders user text safely and has no quota side effects',async()=>{
  const p=await setup({tasks:[sample()],settings:{directory:'',models:['gpt-6-astra','gpt-6.1-sol']}});
  assert.equal(p.get('task-list').children[0].children[0].children[0].textContent,sample().title);
  await p.poll();assert.deepEqual(p.calls.map(c=>c.url),['/api/tasks','/api/tasks']);
  assert.match(p.get('task-count').textContent,/1 个等待/);
});

test('new form uses Beijing seconds, preserves all six efforts and sends an idempotent request',async()=>{
  const p=await setup();await p.event('task-new');
  assert.equal(p.get('task-dialog').open,true);
  assert.match(p.get('task-time').value,/T\d{2}:\d{2}:\d{2}$/);
  p.get('task-title').value='任务';p.get('task-time').value='2026-10-24T00:00:01';
  p.get('task-content').value='内容';p.get('task-effort').value='ultra';
  p.get('task-cwd').value='C:\\project';
  await p.event('task-form','submit');
  const post=p.calls.find(c=>c.url.endsWith('/create')),body=JSON.parse(post.options.body);
  assert.equal(body.runAt,Date.parse('2026-10-24T00:00:01+08:00')/1000);
  assert.equal(body.id,'12345678123412341234123456789abc');
  assert.equal(body.effort,'ultra');assert.equal(body.path,'');assert.equal(p.get('task-dialog').open,false);
});

test('quota start hides required date input and existing Markdown is read-only',async()=>{
  const p=await setup();await p.event('task-new');
  p.get('task-mode').value='quota';await p.event('task-mode','change');
  assert.equal(p.get('task-time').required,false);assert.equal(p.get('task-time-field').hidden,true);
  p.get('task-source').value='existing';await p.event('task-source','change');
  assert.equal(p.get('task-content').readOnly,true);assert.equal(p.get('task-path').required,true);
  assert.equal(p.get('task-read').hidden,false);
});

test('session refresh retries the same task once and updates credentials',async()=>{
  let posts=0;
  const p=await setup(undefined,async(url)=>{
    if(url.endsWith('/create'))return ++posts===1?{ok:false,status:403,json:async()=>({code:'stale_session'})}:{ok:true,json:async()=>({})};
    if(url==='/api/session')return {ok:true,json:async()=>({token:'new'})};
  });
  await p.event('task-new');p.get('task-time').value='2026-10-24T00:00:01';
  p.get('task-cwd').value='C:\\project';
  await p.event('task-form','submit');
  const calls=p.calls.filter(c=>c.url.endsWith('/create'));
  assert.equal(calls.length,2);assert.equal(calls[0].options.body,calls[1].options.body);
  assert.equal(calls[1].options.headers['X-Quota-Token'],'new');
});

test('rejected creation keeps draft and task ID for safe manual retry',async()=>{
  const p=await setup(undefined,async(url)=>url.endsWith('/create')?{ok:false,status:400,json:async()=>({error:'路径不存在'})}:null);
  await p.event('task-new');p.get('task-time').value='2026-10-24T00:00:01';p.get('task-content').value='保留';
  p.get('task-cwd').value='C:\\project';
  await p.event('task-form','submit');await p.event('task-form','submit');
  assert.equal(p.get('task-dialog').open,true);assert.equal(p.get('task-content').value,'保留');
  const posts=p.calls.filter(c=>c.url.endsWith('/create'));
  assert.equal(JSON.parse(posts[0].options.body).id,JSON.parse(posts[1].options.body).id);
  assert.equal(p.get('task-error').textContent,'路径不存在');
});

test('task settings detect local model tags and save multiple selections alongside defaults',async()=>{
  const p=await setup(undefined,async(url)=>url.endsWith('/models')?{ok:true,json:async()=>({models:[
    {model:'gpt-6-astra',label:'GPT-6 Astra'},{model:'gpt-6-sol',label:'GPT-6 Sol'},{model:'gpt-6-luna',label:'GPT-6 Luna'}]})}:null);
  await p.event('task-settings-open');await new Promise(setImmediate);
  const tags=p.get('task-model-tags').children;assert.equal(tags.length,2);
  for(const tag of tags){tag.children[0].checked=true;tag.children[0].events.change();}
  await p.event('task-settings-form','submit');
  const saved=JSON.parse(p.calls.find(c=>c.url.endsWith('/settings')).options.body);
  assert.deepEqual(saved.models,['gpt-6-sol','gpt-6-luna']);
  assert.equal(p.get('task-model').children.length,4);
});

test('model detection failure keeps existing choices and offers retry without altering quota',async()=>{
  const initial={tasks:[],settings:{directory:'E:\\tasks',models:['gpt-6-astra','gpt-6.1-sol','gpt-6-sol']}};
  const p=await setup(initial,async(url)=>url.endsWith('/models')?{ok:false,json:async()=>({error:'CLI未连接'})}:null);
  await p.event('task-settings-open');await new Promise(setImmediate);
  assert.match(p.get('task-model-detection').textContent,/检测失败/);
  await p.event('task-settings-form','submit');
  assert.deepEqual(JSON.parse(p.calls.find(c=>c.url.endsWith('/settings')).options.body).models,['gpt-6-sol']);
  assert.ok(p.calls.every(c=>c.url.startsWith('/api/tasks')));
});

test('first node scrolls to page top, middle cards retain 20px, last node clamps to page bottom',async()=>{
  const p=await setup();
  for(const [i,node] of p.nodes.entries()){
    await node.events.click();
    if(i===0)assert.equal(p.window.scrollY,0);
    else if(i===4)assert.equal(p.window.scrollY,2900);
    else assert.equal(p.get(node.dataset.target).getBoundingClientRect().top,20);
    assert.equal(node.attributes['aria-current'],'location');
    assert.equal(p.get('node-progress').style.height,`${i/4*100}%`);
  }
  await p.event('tasks-jump');assert.equal(p.get('tasks-panel').getBoundingClientRect().top,20);
  assert.equal(p.window.lastScroll.behavior,'smooth');
  p.window.matchMedia=()=>({matches:true});await p.event('quota-chart-jump');
  assert.equal(p.window.lastScroll.behavior,'auto');
});

test('offline refresh keeps prior task rows and reports stale status',async()=>{
  let offline=false;
  const p=await setup({tasks:[sample()]},async()=>{if(offline)throw Error('offline');});
  const before=p.get('task-list').children[0];offline=true;await p.poll();
  assert.equal(p.get('task-list').children[0],before);
  assert.match(p.get('task-sync').textContent,/上次记录/);
});

test('cancellation uses the task endpoint and never calls quota controls',async()=>{
  const p=await setup({tasks:[sample()]});
  const actions=p.get('task-list').children[0].children[4];
  assert.equal(actions.children.at(-2).textContent,'详情');
  const cancel=actions.children.find(b=>b.textContent==='取消');await cancel.events.click();
  assert.equal(p.calls.find(c=>c.url.endsWith('/cancel')).options.body,JSON.stringify({id:'a'.repeat(32)}));
  assert.ok(p.calls.every(c=>c.url.startsWith('/api/tasks')));
});

test('delete icon follows details, uses its own endpoint and cannot delete running tasks',async()=>{
 const p=await setup({tasks:[sample('failed')]});
 const actions=p.get('task-list').children[0].children[4];
 assert.equal(actions.children.at(-2).textContent,'详情');
 const remove=actions.children.at(-1);assert.equal(remove.attributes['aria-label'],'删除任务');
 await remove.events.click();
 assert.deepEqual(JSON.parse(p.calls.find(c=>c.url.endsWith('/delete')).options.body),{id:sample().id});
 const q=await setup({tasks:[sample('running')]});
 const blocked=q.get('task-list').children[0].children[4].children.at(-1);
 assert.equal(blocked.disabled,true);await blocked.events.click();
 assert.equal(q.calls.some(c=>c.url.endsWith('/delete')),false);
});

test('model and effort have their own column and status is shown only in the right capsule',async()=>{
  const task=sample('failed');task.progress='执行被拒绝';
  const p=await setup({tasks:[task]});
  const row=p.get('task-list').children[0];
  assert.equal(row.children.length,5);
  assert.equal(row.children[0].children.length,1);
  assert.equal(row.children[1].className,'task-model');
  assert.equal(row.children[1].textContent,'GPT-6 Astra · ultra');
  assert.equal(row.children[3].textContent,'执行失败');
  assert.equal(row.children[3].title,'执行被拒绝');
});

test('detail paths shorten only descendants of the execution directory and retain full paths on hover',async()=>{
  const cases=[
    ['D:\\a','D:\\a\\c.md','D:\\a\\out\\r.md','...\\c.md','...\\out\\r.md'],
    ['D:\\a\\','d:/A/c.md','D:\\another\\r.md','...\\c.md','D:\\another\\r.md'],
    ['D:\\','D:\\c.md','D:\\r.md','...\\c.md','...\\r.md'],
    ['\\\\server\\share\\a','\\\\server\\share\\a\\c.md','E:\\r.md','...\\c.md','E:\\r.md'],
  ];
  for(const [cwd,path,resultPath,shortPath,shortResult] of cases){
    const task={...sample('completed'),cwd,path,resultPath,progress:'已完成',content:'任务内容',result:'任务结果'};
    const p=await setup({tasks:[task]},async(url)=>url.endsWith('/detail')?{ok:true,json:async()=>({task})}:null);
    await p.get('task-list').children[0].children[4].children.find(b=>b.textContent==='详情').events.click();
    const meta=p.get('task-detail-files');
    assert.equal(p.get('task-detail-cwd').textContent,cwd);
    assert.equal(p.get('task-detail-directory').hidden,false);
    assert.ok(meta.textContent.includes('任务文档：'+shortPath));
    assert.ok(meta.textContent.includes('结果文档：'+shortResult));
    assert.equal(meta.title,'任务文档：'+path+'\n结果文档：'+resultPath);
    assert.equal(p.get('task-detail-status').textContent,'已完成');
    assert.equal(p.get('task-detail-status').dataset.state,'completed');
    assert.equal(p.get('task-detail-progress').textContent,'');
    assert.equal(p.get('task-detail-progress').hidden,true);
  }
});

test('directory actions copy the full path and open the saved task directory with visible failures',async()=>{
  const task={...sample(),cwd:'C:\\work space\\项目',content:'内容'};
  let copied;
  const p=await setup({tasks:[task]},async(url)=>url.endsWith('/detail')?{ok:true,json:async()=>({task})}:null);
  p.context.navigator={clipboard:{writeText:async text=>{copied=text;}}};
  await p.get('task-list').children[0].children[4].children.find(b=>b.textContent==='详情').events.click();
  await p.event('task-copy-cwd');
  assert.equal(copied,task.cwd);
  assert.equal(p.get('task-copy-tooltip').textContent,'已复制路径');
  await p.event('task-open-cwd');
  const call=p.calls.find(c=>c.url.endsWith('/open-directory'));
  assert.deepEqual(JSON.parse(call.options.body),{id:task.id});
  assert.equal(p.get('task-open-cwd').disabled,false);
  p.context.navigator.clipboard.writeText=async()=>{throw Error('权限不足');};
  await p.event('task-copy-cwd');
  assert.match(p.get('task-path-feedback').textContent,/复制失败.*权限不足/);
});

test('detail status capsule updates independently while progress and failure reasons stay visible without duplicated labels',async()=>{
  let task={...sample('running'),cwd:'E:\\tasks',content:'任务',progress:'正在执行第二步',result:''};
  const p=await setup({tasks:[task]},async(url)=>url.endsWith('/detail')?{ok:true,json:async()=>({task})}:null);
  await p.get('task-list').children[0].children[4].children.find(b=>b.textContent==='详情').events.click();
  assert.equal(p.get('task-detail-status').textContent,'执行中');
  assert.equal(p.get('task-detail-progress').textContent,'正在执行第二步');
  assert.equal(p.get('task-detail-progress').hidden,false);
  task={...task,status:'failed',progress:'执行目录不可访问'};
  p.setData({tasks:[task]});await p.poll();
  assert.equal(p.get('task-detail-status').textContent,'执行失败');
  assert.equal(p.get('task-detail-status').attributes['aria-label'],'任务执行状态：执行失败');
  assert.equal(p.get('task-detail-progress').textContent,'执行目录不可访问');
  assert.equal(p.get('task-detail-progress').hidden,false);
});


test('waiting tasks edit their snapshot and retain custom models; new form resets editing',async()=>{
  const task={...sample(),model:'gpt-custom',content:'原内容',cwd:'E:\\tasks'};
  const p=await setup({tasks:[task]},async(url)=>url.endsWith('/detail')?{ok:true,json:async()=>({task})}:null);
  await p.get('task-list').children[0].children[4].children.find(b=>b.textContent==='修改').events.click();
  assert.equal(p.get('task-dialog').open,true);
  assert.equal(p.get('task-model').value,'gpt-custom');
  assert.equal(p.get('task-content').value,'原内容');
  assert.equal(p.get('task-path').disabled,true);
  p.get('task-content').value='修改内容';
  await p.event('task-form','submit');
  const body=JSON.parse(p.calls.find(c=>c.url.endsWith('/edit')).options.body);
  assert.equal(body.id,task.id);assert.equal(body.content,'修改内容');
  await p.event('task-new');assert.equal(p.get('task-path').disabled,false);
  assert.equal(p.get('task-save').textContent,'添加任务');
});

test('started tasks have no edit action and stale edits preserve their draft on rejection',async()=>{
  const p=await setup({tasks:[sample('running')]});
  assert.equal(p.get('task-list').children[0].children[4].children.some(b=>b.textContent==='修改'),false);
  const task={...sample(),content:'保留草稿',cwd:'E:\\tasks'};
  const q=await setup({tasks:[task]},async(url)=>url.endsWith('/detail')?{ok:true,json:async()=>({task})}:
    url.endsWith('/edit')?{ok:false,status:400,json:async()=>({error:'任务已开始'})}:null);
  await q.get('task-list').children[0].children[4].children.find(b=>b.textContent==='修改').events.click();
  await q.event('task-form','submit');
  assert.equal(q.get('task-dialog').open,true);assert.equal(q.get('task-content').value,'保留草稿');
  assert.equal(q.get('task-error').textContent,'任务已开始');
});
