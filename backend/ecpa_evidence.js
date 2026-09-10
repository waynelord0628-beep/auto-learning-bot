// Signed evidence is accepted only from the locally provisioned collector.
function ecpaHex(bytes) { return bytes.map(b => (b & 255).toString(16).padStart(2, '0')).join(''); }
function ecpaHash(s) { return ecpaHex(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, s, Utilities.Charset.UTF_8)); }
function ecpaText(s) { return String(s).normalize('NFC').trim().replace(/\s+/g, ' '); }
function ecpaGit(path, method, payload) {
  const options = {method:method || 'get', headers:githubHeaders(), muteHttpExceptions:true};
  if (payload) { options.contentType = 'application/json'; options.payload = JSON.stringify(payload); }
  const r = UrlFetchApp.fetch('https://api.github.com/repos/' + GITHUB_REPO + '/' + path, options);
  if (r.getResponseCode() < 200 || r.getResponseCode() >= 300) throw new Error('Evidence GitHub operation failed: ' + r.getResponseCode());
  return JSON.parse(r.getContentText());
}
function ecpaReadAt(path, head, fallback) {
  const r = UrlFetchApp.fetch('https://api.github.com/repos/' + GITHUB_REPO + '/contents/' + path + '?ref=' + head,
    {headers:githubHeaders(), muteHttpExceptions:true});
  if (r.getResponseCode() === 404) return fallback;
  if (r.getResponseCode() !== 200) throw new Error('Evidence snapshot read failed');
  const data = JSON.parse(r.getContentText());
  return Utilities.newBlob(Utilities.base64Decode(data.content)).getDataAsString('UTF-8').replace(/^\uFEFF/, '');
}
function handleEcpaEvidence(envelope) {
  if (typeof envelope.payload !== 'string' || envelope.payload.length > 150000 || typeof envelope.signature !== 'string')
    return {ok:false, status:'unauthorized'};
  const key = ecpaHash(GITHUB_TOKEN + ':ecpa-evidence-v1');
  const signature = ecpaHex(Utilities.computeHmacSha256Signature(envelope.payload, key, Utilities.Charset.UTF_8));
  let difference = signature.length ^ envelope.signature.length;
  for (let i=0; i<signature.length; i++) difference |= signature.charCodeAt(i) ^ (envelope.signature.charCodeAt(i) || 0);
  if (difference) return {ok:false, status:'unauthorized'};
  let data;
  try { data = JSON.parse(envelope.payload); } catch(e) { return {ok:false, status:'invalid'}; }
  if (!data || typeof data.course_id !== 'string' || !data.course_id || typeof data.course !== 'string' ||
      !Array.isArray(data.records) || data.records.length > 50) return {ok:false, status:'invalid'};
  if (!data.records.length) return {ok:true, status:'ok', published:0, accepted:0};
  const records = [];
  for (const item of data.records) {
    if (!item || item.source !== 'platform_disclosed' || typeof item.question !== 'string' || !item.question.trim() ||
        !['單選','是非','多選'].includes(item.type) || !Array.isArray(item.options) || !Array.isArray(item.answers) ||
        !item.options.every(x => typeof x === 'string' && x.trim()) || !item.answers.every(x => typeof x === 'string' && x.trim()))
      return {ok:false, status:'invalid'};
    const options = item.options.map(ecpaText), answers = item.answers.map(ecpaText).sort();
    if (options.length < 2 || options.length > 20 || new Set(options).size !== options.length || !answers.length ||
        new Set(answers).size !== answers.length || !answers.every(a => options.includes(a)) ||
        (item.type !== '多選' && answers.length !== 1)) return {ok:false, status:'invalid'};
    records.push({question:ecpaText(item.question), type:item.type, options, answers});
  }
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return {ok:false, status:'busy', retryable:true};
  try {
    const ref = ecpaGit('git/ref/heads/main');
    const head = ref.object.sha;
    const commit = ecpaGit('git/commits/' + head);
    const patches = JSON.parse(ecpaReadAt(ECPA_PATCH_PATH, head, '[]'));
    const evidencePath = 'review/ecpa_evidence/' + ecpaHash(data.course_id) + '.json';
    const receipts = JSON.parse(ecpaReadAt(evidencePath, head, '[]'));
    const versionText = ecpaReadAt(ECPA_VERSION_PATH, head, '0').trim();
    if (!Array.isArray(patches) || !Array.isArray(receipts) || !/^\d+$/.test(versionText)) throw new Error('Invalid evidence storage');
    const seen = new Set(receipts.map(r => r.id));
    let published = 0, accepted = 0, conflicts = 0;
    for (const item of records) {
      const id = ecpaHash(JSON.stringify([data.course_id, item.question, item.type, item.options.slice().sort(), item.answers]));
      if (seen.has(id)) continue;
      seen.add(id);
      const matches = patches.filter(p => ecpaText(p.question) === item.question);
      const old = matches.length === 1 ? matches[0] : null;
      const sameOptions = old && Array.isArray(old.options) && JSON.stringify(old.options.map(ecpaText).sort()) === JSON.stringify(item.options.slice().sort());
      const answer = item.answers.join('、');
      const conflict = matches.length > 1 || (old && (!sameOptions ||
        (old.source === 'platform_disclosed_client' && (old.answer !== answer || String(old.course_id) !== data.course_id)))) ||
        (item.type === '多選' && item.answers.some(a => a.includes('、')));
      const record = {id, ...item, course_id:data.course_id, course:data.course,
        source:'platform_disclosed_client', status:conflict ? 'conflict' : 'confirmed',
        previous_answer:old ? old.answer : null, created_at:new Date().toISOString()};
      receipts.push(record); accepted++;
      if (conflict) { conflicts++; continue; }
      const replacement = {question:item.question, options:item.options, answer, type:item.type,
        course:data.course, course_id:data.course_id, source:'platform_disclosed_client', evidence_id:id};
      if (old) Object.assign(old, replacement); else patches.push(replacement);
      published++;
    }
    if (!accepted) return {ok:true, status:'ok', accepted:0, published:0, conflicts:0};
    const files = [{path:evidencePath, mode:'100644', type:'blob', content:JSON.stringify(receipts,null,2)}];
    if (published) {
      files.push({path:ECPA_PATCH_PATH,mode:'100644',type:'blob',content:JSON.stringify(patches,null,2)});
      files.push({path:ECPA_VERSION_PATH,mode:'100644',type:'blob',content:String(Number(versionText)+1)});
    }
    // Close matching review entries in the same atomic commit as publication.
    const reviewPath = 'review/ecpa/' + ecpaHash(data.course) + '.json';
    const review = JSON.parse(ecpaReadAt(reviewPath, head, '[]'));
    if (!Array.isArray(review)) throw Error('Invalid review queue');
    const reviewBefore = JSON.stringify(review);
    ecpaReconcileReview(review, patches);
    if (JSON.stringify(review) !== reviewBefore)
      files.push({path:reviewPath,mode:'100644',type:'blob',content:JSON.stringify(review,null,2)});
    if (data.dry_run === true) return {ok:true,status:'ok',dry_run:true,accepted,published,conflicts};
    const tree = ecpaGit('git/trees', 'post', {base_tree:commit.tree.sha,tree:files});
    const next = ecpaGit('git/commits', 'post', {message:'fix: publish observed eCPA answer evidence',tree:tree.sha,parents:[head]});
    // Non-forced ref update: concurrent changes fail rather than overwrite another writer.
    ecpaGit('git/refs/heads/main', 'patch', {sha:next.sha,force:false});
    tgSend('eCPA 正解同步\n課程：' + data.course + '\n正式更新：' + published + ' 題\n衝突保留：' + conflicts + ' 題');
    return {ok:true,status:'ok',accepted,published,conflicts,version:Number(versionText)+(published ? 1 : 0)};
  } catch(error) {
    return {ok:false,status:'retry',retryable:true};
  } finally { lock.releaseLock(); }
}
