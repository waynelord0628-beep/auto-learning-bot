const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
let calls=0,fail=false;
const ctx={GITHUB_REPO:'owner/repo',githubHeaders:()=>({Authorization:'test'}),
 Utilities:{base64Decode:s=>Array.from(Buffer.from(s,'base64'))},
 UrlFetchApp:{fetch:(url,options)=>{
   calls++;assert.ok(url.endsWith('/git/blobs/'+'a'.repeat(40)));
   assert.equal(options.headers.Accept,'application/vnd.github.raw+json');
   return {getResponseCode:()=>fail?500:200,getBlob:()=>({getBytes:()=>[91,93]})};
 }}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync(__dirname+'/ecpa_large_file.js','utf8'),ctx);
assert.deepEqual(Array.from(ctx.ecpaFileBytes_({encoding:'base64',content:'W10='})),[91,93]);
assert.equal(calls,0);
assert.deepEqual(Array.from(ctx.ecpaFileBytes_({encoding:'none',content:'',sha:'a'.repeat(40)})),[91,93]);
assert.equal(calls,1);
assert.throws(()=>ctx.ecpaFileBytes_({encoding:'none',sha:'bad'}));
fail=true;assert.throws(()=>ctx.ecpaFileBytes_({encoding:'none',sha:'a'.repeat(40)}));
console.log('PASS: inline and large immutable blob reads, invalid metadata and failed reads do not return empty banks');
