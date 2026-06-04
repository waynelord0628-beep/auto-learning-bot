import sys, io

# Send Taipei E-da console output to both the UI/log and a console when one exists.
# pythonw.exe has sys.stdout = None, so stdout.buffer must be guarded.
class _Tee(io.TextIOBase):
    def __init__(self, *streams):
        self._streams = [st for st in streams if st is not None]

    def write(self, s):
        for st in self._streams:
            try:
                st.write(s)
                st.flush()
            except Exception:
                pass
        return len(s)

    def flush(self):
        for st in self._streams:
            try:
                st.flush()
            except Exception:
                pass

_console = None
if sys.stdout is not None:
    if hasattr(sys.stdout, "buffer"):
        _console = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    else:
        _console = sys.stdout

_logfile = open("taipei_eda_course.log", "a", encoding="utf-8")
sys.stdout = _Tee(_console, _logfile)

import requests, time, urllib3, cv2, numpy as np, ddddocr, json, random, re, os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoAlertPresentException, UnexpectedAlertPresentException

from quiz_bank import do_quiz_with_bank, do_feedback

urllib3.disable_warnings()

_ACTIVE_DRIVER = None

def force_close_active_driver():
    global _ACTIVE_DRIVER
    driver = _ACTIVE_DRIVER
    _ACTIVE_DRIVER = None
    if driver is not None:
        try:
            driver.quit()
        except Exception:
            pass

_ocr = ddddocr.DdddOcr(show_ad=False)

RESIDENCE_TIME = 75   # 每個章節停留秒數

# 載入 config：AI keys 等設定

def load_config(path=None):
    """
    從 config.json 載入設定。
    支援兩種格式：
      - {settings: {...}} 會取 settings
      - 直接傳入 settings dict
    找不到或讀取失敗時回傳空 dict。
    """
    candidates = [
        path,
        r'C:\Users\88697\Desktop\程式開發練習\家愷學長寫的上課\autoLearning--\config.json',
        os.path.join(os.path.dirname(__file__), 'config.json'),
        'config.json',
    ]
    for p in candidates:
        if p and os.path.exists(p):
            try:
                with open(p, encoding='utf-8') as f:
                    raw = json.load(f)
                cfg = raw.get('settings', raw) if isinstance(raw, dict) else {}
                print(f'  [config] 已載入: {p}')
                return cfg
            except Exception as e:
                print(f'  [config] 讀取失敗 {p}: {e}')
    print('  [config] 找不到 config.json，AI 補答停用')
    return {}

# 共用工具

def parse_study_time(study_str):
    s = study_str or ''
    hrs  = int(re.search(r'(\d+)時', s).group(1)) if re.search(r'(\d+)時', s) else 0
    mins = int(re.search(r'(\d+)分', s).group(1)) if re.search(r'(\d+)分', s) else 0
    secs = int(re.search(r'(\d+)秒', s).group(1)) if re.search(r'(\d+)秒', s) else 0
    return hrs * 3600 + mins * 60 + secs

def solve_captcha(img_bytes):
    arr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    img = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    kernel = np.array([[0,-1,0],[-1,5,-1],[0,-1,0]])
    sharp = cv2.filter2D(gray, -1, kernel)
    _, buf = cv2.imencode('.png', sharp)
    raw = _ocr.classification(buf.tobytes())
    digits = ''.join(c for c in raw if c.isdigit())
    return digits if len(digits) == 4 else ''

def get_requests_session(driver):
    s = requests.Session()
    s.headers['User-Agent'] = 'Mozilla/5.0'
    for c in driver.get_cookies():
        s.cookies.set(c['name'], c['value'], domain=c['domain'])
    return s

def dismiss_alerts(driver):
    messages = []
    for _ in range(5):
        try:
            alert = driver.switch_to.alert
            text = alert.text or ''
            messages.append(text)
            print(f'  [Alert] {text}')
            alert.accept()
            time.sleep(0.5)
        except NoAlertPresentException:
            break
    return messages

def has_multi_window_alert(messages):
    return any('禁止多重視窗' in str(msg) for msg in (messages or []))

def deep_commit(driver):
    try:
        driver.execute_script(
            "function deepCommit(win){"
            "  try{ if(win.API) win.API.LMSCommit(''); }catch(e){}"
            "  try{ if(win.API_1484_11) win.API_1484_11.Commit(''); }catch(e){}"
            "  if(win.frames){ for(let i=0;i<win.frames.length;i++) deepCommit(win.frames[i]); }"
            "} deepCommit(window);"
        )
    except Exception:
        pass


