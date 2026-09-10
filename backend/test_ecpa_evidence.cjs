const assert = require('assert/strict');
const fs = require('fs');
const vm = require('vm');
const crypto = require('crypto');
const code = fs.readFileSync(__dirname + '/ecpa_evidence.js', 'utf8') + '\n' + fs.readFileSync(__dirname + '/ecpa_review_worker.js', 'utf8');
function setup() {
 let files = {'patches/questions_patch.json':'[]','patches/db_version.txt':'6003'}, staged, tg=0, writes=0, conflict=false;
 const response=(status,data)=>({getResponseCode:()=>status,getContentText:()=>JSON.stringify(data)});
 const ctx={GITHUB_TOKEN:'test-only-key', GITHUB_REPO:'test/repo', ECPA_PATCH_PATH:'patches/questions_patch.json', ECPA_VERSION_PATH:'patches/db_version.txt',
 githubHeaders:()=>({}),tgSend:()=>tg++,LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock:()=>{}})},
 Utilities:{DigestAlgorithm:{SHA_256:'sha256'},Charset:{UTF_8:'utf8'},
 computeDigest:(_,s)=>[...crypto.createHash('sha256').update(s).digest()],
 computeHmacSha256Signature:(s,k)=>[...crypto.createHmac('sha256',k).update(s).digest()],
 base64Decode:s=>Buffer.from(s,'base64'),newBlob:b=>({getDataAsString:()=>b.toString('utf8')})},
 UrlFetchApp:{fetch:(url,opts)=>{
 const path=url.split('test/repo/')[1];
 if(path==='git/ref/heads/main')return response(200,{object:{sha:'head'}});
 if(path==='git/commits/head')return response(200,{tree:{sha:'base'}});
 if(path.startsWith('contents/')) {const p=path.slice(9).split('?')[0];return p in files?response(200,{content:Buffer.from(files[p]).toString('base64')}):response(404,{});}
 writes++;
 const body=JSON.parse(opts.payload);
 if(path==='git/trees'){staged=body.tree;return response(201,{sha:'tree'});}
 if(path==='git/commits')return response(201,{sha:'commit'});
 if(path==='git/refs/heads/main'){assert.equal(body.force,false);if(conflict)return response(422,{}); for(const f of staged)files[f.path]=f.content;return response(200,{});}
 throw Error(path);
 }}};
 vm.createContext(ctx);vm.runInContext(code,ctx);
 return {ctx, files:()=>files,tg:()=>tg,writes:()=>writes,conflict:()=>conflict=true};
}
function envelope(records, extra={}) {
 const payload=JSON.stringify({course_id:'123',course:'test course',records,...extra});
 const key=crypto.createHash('sha256').update('test-only-key:ecpa-evidence-v1').digest('hex');
 return {payload,signature:crypto.createHmac('sha256',key).update(payload).digest('hex')};
}
const row={question:'Question?',type:'單選',options:['A','B'],answers:['B'],source:'platform_disclosed'};
let s=setup();assert.equal(s.ctx.handleEcpaEvidence({...envelope([row]),signature:'bad'}).status,'unauthorized');assert.equal(s.writes(),0);
assert.equal(s.ctx.handleEcpaEvidence(envelope([{...row,source:'ai_unverified'}])).status,'invalid');assert.equal(s.writes(),0);
assert.equal(s.ctx.handleEcpaEvidence(envelope([row],{dry_run:true})).dry_run,true);assert.equal(s.writes(),0);assert.equal(s.tg(),0);
let result=s.ctx.handleEcpaEvidence(envelope([row,row]));assert.equal(result.published,1);assert.equal(s.files()['patches/db_version.txt'],'6004');assert.equal(s.tg(),1);
assert.equal(s.ctx.handleEcpaEvidence(envelope([row])).accepted,0);assert.equal(s.tg(),1);
result=s.ctx.handleEcpaEvidence(envelope([{...row,answers:['A']}]));assert.equal(result.conflicts,1);assert.equal(result.published,0);assert.equal(JSON.parse(s.files()['patches/questions_patch.json'])[0].answer,'B');
s=setup();s.conflict();assert.equal(s.ctx.handleEcpaEvidence(envelope([row])).status,'retry');assert.equal(s.files()['patches/questions_patch.json'],'[]');assert.equal(s.files()['patches/db_version.txt'],'6003');assert.equal(s.tg(),0);
console.log('PASS: authentication, AI rejection, dry-run, atomic publication, deduplication, conflicts, concurrent-write rollback.');
