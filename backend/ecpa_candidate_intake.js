// eCPA candidate intake: generated answers never enter the published bank here.
function handleEcpaMissing(data) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return {ok:false, status:"busy", retryable:true};
  try {
    const course = String(data.course || "未知課程");
    const missing = Array.isArray(data.missing) ? data.missing : [];
    if (!missing.length) return {ok:true, status:"ok", added:0, pending:0};
    const patches = githubGet(ECPA_PATCH_PATH).content;
    if (!Array.isArray(patches)) throw new Error("Invalid published question bank");
    const existing = new Map(patches.map(q => [q.question, q]));
    const digest = text => Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, text, Utilities.Charset.UTF_8)
      .map(b => (b & 255).toString(16).padStart(2,"0")).join("");
    const path = "review/ecpa/" + digest(course) + ".json";
    const file = githubGet(path);
    if (!Array.isArray(file.content)) throw new Error("Invalid review queue");
    const queue = file.content;
    const seen = new Set(queue.map(q => ecpaReviewIdentity({...q, options:ecpaReviewOptions(q)})));
    const pending = [];
    for (const item of missing) {
      if (!item || !String(item.question || "").trim()) continue;
      const question = String(item.question).trim();
      const old = existing.get(question);
      // A repeat missing report is not evidence of an incorrect answer.
      if (old && data.reason !== "exam_failed") continue;
      const options = ecpaReviewOptions(item);
      const reason = old ? "exam_failed_unverified" : "missing";
      const id = digest(JSON.stringify([question, options.slice().sort(), reason]));
      const identity = ecpaReviewIdentity({...item, question, options});
      if (seen.has(identity)) continue;
      seen.add(identity);
      pending.push({id, question, options, type:String(item.type || ""), course,
        status:"pending", reason, previous_answer:old ? old.answer : null,
        candidate_answer:null, source:"reported_question", created_at:new Date().toISOString()});
    }
    if (!pending.length) return {ok:true, status:"ok", added:0, pending:0};
    const unanswered = pending.filter(q => q.reason === "missing");
    if (unanswered.length) {
      try {
        const answers = openAIAnswerBatch(unanswered);
        if (!Array.isArray(answers)) throw new Error("Invalid AI answer format");
        const indices = new Set();
        answers.forEach(a => {
          if (!a || !Number.isInteger(a.idx) || a.idx < 0 || a.idx >= unanswered.length || indices.has(a.idx)) return;
          indices.add(a.idx);
          if (typeof a.answer !== "string" || !a.answer.trim()) return;
          const item = unanswered[a.idx];
          item.candidate_answer = ecpaReviewAnswer(item, a.answer);
          item.source = "ai_unverified";
        });
      } catch (error) {
        // Persist unanswered questions so an AI outage cannot silently lose them.
        pending.forEach(q => { if (q.reason === "missing") q.ai_error = "generation_failed"; });
      }
    }
    queue.push(...pending);
    ecpaReconcileReview(queue, patches);
    githubPut(path, queue, file.sha, "review: queue " + pending.length + " eCPA questions");
    // Pending candidates are handled by the worker; do not notify on each intake.
    return {ok:true, status:"ok", added:0, pending:pending.length, review_required:false, automatic_review:true};
  } finally {
    lock.releaseLock();
  }
}