def sec_to_hms(total_sec):
    total_sec = max(int(total_sec or 0), 0)
    hrs = total_sec // 3600
    mins = (total_sec % 3600) // 60
    secs = total_sec % 60
    return f'{hrs:02d}:{mins:02d}:{secs:02d}'

def draw_bar(cur_sec, target_sec, width=20):
    if target_sec <= 0:
        pct = 1.0
    else:
        pct = max(0.0, min(float(cur_sec) / float(target_sec), 1.0))
    filled = int(round(pct * width))
    return '[' + ('#' * filled) + ('-' * (width - filled)) + f'] {pct*100:.1f}%'

def pause_and_mute_media(driver):
    try:
        driver.execute_script("""
            function visit(win) {
                try {
                    win.document.querySelectorAll('video,audio').forEach(function(media) {
                        media.muted = true;
                        media.volume = 0;
                        try { media.pause(); } catch(e) {}
                    });
                } catch(e) {}
                try {
                    for (var i = 0; i < win.frames.length; i++) visit(win.frames[i]);
                } catch(e) {}
            }
            visit(window);
        """)
    except Exception:
        pass


# 登入

def do_login(driver, wait, username='T124478221', password='A870628a'):
    driver.get('https://elearning.taipei/mpage/login')
    wait.until(EC.presence_of_element_located((By.ID, 'pid')))
    time.sleep(0.8)
    driver.execute_script("refreshCaptcha();")
    time.sleep(0.8)
    for attempt in range(15):
        captcha_src = driver.execute_script("return document.querySelector('.captcha-img').src;")
        s = get_requests_session(driver)
        img_bytes = s.get(captcha_src, verify=False).content
        digits = solve_captcha(img_bytes)
        print(f'  captcha [{attempt+1}]: {digits!r}')
        if not digits:
            driver.execute_script("refreshCaptcha();"); time.sleep(0.8); continue
        for fid in ['pid', 'password', 'auth']:
            driver.find_element(By.ID, fid).clear()
        driver.find_element(By.ID, 'pid').send_keys(username)
        driver.find_element(By.ID, 'password').send_keys(password)
        driver.find_element(By.ID, 'auth').send_keys(digits)
        driver.find_element(By.CSS_SELECTOR, 'button[type=submit]').click()
        time.sleep(2)
        if 'login' not in driver.current_url:
            print(f'  Login OK -> {driver.current_url}')
            return True
        driver.get('https://elearning.taipei/mpage/login')
        wait.until(EC.presence_of_element_located((By.ID, 'pid')))
        driver.execute_script("refreshCaptcha();"); time.sleep(0.8)
    return False

# 課程清單

def get_course_list(driver, wait):
    """讀取課程清單，回傳 list of dict。"""
    driver.get('https://elearning.taipei/mpage/sso_moodle?redirectPage=courserecord')
    time.sleep(3)
    try:
        btn = wait.until(EC.element_to_be_clickable(
            (By.XPATH, '//button[contains(text(),"更新我的課程")]')
        ))
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(3)
    except Exception:
        pass

    def td_text(cell):
        try:
            value = driver.execute_script(
                "return (arguments[0].innerText || arguments[0].textContent || '').trim();",
                cell,
            )
            return (value or '').strip()
        except Exception:
            return cell.text.strip()

    courses = []
    last_row_count = 0
    for attempt in range(1, 16):
        rows = driver.find_elements(By.CSS_SELECTOR, 'table tbody tr')
        last_row_count = len(rows)
        parsed = []
        blank_rows = 0

        for row in rows:
            cells = row.find_elements(By.CSS_SELECTOR, 'td')
            if len(cells) < 12:
                continue

            values = [td_text(cell) for cell in cells]
            if not any(values):
                blank_rows += 1
                continue

            name = values[0]
            done = values[11]
            study = values[3]
            cert_hrs = values[4]
            score = values[8]
            quest = values[10]
            links = cells[0].find_elements(By.CSS_SELECTOR, 'a[href]')
            href = links[0].get_attribute('href') if links else ''
            parsed.append({
                'name': name, 'done': done, 'href': href,
                'cert_hrs': cert_hrs, 'score': score,
                'quest': quest, 'study': study,
            })

        if parsed:
            return parsed

        if rows:
            print(f'  [掃描] 第 {attempt} 次讀到 {len(rows)} 列，但文字尚未載入，等待中...')
        time.sleep(1)

    print(f'  [掃描] 課程表格未讀到有效文字，row_count={last_row_count}')
    return courses


