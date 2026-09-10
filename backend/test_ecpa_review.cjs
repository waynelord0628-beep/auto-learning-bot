const assert=require('assert/strict'),fs=require('fs'),vm=require('vm'),crypto=require('crypto');
const code=fs.readFileSync(__dirname+'/ecpa_review_worker.js','utf8');
const hash=s=>crypto.createHash('sha256').update(s).digest('hex');
function setup() {
 const files={},props={}; let tg=0, ai=0, conflict=false, staged=[], writes=0;
 const ctx={Date, ECPA_PATCH_PATH:'patches/questions_patch.json',ecpaText:s=>String(s).normalize('NFC').trim().replace(/\s+/g,' '),
  openAIAnswerBatch:questions=>{ai++;return questions.map((q,idx)=>({idx,answer:'2. B'}));},
  LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock:()=>{}})},
  PropertiesService:{getScriptProperties:()=>({getProperty:k=>props[k],setProperty:(k,v)=>props[k]=v})},
  tgSend:()=>tg++,ecpaReadAt:(path,head,fallback)=>files[path]||fallback,
  ecpaGit:(path,method,body)=>{
    if(path==='git/ref/heads/main')return {object:{sha:'head'}};
    if(path==='git/commits/head')return {tree:{sha:'base'}};
    if(path==='contents/review/ecpa?ref=head')return Object.keys(files).filter(p=>p.startsWith('review/ecpa/')).map(p=>({type:'file',path:p,name:p.split('/').pop()}));
    writes++;
    if(path==='git/trees'){staged=body.tree;return {sha:'tree'};}
    if(path==='git/commits')return {sha:'next'};
    if(path==='git/refs/heads/main'){assert.equal(body.force,false);if(conflict)throw Error('race');staged.forEach(f=>files[f.path]=f.content);return {};}
    throw Error(path);
  }};
 vm.createContext(ctx);vm.runInContext(code,ctx);
 return {ctx,files,props,tg:()=>tg,ai:()=>ai,writes:()=>writes,race:()=>conflict=true};
}
const base={id:'q1',question:'Question?',course:'course',type:'單選',options:['A','B'],candidate_answer:'2. B',source:'ai_unverified',reason:'missing',status:'pending'};
let s=setup(),q={...base};s.ctx.ecpaReconcileReview([q],[]);assert.equal(q.candidate_answer,'B');assert.equal(q.status,'pending');
assert.equal(s.ctx.ecpaReviewAnswer({...base,options:['B','A']},'2. B'),'B');
assert.equal(s.ctx.ecpaReviewAnswer(base,'2'),null);
assert.equal(s.ctx.ecpaReviewAnswer(base,'1. unrelated'),null);
assert.equal(s.ctx.ecpaReviewAnswer({...base,type:'多選'},'B'),null);
assert.equal(s.ctx.ecpaReviewAnswer({...base,options:['1. A','2. B']},'B'),'2. B');
q={...base,type:'是非',options:['',''],candidate_answer:'FALSE'};s.ctx.ecpaReconcileReview([q],[]);assert.equal(q.candidate_answer,'錯誤');
q={...base,type:'單選',options:['','']};s.ctx.ecpaReconcileReview([q],[]);assert.equal(q.review_state,'awaiting_options');
q={...base};s.ctx.ecpaReconcileReview([q],[{...base,answer:'A'}]);assert.equal(q.status,'pending');
const verified={...base,answer:'B',source:'platform_disclosed_client',evidence_id:'e1'};
s.ctx.ecpaReconcileReview([q],[verified]);assert.equal(q.status,'resolved');assert.equal(q.resolved_answer,'B');
q={...base};s.ctx.ecpaReconcileReview([q],[{...verified,course:'other'}]);assert.equal(q.status,'pending');
q={...base};s.ctx.ecpaReconcileReview([q],[verified,{...verified,answer:'A'}]);assert.equal(q.review_state,'evidence_conflict');assert.equal(q.candidate_answer,null);
q={...base};const duplicate={...base,id:'q2',candidate_answer:'A'};s.ctx.ecpaReconcileReview([q,duplicate],[]);assert.equal(duplicate.status,'duplicate');assert.equal(q.candidate_answer,null);
q={...base,reason:'exam_failed_unverified'};s.ctx.ecpaReconcileReview([q],[]);assert.equal(q.candidate_answer,null);
q={...base,candidate_answer:null};s.ctx.ecpaReconcileReview([q],[]);s.ctx.ecpaRetryCandidates([q],10,Date.now());assert.equal(q.candidate_answer,'B');assert.equal(s.ai(),1);
q={...base,candidate_answer:null,retry_count:3};s.ctx.ecpaReconcileReview([q],[]);s.ctx.ecpaRetryCandidates([q],10,Date.now());assert.equal(s.ai(),1);assert.equal(q.review_state,'awaiting_evidence');
s=setup();const path='review/ecpa/'+hash('course')+'.json';s.files[path]=JSON.stringify([base]);s.files['patches/questions_patch.json']='[]';
s.ctx.processEcpaReviewQueue();assert.equal(JSON.parse(s.files[path])[0].candidate_answer,'B');assert.equal(s.tg(),0);assert.equal(s.ai(),0);assert.equal(s.files['patches/questions_patch.json'],'[]');
const writes=s.writes();s.ctx.processEcpaReviewQueue();assert.equal(s.writes(),writes);
s=setup();s.files[path]=JSON.stringify([base]);s.race();assert.throws(()=>s.ctx.processEcpaReviewQueue());assert.equal(JSON.parse(s.files[path])[0].candidate_answer,'2. B');assert.equal(s.props.ECPA_REVIEW_CURSOR,undefined);
// Intake remains backward-compatible, silent, and deduplicates across report reasons.
s=setup();let saved=[],puts=0;
s.ctx.Utilities={DigestAlgorithm:{SHA_256:0},Charset:{UTF_8:0},computeDigest:(_,text)=>[...Buffer.from(hash(text),'hex')]};
s.ctx.githubGet=p=>({content:p===s.ctx.ECPA_PATCH_PATH?[]:saved.map(q=>({...q})),sha:'sha'});
s.ctx.githubPut=(p,rows)=>{saved=rows;puts++;};
vm.runInContext(fs.readFileSync(__dirname+'/ecpa_candidate_intake.js','utf8'),s.ctx);
let intake=s.ctx.handleEcpaMissing({course:'course',missing:[base]});assert.equal(intake.pending,1);assert.equal(s.tg(),0);assert.equal(saved[0].candidate_answer,'B');
intake=s.ctx.handleEcpaMissing({course:'course',reason:'exam_failed',missing:[base]});assert.equal(intake.pending,0);assert.equal(puts,1);assert.equal(s.ai(),1);
console.log('PASS: formatting, semantic true/false, no numeric guessing, exact evidence, course isolation, conflicts, bounded retries, quiet idempotent maintenance, atomic race protection.');
