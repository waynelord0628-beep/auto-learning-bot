"""
臺北e大 測驗自動作答模組
- 查 GAS 題庫 → 命中直接答 → 送出
- 未知題：呼叫 AI 分析答案 → 填答 → 送出 → 存 GAS
- AI 失敗 fallback：猜 val=0 送出 → review 讀正解 → 存 GAS → 再考一次
"""

import re, json, time, requests, difflib, threading
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoAlertPresentException

GAS_URL = 'https://script.google.com/macros/s/AKfycbzYUNM--zLlS8El6YR6lIiKerBIz1M6rL2gM8nTGicmEjfh_1TNiBo12YcVsb37J7Cl/exec'


# ── 工具 ─────────────────────────────────────────────

def _normalize(text):
    """去除空白標點，只留中英數，用於模糊比對 key"""
    return re.sub(r'[^\w\u4e00-\u9fff]', '', text or '').strip()

def _dismiss_alerts(driver):
    for _ in range(5):
        try:
            driver.switch_to.alert.accept(); time.sleep(0.4)
        except NoAlertPresentException:
            break

def _click_js(driver, el):
    driver.execute_script("arguments[0].scrollIntoView(true); arguments[0].click();", el)


# ── GAS 題庫存取 ──────────────────────────────────────

def gas_fetch_bank(course_id):
    """
    從 GAS 取得該課程題庫。
    回傳 dict: {normalize(q_text): val_str}
    支援兩種回傳格式：
      - 新格式：{status:'ok', data:[...]}
      - 舊/未支援：[] 或其他 list
    """
    print(f'  [題庫] 正在從 GAS 載入 course_id={course_id}')
    try:
        r = requests.get(GAS_URL,
                         params={'action': 'taipei_quiz_get', 'course_id': str(course_id)},
                         timeout=10)
        resp = r.json()
        # 支援兩種格式
        if isinstance(resp, dict):
            data = resp.get('data', [])
        elif isinstance(resp, list):
            data = resp  # 舊格式或 taipei_eda_quiz sheet 尚未建立
        else:
            data = []
        bank = {}
        for row in data:
            if not isinstance(row, dict): continue
            key = _normalize(row.get('q_text', ''))
            if key:
                bank[key] = str(row.get('val', '0'))
        if bank:
            print(f'  [題庫] 從 GAS 載入 {len(bank)} 題（course_id={course_id}）')
        else:
            print(f'  [題庫] GAS 目前沒有此課程題庫（course_id={course_id}），將用 AI/猜題建立題庫')
        return bank
    except Exception as e:
        print(f'  [題庫] GAS 載入失敗: {e}')
        return {}

def _taipei_question_payload(course_id, q):
    opts = q.get('options', {}) or {}
    return {
        'course_id': str(course_id),
        'q_text': q.get('qtext', ''),
        'opt0': opts.get('0', ''),
        'opt1': opts.get('1', ''),
        'opt2': opts.get('2', ''),
        'opt3': opts.get('3', ''),
    }

def gas_report_missing_questions(course_id, questions, course_name='', username='', config=None):
    """Report Taipei E-da missing questions to GAS/TG without blocking the quiz."""
    if not questions:
        return

    seen = set()
    missing = []
    for q in questions:
        key = _normalize(q.get('qtext', ''))
        if not key or key in seen:
            continue
        seen.add(key)
        missing.append(_taipei_question_payload(course_id, q))

    if not missing:
        return

    gas_url = (config or {}).get('gas_url') or GAS_URL
    payload = {
        'action': 'taipei_quiz_missing',
        'course_id': str(course_id),
        'course': course_name or '未知課程',
        'username': username or '匿名',
        'missing': missing,
    }

    def _post():
        try:
            r = requests.post(gas_url, json=payload, timeout=20)
            result = r.json()
            if result.get('status') == 'ok':
                print(f'  [缺題] 已回報 GAS/TG（{len(missing)} 題，GAS新增 {result.get("added", 0)} 題）')
            else:
                print(f'  [缺題] GAS 回傳異常: {result}')
        except Exception as e:
            print(f'  [缺題] 回報失敗: {e}')

    threading.Thread(target=_post, daemon=True).start()
    print(f'  [缺題] 回報已背景送出（{len(missing)} 題）')