def _clean_status(text):
    return str(text or '').strip()

def is_study_incomplete(course):
    return '未完成' in _clean_status(course.get('done'))

def is_questionnaire_pending(course):
    quest = _clean_status(course.get('quest'))
    # Taipei E-da currently shows questionnaire status as only:
    #   填寫   => pending
    #   已完成 => done
    # Treat '-' / empty as no questionnaire.
    return quest == '填寫'

def is_quiz_passed(course):
    score = _clean_status(course.get('score')).replace(' ', '')
    if not score or score == '-':
        return False
    if any(word in score for word in ['通過', '合格', '已完成', '及格']):
        return True
    nums = []
    for raw in re.findall(r'\d+(?:\.\d+)?', score):
        try:
            nums.append(float(raw))
        except Exception:
            pass
    return bool(nums) and max(nums) >= 100

def is_quiz_pending(course):
    score = _clean_status(course.get('score')).replace(' ', '')
    if not score or score == '-':
        # '-' can mean either no quiz or not tested yet. We only run it after
        # get_course_modules confirms the course actually has a quiz module.
        return False
    if any(word in score for word in ['未通過', '未完成', '不合格', '需補考', '待測驗']):
        return True
    return not is_quiz_passed(course)

def needs_course_processing(course):
    return (
        is_study_incomplete(course) or
        is_quiz_pending(course) or
        is_questionnaire_pending(course)
    )

def taipei_course_priority(course):
    """Order Taipei E-da work by the user's rule.

    0: study time already reached, still needs quiz + questionnaire
    1: study time already reached, only questionnaire is pending
    2: study time not reached yet
    """
    study_needed = is_study_incomplete(course)
    quiz_pending = is_quiz_pending(course)
    questionnaire_pending = is_questionnaire_pending(course)
    if not study_needed and quiz_pending and questionnaire_pending:
        return 0
    if not study_needed and questionnaire_pending:
        return 1
    if study_needed:
        return 2
    if quiz_pending:
        return 0
    return 9

def pending_courses_sorted(courses):
    pending = [c for c in courses if c.get('href') and needs_course_processing(c)]
    return sorted(pending, key=lambda c: (taipei_course_priority(c), c.get('name', '')))


def build_taipei_work_queue(driver, courses):
    """Build the Taipei E-da queue with module-aware priority.

    The course-record table does not reliably tell whether score "-" means
    "no quiz" or "quiz not done". For courses that already reached study time
    and have a pending questionnaire, pre-scan the course page so quiz+feedback
    courses are handled before feedback-only courses.
    """
    queue = []
    for course in courses:
        if not course.get('href') or not needs_course_processing(course):
            continue

        item = dict(course)
        priority = taipei_course_priority(item)
        if not is_study_incomplete(item) and is_questionnaire_pending(item):
            modules = get_course_modules(driver, item['href'])
            item['_modules'] = modules
            if modules.get('quiz_url') and not is_quiz_passed(item):
                priority = 0
            else:
                priority = 1
        queue.append((priority, item.get('name', ''), item))

    return [item for _, _, item in sorted(queue, key=lambda row: (row[0], row[1]))]


# ── 課程模組偵測（quiz / feedback cmid）─────────────────

def get_course_modules(driver, course_href):
    """
    進入課程頁，掃描所有 mod/quiz 和 mod/feedback 連結。
    回傳 dict:
      {
        'course_id': int or None,
        'quiz_url':  str or None,   # mod/quiz/view.php?id=XXXX
        'fb_url':    str or None,   # mod/feedback/view.php?id=XXXX
      }
    """
    result = {'course_id': None, 'quiz_url': None, 'fb_url': None}
    if not course_href:
        return result

    try:
        driver.get(course_href)
        time.sleep(3)
        dismiss_alerts(driver)

        # 從 URL 抓 course_id（支援 course/view.php?id=XXX 或 courserecord 頁）
        m = re.search(r'course[/=](\d+)', driver.current_url)
        if not m:
            m = re.search(r'\?id=(\d+)', driver.current_url)
        if m:
            result['course_id'] = int(m.group(1))

        # 掃所有連結
        links = driver.find_elements(By.CSS_SELECTOR, 'a[href]')
        for lnk in links:
            href = lnk.get_attribute('href') or ''
            if 'mod/quiz/view.php' in href and not result['quiz_url']:
                result['quiz_url'] = href
            if 'mod/feedback/view.php' in href and not result['fb_url']:
                result['fb_url'] = href

        print(f'  [模組] course_id={result["course_id"]} quiz={result["quiz_url"]} fb={result["fb_url"]}')
    except Exception as e:
        print(f'  [模組] 偵測失敗: {e}')

    return result

