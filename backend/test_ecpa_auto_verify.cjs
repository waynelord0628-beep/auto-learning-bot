const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const worker=fs.readFileSync(__dirname+'/ecpa_review_worker.js','utf8');
const code=fs.readFileSync(__dirname+'/ecpa_auto_verify.js','utf8');
const q={id:'q',question:'test question',type:'單選',options:['A','B'],course:'course',reason:'missing',status:'pending',review_state:'candidate_ready',candidate_answer:'A'};
function setup() {
 const files={'queue':JSON.stringify([q]),'patch':'[]','version':'6002'},props={};
 let staged=[],race=false,sends=0,calls=0;
 const ctx={console,Date,ecpaVerifyCourseAnswer:()=>null,ecpaText:s=>String(s).normalize('NFC').trim().replace(/\s+/g,' '),
   ECPA_PATCH_PATH:'patch',ECPA_VERSION_PATH:'version',stripJsonFence:s=>s,
   LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock:()=>{}})},
   PropertiesService:{getScriptProperties:()=>({getProperty:k=>props[k],setProperty:(k,v)=>props[k]=v})},
   ecpaReadAt:(p,h,d)=>files[p]||d,tgSend:()=>{sends++;return true;},
   ecpaGit:(p,m,b)=>{
     if(p==='git/ref/heads/main')return {object:{sha:'head'}};
     if(p==='git/commits/head')return {tree:{sha:'tree'}};
     if(p==='git/trees'){staged=b.tree;return {sha:'newtree'};}
     if(p==='git/commits')return {sha:'next'};
     if(p==='git/refs/heads/main'){assert.equal(b.force,false);if(race)throw Error('race');staged.forEach(f=>files[f.path]=f.content);return {};}
     throw Error(p);
   },UrlFetchApp:{fetch:()=>({getResponseCode:()=>200,getAllHeaders:()=>({'Content-Type':'text/html'}),getContentText:()=>'<p>the original official evidence supports option B</p>'})}};
 vm.createContext(ctx);vm.runInContext(worker+'\n'+code,ctx);
 ctx.ecpaReviewApi=()=>{calls++;return calls%2?{searched:true,data:{answer:'B',sources:[{url:'https://test.gov.tw/page',quote:'original official evidence supports option B'}]}}:{data:{supported:true,answer:'B'}};};
 return {ctx,files,props,calls:()=>calls,sends:()=>sends,race:()=>race=true};
}
let s=setup();
assert.equal(s.ctx.ecpaSourceAllowed('https://test.gov.tw/page'),true);
for(const url of ['https://gov.tw.attacker.com/','https://gov.tw@127.0.0.1/','http://test.gov.tw/','https://127.0.0.1/','https://test.gov.tw:444/'])assert.equal(s.ctx.ecpaSourceAllowed(url),false);
const verdict=s.ctx.ecpaVerifyFromSources(q);assert.equal(verdict.answer,'B');assert.equal(s.calls(),2);
s=setup();s.ctx.UrlFetchApp.fetch=()=>{throw Error('TLS');};assert.equal(s.ctx.ecpaVerifyFromSources(q),null);assert.equal(s.calls(),1);
s=setup();s.ctx.UrlFetchApp.fetch=()=>({getResponseCode:()=>302});assert.equal(s.ctx.ecpaVerifyFromSources(q),null);
s=setup();s.ctx.ecpaReviewApi=()=>({searched:true,data:{answer:'B',sources:[{url:'https://test.gov.tw/',quote:'invented statement not on page'}]}});assert.equal(s.ctx.ecpaVerifyFromSources(q),null);
s=setup();let count=0;s.ctx.ecpaReviewApi=()=>++count===1?{searched:true,data:{answer:'B',sources:[{url:'https://test.gov.tw/',quote:'original official evidence supports option B'}]}}:{data:{supported:true,answer:'A'}};assert.equal(s.ctx.ecpaVerifyFromSources(q),null);
s=setup();assert.equal(s.ctx.ecpaSaveSourceReview('queue',q,verdict,false),1);
assert.equal(s.files.version,'6003');assert.equal(JSON.parse(s.files.patch)[0].source,'official_source_reviewed');
assert.equal(JSON.parse(s.files.queue)[0].status,'resolved');
assert.equal(s.ctx.ecpaSaveSourceReview('queue',q,verdict,false),0);assert.equal(s.files.version,'6003');
let queue=JSON.parse(s.files.queue);s.ctx.ecpaReconcileReview(queue,JSON.parse(s.files.patch));assert.equal(queue[0].review_state,'source_reviewed');
s=setup();s.files.patch=JSON.stringify([{question:q.question,answer:'A'}]);assert.equal(s.ctx.ecpaSaveSourceReview('queue',q,verdict,false),0);assert.equal(JSON.parse(s.files.patch)[0].answer,'A');assert.equal(s.files.version,'6002');
s=setup();s.race();assert.throws(()=>s.ctx.ecpaSaveSourceReview('queue',q,verdict,false));assert.equal(s.files.patch,'[]');assert.equal(s.files.version,'6002');
s=setup();assert.equal(s.ctx.ecpaSaveSourceReview('queue',q,null,true),0);assert.equal(s.files.patch,'[]');assert.equal(s.ctx.ecpaCanAutoVerify(JSON.parse(s.files.queue)[0],Date.now()),false);
assert.equal(s.ctx.ecpaCanAutoVerify({...q,reason:'exam_failed_unverified'},Date.now()),false);
assert.equal(s.ctx.ecpaCanAutoVerify({...q,type:'多選'},Date.now()),false);
assert.equal(s.ctx.ecpaCanAutoVerify({...q,verify_attempts:3,verify_policy:'course_first_v2'},Date.now()),false);
assert.equal(s.ctx.ecpaCanAutoVerify({...q,verify_attempts:3,verify_policy:'course_first_v1',verify_after:'2999-01-01'},Date.now()),true);
assert.equal(s.ctx.ecpaCanAutoVerify({...q,verify_attempts:3,verify_after:'2999-01-01'},Date.now()),true);
s=setup();s.ctx.maintainEcpaReviewQueue=()=>({ok:true});s.ctx.ecpaAutoVerifyBatch=()=>({ok:true});
s.ctx.processEcpaReviewQueue();assert.equal(s.props.ECPA_VERIFY_USED,'3');assert.equal(s.props.ECPA_VERIFY_LEASE_UNTIL,'0');
s.props.ECPA_VERIFY_USED='48';assert.equal(s.ctx.processEcpaReviewQueue().status,'daily_budget');
s=setup();s.ctx.ecpaSaveSourceReview('queue',q,null,true);
let failed=JSON.parse(s.files.queue)[0];
assert.equal(failed.verify_policy,'course_first_v2');assert.equal(failed.verify_attempts,0);
assert.equal(failed.verification_status,'service_error');
assert.ok(Date.parse(failed.verify_after)>Date.now());
assert.ok(Date.parse(failed.verify_after)<Date.now()+2*86400000);
s=setup();vm.runInContext(code,s.ctx);s.ctx.OPENAI_KEY='fake-test-key';
let request;
s.ctx.UrlFetchApp.fetch=(url,options)=>{request={url,options};return {
 getResponseCode:()=>200,getContentText:()=>JSON.stringify({status:'completed',output:[
  {type:'web_search_call',status:'completed',action:{sources:[{url:'https://test.gov.tw/source'}]}},
  {type:'message',content:[{type:'output_text',text:'{"answer":"B"}'}]}
 ]})};};