def gas_save_questions(course_id, questions_with_answers, course_name='', username=''):
    """
    把新答案存回 GAS/GitHub 共用題庫。
    questions_with_answers: [{q_text, val, opt0..opt3}, ...]
    """
    if not questions_with_answers:
        return
    payload = {
        'action': 'taipei_quiz_save',
        'course_id': str(course_id),
        'course': course_name,
        'username': username,
        'questions': [dict(course_id=course_id, **q) for q in questions_with_answers]
    }
    try:
        r = requests.post(GAS_URL, json=payload, timeout=15)
        result = r.json()
        if result.get('status') == 'ok':
            print(f'  [題庫] 已同步共用題庫: added={result.get("added")}, updated={result.get("updated")}')
        else:
            print(f'  [題庫] GAS 回傳異常: {result}')
    except Exception as e:
        print(f'  [題庫] GAS 存入失敗: {e}')

def lookup_bank(bank, q_text, threshold=0.75):
    """在 bank 裡模糊查找 q_text，回傳 val str 或 None"""
    key = _normalize(q_text)
    if key in bank:
        return bank[key]
    matches = difflib.get_close_matches(key, bank.keys(), n=1, cutoff=threshold)
    if matches:
        print(f'  [題庫] fuzzy: {matches[0][:20]}...')
        return bank[matches[0]]
    return None


# ── AI 分析答案 ──────────────────────────────────────

def ai_guess_answer(q_text, options, config):
    """
    呼叫 AI API（OpenAI-compatible 或 Claude）分析題目，回傳正確選項的 val str。
    config: {ai_provider, ai_keys:{Provider:key}, ai_base_url, ai_model}
    回傳 val str（'0'~'3'）或 None（失敗）
    """
    provider = config.get('ai_provider', 'OpenAI')
    ai_keys  = config.get('ai_keys', {})
    api_key  = ai_keys.get(provider) or config.get('ai_api_key', '')
    if not api_key:
        print('  [AI] 無 API key，跳過')
        return None

    base_url = config.get('ai_base_url', 'https://api.openai.com/v1').rstrip('/')
    model    = config.get('ai_model', 'gpt-4o-mini')

    # 建立選項文字清單（去掉編號前綴如 "1. "）
    opts_clean = {}
    for val, text in options.items():
        clean = re.sub(r'^\d+\.\s*', '', text).strip()
        opts_clean[val] = clean

    options_str = '\n'.join(f'{val}. {text}' for val, text in sorted(opts_clean.items()))
    prompt = (
        '你是考試作答助手。請從以下選項中選出正確答案，'
        '只回答正確選項的完整文字，不要編號、不要解釋、不要標點。\n\n'
        f'題目：{q_text}\n\n'
        f'選項：\n{options_str}\n\n'
        '正確答案：'
    )

    try:
        if provider == 'Claude':
            resp = requests.post(
                f'{base_url}/messages',
                headers={'x-api-key': api_key, 'anthropic-version': '2023-06-01',
                         'Content-Type': 'application/json'},
                json={'model': model, 'max_tokens': 150,
                      'messages': [{'role': 'user', 'content': prompt}]},
                timeout=20, verify=False)
            resp.raise_for_status()
            ai_answer = resp.json()['content'][0]['text'].strip()
        else:
            resp = requests.post(
                f'{base_url}/chat/completions',
                headers={'Authorization': f'Bearer {api_key}',
                         'Content-Type': 'application/json'},
                json={'model': model, 'temperature': 0, 'max_tokens': 150,
                      'messages': [{'role': 'user', 'content': prompt}]},
                timeout=20, verify=False)
            resp.raise_for_status()
            ai_answer = resp.json()['choices'][0]['message']['content'].strip()

        print(f'  [AI] 回答: {ai_answer!r}')

        # 去掉 AI 回答的編號前綴（如 "1. 是" → "是"）
        ai_clean = re.sub(r'^\d+[\.、\s]+', '', ai_answer).strip()

        # 把 AI 回答文字比對回 val
        ai_norm = _normalize(ai_clean)
        # 精確比對
        for val, text in opts_clean.items():
            if _normalize(text) == ai_norm:
                print(f'  [AI] 命中 val={val}')
                return val
        # fuzzy 比對
        clean_texts = list(opts_clean.values())
        matches = difflib.get_close_matches(ai_clean, clean_texts, n=1, cutoff=0.6)
        if matches:
            for val, text in opts_clean.items():
                if text == matches[0]:
                    print(f'  [AI] fuzzy 命中 val={val}')
                    return val

        print(f'  [AI] 無法比對回選項，回答: {ai_answer!r}')
        return None

    except Exception as e:
        print(f'  [AI] 呼叫失敗: {e}')
        return None