# ── SCORM 播放 ────────────────────────────────────────

def get_scorm_player_url(driver, wait, course_url):
    driver.get(course_url)
    time.sleep(3)
    if has_multi_window_alert(dismiss_alerts(driver)):
        print('  ⚠️ 臺北E大已偵測到其他上課視窗，請關閉舊課程視窗後重試')
        return None

    def current_is_player():
        url = driver.current_url or ''
        return 'mod/scorm/player.php' in url or bool(get_chapters(driver))

    def find_scorm_link():
        links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="mod/scorm/view.php"], a[href*="mod/scorm"]')
        for link in links:
            href = link.get_attribute('href') or ''
            text = (link.text or link.get_attribute('title') or '').strip()
            if 'mod/scorm/view.php' in href:
                return href, text or href
        if links:
            link = links[0]
            return link.get_attribute('href') or '', (link.text or '').strip()
        return '', ''

    def enter_from_scorm_view():
        if has_multi_window_alert(dismiss_alerts(driver)):
            print('  ⚠️ 臺北E大已偵測到其他上課視窗，停止本次進入播放器')
            return False
        pause_and_mute_media(driver)
        if current_is_player():
            return True

        selectors = [
            'a[href*="mod/scorm/player.php"]',
            'form[action*="mod/scorm/player.php"] input[type=submit]',
            'input[type=submit]',
            'button',
            'a.btn',
        ]
        seen = set()
        for selector in selectors:
            try:
                buttons = driver.find_elements(By.CSS_SELECTOR, selector)
            except Exception:
                buttons = []
            for btn in buttons:
                try:
                    key = btn.id
                    if key in seen:
                        continue
                    seen.add(key)
                    href = btn.get_attribute('href') or ''
                    value = btn.get_attribute('value') or ''
                    text = ((btn.text or '') + ' ' + value + ' ' + href).strip()
                    if href and 'mod/scorm/player.php' in href:
                        print(f'  ▶️ 進入 SCORM player: {href}')
                        driver.get(href)
                    elif any(k in text for k in ['進入', '開始', '繼續', 'Start', 'Enter', 'Launch']):
                        print(f'  ▶️ 點擊 SCORM 進入按鈕: {text[:40]}')
                        driver.execute_script("arguments[0].click();", btn)
                    else:
                        continue

                    for _ in range(20):
                        if has_multi_window_alert(dismiss_alerts(driver)):
                            print('  ⚠️ 臺北E大已偵測到其他上課視窗，停止本次進入播放器')
                            return False
                        pause_and_mute_media(driver)
                        if current_is_player():
                            return True
                        time.sleep(0.25)
                except Exception:
                    pass

        return current_is_player()

    for attempt in range(1, 4):
        scorm_url, label = find_scorm_link()
        if not scorm_url:
            print('  找不到 SCORM 連結，跳過')
            return None

        print(f'  ▶️ 進入課程連結: {(label or scorm_url)[:40]}')
        try:
            # Use same-window navigation. Taipei E-da blocks multiple course windows.
            driver.get(scorm_url)
            time.sleep(2)
        except Exception as e:
            print(f'  ⚠️ 進入 SCORM 連結失敗: {e}')
            return None

        if has_multi_window_alert(dismiss_alerts(driver)):
            print('  ⚠️ 臺北E大已偵測到其他上課視窗，請關閉舊課程視窗後重試')
            return None

        if enter_from_scorm_view():
            pause_and_mute_media(driver)
            return driver.current_url

        print(f'  ⚠️ 尚未進入播放器，重試 {attempt}/3，目前: {driver.current_url}')
        driver.get(course_url)
        time.sleep(2)
        dismiss_alerts(driver)

    return None

