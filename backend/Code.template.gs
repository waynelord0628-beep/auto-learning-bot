const USAGE_PROP_KEY = "usage_clients_json";
// Auto Learning Bot GAS backend
// Deploy as a Web App and use the deployment URL as GAS_URL in the client.

const TG_TOKEN = "CONFIGURE_IN_PRIVATE_DEPLOYMENT";
const TG_CHAT_ID = "CONFIGURE_IN_PRIVATE_DEPLOYMENT";
const OPENAI_KEY = "CONFIGURE_IN_PRIVATE_DEPLOYMENT";
const GITHUB_TOKEN = "CONFIGURE_IN_PRIVATE_DEPLOYMENT";
const GITHUB_REPO = "waynelord0628-beep/auto-learning-bot";

const ECPA_PATCH_PATH = "patches/questions_patch.json";
const ECPA_VERSION_PATH = "patches/db_version.txt";
const TAIPEI_QUIZ_PATH = "patches/taipei_quiz_bank.json";
const ONLINE_WINDOW_SECONDS = 180;

function jsonOut(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function tgSend(msg) {
  if (!TG_TOKEN || !TG_CHAT_ID) return;
  // Notification failure must not turn a successful database write into a failure.
  try {
    UrlFetchApp.fetch(`https://api.telegram.org/bot${TG_TOKEN}/sendMessage`, {
      method: "post",
      contentType: "application/json",
      payload: JSON.stringify({ chat_id: TG_CHAT_ID, text: msg })
    });
    return true;
  } catch (err) {
    console.warn("Telegram notification failed; database result is unchanged.");
    return false;
  }
}

function githubHeaders() {
  return {
    Authorization: `token ${GITHUB_TOKEN}`,
    Accept: "application/vnd.github.v3+json"
  };
}

function githubGetRaw(path) {
  const res = UrlFetchApp.fetch(
    `https://api.github.com/repos/${GITHUB_REPO}/contents/${path}`,
    { headers: githubHeaders(), muteHttpExceptions: true }
  );
  const code = res.getResponseCode();
  if (code === 404) return { exists: false, content: "", sha: null };
  if (code < 200 || code >= 300) {
    throw new Error(`GitHub GET ${path} failed: ${code} ${res.getContentText()}`);
  }
  const data = JSON.parse(res.getContentText());
  const text = Utilities.newBlob(Utilities.base64Decode(data.content))
    .getDataAsString("UTF-8")
    .replace(/^\uFEFF/, "");
  return { exists: true, content: text, sha: data.sha };
}

function githubGet(path) {
  const file = githubGetRaw(path);
  return {
    content: file.exists && file.content ? JSON.parse(file.content) : [],
    sha: file.sha
  };
}

function githubGetText(path) {
  const file = githubGetRaw(path);
  return { content: file.content || "", sha: file.sha };
}

function githubPut(path, content, sha, message) {
  const payload = {
    message,
    content: Utilities.base64Encode(
      Utilities.newBlob(JSON.stringify(content, null, 2), "application/json", "data.json").getBytes()
    )
  };
  if (sha) payload.sha = sha;

  const res = UrlFetchApp.fetch(
    `https://api.github.com/repos/${GITHUB_REPO}/contents/${path}`,
    {
      method: "put",
      contentType: "application/json",
      headers: githubHeaders(),
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    }
  );
  const code = res.getResponseCode();
  if (code < 200 || code >= 300) {
    throw new Error(`GitHub PUT ${path} failed: ${code} ${res.getContentText()}`);
  }
}

function githubPutText(path, text, sha, message) {
  const payload = {
    message,
    content: Utilities.base64Encode(Utilities.newBlob(String(text), "text/plain", "data.txt").getBytes())
  };
  if (sha) payload.sha = sha;

  const res = UrlFetchApp.fetch(
    `https://api.github.com/repos/${GITHUB_REPO}/contents/${path}`,
    {
      method: "put",
      contentType: "application/json",
      headers: githubHeaders(),
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    }
  );
  const code = res.getResponseCode();
  if (code < 200 || code >= 300) {
    throw new Error(`GitHub PUT ${path} failed: ${code} ${res.getContentText()}`);
  }
}

function stripJsonFence(raw) {
  return String(raw || "[]")
    .replace(/^```json\s*/i, "")
    .replace(/^```\s*/i, "")
    .replace(/```\s*$/i, "")
    .trim();
}

function callOpenAI(messages) {
  const res = UrlFetchApp.fetch("https://api.openai.com/v1/chat/completions", {
    method: "post",
    contentType: "application/json",
    headers: { Authorization: `Bearer ${OPENAI_KEY}` },
    payload: JSON.stringify({
      model: "gpt-4o-mini",
      temperature: 0,
      messages: messages
    }),
    muteHttpExceptions: true
  });
  const code = res.getResponseCode();
  if (code < 200 || code >= 300) {
    throw new Error(`OpenAI failed: ${code} ${res.getContentText()}`);
  }
  const data = JSON.parse(res.getContentText());
  return data.choices && data.choices[0] && data.choices[0].message
    ? data.choices[0].message.content
    : "[]";
}

function openAIAnswerBatch(questions) {
  const items = questions.map((m, i) => {
    const opts = Array.isArray(m.options) && m.options.length
      ? "\n選項：\n" + m.options.map((o, j) => `${j + 1}. ${o}`).join("\n")
      : "";
    return `[${i}] 類型：${m.type || ""}\n題目：${m.question || ""}${opts}`;
  }).join("\n\n");

  const raw = callOpenAI([
    {
      role: "system",
      content: "你是公務員數位學習測驗助理。請依題目與選項判斷答案，只回 JSON 陣列：[{\"idx\":0,\"answer\":\"...\"}]。answer 請填答案文字或選項文字，不要解釋。是非題請優先回「正確」或「錯誤」，不要只回 1 或 2。"
    },
    { role: "user", content: items }
  ]);
  return JSON.parse(stripJsonFence(raw));
}

function openAITaipeiAnswerBatch(questions) {
  const items = questions.map((q, i) => {
    const opts = ["opt0", "opt1", "opt2", "opt3"]
      .map((k, idx) => q[k] ? `${idx}. ${q[k]}` : "")
      .filter(Boolean)
      .join("\n");
    return `[${i}] course_id=${q.course_id || ""}\n題目：${q.q_text || ""}\n選項：\n${opts}`;
  }).join("\n\n");

  const raw = callOpenAI([
    {
      role: "system",
      content: "請依題目與選項判斷答案，只回 JSON 陣列：[{\"idx\":0,\"val\":\"0\"}]。val 必須是選項 value，例如 0、1、2、3，不要解釋。"
    },
    { role: "user", content: items }
  ]);
  return JSON.parse(stripJsonFence(raw));
}

function taipeiNormalizeKey(text) {
  return cleanTaipeiQuestionText(text).replace(/\s/g, "").substring(0, 30);
}

function cleanTaipeiQuestionText(text) {
  return String(text || "")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^試題文字\s*/, "")
    .replace(/\s*試題\s*\d+\s*$/, "")
    .replace(/\s*試題\s*\d+\s*回答\s*.*$/, "")
    .replace(/\s*回答\s*1[\.、]?\s*.*$/, "")
    .replace(/\s*清除我的選擇\s*$/, "")
    .trim();
}

function taipeiAnswerDisplay(q, val) {
  const v = String(val || "");
  const opt = q && q["opt" + v] ? String(q["opt" + v]) : "";
  return opt ? `${v}（${opt}）` : v;
}

function handleTaipeiQuizGet(e) {
  const courseId = e && e.parameter ? String(e.parameter.course_id || "") : "";
  const file = githubGet(TAIPEI_QUIZ_PATH);
  const bank = Array.isArray(file.content) ? file.content : [];
  const rows = courseId ? bank.filter(r => String(r.course_id) === courseId) : bank;
  return { status: "ok", data: rows };
}

function handleTaipeiQuizSave(payload) {
  const file = githubGet(TAIPEI_QUIZ_PATH);
  const bank = Array.isArray(file.content) ? file.content : [];
  const sha = file.sha;
  let added = 0;
  let updated = 0;

  (payload.questions || []).forEach(q => {
    const qkey = taipeiNormalizeKey(q.q_text);
    const idx = bank.findIndex(r =>
      String(r.course_id) === String(q.course_id) &&
      String(r.q_key) === qkey
    );
    const entry = {
      course_id: q.course_id,
      q_key: qkey,
      q_text: cleanTaipeiQuestionText(q.q_text || ""),
      val: String(q.val || ""),
      opt0: q.opt0 || "",
      opt1: q.opt1 || "",
      opt2: q.opt2 || "",
      opt3: q.opt3 || "",
      updated_at: new Date().toISOString()
    };
    if (idx >= 0) {
      bank[idx] = Object.assign({}, bank[idx], entry);
      updated++;
    } else {
      bank.push(entry);
      added++;
    }
  });

  if (added || updated) {
    githubPut(TAIPEI_QUIZ_PATH, bank, sha, `taipei_quiz: add ${added} update ${updated}`);
  }
  return { ok: true, status: "ok", added, updated };
}

function handleTaipeiQuizMissing(payload) {
  const course = payload.course || "未知課程";
  const username = payload.username || "未知使用者";
  const missing = payload.missing || payload.questions || [];

  if (!missing.length) {
    return { ok: true, status: "ok", added: 0 };
  }

  const file = githubGet(TAIPEI_QUIZ_PATH);
  const bank = Array.isArray(file.content) ? file.content : [];
  const sha = file.sha;
  // Use the existing course/key identity, with a Set for batch deduplication.
  const keyFor = q => JSON.stringify([String(q.course_id), String(q.q_key)]);
  const knownQuestions = new Set(bank.map(keyFor));
  const newQuestions = missing.filter(q => {
    if (!q || !cleanTaipeiQuestionText(q.q_text)) return false;
    const key = keyFor({ course_id: q.course_id, q_key: taipeiNormalizeKey(q.q_text) });
    if (knownQuestions.has(key)) return false;
    knownQuestions.add(key);
    return true;
  });

  if (!newQuestions.length) {
    return { ok: true, status: "ok", added: 0 };
  }

  const answers = openAITaipeiAnswerBatch(newQuestions);
  const answerMap = {};
  answers.forEach(a => { answerMap[a.idx] = String(a.val); });

  let added = 0;
  const results = [];
  newQuestions.forEach((q, i) => {
    const val = answerMap[i];
    if (val == null || val === "") return;
    bank.push({
      course_id: q.course_id,
      q_key: taipeiNormalizeKey(q.q_text),
      q_text: cleanTaipeiQuestionText(q.q_text || ""),
      val: val,
      opt0: q.opt0 || "",
      opt1: q.opt1 || "",
      opt2: q.opt2 || "",
      opt3: q.opt3 || "",
      updated_at: new Date().toISOString()
    });
    results.push({ q: cleanTaipeiQuestionText(q.q_text || "").substring(0, 35), a: taipeiAnswerDisplay(q, val) });
    added++;
  });

  if (!added) {
    tgSend(`臺北E大 OpenAI 未能補題\n使用者：${username}\n課程：${course}`);
    return { ok: false, status: "error", error: "no answers" };
  }

  githubPut(TAIPEI_QUIZ_PATH, bank, sha, `taipei_quiz_missing: add ${added} questions from ${course}`);
  const lines = results.map((r, i) => `Q${i + 1}: ${r.q}...\nA: ${r.a}`).join("\n\n");
  tgSend(`臺北E大題庫已補題\n使用者：${username}\n課程：${course}\n新增：${added} 題\n\n${lines}`);

  return { ok: true, status: "ok", added };
}

function handleEcpaGetDb() {
  const patches = githubGet(ECPA_PATCH_PATH).content || [];
  return patches
    .filter(p => p.question && p.answer)
    .map(p => ({
      question: p.question,
      answer: p.answer,
      option_a: p.options && p.options[0] ? p.options[0] : "",
      option_b: p.options && p.options[1] ? p.options[1] : "",
      option_c: p.options && p.options[2] ? p.options[2] : "",
      option_d: p.options && p.options[3] ? p.options[3] : ""
    }));
}

function normalizeEcpaAnswer(m, answer) {
  let a = String(answer || "").trim();
  const type = String(m.type || "");
  const options = Array.isArray(m.options) ? m.options.map(o => String(o || "").trim()) : [];
  const upper = a.toUpperCase();

  if (type.indexOf("是非") >= 0 || options.length === 2) {
    if (["1", "A", "O", "T", "TRUE", "YES"].includes(upper)) return options[0] || "正確";
    if (["2", "B", "X", "F", "FALSE", "NO"].includes(upper)) return options[1] || "錯誤";
    if (a.indexOf("正確") >= 0 || a === "是" || a === "對" || a === "○") return options[0] || "正確";
    if (a.indexOf("錯誤") >= 0 || a === "否" || a === "錯" || a === "╳") return options[1] || "錯誤";
  }

  if (/^[1-9]$/.test(a)) {
    const idx = parseInt(a, 10) - 1;
    if (options[idx]) return options[idx];
  }

  return a;
}

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
function handleUsageAction(payload) {
  const now = new Date();
  const nowSec = Math.floor(now.getTime() / 1000);
  const deviceId = String(payload.device_id || "").trim();
  const clients = loadUsageClients();

  if (payload.action === "usage_ping" && deviceId) {
    const old = clients[deviceId] || {};
    clients[deviceId] = {
      device_id: deviceId,
      version: String(payload.version || ""),
      platform: String(payload.platform || ""),
      platform_release: String(payload.platform_release || ""),
      login_type: String(payload.login_type || ""),
      screen: String(payload.screen || ""),
      first_seen: old.first_seen || now.toISOString(),
      last_seen: now.toISOString(),
      last_seen_sec: nowSec
    };
    saveUsageClients(clients);
  }

  const stats = computeUsageStats(clients, nowSec);
  return {
    ok: true,
    status: "ok",
    online: stats.online,
    online_count: stats.online,
    total_devices: stats.total_devices,
    version_counts: stats.version_counts
  };
}

function loadUsageClients() {
  const raw = PropertiesService.getScriptProperties().getProperty(USAGE_PROP_KEY);
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (err) {
    return {};
  }
}

function saveUsageClients(clients) {
  // 清掉太久沒看見的 client，避免 Script Properties 無限長大。
  const nowSec = Math.floor(Date.now() / 1000);
  Object.keys(clients).forEach(id => {
    const lastSeen = Number(clients[id].last_seen_sec || 0);
    if (!lastSeen || nowSec - lastSeen > 60 * 60 * 24 * 30) {
      delete clients[id];
    }
  });
  PropertiesService.getScriptProperties().setProperty(
    USAGE_PROP_KEY,
    JSON.stringify(clients)
  );
}

function computeUsageStats(clients, nowSec) {
  let online = 0;
  const versionCounts = {};
  const ids = Object.keys(clients || {});

  ids.forEach(id => {
    const row = clients[id] || {};
    const lastSeen = Number(row.last_seen_sec || 0);
    const version = String(row.version || "unknown");
    versionCounts[version] = (versionCounts[version] || 0) + 1;
    if (nowSec - lastSeen <= ONLINE_WINDOW_SECONDS) {
      online++;
    }
  });

  return {
    online,
    total_devices: ids.length,
    version_counts: versionCounts
  };
}

function doGet(e) {
  const action = e && e.parameter ? e.parameter.action : "";

  try {
    if (action === "taipei_quiz_get") return jsonOut(handleTaipeiQuizGet(e));
    if (action === "usage_stats") return jsonOut(handleUsageAction({ action: "usage_stats" }));
    if (action === "get_db") return jsonOut(handleEcpaGetDb());
    return jsonOut([]);
  } catch (err) {
    return jsonOut({ ok: false, status: "error", error: err.message });
  }
}

function doPost(e) {
  let action = "invalid_request";
  try {
    const data = JSON.parse(e.postData.contents || "{}");
    action = data.action || "ecpa_missing";

    if (data.action === "ecpa_evidence") return jsonOut(handleEcpaEvidence(data));
    if (data.action === "usage_ping" || data.action === "usage_stats") {
      return jsonOut(handleUsageAction(data));
    }
    if (data.action === "taipei_quiz_save") {
      return jsonOut(handleTaipeiQuizSave(data));
    }
    if (data.action === "taipei_quiz_missing") {
      return jsonOut(handleTaipeiQuizMissing(data));
    }

    return jsonOut(handleEcpaMissing(data));
  } catch (err) {
    reportBackendError(err, action);
    return jsonOut({ ok: false, status: "error", error: err.message });
  }
}

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

// Queue maintenance never promotes an AI answer into the published bank.
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
function processEcpaReviewQueue() {
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

// Error reporting must never amplify heartbeat traffic or leak request payloads.
function reportBackendError(error, action) {
  console.error('Backend request failed: ' + String(error && error.name || 'Error'));
  if (action === 'usage_ping' || action === 'usage_stats') return;
  let lock;
  try {
    lock = LockService.getScriptLock();
    if (!lock.tryLock(100)) return;
    const props = PropertiesService.getScriptProperties();
    const now = Date.now(), last = Number(props.getProperty('BACKEND_ERROR_ALERT_LAST_MS') || 0);
    if (last && now - last < 3600000) return;
    props.setProperty('BACKEND_ERROR_ALERT_LAST_MS', String(now));
    tgSend('GAS 後端發生錯誤，請查看執行記錄。同類通知每小時最多一則。');
  } catch (ignored) {
    // Notification/storage failures must not trigger another notification.
  } finally { if (lock) { try { lock.releaseLock(); } catch (ignored) {} } }
}