def ai_guess_answer_retry(q_text, options, wrong_val, config):
    """
    AI 重答：已知 wrong_val 是錯的，排除後重新猜。
    回傳 val str 或 None。
    """
    opts_excl = {v: t for v, t in options.items() if v != wrong_val}
    if not opts_excl:
        return None
    return ai_guess_answer(q_text, opts_excl, config)


# ── Moodle 測驗操作 ──────────────────────────────────

def _start_or_resume_quiz(driver, wait, quiz_view_url):
    """進測驗頁，點開始/繼續，回傳 attempt URL"""
    driver.get(quiz_view_url)
    time.sleep(3)
    _dismiss_alerts(driver)

    for xpath in [
        '//button[contains(text(),"繼續")] | //a[contains(text(),"繼續")]',
        '//button[contains(text(),"開始測驗")]',
        '//button[contains(text(),"再測驗一次")] | //a[contains(text(),"再測驗一次")]',
    ]:
        try:
            btn = driver.find_element(By.XPATH, xpath)
            _click_js(driver, btn)
            time.sleep(2)
            _dismiss_alerts(driver)
            print(f'  [測驗] 開始/繼續 → {driver.current_url}')
            return driver.current_url
        except:
            pass
    return None

def _read_questions(driver):
    """
    讀取作答頁所有題目與選項。
    回傳 list of dict:
      { name, qtext, options: {val: text}, prefix }
    """
    result = driver.execute_script("""
        function clean(s) { return (s || '').replace(/\\s+/g, ' ').trim(); }
        var out = [];
        var blocks = document.querySelectorAll('.que, div[id^="question-"], .formulation');
        blocks.forEach(function(q, qi) {
            var qtext = q.querySelector('.qtext, .questiontext, .content');
            var qtxt = qtext ? clean(qtext.textContent) : '';
            if (!qtxt) {
                var body = clean(q.textContent);
                qtxt = body.split('\\n')[0] || body.slice(0, 160);
            }
            var opts = {};
            q.querySelectorAll('input[type=radio]').forEach(function(r) {
                var val = r.value;
                if (val === '-1') return;
                var name = r.name;
                var labelTxt = '';
                var labelEl = document.getElementById(r.id + '_label');
                if (labelEl) labelTxt = clean(labelEl.textContent);
                if (!labelTxt) {
                    var lbl = q.querySelector('label[for="' + r.id + '"]');
                    if (lbl) labelTxt = clean(lbl.textContent);
                }
                if (!labelTxt) {
                    var parent = r.closest('div, p, span, li');
                    if (parent) labelTxt = clean(parent.textContent);
                }
                if (!opts._name) opts._name = name;
                opts[val] = labelTxt;
            });
            if (opts._name && qtxt) {
                out.push({name: opts._name, qtext: qtxt, options: opts});
            }
        });
        return out;
    """)
    seen = set()
    deduped = []
    for q in result:
        q['options'].pop('_name', None)
        key = (q.get('name'), _normalize(q.get('qtext', '')))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(q)
    return deduped

def _fill_answers(driver, answers):
    """
    填答：answers = {name: val}
    """
    for name, val in answers.items():
        try:
            r = driver.find_element(By.CSS_SELECTOR,
                f'input[type=radio][name="{name}"][value="{val}"]')
            driver.execute_script("arguments[0].click();", r)
            driver.execute_script(
                "arguments[0].checked=true;"
                "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", r)
        except Exception as e:
            print(f'  [填答] ✗ {name}={val}: {e}')