const actual=s.ctx.ecpaReviewApi({input:'test fixture'});
assert.equal(actual.searched,true);assert.equal(actual.data.answer,'B');
assert.equal(request.url,'https://api.openai.com/v1/responses');
const payload=JSON.parse(request.options.payload);
assert.equal(payload.model,'gpt-4.1');assert.equal(payload.store,false);assert.equal(payload.max_output_tokens,1800);
s.ctx.UrlFetchApp.fetch=()=>({getResponseCode:()=>429});
assert.throws(()=>s.ctx.ecpaReviewApi({input:'test fixture'}),/Review API HTTP 429/);
s=setup();
const extra={...q,id:'second',question:'second question',verify_policy:'course_first_v2',verify_attempts:3,verify_after:'2999-01-01'};
s.files.queue=JSON.stringify([q,extra,{...extra,id:'other',question:'other course question',course:'other course'}]);
s.ctx.ecpaCanUseCourseAnswer=x=>x&&x.status==='pending'&&x.reason==='missing';
const courseVerdict={answer:'B',method:'course_answer_exact_v1',sources:[{url:'https://roddayeye.pixnet.net/course'}]};
assert.equal(s.ctx.ecpaSaveSourceReview('queue',q,courseVerdict,false,[{original:extra,verdict:courseVerdict},{original:JSON.parse(s.files.queue)[2],verdict:courseVerdict}]),2);
assert.equal(s.files.version,'6004');assert.equal(JSON.parse(s.files.patch).length,2);
assert.deepEqual(JSON.parse(s.files.queue).map(x=>x.status),['resolved','resolved','pending']);
console.log('PASS: source checks, independent verdict, atomic whole-course publication, duplicates, cross-course isolation, conflicts, retry limits and budget');
