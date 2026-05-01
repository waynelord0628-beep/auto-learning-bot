# /// script
# dependencies = [
#   "selenium",
#   "requests",
#   "urllib3",
#   "colorama",
#   "psutil",
# ]
# ///

import sys
import io
import time
import os
import re
import random
import logging
import json
import sqlite3
import unicodedata
import ctypes
import threading
from difflib import get_close_matches
import requests
import urllib3
import psutil
import atexit
import signal
import traceback

# 強制 stdout/stderr 使用 UTF-8，避免在 cp950 環境下因 emoji 崩潰
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import UnexpectedAlertPresentException
from colorama import Fore, Style, init

from utils.helpers import get_logger, to_sec, sec_to_str, draw_bar
from utils.webdriver_mgr import download_best_chromedriver

# 禁用冗長日誌與警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.getLogger("selenium").setLevel(logging.ERROR)
logger = get_logger()


def _normalize_q(text: str) -> str:
    """題目正規化：小寫、去空白、只保留中文/英數字。
    用於 _answer_map 的 key 和 difflib fuzzy 比對。
    """
    text = text.lower()
    text = re.sub(r"\s+", "", text)
    # 只保留中文字、英文字母、數字（去掉標點、空白、特殊符號）
    text = re.sub(r"[^\w\u4e00-\u9fff\u3400-\u4dbf]", "", text)
    return text


class UILogHandler(logging.Handler):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def emit(self, record):
        msg = self.format(record)
        if self.callback:
            self.callback(msg)