def get_chapters(driver):
    result = []
    try:
        els = driver.find_elements(By.CSS_SELECTOR, '[data-scoid]')
        for idx, el in enumerate(els, start=1):
            try:
                scoid = el.get_attribute('data-scoid') or ''
                if not scoid:
                    continue
                name = (el.text or el.get_attribute('title') or el.get_attribute('aria-label') or '').strip()
                if not name:
                    try:
                        name = driver.execute_script(
                            """
                            const el = arguments[0];
                            const parts = [];
                            let cur = el;
                            for (let i = 0; cur && i < 3; i++, cur = cur.parentElement) {
                              const txt = (cur.innerText || cur.textContent || '').trim();
                              if (txt) parts.push(txt);
                            }
                            return parts[0] || '';
                            """,
                            el,
                        ) or ''
                    except Exception:
                        name = ''
                if not name:
                    name = f'單元 {idx} ({scoid})'

                icon_class = ''
                try:
                    icons = el.find_elements(By.CSS_SELECTOR, 'i.icon, i, .fa, [class*="check"]')
                    icon_class = ' '.join((ic.get_attribute('class') or '') for ic in icons)
                except Exception:
                    pass

                cls = el.get_attribute('class') or ''
                done = any(k in (icon_class + ' ' + cls) for k in [
                    'fa-check-square-o',
                    'fa-check',
                    'completed',
                    'complete',
                    'finish',
                    'done',
                ])
                result.append({'scoid': scoid, 'name': name, 'done': done, 'icon': icon_class})
            except Exception:
                pass
    except Exception:
        pass
    return result

def click_chapter_by_scoid(driver, scoid):
    try:
        el = driver.find_element(By.CSS_SELECTOR, f'[data-scoid="{scoid}"]')
        driver.execute_script(
            """
            const el = arguments[0];
            const clickable =
              el.querySelector('button, a, [role="button"]') ||
              el.closest('button, a, [role="button"], li, div') ||
              el;
            clickable.scrollIntoView({block:'center'});
            clickable.click();
            """,
            el,
        )
        return True
    except Exception:
        try:
            url = driver.current_url
            if 'scoid=' in url:
                url = re.sub(r'scoid=\d+', f'scoid={scoid}', url)
            elif '#' in url:
                url = url.replace('#', f'&scoid={scoid}#')
            else:
                joiner = '&' if '?' in url else '?'
                url = f'{url}{joiner}scoid={scoid}'
            driver.get(url)
            return True
        except Exception:
            return False

def is_chapter_done(driver, scoid):
    try:
        el = driver.find_element(By.CSS_SELECTOR, f'[data-scoid="{scoid}"]')
        icons = el.find_elements(By.CSS_SELECTOR, 'i.icon')
        icon_class = icons[0].get_attribute('class') if icons else ''
        return 'fa-check-square-o' in icon_class
    except Exception:
        return False

