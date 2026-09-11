// Maintenance consumes evidence; the separate source verifier performs research.
function ecpaReviewIdentity(q) {
  return JSON.stringify([ecpaText(q.question || ''), String(q.type || ''),
    (q.options || []).map(ecpaText).sort()]);
}
function ecpaReviewOptions(q) {
  let options = Array.isArray(q.options) ? q.options.map(ecpaText) : [];
  if (q.type === '是非' && options.length === 2 && options.every(x => !x))
    options = ['正確', '錯誤'];
  return options;
}
function ecpaReviewAnswer(q, value) {
  if (typeof value !== 'string') return null;
  if (q.type === '多選') return null; // Released clients cannot import an answer array yet.
  const options = ecpaReviewOptions(q), answer = ecpaText(value);
  if (options.includes(answer) && answer) return answer;
  // Remove numbering only when the remaining text exactly matches an option.
  const stripped = answer.replace(/^(?:\(?[1-9A-Da-d]\)?[.．、:：)]\s*)/, '');
  if (stripped !== answer && options.includes(stripped)) return stripped;
  const numbered = options.filter(o => o.replace(/^[1-9A-Da-d][.．、:：)]\s+/, '') === answer);
  if (numbered.length === 1) return numbered[0];
  if (q.type === '是非') {
    if (/^(TRUE|T|O|YES|是|對|○)$/i.test(answer) && options.includes('正確')) return '正確';
    if (/^(FALSE|F|X|NO|否|錯|╳)$/i.test(answer) && options.includes('錯誤')) return '錯誤';
  }
  return null; // No numeric guessing or multi-select splitting.
}
function ecpaReconcileReview(queue, patches) {
  const bank = new Map();
  patches.forEach(p => {
    if (!p || !Array.isArray(p.options)) return;
    const key = ecpaReviewIdentity(p);
    if (!bank.has(key)) bank.set(key, []);
    bank.get(key).push(p);
  });
  const seen = new Map();
  for (const q of queue) {
    if (!q || typeof q.question !== 'string') continue;
    q.options = ecpaReviewOptions(q);
    const key = ecpaReviewIdentity(q), first = seen.get(key);
    if (first) {
      q.status = 'duplicate'; q.review_state = 'duplicate'; q.duplicate_of = first.id;
      // Disagreement between duplicate candidates is not resolved by last-write-wins.
      const a = ecpaReviewAnswer(first, first.candidate_answer), b = ecpaReviewAnswer(q, q.candidate_answer);
      if (a && b && a !== b) {
        first.candidate_answer = null; first.review_state = 'candidate_conflict';
        first.candidate_conflict = true;
      }
      continue;
    }
    seen.set(key, q);
    const matches = (bank.get(key) || []).filter(p => p.course === q.course);
    const trusted = matches.filter(p => p.source === 'platform_disclosed_client' && p.evidence_id);
    const answers = [...new Set(trusted.map(p => p.answer))];
    if (answers.length === 1) {
      q.status = 'resolved'; q.review_state = 'platform_confirmed';
      q.resolved_answer = answers[0]; q.evidence_id = trusted[0].evidence_id;
      q.candidate_answer = null;
      continue;
    }
    if (answers.length > 1) {
      q.status = 'pending'; q.review_state = 'evidence_conflict'; q.candidate_answer = null;
      continue;
    }
    const reviewed = matches.filter(p => p.source_review &&
      ((p.source === 'official_source_reviewed' && p.source_review.method === 'official_source_two_pass_v1') ||
       (p.source === 'course_answer_page' && p.source_review.method === 'course_answer_exact_v1')));
    if (reviewed.length === 1 && !trusted.length) {
      q.status = 'resolved'; q.review_state = 'source_reviewed';
      q.resolved_answer = reviewed[0].answer; q.candidate_answer = null;
      continue;
    }
    // Keep status=pending for released 2.1.9 readers; review_state carries detail.
    q.status = 'pending';
    if (q.options.length < 2 || q.options.some(x => !x) || new Set(q.options).size !== q.options.length) {
      q.review_state = 'awaiting_options'; q.candidate_answer = null; continue;
    }
    if (q.reason === 'exam_failed_unverified' || q.candidate_conflict) {
      q.review_state = q.candidate_conflict ? 'candidate_conflict' : 'awaiting_evidence';
      q.candidate_answer = null; continue;
    }
    if (q.type === '多選') {
      q.review_state = 'awaiting_multiselect_support';
      if (q.candidate_answer) q.original_candidate_answer = q.original_candidate_answer || q.candidate_answer;
      q.candidate_answer = null; continue;
    }
    const answer = ecpaReviewAnswer(q, q.candidate_answer);
    q.candidate_answer = answer;
    q.review_state = answer ? 'candidate_ready' : ((q.retry_count || 0) >= 3 ? 'awaiting_evidence' : 'awaiting_candidate');
    if (answer) q.source = 'ai_unverified';
  }
  return queue;
}
function ecpaRetryCandidates(queue, budget, now) {
  const retry = queue.filter(q => q.status === 'pending' && q.review_state === 'awaiting_candidate' &&
    (q.retry_count || 0) < 3 && (!q.retry_after || Date.parse(q.retry_after) <= now)).slice(0, budget);
  if (!retry.length) return 0;
  retry.forEach(q => { q.retry_count = (q.retry_count || 0) + 1;
    q.retry_after = new Date(now + 86400000).toISOString(); });
  try {
    const answers = openAIAnswerBatch(retry), seen = new Set();
    if (!Array.isArray(answers)) throw Error('Invalid answer format');
    answers.forEach(a => {
      if (!a || !Number.isInteger(a.idx) || a.idx < 0 || a.idx >= retry.length || seen.has(a.idx)) return;
      seen.add(a.idx);
      const q = retry[a.idx], answer = ecpaReviewAnswer(q, a.answer);
      if (answer) { q.candidate_answer = answer; q.source = 'ai_unverified';
        q.review_state = 'candidate_ready'; delete q.ai_error; }
    });
  } catch (e) { retry.forEach(q => { q.ai_error = 'generation_failed'; }); }
  retry.forEach(q => { if (!q.candidate_answer && q.retry_count >= 3) q.review_state = 'awaiting_evidence'; });
  return retry.length;
}
function maintainEcpaReviewQueue() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return {ok:false, status:'busy'};
  try {
    const head = ecpaGit('git/ref/heads/main').object.sha;
    const commit = ecpaGit('git/commits/' + head);
    const listing = JSON.parse(ecpaReadAtDirectory('review/ecpa', head));
    const paths = listing.filter(f => f.type === 'file' && /^[a-f0-9]{64}\.json$/.test(f.name)).map(f => f.path).sort();
    if (!paths.length) return {ok:true, processed:0};
    if (listing.length >= 1000) throw Error('Review directory needs sharding');
    const props = PropertiesService.getScriptProperties();
    const previous = props.getProperty('ECPA_REVIEW_CURSOR') || '';
    let start = paths.findIndex(p => p > previous); if (start < 0) start = 0;
    const selected = paths.slice(start, start + 10);
    const patches = JSON.parse(ecpaReadAt(ECPA_PATCH_PATH, head, '[]'));
    if (!Array.isArray(patches)) throw Error('Invalid bank');
    const files = []; let budget = 10, last = previous;
    const deadline = Date.now() + 180000;
    for (const path of selected) {
      if (Date.now() >= deadline) break;
      const before = ecpaReadAt(path, head, '[]'), queue = JSON.parse(before);
      if (!Array.isArray(queue)) throw Error('Invalid queue');
      const original = JSON.stringify(queue);
      ecpaReconcileReview(queue, patches);
      budget -= ecpaRetryCandidates(queue, budget, Date.now());
      if (JSON.stringify(queue) !== original) files.push({path, mode:'100644',type:'blob',content:JSON.stringify(queue,null,2)});
      last = path;
    }
    if (files.length) {
      const tree = ecpaGit('git/trees','post',{base_tree:commit.tree.sha,tree:files});
      const next = ecpaGit('git/commits','post',{message:'review: reconcile candidate queue automatically',tree:tree.sha,parents:[head]});
      ecpaGit('git/refs/heads/main','patch',{sha:next.sha,force:false});
    }
    props.setProperty('ECPA_REVIEW_CURSOR',last);
    props.setProperty('ECPA_REVIEW_LAST_OK',new Date().toISOString());
    return {ok:true, changed_files:files.length};
  } finally { lock.releaseLock(); }
}
function ecpaReadAtDirectory(path, head) {
  return JSON.stringify(ecpaGit('contents/' + path + '?ref=' + head));
}