class AdminEfficiencyPilot:
    def __init__(self, config_path=None, log_callback=None, config_override=None):
        self.config = self.load_config(config_path)

        # ⭐ 重要：config_override 要完整覆蓋
        if config_override:
            # ⭐ 只更新傳入的字段，保留其他設定
            self.config.update(config_override)

        if "settings" in self.config:
            for key, value in self.config["settings"].items():
                if key not in self.config:
                    self.config[key] = value

        # ⭐ 把 accounts[0] 的欄位展開到頂層（供 login_ecpa/login_egov 使用）
        accounts = self.config.get("accounts", [])
        if accounts and isinstance(accounts, list) and len(accounts) > 0:
            acc = accounts[0]
            if "account" not in self.config and "account" in acc:
                self.config["account"] = acc["account"]
            if "password" not in self.config and "password" in acc:
                self.config["password"] = acc["password"]
            if "login_type" not in self.config and "login_type" in acc:
                self.config["login_type"] = acc["login_type"]

        # ⭐ 調試：打印最終配置
        logger.info(f"📋 最終配置: headless={self.config.get('headless', True)}")
        logger.info(f"📋 settings={self.config.get('settings', {})}")

        self.version = "V2.0.1"
        self.changelog = (
            "• 修正背景模式出現「您非本門課的學生」錯誤\n"
            "• 修正重啟後 API 回 0 筆誤判全部完成\n"
            "• 修正 blacklist 過濾誤殺正常單元\n"
            "• 修正課程分頁查詢只讀第一頁的問題"
        )
        self._update_checked = False
        # 打包成 exe 時用 exe 所在目錄；一般執行時用腳本所在目錄
        import sys

        base_dir = (
            os.path.dirname(sys.executable)
            if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__))
        )
        if config_path is None:
            config_path = os.path.join(base_dir, "config.json")

        # 讀取題庫答案
        # 優先從 questions.db（SQLite）載入，建立 normalized lookup dict
        # fallback: answers.json -> answer.json
        self.answer_path = os.path.join(base_dir, "answers.json")
        # _answer_map: normalize(q) -> {"answer":..., "options":[...], "question":...}
        # _answer_keys: key list 供 difflib fuzzy 使用
        self._answer_map = {}
        self._answer_keys = []
        self.answers = []  # 向後相容
        loaded = False

        # 優先：questions.db（SQLite，含選項結構）
        db_path = os.path.join(base_dir, "questions.db")
        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT question, option_a, option_b, option_c, option_d, answer FROM questions"
                ).fetchall()
                conn.close()
                for row in rows:
                    q = (row["question"] or "").strip()
                    a = (row["answer"] or "").strip()
                    if not q or not a:
                        continue
                    opts = [
                        (row["option_a"] or "").strip(),
                        (row["option_b"] or "").strip(),
                        (row["option_c"] or "").strip(),
                        (row["option_d"] or "").strip(),
                    ]
                    opts = [o for o in opts if o]
                    nk = _normalize_q(q)
                    if nk and nk not in self._answer_map:
                        self._answer_map[nk] = {
                            "answer": a,
                            "options": opts,
                            "question": q,
                        }
                self._answer_keys = list(self._answer_map.keys())
                logger.info(
                    f"📚 已載入題庫（questions.db）：{len(self._answer_map)} 題"
                )
                loaded = True
            except Exception as e:
                logger.warning(f"📚 questions.db 讀取失敗: {e}")

        # fallback: answers.json
        if not loaded and os.path.exists(self.answer_path):
            try:
                with open(self.answer_path, encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, list):
                    for item in raw:
                        q = item.get("題目", "").strip()
                        a = item.get("答案", "").strip()
                        if q and a:
                            self.answers.append((q, a))
                            nk = _normalize_q(q)
                            if nk and nk not in self._answer_map:
                                self._answer_map[nk] = {
                                    "answer": a,
                                    "options": [],
                                    "question": q,
                                }
                    self._answer_keys = list(self._answer_map.keys())
                    logger.info(
                        f"📚 已載入題庫（answers.json）：{len(self._answer_map)} 題"
                    )
                    loaded = True
            except Exception as e:
                logger.warning(f"📚 answers.json 讀取失敗: {e}")

        # fallback: answer.json
        if not loaded:
            fallback_path = os.path.join(base_dir, "answer.json")
            if os.path.exists(fallback_path):
                try:
                    with open(fallback_path, encoding="utf-8") as f:
                        raw = json.load(f)
                    for k, val in raw.items():
                        if k.startswith("_"):
                            continue
                        a = val[0] if isinstance(val, list) else str(val)
                        self.answers.append((k, a))
                        nk = _normalize_q(k)
                        if nk and nk not in self._answer_map:
                            self._answer_map[nk] = {
                                "answer": a,
                                "options": [],
                                "question": k,
                            }
                    self._answer_keys = list(self._answer_map.keys())
                    logger.info(
                        f"📚 已載入題庫（answer.json）：{len(self._answer_map)} 題"
                    )
                    loaded = True
                except Exception as e:
                    logger.warning(f"📚 answer.json 讀取失敗: {e}")

        if not loaded:
            logger.info("📚 未找到題庫檔案，跳過自動作答功能")

        self.api_url = "https://elearn.hrd.gov.tw/mooc/user/co_get_course.php"
        self.stat_url = "https://elearn.hrd.gov.tw/mooc/user/learn_stat.php"
        self.ecpa_url = "https://ecpa.dgpa.gov.tw/webform/clogin.aspx?returnUrl=https://elearn.hrd.gov.tw/sso_verify.php"

        self.driver = None
        self.wait = None
        self.http_session = requests.Session()
        self.current_idx = 0
        self.total_courses = 0
        self._driver_service = None
        self._managed_pids = set()
        self.log_callback = log_callback
        self.running = True  # 停止開關
        self._exam_fail_counts = {}  # course_id → 不及格次數
        self._completed_in_session = (
            set()
        )  # course_id → 本次已成功處理（考試通過+問卷完成）

        # 防螢幕關閉
        self._keep_awake_stop = threading.Event()
        self._keep_awake_thread = None

        if self.log_callback:
            if not any(isinstance(h, UILogHandler) for h in logger.handlers):
                ui_handler = UILogHandler(self.log_callback)
                ui_handler.setFormatter(
                    logging.Formatter(
                        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
                    )
                )
                logger.addHandler(ui_handler)

        # 初始化日誌檔案 (每次覆蓋)
        self.log_file = os.path.join(base_dir, "debug.log")

        if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
            fh = logging.FileHandler(self.log_file, mode="w", encoding="utf-8")
            fh.setFormatter(
                logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
            )
            logger.addHandler(fh)

        # 無論怎麼結束（Ctrl+C、關視窗、正常結束）都會清理
        atexit.register(self._cleanup)
        signal.signal(signal.SIGTERM, lambda *_: self._cleanup())
        try:
            signal.signal(
                signal.SIGBREAK, lambda *_: self._cleanup()
            )  # Windows Ctrl+Break
        except (AttributeError, OSError):
            pass

    def load_config(self, path):
        if path is None:
            path = "config.json"  # ⭐ 預設路徑

        # 第一次建立設定檔
        if not os.path.exists(path):
            config_data = {
                "accounts": [],
                "settings": {
                    "headless": True,
                    "target_percentage": 1.05,
                    "residence_time": 75,
                },
                "blacklist": ["課程環境", "勘誤說明", "前言", "新手導覽", "課程簡介", "環境檢測"],
            }

            with open(path, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=4, ensure_ascii=False)

        else:
            with open(path, "r", encoding="utf-8") as f:
                config_data = json.load(f)

        # ⭐ 關鍵：確保必要的設定存在（合併）
        if "settings" not in config_data:
            config_data["settings"] = {}

        # ⭐ 確保 blacklist 存在
        if "blacklist" not in config_data:
            config_data["blacklist"] = ["課程環境", "勘誤說明", "前言", "新手導覽", "課程簡介", "環境檢測"]

        # ⭐ 直接回傳完整配置
        return config_data

    def _start_keep_awake(self):
        """啟用防螢幕關閉：SetThreadExecutionState + 定時滑鼠微動備援"""
        # 1. Windows API：告訴系統目前有任務，不要關螢幕
        try:
            ES_CONTINUOUS = 0x80000000
            ES_DISPLAY_REQUIRED = 0x00000002
            ES_SYSTEM_REQUIRED = 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED
            )
            logger.info("🖥️ 防螢幕關閉已啟用（SetThreadExecutionState）")
        except Exception as e:
            logger.warning(f"防螢幕 API 呼叫失敗（將改用滑鼠微動備援）: {e}")

        # 2. 備援：每 60 秒微動滑鼠 1 pixel 再移回
        self._keep_awake_stop.clear()

        def _mouse_nudge():
            try:
                import ctypes as _ct

                pt = _ct.wintypes.POINT()
                while not self._keep_awake_stop.wait(60):
                    _ct.windll.user32.GetCursorPos(_ct.byref(pt))
                    _ct.windll.user32.SetCursorPos(pt.x + 1, pt.y)
                    time.sleep(0.1)
                    _ct.windll.user32.SetCursorPos(pt.x, pt.y)
            except Exception:
                pass

        self._keep_awake_thread = threading.Thread(
            target=_mouse_nudge, daemon=True, name="KeepAwake"
        )
        self._keep_awake_thread.start()

    def _stop_keep_awake(self):
        """停用防螢幕關閉，還原系統設定"""
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(
                0x80000000
            )  # ES_CONTINUOUS only
            logger.info("🖥️ 防螢幕關閉已停用，系統還原正常省電設定")
        except Exception:
            pass
        self._keep_awake_stop.set()

    def _cleanup(self):
        """統一清理入口，重複呼叫安全（atexit/signal/finally 都指向這裡）。"""
        self._stop_keep_awake()
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
        self._kill_managed_processes()

    def kill_orphan_drivers(self):
        """
        啟動前清理：只殺「孤立的 chromedriver」。
        判斷標準：行程名稱是 chromedriver，但父行程不是本程式（即上次執行殘留的）。
        完全不碰使用者自己開的 chrome.exe。
        """
        my_pid = os.getpid()
        killed = []
        for proc in psutil.process_iter(["pid", "name", "ppid"]):
            try:
                name = (proc.info["name"] or "").lower()
                if "chromedriver" not in name:
                    continue
                # 父行程不是本程式 → 視為上次殘留的孤立 driver
                if proc.info["ppid"] != my_pid:
                    # 連同它啟動的 chrome 子行程一起清掉
                    for child in proc.children(recursive=True):
                        try:
                            child.kill()
                            killed.append(f"{child.name()}(PID {child.pid})")
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    proc.kill()
                    killed.append(f"{proc.name()}(PID {proc.pid})")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if killed:
            logger.info(f"🧹 已清除孤立 driver 行程：{', '.join(killed)}")
            time.sleep(0.5)
        else:
            logger.info("✅ 無殘留 driver 行程。")

    def _kill_managed_processes(self):
        """結束時清理：只殺本次自己記錄的 PID 樹，不影響使用者其他 Chrome。"""
        if not self._managed_pids:
            return
        for pid in list(self._managed_pids):
            try:
                proc = psutil.Process(pid)
                for child in proc.children(recursive=True):
                    try:
                        child.kill()
                        logger.info(f"🧹 終止子行程：{child.name()}(PID {child.pid})")
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                proc.kill()
                logger.info(f"🧹 終止主行程：{proc.name()}(PID {pid})")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        self._managed_pids.clear()
        time.sleep(0.5)

    @staticmethod
    def _clean_answer(ans: str) -> str:
        """去除答案的數字前綴（如 '2.唾液' → '唾液'）及 || 後綴"""
        import re as _re

        # 去除 '1.', '2. ', '3、' 等數字前綴（保留純數字答案如 '0.74'）
        ans = _re.sub(r"^\d+[.\uff0e\u3001]\s*(?=[^\d])", "", ans).strip()
        # 去除 '||' 後綴（標注符號）
        ans = _re.sub(r"\s*\|\|.*$", "", ans).strip()
        return ans

    def _find_answer(self, question_text):
        """在題庫中查詢答案。

        策略（依優先順序）：
        1. normalize 後精準比對 _answer_map
        2. difflib fuzzy 比對（cutoff=0.82）
        3. 都找不到 → None

        回傳 str（答案文字）或 None。
        """
        if not question_text or len(question_text.strip()) < 4:
            return None
        q_norm = _normalize_q(question_text.strip())
        if not q_norm or len(q_norm) < 4:
            return None

        # 1. 精準比對
        row = self._answer_map.get(q_norm)
        if row:
            return self._clean_answer(row["answer"])

        # 2. difflib fuzzy（只在有 key list 時執行，避免空集合 warning）
        if self._answer_keys:
            matches = get_close_matches(q_norm, self._answer_keys, n=1, cutoff=0.82)
            if matches:
                row = self._answer_map[matches[0]]
                logger.debug(f"   🔍 fuzzy match: {row['question'][:30]!r}")
                return self._clean_answer(row["answer"])

        # 3. 向後相容：舊 self.answers list 雙向包含比對（只在未從 DB 載入時有資料）
        if self.answers:
            q = question_text.strip()
            MIN_LEN = 12
            for keyword, ans in self.answers:
                if keyword in q:
                    if len(keyword) >= MIN_LEN and len(q) >= MIN_LEN:
                        return self._clean_answer(ans)
                elif q in keyword:
                    if len(q) >= MIN_LEN:
                        return self._clean_answer(ans)

        return None

    def _accept_alert(self):
        """若有 alert/confirm 對話框則點確定，無則跳過"""
        try:
            WebDriverWait(self.driver, 3).until(EC.alert_is_present())
            self.driver.switch_to.alert.accept()
            return True
        except Exception:
            return False

    def _harvest_correct_answers(self, view_result_url: str) -> dict:
        """
        從 view_result.php 頁面讀取正確答案。
        流程：
          1. 取得 queryStr 和 isReadAnswer（JS 變數）
          2. 若 isReadAnswer != '1'，用 http_session GET set_see_question_result.php
          3. reload 後，找每題 span[style*='background-color: green'] input → 取 value
          4. 回傳 {題目關鍵字: 答案value} dict（可能為空）
        """
        result = {}
        try:
            # 確認在正確視窗
            time.sleep(1)
            query_str = self.driver.execute_script(
                "try { return typeof queryStr !== 'undefined' ? queryStr : null; } catch(e) { return null; }"
            )
            is_read = self.driver.execute_script(
                "try { return typeof isReadAnswer !== 'undefined' ? isReadAnswer : '0'; } catch(e) { return '0'; }"
            )
            if not query_str:
                logger.debug("   harvest: 無法取得 queryStr，放棄")
                return result

            logger.info(f"   📖 嘗試讀取正確答案（isReadAnswer={is_read}）")

            if is_read != "1":
                # 呼叫 set_see_question_result.php
                base_url = view_result_url.split("/learn/")[0]
                api_url = (
                    f"{base_url}/learn/exam/set_see_question_result.php?{query_str}"
                )
                ua = self.driver.execute_script("return navigator.userAgent;")
                # 同步 cookie 到 http_session
                for c in self.driver.get_cookies():
                    self.http_session.cookies.set(
                        c["name"], c["value"], domain=c["domain"]
                    )
                resp = self.http_session.get(
                    api_url,
                    headers={"User-Agent": ua, "Referer": view_result_url},
                    timeout=10,
                )
                logger.debug(
                    f"   set_see_question_result: {resp.status_code} / {resp.text[:50]!r}"
                )
                if resp.text.strip() == "1":
                    self.driver.refresh()
                    time.sleep(3)
                else:
                    logger.warning(
                        f"   ⚠️ set_see_question_result 無法公布答案（server 回應：{resp.text.strip()[:50]!r}），此課程可能不開放答案"
                    )
                    return result

            # 讀取每題的正確答案（span[style*=green] input）+ 選項文字
            q_data = self.driver.execute_script(
                """
                var result = [];
                var rows = document.querySelectorAll('tr.bg03.font01, tr.bg04.font01');
                for (var i = 0; i < rows.length; i++) {
                    var row = rows[i];
                    var p = row.querySelector('p');
                    var qText = p ? p.innerText.trim() : '';
                    if (!qText) continue;
                    // 找 background-color: green 的 span 裡的 input
                    var spans = row.querySelectorAll('span');
                    var correctVals = [];
                    var correctTexts = [];
                    for (var j = 0; j < spans.length; j++) {
                        var bg = spans[j].style.backgroundColor;
                        if (bg === 'green' || bg === 'rgb(0, 128, 0)') {
                            var inp = spans[j].querySelector('input');
                            if (inp) correctVals.push(inp.value);
                            // 取選項文字（span 內去掉 input 的文字）
                            var spanText = spans[j].innerText || spans[j].textContent || '';
                            spanText = spanText.replace(/^[\\s\\d.]+/, '').trim();
                            if (spanText) correctTexts.push(spanText);
                        }
                    }
                    if (correctVals.length > 0) {
                        result.push({q: qText, ans: correctVals, texts: correctTexts});
                    }
                }
                return result;
                """
            )

            if not q_data:
                logger.warning("   ⚠️ 未讀到任何正確答案（可能頁面未更新或格式不符）")
                return result

            # 轉換格式並寫入 answers.json（優先用選項文字，其次用 value）
            for item in q_data:
                q_text = item["q"]
                ans_vals = item["ans"]
                ans_texts = item.get("texts", [])
                # 去掉題號前綴（如 "1. " "（1）" 等），與 auto_exam 的題目文字一致
                q_text_clean = re.sub(r"^[\d０-９]+[.．、。）)\s]+", "", q_text).strip()
                # 保留題目全文作為 key（不截 30 字，避免碰撞）
                key = q_text_clean.strip()
                # 優先用選項文字作為答案，多選以「、」合併為一個字串
                if ans_texts:
                    ans_str = (
                        "、".join(ans_texts) if len(ans_texts) > 1 else ans_texts[0]
                    )
                else:
                    # fallback: 用 input value
                    ans_str = (
                        "、".join(ans_vals)
                        if len(ans_vals) > 1
                        else (ans_vals[0] if ans_vals else "")
                    )
                result[key] = ans_str
                logger.debug(f"   harvest: {key!r} => {ans_str!r}")

            logger.info(f"   📖 讀到 {len(result)} 題正確答案")

            # 寫入 answers.json（list 格式 [{"題目": ..., "答案": ...}]）
            try:
                answers_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "answers.json"
                )
                existing_list = []
                if os.path.exists(answers_path):
                    with open(answers_path, encoding="utf-8") as f:
                        existing_list = json.load(f)
                # 建立題目全文→index 的快速查找（雙向比對以找到相同題目）
                existing_keys = {}
                for idx_e, entry in enumerate(existing_list):
                    ek = entry.get("題目", "").strip()
                    existing_keys[ek] = idx_e
                # 更新或新增
                added = 0
                for key, ans_str in result.items():
                    # 先嘗試精確匹配，再嘗試雙向包含
                    matched_idx = None
                    if key in existing_keys:
                        matched_idx = existing_keys[key]
                    else:
                        for ek, idx_e in existing_keys.items():
                            if (
                                key
                                and ek
                                and len(key) >= 8
                                and len(ek) >= 8
                                and (key in ek or ek in key)
                            ):
                                matched_idx = idx_e
                                break
                    if matched_idx is not None:
                        # 更新現有條目
                        existing_list[matched_idx]["答案"] = ans_str
                    else:
                        existing_list.append({"題目": key, "答案": ans_str})
                        added += 1
                with open(answers_path, "w", encoding="utf-8") as f:
                    json.dump(existing_list, f, ensure_ascii=False, indent=2)
                logger.info(
                    f"   ✅ 已將 {len(result)} 題答案寫入 answers.json（新增 {added} 題，共 {len(existing_list)} 題）"
                )
                # 更新記憶體中的 answers（list of (題目, 答案)）
                for key, ans_str in result.items():
                    self.answers.append((key, ans_str))
                    # 同步更新 _answer_map（讓本次後續題目也能命中）
                    nk = _normalize_q(key)
                    if nk and nk not in self._answer_map:
                        self._answer_map[nk] = {"answer": ans_str, "options": [], "question": key}
                        self._answer_keys.append(nk)
            except Exception as e:
                logger.warning(f"   ⚠️ 寫入 answers.json 失敗: {e}")

            # 同步寫入 questions.db（INSERT OR REPLACE）
            try:
                import sys as _sys
                _base = (
                    os.path.dirname(_sys.executable)
                    if getattr(_sys, "frozen", False)
                    else os.path.dirname(os.path.abspath(__file__))
                )
                db_path = os.path.join(_base, "questions.db")
                conn = sqlite3.connect(db_path)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS questions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        question TEXT UNIQUE NOT NULL,
                        option_a TEXT, option_b TEXT, option_c TEXT, option_d TEXT,
                        answer TEXT
                    )
                """)
                db_added = 0
                for key, ans_str in result.items():
                    cur = conn.execute(
                        "SELECT id FROM questions WHERE question = ?", (key,)
                    ).fetchone()
                    if cur:
                        conn.execute(
                            "UPDATE questions SET answer = ? WHERE question = ?",
                            (ans_str, key),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO questions (question, answer) VALUES (?, ?)",
                            (key, ans_str),
                        )
                        db_added += 1
                conn.commit()
                conn.close()
                logger.info(
                    f"   💾 已同步 {len(result)} 題到 questions.db（新增 {db_added} 題）"
                )
            except Exception as e:
                logger.warning(f"   ⚠️ 寫入 questions.db 失敗: {e}")

        except Exception as e:
            logger.debug(f"   harvest_correct_answers 失敗: {e}")

        return result

    def auto_exam(self, course):
        """時數達標後，自動進入測驗並作答。回傳 True=通過, False=未通過/失敗"""
        if not self._answer_map and not self.answers:
            logger.info("   📝 未設定題庫，跳過自動作答")
            return False

        course_id = str(course.get("course_id", ""))

        # 不及格超過 3 次，跳過此課程
        fail_count = self._exam_fail_counts.get(course_id, 0)
        if fail_count >= 3:
            logger.warning(
                f"   ⚠️ 課程「{course.get('caption', course_id)}」已不及格 {fail_count} 次，跳過，請使用者自行完成測驗"
            )
            return False

        logger.info("   📝 開始自動作答流程...")
        # 以「目前所在視窗」為課程教室主視窗（不論從哪條路徑進入）
        main_window = self.driver.current_window_handle

        try:
            # ── 1. 切回教室主視窗，點左側 sidebar「測驗/考試」──
            # sidebar 在 mooc_sysbar frame（<frame name="mooc_sysbar">）
            self.driver.switch_to.window(main_window)
            self.driver.switch_to.default_content()

            try:
                self.driver.switch_to.frame("mooc_sysbar")
                exam_link = self.driver.find_element(
                    By.CSS_SELECTOR, "a[href*='exam/exam_list.php']"
                )
                self.driver.execute_script("arguments[0].click();", exam_link)
                logger.info("   📝 已點擊「測驗/考試」")
            except Exception as e:
                logger.warning(f"   ⚠️ 找不到測驗連結（mooc_sysbar）: {e}")
                return False

            time.sleep(2)

            # ── 2. 切到 s_main frame（frameset 結構，非 iframe）──
            self.driver.switch_to.default_content()
            try:
                self.driver.switch_to.frame("s_main")
            except Exception:
                logger.warning("   ⚠️ 無法切換到 s_main frame")
                return False

            time.sleep(1)

            # ── 2a. 檢查是否已通過（綠色「已通過」div 或已公布答案文字）──
            try:
                # 方法1：找 div.process-btn 內含「已通過」span（來自截圖結構）
                passed_els = self.driver.find_elements(
                    By.XPATH,
                    "//div[contains(@class,'process-btn')]//span[contains(text(),'已通過')]",
                )
                # 方法2：找「已選擇公布答案，不得再進行測驗」提示文字
                if not passed_els:
                    passed_els = self.driver.find_elements(
                        By.XPATH, "//*[contains(text(),'已選擇公布答案')]"
                    )
                if passed_els:
                    logger.info("   ✅ 測驗已通過（先前已完成），跳過作答")
                    return True
            except Exception:
                pass

            # ── 3. 點「進行測驗」──
            try:
                pay_btn = self.driver.find_element(
                    By.CSS_SELECTOR, "div.process-btn.pay.active"
                )
                self.driver.execute_script("arguments[0].click();", pay_btn)
                logger.info("   📝 已點擊「進行測驗」")
            except Exception as e:
                logger.warning(f"   ⚠️ 找不到「進行測驗」按鈕: {e}")
                return False

            time.sleep(3)

            # ── 4. 切換到新跳出的考試視窗 ──
            all_handles = self.driver.window_handles
            exam_window = next((h for h in all_handles if h != main_window), None)
            if not exam_window:
                logger.warning("   ⚠️ 未偵測到考試視窗")
                return False

            self.driver.switch_to.window(exam_window)
            logger.info("   📝 已切換至考試視窗")

            # 等待考試頁面載入完成（最多 15 秒）
            try:
                WebDriverWait(self.driver, 15).until(
                    lambda d: d.execute_script(
                        'var inputs = document.querySelectorAll(\'input[type="button"], input[type="submit"]\');'
                        "for(var i=0;i<inputs.length;i++){var v=inputs[i].value||''; if(v.indexOf('\u958b\u59cb')!==-1||v.indexOf('\u4f5c\u7b54')!==-1) return true;}"
                        "return inputs.length > 0;"
                    )
                )
            except Exception:
                # timeout 了，繼續往下嘗試（舊版 fallback）
                pass

            # 記錄 exam_start URL（含 course_id+attempt+token）供步驟10推算 view_result URL
            exam_start_url = self.driver.current_url

            # ── 5. 點「開始作答」 ──
            # 用 JS 點擊（避免 StaleElementReferenceException）
            # 頁面有 type=button 的「開始作答」，是第一個 input[type=button]
            try:
                clicked = self.driver.execute_script(
                    """
                    var inputs = document.querySelectorAll('input');
                    for (var i = 0; i < inputs.length; i++) {
                        var v = inputs[i].value || '';
                        // 「開始作答」Unicode: \u958b\u59cb\u4f5c\u7b54
                        if (v.indexOf('\u958b\u59cb') !== -1) {
                            inputs[i].click();
                            return v;
                        }
                    }
                    // fallback：點第一個 type=button
                    var btn = document.querySelector('input[type="button"]');
                    if (btn) { btn.click(); return btn.value; }
                    return null;
                    """
                )
                if clicked:
                    logger.info(f"   📝 已點擊開始按鈕：{clicked!r}")
                else:
                    logger.warning("   ⚠️ 找不到開始作答按鈕")
                    return False
            except Exception as e:
                logger.warning(f"   ⚠️ 點擊開始作答失敗: {e}")
                return False

            time.sleep(2)

            # ── 6. 逐題作答 ──
            answered = 0
            skipped = 0

            rows = self.driver.find_elements(
                By.CSS_SELECTOR, "tr.bg03.font01, tr.bg04.font01"
            )

            # ── DOM 診斷（首次執行時印出） ──
            try:
                iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
                frames = self.driver.find_elements(By.TAG_NAME, "frame")
                logger.info(
                    f"   [DOM] iframe 數量: {len(iframes)}, frame 數量: {len(frames)}"
                )
                logger.info(
                    f"   [DOM] iframe names: {[f.get_attribute('name') or f.get_attribute('id') or '?' for f in iframes]}"
                )
                logger.info(f"   [DOM] rows 數量: {len(rows)}")
                if rows:
                    first_html = self.driver.execute_script(
                        "return arguments[0].outerHTML;", rows[0]
                    )
                    logger.info(
                        f"   [DOM] 第一個row HTML (前1000字): {first_html[:1000]}"
                    )
                else:
                    # rows 為空，印出整個 table body 的 HTML 幫助診斷
                    page_sample = self.driver.execute_script(
                        "var t = document.querySelector('table'); return t ? t.outerHTML.substring(0,2000) : document.body.innerHTML.substring(0,2000);"
                    )
                    logger.info(
                        f"   [DOM] rows 為空，頁面 table HTML (前2000字): {page_sample}"
                    )
            except Exception as _dom_e:
                logger.info(f"   [DOM] 診斷失敗: {_dom_e}")

            for row in rows:
                try:
                    # ── 題目文字擷取 ──
                    # 頁面結構：<td align="left"> 純文字節點（題目）<ol>選項</ol></td>
                    # 用 JS 取 td 內、ol/ul 之前的文字節點，排除選項污染
                    try:
                        q_text = (
                            self.driver.execute_script(
                                """
                            // 優先找含有 ol/input 的 td（真正的題目+選項 td）
                            // 避免選到第一欄的「單選/是非/多選」標籤 td（含 nowrap 屬性）
                            var tds = arguments[0].querySelectorAll('td');
                            var td = null;
                            for (var j = 0; j < tds.length; j++) {
                                if (tds[j].querySelector('ol, ul, input')) {
                                    td = tds[j];
                                    break;
                                }
                            }
                            // fallback: 找不含 nowrap 的 td[align="left"]
                            if (!td) {
                                var candidates = arguments[0].querySelectorAll('td[align="left"]');
                                for (var k = 0; k < candidates.length; k++) {
                                    if (!candidates[k].hasAttribute('nowrap')) {
                                        td = candidates[k];
                                        break;
                                    }
                                }
                            }
                            if (!td) td = arguments[0].querySelector('td');
                            if (!td) return '';
                            var text = '';
                            for (var i = 0; i < td.childNodes.length; i++) {
                                var n = td.childNodes[i];
                                if (n.nodeType === 3) {
                                    text += n.textContent;
                                } else if (n.nodeName === 'P' || n.nodeName === 'STRONG' || n.nodeName === 'SPAN') {
                                    text += n.innerText || n.textContent;
                                    break;
                                } else if (n.nodeName === 'OL' || n.nodeName === 'UL') {
                                    break;
                                }
                            }
                            text = text.trim();
                            text = text.replace(/^[\\d]+[.\\s]+/, '').trim();
                            return text;
                            """,
                                row,
                            )
                            or ""
                        )
                    except Exception:
                        q_text = ""
                    if not q_text:
                        try:
                            q_el = row.find_element(By.TAG_NAME, "p")
                            q_text = q_el.text.strip()
                        except Exception:
                            q_text = row.text.strip().split("\n")[0]

                    ans = self._find_answer(q_text)

                    radios = row.find_elements(By.CSS_SELECTOR, "input[type='radio']")
                    checkboxes = row.find_elements(
                        By.CSS_SELECTOR, "input[type='checkbox']"
                    )

                    # ── 取得本題所有選項文字（用於文字比對）──
                    # 選項在 <ol>/<ul> 內的 <li> 裡
                    try:
                        option_texts = (
                            self.driver.execute_script(
                                """
                            // 同樣優先找含有 ol/input 的 td
                            var tds = arguments[0].querySelectorAll('td');
                            var td = null;
                            for (var j = 0; j < tds.length; j++) {
                                if (tds[j].querySelector('ol, ul, input')) {
                                    td = tds[j];
                                    break;
                                }
                            }
                            if (!td) {
                                var candidates = arguments[0].querySelectorAll('td[align="left"]');
                                for (var k = 0; k < candidates.length; k++) {
                                    if (!candidates[k].hasAttribute('nowrap')) {
                                        td = candidates[k];
                                        break;
                                    }
                                }
                            }
                            if (!td) td = arguments[0].querySelector('td');
                            if (!td) return [];
                            var items = td.querySelectorAll('ol li, ul li');
                            var texts = [];
                            for (var i = 0; i < items.length; i++) {
                                // 取 li 的文字，排除內部 input 元素的 value
                                var li = items[i];
                                var text = '';
                                for (var k = 0; k < li.childNodes.length; k++) {
                                    var cn = li.childNodes[k];
                                    if (cn.nodeType === 3) {
                                        text += cn.textContent;
                                    } else if (cn.nodeName !== 'INPUT' && cn.nodeName !== 'SPAN') {
                                        text += cn.innerText || cn.textContent || '';
                                    } else if (cn.nodeName === 'SPAN') {
                                        // SPAN 內可能有 input，只取文字節點
                                        for (var m = 0; m < cn.childNodes.length; m++) {
                                            if (cn.childNodes[m].nodeType === 3) {
                                                text += cn.childNodes[m].textContent;
                                            }
                                        }
                                    }
                                }
                                texts.push(text.trim());
                            }
                            return texts;
                            """,
                                row,
                            )
                            or []
                        )
                    except Exception:
                        option_texts = []

                    logger.info(f"   題目: {q_text[:50]!r}")
                    logger.info(f"   選項: {[t[:20] for t in option_texts]!r}")
                    logger.info(f"   答案: {ans!r}")
                    if checkboxes:
                        if ans is not None:
                            ans_text = (
                                ans
                                if isinstance(ans, str)
                                else (ans[0] if isinstance(ans, list) else str(ans))
                            )
                            ans_norm = ans_text.strip()
                            # 多選答案以「、」分隔，拆成清單分別比對
                            ans_parts = [
                                p.strip() for p in ans_norm.split("、") if p.strip()
                            ]
                            if not ans_parts:
                                ans_parts = [ans_norm]
                            # 「以上皆是/以上皆可/以上皆正確/以上皆對/all of the above」→ 全選
                            ALL_ABOVE_PATTERNS = [
                                "以上皆是",
                                "以上皆可",
                                "以上皆正確",
                                "以上皆對",
                                "all of the above",
                                "ll of the above",
                            ]
                            is_all_above = any(
                                p in ans_norm for p in ALL_ABOVE_PATTERNS
                            )
                            if is_all_above:
                                for cb in checkboxes:
                                    self.driver.execute_script(
                                        "arguments[0].click();", cb
                                    )
                                logger.debug(
                                    f"   ✅ 全選（以上皆是）：{q_text[:20]}..."
                                )
                            else:
                                # 先嘗試 value 比對（向後相容 1/2/3/4/a/b/c/d）
                                letter_to_num = {
                                    "a": "1",
                                    "b": "2",
                                    "c": "3",
                                    "d": "4",
                                    "e": "5",
                                    "f": "6",
                                    "g": "7",
                                    "h": "8",
                                }
                                ans_list = ans if isinstance(ans, list) else ans_parts
                                ans_list_norm = [a.lower() for a in ans_list]
                                value_matched = False
                                for cb in checkboxes:
                                    cb_val = (cb.get_attribute("value") or "").lower()
                                    cb_letter = {
                                        v: k for k, v in letter_to_num.items()
                                    }.get(cb_val, cb_val)
                                    if (
                                        cb_val in ans_list_norm
                                        or cb_letter in ans_list_norm
                                    ):
                                        self.driver.execute_script(
                                            "arguments[0].click();", cb
                                        )
                                        value_matched = True
                                # fallback: 用答案文字比對選項文字（支援多選拆分）
                                if not value_matched:
                                    for i, cb in enumerate(checkboxes):
                                        opt_text = (
                                            option_texts[i].strip()
                                            if i < len(option_texts)
                                            else ""
                                        )
                                        if opt_text:
                                            # 任一答案部分與選項文字雙向包含即命中
                                            for part in ans_parts:
                                                if part and (
                                                    part in opt_text or opt_text in part
                                                ):
                                                    self.driver.execute_script(
                                                        "arguments[0].click();", cb
                                                    )
                                                    break
                        else:
                            # 無答案：隨機勾 2~3 個 checkbox
                            n = len(checkboxes)
                            pick_count = min(n, random.randint(2, max(2, n - 1)))
                            picks = random.sample(checkboxes, pick_count)
                            for pick in picks:
                                self.driver.execute_script(
                                    "arguments[0].click();", pick
                                )
                            logger.debug(
                                f"   🎲 多選隨機作答({pick_count}/{n})：{q_text[:20]}..."
                            )
                        answered += 1

                    elif radios:
                        idx = None
                        if ans is not None:
                            ans_str = (
                                ans
                                if isinstance(ans, str)
                                else (ans[0] if isinstance(ans, list) else str(ans))
                            )
                            ans_norm = ans_str.strip()

                            if len(radios) == 2:
                                # 是非題
                                # 先嘗試 value 比對（T/F/O/X）
                                ans_upper = ans_norm.upper()
                                if ans_upper in ("O", "T", "TRUE"):
                                    idx = 0
                                elif ans_upper in ("X", "F", "FALSE"):
                                    idx = 1
                                else:
                                    # 文字答案：○/是/對/正確 → 0；╳/否/錯/錯誤 → 1
                                    TRUE_WORDS = ["○", "是", "對", "正確", "true"]
                                    FALSE_WORDS = [
                                        "╳",
                                        "否",
                                        "錯",
                                        "錯誤",
                                        "false",
                                        "非",
                                    ]
                                    ans_lower = ans_norm.lower()
                                    if any(w in ans_lower for w in TRUE_WORDS):
                                        idx = 0
                                    elif any(w in ans_lower for w in FALSE_WORDS):
                                        idx = 1
                                    else:
                                        idx = 0  # 預設選第一個
                            else:
                                # 單選題：先比對 radio value（向後相容 1/2/3/4/A/B/C/D）
                                ans_upper = ans_norm.upper()
                                letter_to_val = {"A": "1", "B": "2", "C": "3", "D": "4"}
                                target_val = letter_to_val.get(ans_upper, ans_upper)
                                for i, r in enumerate(radios):
                                    rv = (r.get_attribute("value") or "").upper()
                                    if rv == target_val or rv == ans_upper:
                                        idx = i
                                        break
                                # fallback: 用答案文字比對選項文字
                                ALL_ABOVE_PATTERNS = [
                                    "以上皆是",
                                    "以上皆可",
                                    "以上皆正確",
                                    "以上皆對",
                                    "all of the above",
                                    "ll of the above",
                                ]
                                is_all_above = any(
                                    p in ans_norm for p in ALL_ABOVE_PATTERNS
                                )
                                if idx is None:
                                    if is_all_above:
                                        idx = len(radios) - 1  # 通常「以上皆是」在最後
                                    elif option_texts:
                                        # 選最長匹配（避免短答案誤命中多個選項）
                                        best_idx = None
                                        best_len = 0
                                        for i, opt_text in enumerate(option_texts):
                                            opt_clean = opt_text.strip()
                                            if (
                                                ans_norm
                                                and opt_clean
                                                and (
                                                    ans_norm in opt_clean
                                                    or opt_clean in ans_norm
                                                )
                                            ):
                                                match_len = min(
                                                    len(ans_norm), len(opt_clean)
                                                )
                                                if match_len > best_len:
                                                    best_len = match_len
                                                    best_idx = i
                                        idx = best_idx
                                # 最終 fallback: index mapping
                                if idx is None:
                                    opt_map = {
                                        "A": 0,
                                        "B": 1,
                                        "C": 2,
                                        "D": 3,
                                        "1": 0,
                                        "2": 1,
                                        "3": 2,
                                        "4": 3,
                                    }
                                    idx = opt_map.get(ans_upper, None)
                        else:
                            # 無答案：是非題預設選○（第一個選項，佔題庫63.7%）
                            # 單選題隨機選
                            if len(radios) == 2:
                                idx = 0  # 是非題預設 ○（True）
                                logger.info(
                                    f"   🔵 是非題預設選○（題庫無此題）：{q_text[:30]!r}"
                                )
                            else:
                                idx = random.randrange(len(radios))
                                logger.info(
                                    f"   🎲 單選隨機作答（題庫無此題）：{q_text[:30]!r}"
                                )

                        if idx is not None and idx < len(radios):
                            self.driver.execute_script(
                                "arguments[0].click();", radios[idx]
                            )
                            answered += 1
                        else:
                            skipped += 1

                except Exception as e:
                    logger.debug(f"   ⚠️ 作答某題時發生錯誤: {e}")
                    skipped += 1

            logger.info(f"   📝 作答完成：{answered} 題已答，{skipped} 題略過")

            # ── 7. 點「送出答案，結束測驗」──
            # 頁面有兩個 submit 按鈕：
            #   - form[name='responseForm']（save_answer.php，target='submitTarget'）
            #     點擊後會出現 alert，接受後整頁跳轉到 view_result.php
            #   - form[name='buttonLine']（exam_start.php，target=''）→ 退出考試按鈕，不送答案
            # 需要點 responseForm 的按鈕。
            time.sleep(1)
            try:
                # 明確切回考試視窗（以防 step 6 中 JS click 意外改變了 focus）
                try:
                    self.driver.switch_to.window(exam_window)
                except Exception:
                    pass

                cur_url = self.driver.current_url
                logger.info(f"   📝 送出前 URL: {cur_url}")

                result = self.driver.execute_script(
                    """
                    var btns = document.querySelectorAll('input[type="submit"]');
                    var info = [];
                    var clicked = null;
                    // 優先找 form[name='responseForm'] 的 submit（送出答案）
                    for (var i = 0; i < btns.length; i++) {
                        var b = btns[i];
                        var style = window.getComputedStyle(b);
                        var hidden = (style.display === 'none' || style.visibility === 'hidden');
                        var formName = b.form ? (b.form.name || b.form.id || '') : '';
                        info.push({i: i, value: b.value, display: style.display, formName: formName, hidden: hidden});
                        if (!hidden && formName === 'responseForm' && clicked === null) {
                            b.click();
                            clicked = 'btn_' + i + '_responseForm';
                        }
                    }
                    // fallback: 點第一個非 hidden 的 submit
                    if (clicked === null) {
                        for (var j = 0; j < btns.length; j++) {
                            var b2 = btns[j];
                            var style2 = window.getComputedStyle(b2);
                            if (style2.display !== 'none' && style2.visibility !== 'hidden') {
                                b2.click();
                                clicked = 'btn_' + j + '_fallback';
                                break;
                            }
                        }
                    }
                    return {total: btns.length, info: info, clicked: clicked};
                    """
                )
                if result is None:
                    # None 通常表示 click 觸發了頁面跳轉（form submit 成功），繼續執行
                    logger.info(
                        "   📝 execute_script 返回 None（頁面已跳轉，推測 submit 成功）"
                    )
                else:
                    logger.info(
                        f"   📝 submit診斷: total={result.get('total')}, clicked={result.get('clicked')}, info={result.get('info')}"
                    )
                    if result.get("clicked") is None:
                        # fallback: JS form submit（responseForm）
                        logger.warning(
                            "   ⚠️ 所有按鈕都被隱藏，嘗試 JS form.submit()..."
                        )
                        self.driver.execute_script(
                            """
                            var form = document.querySelector('form[name="responseForm"]');
                            if (!form) form = document.querySelector('form[action*="save_answer"]');
                            if (form) { form.submit(); }
                            """
                        )
            except Exception as e:
                logger.warning(f"   ⚠️ 送出按鈕處理失敗: {e}")
                return False

            # ── 8. 處理「你確定要繳交嗎？」alert ──
            time.sleep(1)
            if self._accept_alert():
                logger.info("   📝 已確認繳交")
            else:
                logger.warning("   ⚠️ 未出現繳交確認框")

            # 等待結果頁載入
            time.sleep(3)

            # ── 9. 讀取成績，判斷是否通過 ──
            # form 使用 target="submitTarget"（隱藏 iframe），結果可能在 iframe 裡
            # 也可能整頁換頁。兩個地方都嘗試讀。
            passed = False
            try:
                # 先讀主頁面
                body_text = self.driver.execute_script(
                    "return document.body ? document.body.innerText : '';"
                )
                # 再嘗試讀 submitTarget iframe（如果存在）
                try:
                    iframe_text = self.driver.execute_script(
                        """
                        var f = document.querySelector('[name="submitTarget"], #submitTarget, iframe[name="submitTarget"]');
                        if (f && f.contentDocument) return f.contentDocument.body.innerText;
                        return '';
                        """
                    )
                    body_text = body_text + " " + (iframe_text or "")
                except Exception:
                    pass

                # 「及格」= \u53ca\u683c, 「不及格」= \u4e0d\u53ca\u683c
                if "\u4e0d\u53ca\u683c" in body_text:
                    self._exam_fail_counts[course_id] = (
                        self._exam_fail_counts.get(course_id, 0) + 1
                    )
                    fail_now = self._exam_fail_counts[course_id]
                    if fail_now >= 3:
                        logger.warning(
                            f"   ❌ 測驗不及格，已累計 {fail_now} 次。"
                            f" 課程「{course.get('caption', course_id)}」將跳過，請使用者自行完成測驗"
                        )
                    else:
                        logger.warning(
                            f"   ❌ 測驗不及格（第 {fail_now} 次），下次仍會重試"
                        )
                elif "\u53ca\u683c" in body_text:
                    logger.info("   ✅ 測驗通過（及格）！")
                    passed = True
                    # 通過後清除不及格計數
                    self._exam_fail_counts.pop(course_id, None)
                else:
                    logger.info("   📝 無法判斷成績，請自行確認")
            except Exception:
                pass

            # ── 10. 不及格時嘗試讀取正確答案 ──
            # 考試 form submit 後，exam_window 已整頁跳轉到 view_result.php。
            # 直接對當前視窗呼叫 _harvest_correct_answers。
            # 若當前頁面不是 view_result.php，從 exam_start_url 推算後再 navigate。
            if not passed:
                time.sleep(1)
                try:
                    self.driver.switch_to.window(exam_window)
                    cur_exam_url = self.driver.current_url
                    # 若已在 view_result.php，直接讀取
                    if "view_result" in cur_exam_url:
                        self._harvest_correct_answers(cur_exam_url)
                    else:
                        # 從 exam_start_url 推算 view_result URL
                        # exam_start.php?{course_id}+{attempt}+{token}+0
                        # → view_result.php?{course_id}+{attempt}+{token}
                        m = re.search(r"exam_start\.php\?(.+?)\+0$", exam_start_url)
                        if m:
                            base = exam_start_url.split("/learn/")[0]
                            vr_url = f"{base}/learn/exam/view_result.php?{m.group(1)}"
                            logger.debug(f"   步驟10 推算 view_result URL: {vr_url!r}")
                            self.driver.get(vr_url)
                            time.sleep(2)
                            self._harvest_correct_answers(vr_url)
                        else:
                            logger.debug(
                                f"   步驟10 無法從 exam_start URL 推算 view_result（格式不符）: {exam_start_url!r}"
                            )
                except Exception as e:
                    logger.debug(f"   步驟10 公布答案失敗: {e}")

            return passed

        except Exception as e:
            logger.error(f"   ❌ 自動作答發生錯誤: {e}")
            return False

        finally:
            # 關閉所有多餘視窗（考試視窗、查看結果視窗），切回主視窗
            try:
                for h in list(self.driver.window_handles):
                    if h != main_window:
                        try:
                            self.driver.switch_to.window(h)
                            self.driver.close()
                        except Exception:
                            pass
            except Exception:
                pass
            try:
                self.driver.switch_to.window(main_window)
            except Exception:
                pass
            try:
                self.driver.switch_to.default_content()
            except Exception:
                pass

    def auto_questionnaire(self, course):
        """考試通過後，自動填寫問卷/評價。回傳 True=完成, False=失敗/跳過"""
        logger.info("   📋 開始自動填寫問卷流程...")
        main_window = self.driver.current_window_handle

        try:
            # ── 1. 切回主視窗，點左側 sidebar「問卷/評價」──
            # sidebar 在 mooc_sysbar frame
            self.driver.switch_to.window(main_window)
            self.driver.switch_to.default_content()

            try:
                self.driver.switch_to.frame("mooc_sysbar")
                q_link = self.driver.find_element(
                    By.CSS_SELECTOR,
                    "a[href*='questionnaire/questionnaire_list.php']",
                )
                self.driver.execute_script("arguments[0].click();", q_link)
                logger.info("   📋 已點擊「問卷/評價」")
            except Exception as e:
                logger.warning(f"   ⚠️ 找不到問卷連結（mooc_sysbar）: {e}")
                return False

            time.sleep(2)

            # ── 2. 切到 s_main frame ──
            self.driver.switch_to.default_content()
            try:
                self.driver.switch_to.frame("s_main")
            except Exception:
                logger.warning("   ⚠️ 無法切換到 s_main frame")
                return False

            time.sleep(1)

            # ── 2a. 檢查是否已填過（沒有「填寫問卷」按鈕則視為已完成）──
            pay_btns = self.driver.find_elements(
                By.CSS_SELECTOR, "div.process-btn.pay.active"
            )
            if not pay_btns:
                logger.info("   📋 無可填寫的問卷（已完成或不需填寫）")
                return True

            # ── 3. 點「填寫問卷」──
            self.driver.execute_script("arguments[0].click();", pay_btns[0])
            logger.info("   📋 已點擊「填寫問卷」")
            time.sleep(3)

            # ── 4. 切換到新跳出的問卷視窗 ──
            all_handles = self.driver.window_handles
            q_window = next((h for h in all_handles if h != main_window), None)
            if not q_window:
                logger.warning("   ⚠️ 未偵測到問卷視窗")
                return False

            self.driver.switch_to.window(q_window)
            logger.info("   📋 已切換至問卷視窗")
            time.sleep(2)

            # ── 5. 填寫問卷（radio 選 value=1，checkbox 選第一個，textarea 跳過）──
            rows = self.driver.find_elements(
                By.CSS_SELECTOR, "tr.bg03.font01, tr.bg04.font01"
            )
            answered = 0
            for row in rows:
                try:
                    # checkbox：勾選第一個（value="1"）
                    checkboxes = row.find_elements(
                        By.CSS_SELECTOR, "input[type='checkbox']"
                    )
                    if checkboxes:
                        self.driver.execute_script(
                            "arguments[0].click();", checkboxes[0]
                        )
                        answered += 1
                        continue

                    # radio：選第一個選項
                    radios = row.find_elements(By.CSS_SELECTOR, "input[type='radio']")
                    if radios:
                        self.driver.execute_script("arguments[0].click();", radios[0])
                        answered += 1
                        continue

                    # textarea：跳過

                except Exception as e:
                    logger.debug(f"   ⚠️ 填寫某題時發生錯誤: {e}")

            logger.info(f"   📋 問卷填寫完成：{answered} 題已填")

            # ── 6. 點「確定繳交」──
            time.sleep(1)
            try:
                # 先嘗試精確 value 匹配，fallback 用 JS click 任何可見 submit
                submitted_q = self.driver.execute_script(
                    """
                    var btns = document.querySelectorAll('input[type="submit"]');
                    for (var i = 0; i < btns.length; i++) {
                        var style = window.getComputedStyle(btns[i]);
                        if (style.display !== 'none' && style.visibility !== 'hidden') {
                            btns[i].click();
                            return btns[i].value || 'btn_' + i;
                        }
                    }
                    return null;
                    """
                )
                if submitted_q:
                    logger.info(
                        f"   📋 已點擊「確定繳交」（{submitted_q!r}），等待確認框..."
                    )
                elif submitted_q is None:
                    # None = page navigated during click (submit succeeded)
                    logger.info("   📋 問卷已送出（頁面已跳轉）")
                else:
                    logger.warning("   ⚠️ 找不到「確定繳交」按鈕")
                    return False
            except Exception as e:
                logger.warning(f"   ⚠️ 找不到「確定繳交」按鈕: {e}")
                return False

            # ── 7. 處理「你確定要繳交嗎？」alert ──
            time.sleep(1)
            if self._accept_alert():
                logger.info("   📋 已確認繳交")
            else:
                logger.warning("   ⚠️ 未出現繳交確認框")

            # ── 8. 處理「更新完畢。」alert ──
            time.sleep(2)
            if self._accept_alert():
                logger.info("   📋 問卷已完成（更新完畢）")
            else:
                logger.warning("   ⚠️ 未出現「更新完畢」確認框")

            return True

        except Exception as e:
            logger.error(f"   ❌ 自動填寫問卷發生錯誤: {e}")
            return False

        finally:
            # 關閉問卷視窗，切回主視窗
            try:
                if self.driver.current_window_handle != main_window:
                    self.driver.close()
            except Exception:
                pass
            try:
                self.driver.switch_to.window(main_window)
            except Exception:
                pass

    def init_engine(self):
        self.kill_orphan_drivers()
        try:
            driver_path = os.path.abspath(download_best_chromedriver())
            if not os.path.exists(driver_path):
                logger.error(f"找不到驅動程式檔案: {driver_path}")
                return False

            logger.info(f"🚀 正在啟動輔助引擎...")
            options = Options()
            options.add_argument("--mute-audio")
            # 加速啟動
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-background-networking")
            options.add_argument("--disable-sync")
            options.add_argument("--no-first-run")
            options.add_argument("--no-default-browser-check")
            options.add_argument("--disable-default-apps")

            # ⭐ 關鍵：從 self.config 直接讀取
            headless_mode = self.config.get("headless", True)

            # ⭐ 調試
            logger.info(
                f"🔧 Headless 模式: {headless_mode} (類型: {type(headless_mode).__name__})"
            )

            if headless_mode:
                # 背景執行
                logger.info("⚙️ 使用 Headless 模式（背景執行）")
                options.add_argument("--headless=old")
                options.add_argument("--window-size=1920,1080")
                options.add_argument("--disable-blink-features=AutomationControlled")
            else:
                # ⭐ 顯示窗口
                logger.info("🖥️ 使用顯示模式（有窗口）")
                options.add_argument("--window-size=1920,1080")
                options.add_argument("--disable-blink-features=AutomationControlled")

            self._driver_service = Service(driver_path)
            self.driver = webdriver.Chrome(
                service=self._driver_service, options=options
            )
            if self._driver_service.process:
                self._managed_pids.add(self._driver_service.process.pid)

            self.driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            self.wait = WebDriverWait(self.driver, 30)
            logger.info(f"✅ 引擎就緒：{Fore.GREEN}{self.version}{Style.RESET_ALL}")
            return True
        except Exception as e:
            logger.error(f"引擎初始化失敗: {e}")
            return False

    def sync_session(self) -> bool:
        if not self.driver:
            logger.error("sync_session: driver 尚未初始化，無法同步 session")
            return False
        try:
            self.http_session.cookies.clear()
            for cookie in self.driver.get_cookies():
                self.http_session.cookies.set(
                    cookie["name"], cookie["value"], domain=cookie["domain"]
                )

            # 動態獲取 User-Agent 並移除 Headless 標記
            raw_ua = self.driver.execute_script("return navigator.userAgent")
            clean_ua = raw_ua.replace("HeadlessChrome", "Chrome")

            self.http_session.headers.update(
                {
                    "User-Agent": clean_ua,
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Origin": "https://elearn.hrd.gov.tw",
                    "Referer": self.stat_url,
                }
            )
            return True
        except Exception as e:
            logger.error(f"sync_session 失敗: {e}")
            return False

    def _wait_for_redirect_and_sync(
        self, success_msg: str, check_no_login: bool = False
    ) -> bool:
        """等待重新導向至 elearn.hrd.gov.tw 後同步 session（登入共用邏輯）"""
        for _ in range(60):
            if not self.running:
                logger.info("🛑 使用者手動停止（登入中）")
                return False
            url = self.driver.current_url
            url_ok = "elearn.hrd.gov.tw" in url and (
                not check_no_login or "login" not in url
            )
            if url_ok:
                logger.info(success_msg)
                self.driver.get(self.stat_url)
                if not self.safe_sleep(5):
                    return False
                self.sync_session()
                return True
            time.sleep(0.5)
        return False

    def login(self):
        login_type = self.config.get("login_type", "ecpa")

        if login_type == "egov":
            return self.login_egov()
        else:
            return self.login_ecpa()

    def login_ecpa(self):
        try:
            logger.info("🔑 正在對接 eCPA 登入系統...")
            self.driver.get(self.ecpa_url)
            self.wait.until(EC.presence_of_element_located((By.ID, "aliasid")))

            user_f = self.driver.find_element(By.ID, "aliasid")
            pass_f = self.driver.find_element(By.ID, "pas")

            for c in self.config["account"]:
                user_f.send_keys(c)
                time.sleep(random.uniform(0.01, 0.03))

            for c in self.config["password"]:
                pass_f.send_keys(c)
                time.sleep(random.uniform(0.01, 0.03))

            self.driver.execute_script(
                "document.querySelector('#idarea button').click();"
            )

            return self._wait_for_redirect_and_sync(
                "✅ 系統身分驗證成功！", check_no_login=True
            )

        except Exception as e:
            logger.error(f"登入異常: {e}")
            return False

    def login_egov(self):
        try:
            logger.info("🔑 使用我的E政府登入...")

            self.driver.get(
                "https://www.cp.gov.tw/portal/Clogin.aspx?ReturnUrl=https://elearn.hrd.gov.tw/egov_login.php&ver=Simple&Level=1"
            )

            # 等 modal 出現（關鍵）
            self.wait.until(EC.presence_of_element_located((By.ID, "modal1")))

            # 用 ID 抓
            user_f = self.wait.until(
                EC.element_to_be_clickable(
                    (By.ID, "AccountPassword_simple_txt_account")
                )
            )
            pass_f = self.wait.until(
                EC.element_to_be_clickable(
                    (By.ID, "AccountPassword_simple_txt_password")
                )
            )

            user_f.clear()
            pass_f.clear()

            user_f.send_keys(self.config["account"])
            pass_f.send_keys(self.config["password"])

            # 登入按鈕
            login_btn = self.driver.find_element(
                By.ID, "AccountPassword_simple_btn_LoginHandler"
            )

            # 用 JS 點（避免被擋）
            self.driver.execute_script("arguments[0].click();", login_btn)

            return self._wait_for_redirect_and_sync("✅ E政府登入成功")

        except Exception as e:
            logger.error(f"E政府登入失敗: {e}")
            return False

    def get_progress_api(self, course_id):
        cache_key = str(course_id)
        now = time.time()
        cached = getattr(self, "_progress_cache", {})
        if cache_key in cached:
            result, ts = cached[cache_key]
            if now - ts < 30:
                return result
        try:
            current_year = time.strftime("%Y")
            # 多頁查詢，避免課程在 page>1 時查不到進度
            for _page in range(1, 21):
                payload = f"year={current_year}&keyword=&course_type=single&page={_page}&orderby=&sort="
                resp = self.http_session.post(
                    self.api_url, data=payload, verify=False, timeout=10
                )
                data = resp.json().get("data", [])
                for c in data:
                    if str(c.get("course_id")) == str(course_id):
                        cur_s = to_sec(c.get("rss", "00:00:00"))
                        target_s = to_sec(
                            c.get("criteria_content_hour", "00:30:00")
                        ) * self.config.get("target_percentage", 1.0)
                        result = {
                            "cur_str": sec_to_str(cur_s),
                            "target_str": sec_to_str(target_s),
                            "cur_sec": cur_s,
                            "target_sec": target_s,
                        }
                        if not hasattr(self, "_progress_cache"):
                            self._progress_cache = {}
                        self._progress_cache[cache_key] = (result, now)
                        return result
                if len(data) < 50:
                    break  # 最後一頁，不再繼續
        except Exception as e:
            logger.debug(f"進度查詢失敗: {e}")
        return None

    def study_process(self, course):
        logger.info(
            f"📖 [{self.current_idx}/{self.total_courses}] 正在協助研習：{Fore.YELLOW}{course['caption']}{Style.RESET_ALL}"
        )
        session_start = time.time()
        last_prog_sec = -1
        last_prog_time = time.time()

        try:
            # ⭐ 檢查點 1
            if not self.running:
                logger.info("🛑 使用者手動停止（study_process 開始）")
                return "STOP"

            # 確保 driver 在 stat_url（gotoCourse 函式只在該頁面定義）
            self.driver.get(self.stat_url)
            if not self.safe_sleep(3):
                return "STOP"

            self.driver.execute_script(f"gotoCourse({course['course_id']})")
            if not self.safe_sleep(5):
                return "STOP"

            # ⭐ 進入課程後先攔截 alert（如「您非本門課的學生」）
            try:
                WebDriverWait(self.driver, 3).until(EC.alert_is_present())
                alert = self.driver.switch_to.alert
                alert_text = alert.text
                alert.accept()
                logger.warning(f"⚠️ gotoCourse 後偵測到 Alert：{alert_text}")
                if any(kw in alert_text for kw in ["非本門課", "無法上課", "無權限", "不開放", "未選課"]):
                    logger.warning(f"⚠️ 此課程無法進入（{alert_text}），永久跳過。")
                    return "SKIP"
                elif any(kw in alert_text for kw in ["閒置", "重新登入", "登出"]):
                    return "RELOGIN"
            except Exception:
                pass  # 無 alert，正常繼續

            # ⭐ 檢查點 2
            if not self.running:
                logger.info("🛑 使用者手動停止（進入課程）")
                return "STOP"

            try:
                self.wait.until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "button.btn-primary"))
                ).click()
            except Exception:
                if not self.running:  # ⭐ 檢查點 3
                    logger.info("🛑 使用者手動停止（等待按鈕）")
                    return "STOP"
                self.driver.execute_script(
                    "document.querySelector('button.btn-primary').click();"
                )

            for _ in range(10):
                if not self.running:  # ⭐ 檢查點 4
                    logger.info("🛑 使用者手動停止（等待教室載入）")
                    return "STOP"
                time.sleep(1)

            classroom_h = self.driver.window_handles[-1]
            self.driver.switch_to.window(classroom_h)

            attempted = set()
            frame_fail_count = 0

            while self.running:
                # 1. 檢查單次累計時數是否超過 2 小時 (7200秒)
                if time.time() - session_start > 7200:
                    logger.warning(
                        f"   ⚠️ 單一課程研習已達 2 小時，為避免異常，將切換課程。"
                    )
                    break

                prog = self.get_progress_api(course["course_id"])
                if prog:
                    logger.info(
                        f"   📊 研習進度：{prog['cur_str']} / {prog['target_str']} {draw_bar(prog['cur_sec'], prog['target_sec'])}"
                    )

                    if prog["cur_sec"] > last_prog_sec:
                        last_prog_sec = prog["cur_sec"]
                        last_prog_time = time.time()
                    elif time.time() - last_prog_time > 600:
                        logger.error(
                            f"   🛑 進度停滯超過 10 分鐘，正在強制執行重啟救回機制。"
                        )
                        return "STALLED"
                    elif time.time() - last_prog_time > 300:
                        logger.warning(f"   ⚠️ 進度已停滯 5 分鐘，請注意連線狀態。")

                    if prog["cur_sec"] >= prog["target_sec"]:
                        logger.info(f"   ✨ {Fore.GREEN}時數已達標！{Style.RESET_ALL}")
                        break

                # ⭐ 檢查點 6（frame 操作前）
                if not self.running:
                    logger.info("🛑 使用者手動停止（frame 操作前）")
                    return "STOP"

                self.driver.switch_to.window(classroom_h)
                self.driver.switch_to.default_content()

                try:
                    self.driver.switch_to.frame("s_catalog")
                    self.driver.switch_to.frame("pathtree")
                    frame_fail_count = 0

                    all_links = [
                        l
                        for l in self.driver.find_elements(By.TAG_NAME, "a")
                        if l.text.strip()
                    ]
                    links = [
                        l for l in all_links
                        if l.text.strip() not in self.config["blacklist"]
                    ]
                    # 診斷：若無可用 link，記錄原始清單
                    if not links:
                        all_texts = [l.text.strip() for l in all_links]
                        logger.warning(f"   ⚠️ pathtree 無可選單元，原始清單({len(all_texts)}筆): {all_texts[:20]}")
                    target = next(
                        (l for l in links if l.text not in attempted),
                        random.choice(links) if links else None,
                    )
                    # 所有單元都已嘗試過 → 重置讓下一輪重新輪
                    if target is None and links:
                        logger.info("   🔄 所有單元已輪完，重置重新輪...")
                        attempted.clear()
                        target = random.choice(links)

                    if target:
                        # ⭐ 檢查點 7（進入單元前）
                        if not self.running:
                            logger.info("🛑 使用者手動停止（進入單元前）")
                            return "STOP"

                        u_name = target.text.strip()
                        attempted.add(u_name)
                        logger.info(f"   📍 進入單元：{u_name[:20]}...")
                        self.driver.execute_script("arguments[0].click();", target)

                        w_time = self.config.get("residence_time", 75)
                        st = time.time()
                        while time.time() - st < w_time:
                            # ⭐ 檢查點 8（停留時間內）
                            if not self.running:
                                logger.info("🛑 使用者手動停止（停留中）")
                                return "STOP"

                            time.sleep(1)
                            self.driver.switch_to.window(classroom_h)
                            self.driver.execute_script(
                                "function deepCommit(win){ try{if(win.API)win.API.LMSCommit('');}catch(e){} if(win.frames){for(let i=0;i<win.frames.length;i++)deepCommit(win.frames[i]);}} deepCommit(window);"
                            )
                    else:
                        for _ in range(30):
                            # ⭐ 檢查點 9（無目標課程時）
                            if not self.running:
                                logger.info("🛑 使用者手動停止（無課程可選）")
                                return "STOP"
                            time.sleep(1)
                except Exception as e:
                    logger.warning(f"   ⚠️ frame 切換失敗: {e}")
                    frame_fail_count += 1
                    # 診斷：記錄當前 URL 與視窗數量，幫助判斷頁面狀態
                    try:
                        logger.warning(f"   🔍 當前 URL: {self.driver.current_url}, 視窗數: {len(self.driver.window_handles)}")
                        frames = self.driver.find_elements(By.TAG_NAME, "iframe")
                        frame_ids = [f.get_attribute("name") or f.get_attribute("id") or "(no id)" for f in frames]
                        logger.warning(f"   🔍 頁面 iframe 清單: {frame_ids}")
                    except Exception as diag_e:
                        logger.warning(f"   🔍 診斷失敗: {diag_e}")
                    if frame_fail_count >= 5:
                        logger.error(
                            f"   ❌ 連續 5 次找不到課程選單，視窗可能已毀損，嘗試重啟。"
                        )
                        return "STALLED"
                    for _ in range(30):
                        # ⭐ 檢查點 10（frame 異常時）
                        if not self.running:
                            logger.info("🛑 使用者手動停止（frame 異常等待中）")
                            return "STOP"
                        time.sleep(1)

            # ⭐ 檢查點 11（結束前）
            if not self.running:
                logger.info("🛑 使用者手動停止（課程結束前）")
                return "STOP"

            # 時數達標，嘗試自動作答測驗，通過後填寫問卷
            if self.running:
                exam_passed = self.auto_exam(course)
                if self.running and exam_passed:
                    self.auto_questionnaire(course)

            logger.info("   🔄 返回學習概況清單...")
            self.driver.get(self.stat_url)
            if not self.safe_sleep(5):
                return "STOP"
            self.sync_session()
            return "SUCCESS"

        except UnexpectedAlertPresentException as e:
            # 偵測「閒置過久被登出」Alert
            alert_text = ""
            try:
                alert = self.driver.switch_to.alert
                alert_text = alert.text
                alert.accept()
                logger.warning(f"⚠️ 偵測到 Alert：{alert_text}")
            except Exception:
                alert_text = str(e)
            if "閒置" in alert_text or "重新登入" in alert_text or "登出" in alert_text:
                logger.warning("🔄 帳號閒置被登出，嘗試重新登入後繼續當前課程...")
                try:
                    self.driver.get(self.stat_url)
                except Exception:
                    pass
                time.sleep(3)
                if self.login():
                    logger.info("✅ 重新登入成功，將重試當前課程。")
                    return "RELOGIN"
                else:
                    logger.error("❌ 重新登入失敗，跳過當前課程。")
                    return "ERROR"
            elif any(kw in alert_text for kw in ["非本門課", "無法上課", "無權限", "不開放", "未選課"]):
                logger.warning(f"⚠️ 此課程無法上課（{alert_text}），永久跳過。")
                try:
                    self.driver.get(self.stat_url)
                except Exception:
                    pass
                time.sleep(3)
                return "SKIP"
            else:
                logger.error(f"   ❌ 研習異常（Alert）: {alert_text}", exc_info=True)
                try:
                    self.driver.get(self.stat_url)
                except Exception:
                    pass
                time.sleep(5)
                return "ERROR"

        except Exception as e:
            logger.error(f"   ❌ 研習異常: {e}", exc_info=True)
            try:
                self.driver.get(self.stat_url)
            except Exception:
                pass
            time.sleep(5)
            return "ERROR"

    def check_update(self):
        """啟動時檢查 GitHub 是否有新版本，有則透過 UI 發送通知訊號"""
        VERSION_URL = "https://raw.githubusercontent.com/waynelord0628-beep/auto-learning-bot/main/version.txt"
        DOWNLOAD_URL = "https://drive.google.com/drive/u/0/folders/1Fm6CwmV2AsoWaUOGV0V5hZbgP_GJrU8g"
        try:
            resp = requests.get(VERSION_URL, timeout=5)
            latest = resp.text.strip()
            if latest and latest != self.version:
                logger.info(f"🆕 發現新版本 {latest}（目前 {self.version}），請前往下載最新版。")
                if hasattr(self, "update_signal"):
                    self.update_signal.emit(latest, self.changelog, DOWNLOAD_URL)
            else:
                logger.info(f"✅ 已是最新版本（{self.version}）")
        except Exception as e:
            logger.debug(f"版本檢查失敗（無網路或暫時性問題）: {e}")

    def safe_sleep(self, seconds):
        """⭐ 正確位置：在類內"""
        for _ in range(int(seconds)):
            if not self.running:
                logger.info("🛑 使用者手動停止")
                return False
            time.sleep(1)
        return True

    def run(self):
        """⭐ 正確位置：在類內"""
        self._start_keep_awake()
        self.check_update()
        print(
            f"\n{Fore.CYAN}{'=' * 60}\n【行政效能領航員 - 數位研習輔助方案 {self.version}】\n{'=' * 60}{Style.RESET_ALL}"
        )
        try:
            if not self.init_engine():
                if sys.stdin:
                    input(
                        f"\n{Fore.RED}❌ 引擎啟動失敗，請檢查驅動程式後按 Enter 退出...{Style.RESET_ALL}"
                    )
                return

            if not self.login():
                login_type = self.config.get("login_type", "ecpa")
                if login_type == "egov":
                    msg = "❌ 登入失敗！請確認『我的E政府』帳密正確，或是否出現驗證碼。"
                else:
                    msg = "❌ 登入失敗！請確認 eCPA 帳密正確且無驗證碼要求。"
                if sys.stdin:
                    input(f"\n{Fore.RED}{msg} 按 Enter 退出...{Style.RESET_ALL}")
                return

            while self.running:
                try:
                    cur_y = time.strftime("%Y")
                    try:
                        # 撈所有分頁，避免只查 page=1 而遺漏後面頁的課程
                        courses = []
                        for _page in range(1, 20):
                            _payload = f"year={cur_y}&keyword=&course_type=single&page={_page}&orderby=&sort="
                            _r = self.http_session.post(
                                self.api_url, data=_payload, verify=False, timeout=10
                            )
                            _data = _r.json().get("data", [])
                            courses.extend(_data)
                            if len(_data) < 50:
                                break  # 不足 50 筆代表已是最後一頁
                    except Exception as e:
                        logger.error(f"無法讀取列表，重試中... ({e})")
                        for _ in range(10):
                            if not self.running:  # ⭐ 重試時也檢查
                                logger.info("🛑 使用者手動停止")
                                break
                            time.sleep(1)
                        if not self.running:
                            break
                        continue

                    # ⭐ 檢查點（取得課程後）
                    if not self.running:
                        logger.info("🛑 已收到停止指令（取得課程後）")
                        break

                    logger.info(f"📋 API 回傳課程總數：{len(courses)} 筆")

                    # ⭐ 防呆：API 回 0 筆可能是 session 失效，重新 sync 再試一次
                    if len(courses) == 0:
                        logger.warning("⚠️ API 回傳 0 筆，嘗試重新同步 session 後重查...")
                        self.sync_session()
                        time.sleep(3)
                        try:
                            courses = []
                            for _page in range(1, 20):
                                _payload = f"year={cur_y}&keyword=&course_type=single&page={_page}&orderby=&sort="
                                _r = self.http_session.post(
                                    self.api_url, data=_payload, verify=False, timeout=10
                                )
                                _data = _r.json().get("data", [])
                                courses.extend(_data)
                                if len(_data) < 50:
                                    break
                        except Exception as e:
                            logger.error(f"重查失敗: {e}")
                        logger.info(f"📋 重查後課程總數：{len(courses)} 筆")
                        if len(courses) == 0:
                            logger.warning("⚠️ 重查仍為 0 筆，等待 30 秒後繼續（可能是暫時性問題）...")
                            for _ in range(30):
                                if not self.running:
                                    break
                                time.sleep(1)
                            continue  # 回到 while 迴圈頂端重新整個流程

                    pending = [
                        c
                        for c in courses
                        if to_sec(c.get("rss", "00:00:00"))
                        < to_sec(c.get("criteria_content_hour", "00:00:00"))
                        * self.config.get("target_percentage", 1.0)
                        # 考試已通過且問卷已填 → 視為真正完成，不再上課補時數
                        and not (
                            c.get("exam_score") is not None and c.get("fill") == "1"
                        )
                        # 本次 session 已永久跳過（如「非本門課」）的課程
                        and str(c.get("course_id", "")) not in self._completed_in_session
                    ]
                    if pending:
                        logger.info(
                            f"⏳ 待上課程 {len(pending)} 筆："
                            + "、".join(c.get("caption", "?")[:15] for c in pending[:5])
                            + ("..." if len(pending) > 5 else "")
                        )

                    # 時數已達標 且 考試未通過 或 問卷未填 的課程
                    def _needs_exam_or_questionnaire(c):
                        c_id = str(c.get("course_id", ""))
                        # 本次已成功處理過，跳過
                        if c_id in self._completed_in_session:
                            return False
                        hours_done = to_sec(c.get("rss", "00:00:00")) >= to_sec(
                            c.get("criteria_content_hour", "00:00:00")
                        ) * self.config.get("target_percentage", 1.0)
                        if not hours_done:
                            return False
                        # 有考試且未通過（exam_score 為 null/None 且 exam_exists=="1"）
                        needs_exam = (
                            c.get("exam_exists") == "1" and c.get("exam_score") is None
                        )
                        # 有問卷且未填（fill=="0" 且 write_questionnaire 非空）
                        needs_questionnaire = c.get("fill") == "0" and bool(
                            c.get("write_questionnaire", "")
                        )
                        return needs_exam or needs_questionnaire

                    completed_hours = [
                        c for c in courses if _needs_exam_or_questionnaire(c)
                    ]

                    if not pending and not completed_hours:
                        break

                    # ── 第一步：先對時數已達標但考試/問卷未完成的課程執行 ──
                    # （初始使用者全部 pending 時，completed_hours 為空，此段直接跳過）
                    if completed_hours:
                        all_exam_done = True
                        for c in completed_hours:
                            if not self.running:
                                break
                            logger.info(
                                f"📝 對已達標課程執行考試/問卷：{c.get('caption', '')}"
                            )
                            # 導航到學習統計頁，再進入課程教室
                            self.driver.get(self.stat_url)
                            if not self.safe_sleep(3):
                                break
                            try:
                                self.driver.execute_script(
                                    f"gotoCourse({c['course_id']})"
                                )
                                if not self.safe_sleep(5):
                                    break
                                # 點「開始上課」按鈕（如有）
                                # 教室在同一視窗載入（不開新視窗），直接繼續
                                try:
                                    btn = self.wait.until(
                                        EC.element_to_be_clickable(
                                            (By.CSS_SELECTOR, "button.btn-primary")
                                        )
                                    )
                                    self.driver.execute_script(
                                        "arguments[0].click();", btn
                                    )
                                    if not self.safe_sleep(5):
                                        break
                                except Exception:
                                    pass
                                logger.info(
                                    f"   📝 已進入課程教室，URL: {self.driver.current_url}"
                                )
                            except Exception as e:
                                logger.debug(f"導航課程失敗: {e}")

                            passed = self.auto_exam(c)
                            if passed and self.running:
                                self.auto_questionnaire(c)
                                # 記錄本次已處理（避免每次迴圈重複執行）
                                self._completed_in_session.add(
                                    str(c.get("course_id", ""))
                                )
                            if not passed:
                                # 若不及格次數已達上限，視為「跳過」不阻擋結束
                                c_id = str(c.get("course_id", ""))
                                if self._exam_fail_counts.get(c_id, 0) < 3:
                                    all_exam_done = False
                                else:
                                    # 已達上限，本次不再重試，加入已處理集合
                                    self._completed_in_session.add(c_id)

                        if not self.running:
                            break

                        # 若全部達標課程都處理完，且無 pending，則全部完成
                        if all_exam_done and not pending:
                            break

                    # ── 第二步：處理時數未達標的課程（上課）──
                    if pending:
                        self.total_courses = len(pending) + (self.current_idx)
                        self.current_idx += 1
                        res = self.study_process(pending[0])

                        if res == "STOP":
                            logger.info("🛑 使用者已停止程式")
                            break

                        if res == "RELOGIN":
                            # 閒置登出後已重新登入，重試當前課程（退回 index）
                            logger.info("🔄 閒置登出重新登入成功，重試當前課程...")
                            self.current_idx -= 1
                            continue

                        if res == "STALLED":
                            logger.warning("🚀 偵測到停滯，正在重新啟動輔助引擎...")
                            self._cleanup()
                            if not self.safe_sleep(5):
                                break
                            if not self.init_engine() or not self.login():
                                logger.error("❌ 引擎重啟或登入失敗，無法繼續。")
                                break
                            self.current_idx -= 1
                        elif res == "SKIP":
                            # 永久性無法上課（如「您非本門課的學生」），排除此課程
                            c_id = str(pending[0].get("course_id", ""))
                            if c_id:
                                self._completed_in_session.add(c_id)
                            logger.info(f"⏭️ 已永久跳過課程，繼續下一門...")
                        elif res == "ERROR":
                            logger.info("⏳ 發生研習異常，稍後嘗試下一門課程...")
                            time.sleep(5)

                except Exception as e:
                    logger.error(f"⚠️ 核心迴圈發生錯誤: {e}")
                    # 偵測 WebDriver session 失效（Chrome crash / HTTPConnectionPool）
                    err_str = str(e)
                    if (
                        "HTTPConnectionPool" in err_str
                        or "Failed to establish a new connection" in err_str
                        or "session" in err_str.lower()
                        or "WebDriver" in err_str
                        or "chrome not reachable" in err_str.lower()
                    ):
                        logger.warning(
                            "🔄 偵測到瀏覽器 session 失效，嘗試重建引擎並重新登入..."
                        )
                        self._cleanup()
                        if not self.safe_sleep(5):
                            break
                        if not self.init_engine() or not self.login():
                            logger.error("❌ 引擎重啟或登入失敗，無法繼續。")
                            break
                        logger.info("✅ 引擎重建成功，從當前課程繼續...")
                    else:
                        self.safe_sleep(10)

            logger.info(f"🏆 {Fore.GREEN}所有任務圓滿達成！{Style.RESET_ALL}")
            if sys.stdin:
                input(f"\n{Fore.GREEN}✓ 程式執行完畢，按 Enter 關閉。{Style.RESET_ALL}")

        except KeyboardInterrupt:
            print(
                f"\n{Fore.YELLOW}⚠️ 使用者中斷（Ctrl+C），正在安全退出...{Style.RESET_ALL}"
            )

        except Exception as e:
            logger.critical(f"🔥 程式發生致命錯誤: {e}")
            if sys.stdin:
                input(
                    f"\n{Fore.RED}❌ 發生嚴重錯誤，請查看 debug.log 並按 Enter 退出...{Style.RESET_ALL}"
                )
        finally:
            self._cleanup()


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="行政效能領航員 自動化工具")
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="強制以 headless（背景）模式執行，不顯示瀏覽器視窗",
    )
    parser.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        help="強制以有視窗模式執行（可覆蓋 config.json 設定）",
    )
    # 讓 argparse 只解析已知參數，避免因其他 argv 而報錯
    args, _ = parser.parse_known_args()

    override = {}
    # 只有明確傳入 --headless 或 --no-headless 時才覆蓋 config.json
    if "--headless" in sys.argv:
        override["headless"] = True
    elif "--no-headless" in sys.argv:
        override["headless"] = False

    AdminEfficiencyPilot(config_override=override if override else None).run()