def do_scorm_course(driver, wait, course, config=None, should_continue=None):
    config = config or {}
    should_continue = should_continue or (lambda: True)

    name = course['name']
    href = course['href']
    try:
        cert_hrs = float(course.get('cert_hrs') or 0)
    except Exception:
        cert_hrs = 0

    target_percentage = float(config.get('target_percentage', 1.0) or 1.0)
    # Taipei E-da completion time is half of the certified hours.
    # Example: 1 certified hour requires 30 minutes; target_percentage adds buffer.
    criteria_sec = int(cert_hrs * 3600 * 0.5)
    target_sec = int(criteria_sec * target_percentage)
    already_sec = parse_study_time(course.get('study', ''))
    remain_sec = max(target_sec - already_sec, 0)

    print(f'課程: {name[:60]}')
    if target_sec > 0:
        print(f'目標: {target_sec//60} 分鐘 | 已有: {already_sec//60} 分 {already_sec%60} 秒 | 還需: {remain_sec//60} 分 {remain_sec%60} 秒')
    else:
        print('目標: 無認證時數要求，僅檢查章節狀態')

    scorm_view_url = get_scorm_player_url(driver, wait, href)
    if not scorm_view_url:
        print('  找不到 SCORM 連結，跳過')
        return False

    print(f'  Player URL: {driver.current_url}')
    pause_and_mute_media(driver)

    chapters = get_chapters(driver)
    if not chapters:
        print('  ⚠️ 找不到章節，等待後重試...')
        time.sleep(10)
        chapters = get_chapters(driver)

    if not chapters:
        print('  ⚠️ SCORM 頁面沒有讀到任何章節，避免空迴圈補時間，跳過此課程')
        return False

    scoid_order = [ch['scoid'] for ch in chapters]
    start_time = time.time()
    round_num = 0

    while should_continue():
        round_num += 1
        elapsed_sec = time.time() - start_time
        chapters = get_chapters(driver)
        ch_map = {ch['scoid']: ch for ch in chapters}
        pending = [s for s in scoid_order if not ch_map.get(s, {}).get('done', False)]
        all_done = len(pending) == 0
        time_ok = elapsed_sec >= remain_sec

        if all_done and (time_ok or remain_sec == 0):
            print(f'  ✅ 章節完成且本輪補時達標，已補跑 {elapsed_sec/60:.1f} 分鐘')
            break

        to_visit = pending if pending else list(scoid_order)

        for scoid in to_visit:
            if not should_continue():
                print('  使用者已停止臺北E大流程')
                return False

            elapsed_sec = time.time() - start_time
            if not pending and remain_sec > 0 and elapsed_sec >= remain_sec:
                break

            elapsed_sec = time.time() - start_time
            print(f'  研習進度：{sec_to_hms(already_sec + elapsed_sec)} / {sec_to_hms(target_sec)} {draw_bar(already_sec + elapsed_sec, target_sec)}')
            ch_info = ch_map.get(scoid, {'name': scoid, 'done': False})
            print(f'  進入單元：{ch_info["name"][:40]}...')

            if not click_chapter_by_scoid(driver, scoid):
                print('      ⚠️ 點擊失敗，跳過')
                continue
            time.sleep(1)
            dismiss_alerts(driver)
            pause_and_mute_media(driver)

            st = time.time()
            while should_continue() and time.time() - st < RESIDENCE_TIME:
                time.sleep(1)
                pause_and_mute_media(driver)
                deep_commit(driver)

            if is_chapter_done(driver, scoid):
                print('      ✅ 單元已完成')

        if time.time() - start_time > 7200:
            print('  ⚠️ 單一課程研習已達 2 小時，先切換下一門課')
            break

    if not should_continue():
        return False

    print('  點擊離開按鈕...')
    try:
        leave = wait.until(EC.element_to_be_clickable(
            (By.XPATH, '//*[contains(text(),"離開時請點選此按鈕")]')
        ))
        driver.execute_script("arguments[0].click();", leave)
        time.sleep(3)
        dismiss_alerts(driver)
        print(f'  離開後: {driver.current_url}')
    except Exception as e:
        print(f'  離開按鈕: {e}')

    return True

# ── 主程式 ────────────────────────────────────────────

# 載入 config（AI keys）


def _is_pid_alive(pid):
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except Exception:
        return False

def _acquire_taipei_run_lock():
    lock_path = os.path.join(os.path.dirname(__file__), ".taipei_eda_course.lock")
    try:
        if os.path.exists(lock_path):
            try:
                with open(lock_path, "r", encoding="utf-8") as f:
                    old_pid = (f.read() or "").strip()
            except Exception:
                old_pid = ""
            stale_by_age = (time.time() - os.path.getmtime(lock_path)) > 6 * 60 * 60
            if old_pid and _is_pid_alive(old_pid) and not stale_by_age:
                print("臺北E大流程已在執行中，請先停止目前流程或關閉舊視窗。")
                return None
            try:
                os.remove(lock_path)
            except Exception:
                pass
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return lock_path
    except Exception as e:
        print(f"臺北E大流程鎖建立失敗，仍繼續執行: {e}")
        return ""

def _release_taipei_run_lock(lock_path):
    if not lock_path:
        return
    try:
        if os.path.exists(lock_path):
            os.remove(lock_path)
    except Exception:
        pass