def _submit_quiz(driver, wait):
    """點完成作答 → summary → 全部送出並結束 → modal confirm → review URL"""
    # 完成作答
    finish = driver.execute_script("""
        for (var b of document.querySelectorAll('button,input[type=submit]')) {
            if ((b.value||b.textContent||'').trim().indexOf('完成作答') !== -1) return b;
        }
        return null;
    """)
    if not finish:
        print('  [測驗] ✗ 找不到完成作答')
        return None
    _click_js(driver, finish)
    time.sleep(2); _dismiss_alerts(driver)

    # 全部送出並結束
    confirm_btn = driver.execute_script("""
        for (var b of document.querySelectorAll('button,input[type=submit]')) {
            var t = (b.value||b.textContent||'').trim();
            if (t.indexOf('全部送出並結束') !== -1 || t.indexOf('送出所有答案並結束') !== -1) return b;
        }
        return null;
    """)
    if confirm_btn:
        driver.execute_script("arguments[0].click();", confirm_btn)
        time.sleep(1.5)
        try:
            modal_save = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[data-action="save"]'))
            )
            driver.execute_script("arguments[0].click();", modal_save)
        except:
            try: driver.switch_to.alert.accept()
            except: pass
        time.sleep(2); _dismiss_alerts(driver)

    print(f'  [測驗] review → {driver.current_url}')
    return driver.current_url

def _read_correct_from_review(driver):
    """
    從 review 頁讀每題的正解 val。
    策略：
      1. 找 .answer div.correct 裡的 radio（Moodle 標示正確選項，需開啟顯示正解）
      2. 若題目整體標示 .que.correct → checked radio = 正解
      3. 若題目整體標示 .que.incorrect → 記錄題目是非題還是 MCQ
         - 是非題 (options=2)：翻轉 checked val
         - MCQ：無法判斷，回傳 None（後續 AI 重答）
    回傳 {name: val}（確認正確的才加入）
    """
    result = driver.execute_script("""
        var out = {};
        document.querySelectorAll('.que').forEach(function(q) {
            var name   = null;
            var val    = null;
            var radios = Array.from(q.querySelectorAll('input[type=radio]')).filter(
                function(r){ return r.value !== '-1'; });
            if (!radios.length) return;

            // 1. 優先：.answer div.correct 裡有 radio（正解標示）
            var correctDiv = q.querySelector(
                '.answer .r0.correct, .answer .r1.correct, ' +
                '.answer .r2.correct, .answer .r3.correct, ' +
                '.answer div.correct');
            if (correctDiv) {
                var r = correctDiv.querySelector('input[type=radio]');
                if (r && r.value !== '-1') { name = r.name; val = r.value; }
            }

            // 2. 題目本身標示 correct（我們答對了）
            if (!name && q.classList.contains('correct')) {
                radios.forEach(function(r){ if (r.checked) { name=r.name; val=r.value; } });
            }

            // 3. 題目本身標示 incorrect（答錯了）
            var isIncorrect = q.classList.contains('incorrect');
            if (!name && isIncorrect) {
                var checked_r = null;
                radios.forEach(function(r){ if (r.checked) checked_r = r; });
                if (checked_r) {
                    // 是非題 (2個選項)：翻轉
                    if (radios.length === 2) {
                        var other = radios.find(function(r){ return r !== checked_r; });
                        if (other) { name = other.name; val = other.value; }
                    }
                    // MCQ：無法確定，跳過，避免把錯選項存回題庫
                }
            }

            // 4. 最後 fallback：checked radio，只在頁面沒有標示 incorrect 時保底
            if (!name && !isIncorrect) {
                radios.forEach(function(r){ if (r.checked) { name=r.name; val=r.value; } });
            }

            if (name && val !== null) out[name] = val;
        });
        return out;
    """)
    return result

def _get_score_from_review(driver):
    """從 review 頁讀成績，回傳 (score_text, is_100)"""
    try:
        body = driver.find_element(By.TAG_NAME, 'body').text
        for line in body.split('\n'):
            if '分' in line and ('得' in line or '滿分' in line):
                return line.strip()
    except: pass
    return ''

def _is_100(score_text):
    """判斷成績是否為 100 分（抓「得X.XX分」的分子）"""
    m = re.search(r'得\s*([\d.]+)\s*分', score_text)
    if m:
        try: return float(m.group(1)) >= 100
        except: pass
    # fallback: 找 X/Y 格式
    m2 = re.search(r'(\d+)\s*/\s*\d+', score_text)
    if m2:
        try: return int(m2.group(1)) >= 100
        except: pass
    return False


