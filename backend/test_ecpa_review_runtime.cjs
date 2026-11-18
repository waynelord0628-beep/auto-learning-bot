const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const auto=fs.readFileSync(__dirname+'/ecpa_auto_verify.js','utf8');
const worker=fs.readFileSync(__dirname+'/ecpa_review_worker.js','utf8');
const template=fs.readFileSync(__dirname+'/Code.template.gs','utf8');
for(const file of ['ecpa_auto_verify.js','ecpa_review_worker.js','ecpa_course_sources.js']) {
 for(const match of fs.readFileSync(__dirname+'/'+file,'utf8').matchAll(/^function (\w+)\([\s\S]*?^}/gm)) {
  assert.equal(template.match(new RegExp('^function '+match[1]+'\\([\\s\\S]*?^}', 'm'))?.[0],match[0],match[1]);
 }
}
function setup() {
 let now=1000000,calls=0,maintenance=0;
 const props={},q={id:'q',course:'course',question:'question',type:'單選',options:['A','B'],status:'pending',reason:'missing',review_state:'candidate_ready'};
 const path='review/ecpa/'+'a'.repeat(64)+'.json';
 const ctx={console,Date:class extends Date {static now(){return now;}},
  PropertiesService:{getScriptProperties:()=>({getProperty:k=>props[k],setProperty:(k,v)=>props[k]=v})},
  LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock:()=>{}})},
  ecpaText:s=>String(s).trim(),tgSend:()=>true,
  ecpaGit:()=>({object:{sha:'head'}}),ecpaReadAt:()=>JSON.stringify([q])};
 vm.createContext(ctx);vm.runInContext(worker+'\n'+auto,ctx);
 ctx.maintainEcpaReviewQueue=()=>{maintenance++;return {ok:true};};
 return {ctx,props,q,path,advance:ms=>now+=ms,maintenance:()=>maintenance,apiCalls:()=>calls,
  activate:()=>{props.ECPA_VERIFY_LEASE='owner';props.ECPA_VERIFY_LEASE_UNTIL=String(now+600000);ctx.ecpaReviewRun={props:ctx.PropertiesService.getScriptProperties(),lease:'owner',deadline:now+240000,workKey:'question'};}};
}
let s=setup();s.props.ECPA_VERIFY_LEASE_UNTIL='9999999';
assert.equal(s.ctx.processEcpaReviewQueue().status,'verifier_busy');assert.equal(s.maintenance(),0);
s=setup();s.props.ECPA_VERIFY_DAY=new Date(1000000).toISOString().slice(0,10);s.props.ECPA_VERIFY_USED='47';
s.ctx.ecpaAutoVerifyBatch=(deadline,slots)=>{assert.equal(deadline,1240000);assert.equal(slots,1);return {ok:true};};
s.ctx.processEcpaReviewQueue();assert.equal(s.props.ECPA_VERIFY_USED,'48');assert.equal(s.props.ECPA_VERIFY_LEASE_UNTIL,'0');
s=setup();s.activate();s.advance(130000);
assert.throws(()=>s.ctx.ecpaReviewBudget(75000,'api'),e=>e.code==='budget_yield');
let calls=0;s=setup();s.activate();
assert.equal(s.ctx.ecpaReviewStep('research',()=>++calls),1);
s.ctx.ecpaReviewRun=null;s.activate();
assert.equal(s.ctx.ecpaReviewStep('research',()=>++calls),1);assert.equal(calls,1);
s.props.ECPA_VERIFY_LEASE='other';assert.throws(()=>s.ctx.ecpaReviewBudget(1,'read'),e=>e.code==='lease_lost');
s=setup();s.ctx.maintainEcpaReviewQueue=()=>{s.advance(200000);s.ctx.ecpaReviewBudget(1,'maintenance');};
assert.equal(s.ctx.processEcpaReviewQueue().status,'budget_yield');assert.equal(s.props.ECPA_VERIFY_LEASE_UNTIL,'0');
assert.equal(s.props.ECPA_VERIFY_LAST_OK,undefined);
s=setup();s.activate();s.ctx.ecpaGit=p=>p.startsWith('contents/')?[{type:'file',name:'a'.repeat(64)+'.json',path:s.path}]:{object:{sha:'head'}};
s.ctx.ecpaVerifyFromSources=()=>{throw s.ctx.ecpaReviewYield('budget_yield');};
assert.throws(()=>s.ctx.ecpaAutoVerifyBatch(1240000,1),e=>e.code==='budget_yield');
assert.equal(JSON.parse(s.props.ECPA_VERIFY_POSITION).id,'q');assert.equal(s.props.ECPA_VERIFY_CURSOR,undefined);
s.ctx.LockService.getScriptLock=()=>({tryLock:()=>false});
assert.throws(()=>s.ctx.ecpaSaveSourceReview(s.path,s.q,null,false),e=>e.code==='save_busy');
assert.equal(s.ctx.ecpaReviewFailure({httpStatus:429}).code,'service_error');
assert.equal(s.ctx.ecpaReviewFailure({httpStatus:401}).code,'blocked_config');
assert.equal(s.ctx.ecpaReviewFailure({name:'ReviewInvalidData'}).code,'invalid_response');
console.log('PASS: template parity, early lease, shared deadline, final quota slot, durable paid-stage reuse, lease loss, yield checkpoint and save acknowledgement');