def run_taipei_eda(config_override=None, should_continue=None, log_callback=None):
    """Run the Taipei E-learning workflow from the GUI/back-end dispatcher."""
    should_continue = should_continue or (lambda: True)
    config = load_config()

    original_stdout = sys.stdout
    if log_callback:
        class _UILog(io.TextIOBase):
            def write(self, s):
                text = str(s)
                if text.strip():
                    for line in text.rstrip().splitlines():
                        log_callback(line)
                return len(text)

            def flush(self):
                pass

        sys.stdout = _Tee(original_stdout, _UILog())

    driver = None
    lock_path = _acquire_taipei_run_lock()
    if lock_path is None:
        return False
    try:
        if config_override:
            config.update(config_override)

        username = config.get('account') or config.get('username') or ''
        password = config.get('password') or ''
        if not username or not password:
            print('臺北E大登入失敗：缺少帳號或密碼')
            return False

        opts = Options()
        if config.get('headless', False):
            opts.add_argument('--headless=new')
        opts.add_argument('--disable-gpu')
        opts.add_argument('--mute-audio')
        opts.add_argument('--no-sandbox')

        driver = webdriver.Chrome(options=opts)
        global _ACTIVE_DRIVER
        _ACTIVE_DRIVER = driver
        driver.set_window_size(1400, 900)
        wait = WebDriverWait(driver, 20)

        print('=== 登入 ===')
        if not do_login(driver, wait, username=username, password=password):
            print('登入失敗')
            return False

        # 建立 ap1 session
        driver.get('https://elearning.taipei/mpage/sso_moodle?redirectPage=courserecord')
        time.sleep(4)

        print('\n=== 掃描課程清單 ===')
        courses = get_course_list(driver, wait)
        incomplete = build_taipei_work_queue(driver, courses)

        print(f'  課程總數: {len(courses)} 筆，待處理: {len(incomplete)} 筆')
        if not incomplete:
            print('\n沒有待處理課程！')
            return True

        print(f'\n共 {len(incomplete)} 門待處理課程，開始依序處理...')

        stopped = False
        for course in incomplete:
            if not should_continue():
                print('使用者已停止臺北E大流程')
                stopped = True
                break

            print(f'\n{"="*60}')
            print(f'處理: {course["name"]}')

            modules = course.pop('_modules', None) or get_course_modules(driver, course['href'])
            course_id = modules.get('course_id')
            if not course_id:
                m = re.search(r'id=(\d+)', course['href'] or '')
                if m:
                    course_id = int(m.group(1))

            study_needed = is_study_incomplete(course)
            if study_needed:
                scorm_ok = do_scorm_course(driver, wait, course, config=config, should_continue=should_continue)
                if not scorm_ok:
                    print('  ⚠️ SCORM 上課失敗，跳過測驗/問卷')
                    continue
            else:
                print('  ✅ 上課時數已達標，跳過上課，檢查測驗/問卷')

            quiz_url = modules.get('quiz_url')
            if quiz_url and course_id and not is_quiz_passed(course):
                print(f'\n  📝 測驗 (course_id={course_id})')
                score_text, is_100 = do_quiz_with_bank(
                    driver, wait,
                    course_id=course_id,
                    quiz_view_url=quiz_url,
                    config=config,
                    course_name=course.get('name', ''),
                    username=config.get('name', '') or config.get('account', ''),
                )
                print(f'  測驗結果: {score_text} | 100分: {is_100}')
            elif quiz_url and course_id:
                print('  ✅ 測驗已完成/通過，跳過')
            elif quiz_url and not course_id:
                print('  ⚠️ 找到測驗但無法取得 course_id，跳過')
            else:
                print('  無測驗')

            fb_url = modules.get('fb_url')
            quest = _clean_status(course.get('quest'))
            if is_questionnaire_pending(course):
                if not fb_url:
                    print('  重新掃描 feedback URL...')
                    modules2 = get_course_modules(driver, course['href'])
                    fb_url = modules2.get('fb_url')

                if fb_url:
                    print('\n  📋 問卷')
                    do_feedback(driver, wait, feedback_view_url=fb_url)
                else:
                    print('  ⚠️ 問卷狀態為填寫，但找不到問卷入口')
            elif quest == '已完成':
                print('  ✅ 問卷已完成，跳過')
            else:
                print('  無問卷')
            time.sleep(3)

        print('\n=== 最終課程狀態 ===')
        courses_final = get_course_list(driver, wait)
        final_incomplete = pending_courses_sorted(courses_final)
        print(f'  課程總數: {len(courses_final)} 筆，待處理: {len(final_incomplete)} 筆')
        if final_incomplete:
            preview = '、'.join(c["name"][:18] for c in final_incomplete[:5])
            suffix = '...' if len(final_incomplete) > 5 else ''
            print(f'  尚未完成: {preview}{suffix}')

        print('\n完成！')
        return not stopped
    finally:
        force_close_active_driver()
        _release_taipei_run_lock(lock_path)
        if log_callback:
            sys.stdout = original_stdout


if __name__ == "__main__":
    run_taipei_eda()