# ── 主函式 ──────────────────────────────────────────

def do_quiz_with_bank(driver, wait, course_id, quiz_view_url, config=None, course_name='', username=''):
    """
    臺北E大測驗流程：
    1. 第一次只用 GAS 題庫作答。
    2. 不及格才第二次啟用本機 AI 補答。
    3. 若本機無 AI 或仍未通過，最多跑 3 次；缺題背景回報 GAS/TG，由 GAS 端 AI 補 JSON DB。
    回傳 (score_text, is_100: bool)
    """
    if config is None:
        config = {}

    provider = config.get('ai_provider', 'OpenAI')
    ai_keys = config.get('ai_keys', {}) or {}
    has_local_ai = bool(ai_keys.get(provider) or config.get('ai_api_key', ''))

    print(f'\n=== 測驗 (course_id={course_id}) ===')
    print('  [題庫] 使用臺北E大 GAS/GitHub 共用題庫')
    print('  [測驗] 第一次只用題庫；未通過才啟用本機 AI')

    best_score_text = ''
    best_is_100 = False
    reported_missing_keys = set()

    def _question_record(q, val):
        return {
            'q_text': q['qtext'], 'val': val,
            'opt0': q['options'].get('0',''), 'opt1': q['options'].get('1',''),
            'opt2': q['options'].get('2',''), 'opt3': q['options'].get('3',''),
        }

    def _save_known_answers(to_save):
        if not to_save:
            return
        deduped = []
        seen = set()
        for item in to_save:
            key = _normalize(item.get('q_text', ''))
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        if deduped:
            gas_save_questions(course_id, deduped, course_name=course_name, username=username)
            print(f'  [題庫] 本次同步共用題庫 {len(deduped)} 題')

    def _report_missing_once(missing_qs):
        fresh = []
        for q in missing_qs:
            key = _normalize(q.get('qtext', ''))
            if not key or key in reported_missing_keys:
                continue
            reported_missing_keys.add(key)
            fresh.append(q)
        if fresh:
            gas_report_missing_questions(
                course_id,
                fresh,
                course_name=course_name,
                username=username,
                config=config,
            )

    def _run_attempt(attempt_no, use_local_ai):
        print(f'\n  [測驗] 第 {attempt_no} 次作答：' + ('題庫 + 本機AI' if use_local_ai else '只用題庫'))

        bank = gas_fetch_bank(course_id)
        attempt_url = _start_or_resume_quiz(driver, wait, quiz_view_url)
        if not attempt_url:
            print('  [測驗] ✗ 無法開始作答')
            return '', False, {}, {}

        questions = _read_questions(driver)
        print(f'  [測驗] 讀到 {len(questions)} 題')
        if not questions:
            try:
                body_preview = driver.find_element(By.TAG_NAME, 'body').text[:300]
            except Exception:
                body_preview = ''
            print(f'  [測驗] ⚠️ 沒讀到題目，頁面文字預覽: {body_preview!r}')

        answers = {}
        missing_qs = []
        ai_answered = []

        for q in questions:
            val = lookup_bank(bank, q['qtext'])
            if val is not None:
                answers[q['name']] = val
                print(f'  ✓ [庫] {q["qtext"][:28]}... → val={val}')
                continue

            if use_local_ai and has_local_ai:
                val = ai_guess_answer(q['qtext'], q['options'], config)
                if val is not None:
                    answers[q['name']] = val
                    ai_answered.append((q, val))
                    print(f'  ✓ [AI] {q["qtext"][:28]}... → val={val}')
                    continue

            answers[q['name']] = '0'
            missing_qs.append(q)
            print(f'  ? [猜] {q["qtext"][:28]}... → val=0')

        _report_missing_once(missing_qs)

        _fill_answers(driver, answers)
        _submit_quiz(driver, wait)
        time.sleep(2)

        correct_by_name = _read_correct_from_review(driver)
        score_text = _get_score_from_review(driver)
        is_100 = _is_100(score_text)
        print(f'  [成績{attempt_no}] {score_text}  (100分: {is_100})')

        name_to_q = {q['name']: q for q in questions}
        correct_by_text = {}
        to_save = []

        for name, val in correct_by_name.items():
            q = name_to_q.get(name)
            if not q:
                continue
            correct_by_text[q['qtext']] = val
            to_save.append(_question_record(q, val))

        for q, ai_val in ai_answered:
            real_val = correct_by_name.get(q['name'])
            if real_val and real_val != ai_val:
                print(f'  [AI誤] {q["qtext"][:28]}... AI={ai_val} 正解={real_val}')
            if not real_val:
                to_save.append(_question_record(q, ai_val))

        _save_known_answers(to_save)
        return score_text, is_100, correct_by_text, answers

    attempt_plan = [False]
    if has_local_ai:
        attempt_plan.append(True)
    else:
        print('  [AI] 本機未設定 AI key，將用題庫/猜答最多跑 3 次，缺題交給 GAS 端 AI 補庫')
        attempt_plan.append(False)
    attempt_plan.append(False)

    for idx, use_ai in enumerate(attempt_plan, start=1):
        score_text, is_100, _, _ = _run_attempt(idx, use_ai)
        if score_text:
            best_score_text = score_text
            best_is_100 = is_100
        if is_100:
            return score_text, True
        if idx == 1:
            print('  [測驗] 第一次未滿分，準備第二次補答')
        elif idx < len(attempt_plan):
            print('  [測驗] 尚未滿分，繼續下一次')

    return best_score_text, best_is_100


