"""
臺北e大 測驗自動作答模組
- 依答案文字比對當次選項，避免選項隨機排序造成錯位
- 考後僅同步平台明確確認的正解；錯誤／未確認題下輪重新補答
- 最多三次，AI 候選答案不直接當作正解入庫
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

def _clean_question_text(text):
    """清掉 Moodle 題塊雜訊，只保留真正題幹。"""
    text = re.sub(r'\s+', ' ', text or '').strip()
    text = re.sub(r'^試題文字\s*', '', text)
    text = re.sub(r'\s*試題\s*\d+\s*$', '', text)
    text = re.sub(r'\s*試題\s*\d+\s*回答\s*.*$', '', text)
    text = re.sub(r'\s*回答\s*1[\.、]?\s*.*$', '', text)
    text = re.sub(r'\s*清除我的選擇\s*$', '', text)
    return text.strip()

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
    回傳 dict: {normalize(q_text): 原始題目／選項紀錄}
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
                bank[key] = dict(row)
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
        'rejected_answers': list(q.get('rejected_answers', [])),
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
                print(f'  [缺題] 已回報 GAS/TG（{len(missing)} 題，GAS新增 {result.get("added", 0)} 題、重新補答 {result.get("updated", 0)} 題）')
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
            return True
        else:
            print(f'  [題庫] GAS 回傳異常: {result}')
    except Exception as e:
        print(f'  [題庫] GAS 存入失敗: {e}')
    return False

def _option_text(text):
    """Ignore option labels, never the meaning of the answer."""
    text = re.sub(r'^\s*(?:[a-zA-Z][.、)]\s*|\d+[.、)]\s+)', '', str(text or ''))
    return re.sub(r'\s+', '', text).removesuffix('。')


def _question_identity(q):
    return (_normalize(q.get('qtext', '')),
            tuple(sorted(_option_text(t) for t in q.get('options', {}).values())))


def _answer_value(answer_text, options):
    matches = [str(v) for v, text in options.items()
               if _option_text(text) == _option_text(answer_text)]
    return matches[0] if len(matches) == 1 else None


def lookup_bank(bank, q_text, threshold=0.75, options=None):
    """Match answer text back to current options; stored radio values may shuffle."""
    key = _normalize(q_text)
    if key not in bank:
        matches = difflib.get_close_matches(key, bank.keys(), n=1, cutoff=threshold)
        if not matches:
            return None
        key = matches[0]
        print(f'  [題庫] fuzzy: {key[:20]}...')
    entry = bank[key]
    if not isinstance(entry, dict):
        return str(entry) if options is None else None
    val = str(entry.get('val', ''))
    if options is None:
        return val or None
    stored = {str(i): entry.get('opt' + str(i), '') for i in range(4)}
    stored = {v: text for v, text in stored.items() if text}
    if val not in stored or sorted(map(_option_text, stored.values())) != sorted(map(_option_text, options.values())):
        return None
    return _answer_value(stored[val], options)


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
        var blocks = document.querySelectorAll('.que, div[id^="question-"]');
        blocks.forEach(function(q, qi) {
            var qtext = q.querySelector('.qtext');
            if (!qtext) {
                qtext = q.querySelector('.formulation .qtext, .formulation .questiontext');
            }
            var qtxt = qtext ? clean(qtext.textContent) : '';
            if (!qtxt) {
                var formulation = q.querySelector('.formulation');
                if (formulation) {
                    var clone = formulation.cloneNode(true);
                    clone.querySelectorAll('.answer, input, label, .ablock').forEach(function(el){ el.remove(); });
                    qtxt = clean(clone.textContent);
                }
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
        q['qtext'] = _clean_question_text(q.get('qtext', ''))
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

def _review_result(record):
    """Promote only explicit platform feedback; an unchecked state proves nothing."""
    options = record.get('options', {})
    selected = record.get('selected')
    correct = {str(v) for v in record.get('correct_values', []) if str(v) in options}
    incorrect = {str(v) for v in record.get('incorrect_values', []) if str(v) in options}
    state = str(record.get('state', '')).strip().lower()
    classes = str(record.get('classes', '')).split()
    wrong = (state in ('不正確', '錯誤', '答錯', 'incorrect') or 'incorrect' in classes
             or selected in incorrect)
    right = state in ('正確', '答對', 'correct') or 'correct' in classes
    disclosed = re.sub(r'^(?:正確答案(?:是|為)?|the correct answer is)\s*[:：]?\s*',
                       '', str(record.get('rightanswer', '')).strip(), flags=re.I)
    if disclosed:
        value = _answer_value(disclosed, options)
        if value is not None:
            correct.add(value)
    value = None
    if len(correct) == 1:
        candidate = next(iter(correct))
        if candidate not in incorrect and not (wrong and candidate == selected):
            value = candidate
    elif not correct and right and not wrong and selected in options:
        value = selected
    elif not correct and wrong and selected in options and len(options) == 2:
        value = next(v for v in options if v != selected)
    rejected = selected if selected in options and (wrong or (value is not None and value != selected)) else None
    return {'correct_val': value, 'wrong_val': rejected}


def _read_review_feedback(driver):
    records = driver.execute_script("""
        function clean(s) { return (s || '').replace(/\\s+/g, ' ').trim(); }
        return Array.from(document.querySelectorAll('.que')).map(function(q) {
            var radios = Array.from(q.querySelectorAll('input[type=radio]')).filter(function(r) {return r.value !== '-1';});
            if (!radios.length) return null;
            var selected = radios.find(function(r) {return r.checked;}), options = {}, correct = [], incorrect = [];
            radios.forEach(function(r) {
                var label = document.getElementById(r.id + '_label') || q.querySelector('label[for="' + r.id + '"]');
                var row = r.closest('.r0, .r1, .r2, .r3, .r4, .r5') || r.parentElement;
                options[r.value] = clean(label ? label.textContent : (row ? row.textContent : ''));
                if (row && (row.classList.contains('correct') || row.querySelector('.correct, .fa-check.text-success'))) correct.push(r.value);
                if (row && (row.classList.contains('incorrect') || row.querySelector('.incorrect, .fa-times.text-danger'))) incorrect.push(r.value);
            });
            var state = q.querySelector('.state'), disclosed = q.querySelector('.rightanswer');
            return {name:radios[0].name, options:options, selected:selected ? selected.value : null,
                    correct_values:correct, incorrect_values:incorrect, classes:q.className,
                    state:state ? clean(state.textContent) : '', rightanswer:disclosed ? clean(disclosed.textContent) : ''};
        }).filter(Boolean);
    """)
    return {record['name']: _review_result(record) for record in records}


def _read_correct_from_review(driver):
    return {name: result['correct_val'] for name, result in _read_review_feedback(driver).items()
            if result['correct_val'] is not None}


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
    2. 未滿分時重新檢查未確認／錯誤答案，第二、三次啟用本機 AI 補答。
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
    saved_answers = set()
    reported_missing_keys = set()
    confirmed_answers = {}
    rejected_answers = {}
    needs_recheck = set()

    def _question_record(q, val):
        return {
            'q_text': q['qtext'], 'val': val, 'evidence': 'platform_feedback',
            'opt0': q['options'].get('0',''), 'opt1': q['options'].get('1',''),
            'opt2': q['options'].get('2',''), 'opt3': q['options'].get('3',''),
        }

    def _save_known_answers(to_save):
        if not to_save:
            return
        deduped = []
        seen = set()
        for item in to_save:
            key = (_normalize(item.get('q_text', '')),
                   tuple(sorted(_option_text(item.get('opt' + str(i), '')) for i in range(4))),
                   _option_text(item.get('opt' + str(item.get('val', '')), '')))
            if not key[0] or key in seen or key in saved_answers:
                continue
            seen.add(key)
            deduped.append(item)
        if deduped:
            if gas_save_questions(course_id, deduped, course_name=course_name, username=username):
                saved_answers.update(seen)
                print(f'  [題庫] 本次同步共用題庫 {len(deduped)} 題')

    def _report_missing_once(missing_qs):
        fresh = []
        for q in missing_qs:
            key = (_normalize(q.get('qtext', '')), tuple(sorted(map(_option_text, q.get('rejected_answers', [])))))
            if not key[0] or key in reported_missing_keys:
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

        for q in questions:
            identity = _question_identity(q)
            val = _answer_value(confirmed_answers.get(identity, ''), q['options']) if identity in confirmed_answers else None
            if val is None and identity not in needs_recheck:
                val = lookup_bank(bank, q['qtext'], options=q['options'])
            rejected = rejected_answers.get(identity, set())
            if val is not None and _option_text(q['options'].get(val)) in rejected:
                val = None
            if val is not None:
                answers[q['name']] = val
                print(f'  ✓ [庫] {q["qtext"][:28]}... → val={val}')
                continue

            if use_local_ai and has_local_ai:
                available = {v: t for v, t in q['options'].items() if _option_text(t) not in rejected}
                val = ai_guess_answer(q['qtext'], available, config) if available else None
                if val in available:
                    answers[q['name']] = val
                    print(f'  ✓ [AI] {q["qtext"][:28]}... → val={val}')
                    continue

            available = [v for v, text in q['options'].items() if _option_text(text) not in rejected]
            if not available:
                print('  [測驗] 所有選項均被平台否定，停止自動作答，請人工確認')
                return '', False, {}, {}
            answers[q['name']] = available[0]
            missing_qs.append(q)
            print(f'  ? [猜] {q["qtext"][:28]}... → val={answers[q["name"]]}')

        _fill_answers(driver, answers)
        _submit_quiz(driver, wait)
        time.sleep(2)

        feedback = _read_review_feedback(driver)
        correct_by_name = {name: r['correct_val'] for name, r in feedback.items() if r['correct_val'] is not None}
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
            confirmed_answers[_question_identity(q)] = q['options'][val]
            needs_recheck.discard(_question_identity(q))
            to_save.append(_question_record(q, val))

        unconfirmed = []
        for q in questions:
            identity = _question_identity(q)
            result = feedback.get(q['name'], {})
            wrong_val = result.get('wrong_val')
            if wrong_val in q['options']:
                rejected_answers.setdefault(identity, set()).add(_option_text(q['options'][wrong_val]))
                print(f'  [錯題] {q["qtext"][:28]}... 已排除：{q["options"][wrong_val]}')
            if q['name'] not in correct_by_name and not is_100:
                needs_recheck.add(identity)
                unconfirmed.append(dict(q, rejected_answers=[text for text in q['options'].values()
                    if _option_text(text) in rejected_answers.get(identity, set())]))
        if unconfirmed:
            print(f'  [測驗] {len(unconfirmed)} 題未獲平台正解確認，下輪重新補答；不存入正解題庫')
        _report_missing_once(unconfirmed)

        _save_known_answers(to_save)
        return score_text, is_100, correct_by_text, answers

    attempt_plan = [False]
    if has_local_ai:
        attempt_plan.append(True)
    else:
        print('  [AI] 本機未設定 AI key，將用題庫/猜答最多跑 3 次，缺題交給 GAS 端 AI 補庫')
        attempt_plan.append(False)
    attempt_plan.append(has_local_ai)

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
