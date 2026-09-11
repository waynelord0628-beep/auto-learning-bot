const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
function setup(brokenSource=false){let tg=0,props={},broken=false;const ctx={console:{error:()=>{}},Date,
 PropertiesService:{getScriptProperties:()=>({getProperty:k=>{if(broken)throw Error('storage failed');return props[k]||null;},setProperty:(k,v)=>props[k]=v})},
 LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock:()=>{}})},
 ContentService:{MimeType:{JSON:1},createTextOutput:t=>({setMimeType:()=>JSON.parse(t)})}};
 vm.createContext(ctx);let source=fs.readFileSync(__dirname+'/Code.template.gs','utf8'); if(brokenSource)source=source.replace(/^const USAGE_PROP_KEY.*$/m,''); vm.runInContext(source,ctx);ctx.tgSend=()=>{tg++;};
 return {ctx,tg:()=>tg,breakStorage:()=>broken=true};}
const post=(s,data)=>s.ctx.doPost({postData:{contents:JSON.stringify(data)}});
let old=setup(true);assert.equal(post(old,{action:'usage_ping',device_id:'test'}).ok,false);assert.equal(old.tg(),0);
let s=setup();for(let i=0;i<200;i++)assert.equal(post(s,{action:'usage_ping',device_id:'test',version:'V2.1.9'}).ok,true);
assert.equal(post(s,{action:'usage_stats'}).online,1);assert.equal(s.tg(),0);
s.breakStorage();for(let i=0;i<200;i++)assert.equal(post(s,{action:'usage_ping',device_id:'test'}).ok,false);assert.equal(s.tg(),0);
s=setup();for(let i=0;i<200;i++)s.ctx.doPost({postData:{contents:'invalid json'}});assert.equal(s.tg(),1);
console.log('PASS: reproduced original defect; 200 successful heartbeats; 200 storage failures silent; 200 malformed requests produce only one alert.');