def do_feedback(driver, wait, feedback_view_url):
    """填問卷：radio 選最大值，textarea 填預設文字，送出"""
    print(f'\n=== 問卷 ===')
    driver.get(feedback_view_url)
    time.sleep(3)
    _dismiss_alerts(driver)

    # 偵測是否已完成
    page_text = driver.find_element(By.TAG_NAME, 'body').text
    done_keywords = ['謝謝您的回覆', '您已經完成這活動', '已完成', 'already completed']
    if any(kw in page_text for kw in done_keywords) or 'completed' in driver.current_url:
        print('  ✅ 問卷已完成（跳過）')
        return True

    try:
        start = driver.find_element(By.XPATH,
            '//a[contains(text(),"開始填寫") or contains(text(),"填寫回答") or contains(text(),"再次填寫")]')
        driver.get(start.get_attribute('href'))
        time.sleep(3)
        _dismiss_alerts(driver)
    except: pass

    radio_groups = {}
    for r in driver.find_elements(By.CSS_SELECTOR, 'input[type=radio]'):
        name = r.get_attribute('name') or ''
        val  = r.get_attribute('value') or ''
        if name and val:
            radio_groups.setdefault(name, []).append(val)

    for name, vals in radio_groups.items():
        max_val = max(vals, key=lambda v: int(v) if v.lstrip('-').isdigit() and int(v) >= 0 else -999)
        try:
            r = driver.find_element(By.CSS_SELECTOR,
                f'input[type=radio][name="{name}"][value="{max_val}"]')
            driver.execute_script("arguments[0].click();", r)
        except Exception as e:
            print(f'    ✗ {name}: {e}')

    for ta in driver.find_elements(By.CSS_SELECTOR, 'textarea'):
        try:
            ta.clear()
            ta.send_keys('課程內容豐富實用，解說清晰易懂，獲益良多，感謝臺北ｅ大提供優質學習資源。')
        except: pass

    try:
        submitted = driver.execute_script("""
            var keywords = ['送出並結束', '提交問卷', 'Submit'];
            var btns = Array.from(document.querySelectorAll(
                'input[type=submit], button[type=submit], button'));
            for (var b of btns) {
                var txt = (b.value || b.textContent || '').trim();
                if (keywords.some(function(k){ return txt.indexOf(k) >= 0; })) {
                    b.scrollIntoView(true); b.click(); return txt;
                }
            }
            return null;
        """)
        if submitted:
            time.sleep(3)
            _dismiss_alerts(driver)
            print(f'  問卷送出: {submitted!r} | {driver.title}')
            return True
        else:
            print('  ✗ 找不到送出按鈕')
            return False
    except Exception as e:
        print(f'  ✗ 問卷送出失敗: {e}')
        return False
