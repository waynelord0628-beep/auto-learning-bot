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
from selenium.common.exceptions import UnexpectedAlertPresentException, NoAlertPresentException
from colorama import Fore, Style, init

from utils.helpers import get_logger, to_sec, sec_to_str, draw_bar
from utils.webdriver_mgr import download_best_chromedriver
from utils.playback_wait import wait_with_heartbeat
from utils.media_playback import start_unstarted_video

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


def _version_tuple(version):
    nums = re.findall(r"\d+", str(version or ""))
    return tuple(int(n) for n in nums[:3]) if nums else (0,)


def _is_newer_version(latest, current):
    return _version_tuple(latest) > _version_tuple(current)


class AdminEfficiencyPilot:
    VERSION = "V2.1.9"
    CHANGELOG = (
        "• 新增入口一鍵更新，下載完成後自動重新啟動\n"
        "• 改善上課播放穩定性與部分課程單元切換\n"
        "• 改善操作流暢度與任務啟動、停止流程\n"
        "• 改善部分題型作答相容性，減少重複通知"
    )

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
            if "name" not in self.config and "name" in acc:
                self.config["name"] = acc["name"]

        # ⭐ 調試：打印最終配置（遮蔽敏感欄位）
        logger.info(f"📋 最終配置: headless={self.config.get('headless', True)}")
        _safe_settings = {k: self.config.get("settings", {}).get(k) for k in ("headless", "disable_gpu", "login_type", "ai_provider")}
        logger.info(f"📋 settings={_safe_settings}")

        self.version = self.VERSION
        self.changelog = self.CHANGELOG
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
        loaded = self.config.get("login_type") == "taipei_eda"

        # 優先：questions.db（SQLite，含選項結構）
        db_path = os.path.join(base_dir, "questions.db")
        if not loaded and os.path.exists(db_path):
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
        self._managed_process_times = {}
        self._cleanup_lock = threading.Lock()
        self.log_callback = log_callback
        self.running = True  # 停止開關
        self._exam_fail_counts = {}  # course_id → 不及格次數
        self._completed_in_session = (
            set()
        )  # course_id → 本次已成功處理（考試通過+問卷完成）
        self._last_course_count = 0

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
            from logging.handlers import RotatingFileHandler
            fh = RotatingFileHandler(self.log_file, maxBytes=8 * 1024 * 1024, backupCount=3, encoding="utf-8")
            fh.setFormatter(
                logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
            )
            logger.addHandler(fh)

        # 無論怎麼結束（Ctrl+C、關視窗、正常結束）都會清理
        atexit.register(self._cleanup)
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, lambda *_: self._cleanup())
            try:
                signal.signal(
                    signal.SIGBREAK, lambda *_: self._cleanup()
                )  # Windows Ctrl+Break
            except (AttributeError, OSError):
                pass

        # GAS 題庫靜默背景同步（啟動時）
        _t = threading.Thread(target=self._update_db_from_gas, daemon=True, name="GAS-DB-Sync")
        _t.start()

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

    def _cleanup(self, stop=True):
        """統一清理入口，重複呼叫安全（atexit/signal/finally 都指向這裡）。"""
        with self._cleanup_lock:
            if stop:
                self.running = False
                self._stop_keep_awake()
            if self.config.get("login_type") == "taipei_eda":
                try:
                    from taipei_eda_course import force_close_active_driver
                    force_close_active_driver(owner=self)
                except Exception:
                    pass
            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass
                self.driver = None
            self._kill_managed_processes()
            if stop:
                session = getattr(self, "http_session", None)
                if session is not None:
                    session.close()


    def kill_orphan_drivers(self):
        """Only clean drivers owned by this instance; another parent is not an orphan."""
        self._kill_managed_processes()

    def _kill_managed_processes(self):
        """結束時清理：只殺本次自己記錄的 PID 樹，不影響使用者其他 Chrome。"""
        if not self._managed_pids:
            return
        for pid in list(self._managed_pids):
            try:
                proc = psutil.Process(pid)
                if proc.create_time() != self._managed_process_times.get(pid):
                    continue  # PID has been reused, or ownership was never recorded.
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
        self._managed_process_times.clear()
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

    def _ai_find_answer(self, question_text: str, option_texts: list):
        """題庫找不到答案時，呼叫 AI API 協助選答。
        支援 OpenAI / Gemini / Groq（Bearer）及 Claude（x-api-key）。
        自動 fallback：先用便宜模型，失敗再升級。
        """
        # 各服務的 fallback 鏈（便宜 → 貴）
        _FALLBACK = {
            "OpenAI": ["gpt-4o-mini", "gpt-4o"],
            "Gemini": ["gemini-3.1-flash-lite", "gemini-3.5-flash", "gemini-2.5-flash"],
            "Claude": ["claude-haiku-4-5", "claude-sonnet-4-6"],
            "Groq":   ["llama-3.1-8b-instant", "llama-3.3-70b-versatile"],
        }

        provider = self.config.get("ai_provider", "OpenAI")
        ai_keys  = self.config.get("ai_keys", {})
        api_key  = ai_keys.get(provider) or self.config.get("ai_api_key", "")
        if not api_key or not option_texts:
            return None
        base_url = self.config.get("ai_base_url", "https://api.openai.com/v1").rstrip("/")

        # 使用者設定的模型優先；若不在 fallback 鏈裡則以此為起點
        configured_model = self.config.get("ai_model", "gpt-4o-mini")
        chain = _FALLBACK.get(provider, [configured_model])
        # 從使用者設定的模型開始，忽略前面更便宜的（尊重使用者選擇）
        if configured_model in chain:
            chain = chain[chain.index(configured_model):]
        else:
            chain = [configured_model] + chain

        cleaned_options = [
            str(opt).strip() if str(opt).strip() else f"選項{i + 1}"
            for i, opt in enumerate(option_texts)
        ]
        options_str = "\n".join(
            [f"{i + 1}. {opt}" for i, opt in enumerate(cleaned_options)]
        )
        prompt = (
            "你是考試作答助手。請從以下選項中選出正確答案。\n"
            "務必只回覆一個選項編號，例如 1、2、3、4。\n"
            "不要回覆選項文字，不要解釋，不要加前後綴。\n\n"
            f"題目：{question_text}\n\n"
            f"選項：\n{options_str}\n\n"
            "正確答案選項編號："
        )

        for model in chain:
            try:
                if provider == "Claude":
                    resp = requests.post(
                        f"{base_url}/messages",
                        headers={
                            "x-api-key":         api_key,
                            "anthropic-version": "2023-06-01",
                            "Content-Type":      "application/json",
                        },
                        json={
                            "model":    model,
                            "max_tokens": 150,
                            "messages": [{"role": "user", "content": prompt}],
                        },
                        timeout=20,
                        verify=False,
                    )
                else:
                    resp = requests.post(
                        f"{base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type":  "application/json",
                        },
                        json={
                            "model":       model,
                            "messages":    [{"role": "user", "content": prompt}],
                            "max_tokens":  150,
                            "temperature": 0,
                        },
                        timeout=20,
                        verify=False,
                    )

                # 429 / 503 → 嘗試下一個模型；其他錯誤直接拋出
                if resp.status_code in (429, 503):
                    logger.warning(f"   ⚠️ AI [{model}] 回傳 {resp.status_code}，嘗試下一個模型")
                    continue
                resp.raise_for_status()

                if provider == "Claude":
                    answer = resp.json()["content"][0]["text"].strip()
                else:
                    answer = resp.json()["choices"][0]["message"]["content"].strip()

                if not re.fullmatch(r"[1-9][0-9]*", answer) or not 1 <= int(answer) <= len(cleaned_options):
                    logger.warning(f"   ⚠️ AI [{model}] 回覆不是有效選項編號，嘗試下一個模型")
                    continue
                logger.info(f"   🤖 AI 補充答案（{model}）：{answer!r}")
                return answer

            except Exception as e:
                logger.warning(f"   ⚠️ AI [{model}] 呼叫失敗: {e}")
                continue

        logger.warning("   ⚠️ 所有 AI 模型均失敗，放棄補答")
        return None

    def _save_answers_to_db(self, answers: dict, source: str = ""):
        """將 {題目: 答案} dict upsert 進 questions.db 並同步記憶體。
        answers: {q_text: ans_str}
        source:  用於 log 標注來源（如 'AI' / 'harvest'）
        """
        if not answers:
            return
        import sys as _sys
        _base = (
            os.path.dirname(_sys.executable)
            if getattr(_sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__))
        )
        db_path = os.path.join(_base, "questions.db")
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS questions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT UNIQUE NOT NULL,
                    option_a TEXT, option_b TEXT, option_c TEXT, option_d TEXT,
                    answer TEXT
                )
            """)
            added = 0
            for q_text, ans_str in answers.items():
                cur = conn.execute(
                    "SELECT id FROM questions WHERE question = ?", (q_text,)
                ).fetchone()
                if cur:
                    conn.execute(
                        "UPDATE questions SET answer = ? WHERE question = ?",
                        (ans_str, q_text),
                    )
                else:
                    conn.execute(
                        "INSERT INTO questions (question, answer) VALUES (?, ?)",
                        (q_text, ans_str),
                    )
                    added += 1
                # 同步記憶體（不論新增或更新都要覆蓋，避免記憶體留有舊錯誤答案）
                nk = _normalize_q(q_text)
                if nk:
                    if nk not in self._answer_map:
                        self._answer_keys.append(nk)
                    self._answer_map[nk] = {"answer": ans_str, "options": [], "question": q_text}
            conn.commit()
            conn.close()
            tag = f"（{source}）" if source else ""
            logger.info(f"   💾 已同步 {len(answers)} 題到 questions.db{tag}（新增 {added} 題）")
        except Exception as e:
            logger.warning(f"   ⚠️ 寫入 questions.db 失敗: {e}")

    # ── GAS 題庫同步 URL（送出缺題 + 下載更新共用同一個 endpoint）──
    _GAS_DB_URL = "https://script.google.com/macros/s/AKfycbzYUNM--zLlS8El6YR6lIiKerBIz1M6rL2gM8nTGicmEjfh_1TNiBo12YcVsb37J7Cl/exec"
    _GAS_PATCH_URL = "https://raw.githubusercontent.com/waynelord0628-beep/auto-learning-bot/main/patches/questions_patch.json"

    def _update_db_from_gas(self):
        """啟動時靜默背景同步：從 GitHub Raw 直接下載 questions_patch.json 並 upsert 進本地 questions.db。

        格式：[{"question":"...","answer":"...","options":["A","B","C","D"],...}, ...]
        """
        conn = None
        try:
            logger.info("📥 正在從雲端同步最新題庫（背景）...")
            resp = requests.get(
                self._GAS_PATCH_URL,
                timeout=30,
                verify=False,
            )
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list) or len(data) == 0:
                logger.info("📥 GAS 回傳題庫為空或格式不符，略過")
                return

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
            added = 0
            updated = 0
            pending_memory = {}
            for item in data:
                q = (item.get("question") or "").strip()
                a = (item.get("answer") or "").strip()
                if not q or not a:
                    continue
                # 支援 options:[] 格式（questions_patch.json）與 option_a/b/c/d 格式（GAS doGet）
                opts = item.get("options") or []
                oa = (opts[0] if len(opts) > 0 else item.get("option_a") or "").strip()
                ob = (opts[1] if len(opts) > 1 else item.get("option_b") or "").strip()
                oc = (opts[2] if len(opts) > 2 else item.get("option_c") or "").strip()
                od = (opts[3] if len(opts) > 3 else item.get("option_d") or "").strip()
                existing = conn.execute(
                    "SELECT id, answer, option_a, option_b, option_c, option_d FROM questions WHERE question = ?", (q,)
                ).fetchone()
                if existing:
                    if tuple(value or "" for value in existing[1:]) != (a, oa, ob, oc, od):
                        conn.execute(
                            "UPDATE questions SET answer=?, option_a=?, option_b=?, option_c=?, option_d=? WHERE question=?",
                            (a, oa, ob, oc, od, q),
                        )
                        updated += 1
                else:
                    conn.execute(
                        "INSERT INTO questions (question, option_a, option_b, option_c, option_d, answer) VALUES (?,?,?,?,?,?)",
                        (q, oa, ob, oc, od, a),
                    )
                    added += 1
                # Publish only after the entire database transaction succeeds.
                nk = _normalize_q(q)
                if nk:
                    pending_memory[nk] = {
                        "answer": a,
                        "options": [o for o in [oa, ob, oc, od] if o],
                        "question": q,
                    }
            conn.commit()
            # Update existing containers: callers may hold references to them.
            new_keys = [key for key in pending_memory if key not in self._answer_map]
            self._answer_map.update(pending_memory)
            self._answer_keys.extend(new_keys)
            logger.info(
                f"📥 GAS 題庫同步完成：新增 {added} 題，更新 {updated} 題"
                f"，記憶體共 {len(self._answer_map)} 題"
            )
        except Exception as e:
            logger.warning(f"📥 GAS 題庫同步失敗（不影響本機題庫）: {e}")
        finally:
            if conn is not None:
                conn.close()

    def _accept_alert(self):
        """若有 alert/confirm 對話框則點確定，無則跳過"""
        try:
            WebDriverWait(self.driver, 3).until(EC.alert_is_present())
            self.driver.switch_to.alert.accept()
            return True
        except Exception:
            return False

    def _harvest_correct_answers(self, view_result_url: str) -> dict:
        """Read an already disclosed key; never request disclosure or end retries."""
        try:
            if "view_result" not in self.driver.current_url:
                return {}
            data = self.driver.execute_script("""
                if (typeof isReadAnswer === 'undefined' || String(isReadAnswer) !== '1') return [];
                return Array.from(document.querySelectorAll('tr.bg03.font01, tr.bg04.font01')).map(row => {
                    const p = row.querySelector('p');
                    const spans = Array.from(row.querySelectorAll('span')).filter(span =>
                        ['green', 'rgb(0, 128, 0)'].includes(span.style.backgroundColor) && span.querySelector('input'));
                    const controls = Array.from(row.querySelectorAll('input[type="radio"], input[type="checkbox"]'));
                    const optionText = control => {
                        const value = control.value.toUpperCase();
                        if (value === 'T') return '正確';
                        if (value === 'F') return '錯誤';
                        const li = control.closest('li');
                        return li ? li.innerText.trim() : '';
                    };
                    return {question: p ? p.innerText.trim() : '',
                        options: controls.map(optionText),
                        type: controls.some(c => c.type === 'checkbox') ? '多選' :
                            (controls.length === 2 && controls.every(c => ['T', 'F'].includes(c.value.toUpperCase())) ? '是非' : '單選'),
                        answers: spans.map(span => {
                        const value = span.querySelector('input').value.toUpperCase();
                        if (value === 'T') return '正確';
                        if (value === 'F') return '錯誤';
                        return span.innerText.trim();
                    })};
                });
            """) or []
            from utils.answer_evidence import text
            result = {}
            ambiguous = set()
            for item in data:
                key = text(re.sub(r"^[\d０-９]+[.．、。）)\s]+", "", item.get("question", "")))
                record = {field: item.get(field) for field in ("answers", "options", "type")}
                if key and record["answers"]:
                    if key in result and result[key] != record:
                        ambiguous.add(key)
                    result[key] = record
            return {key: value for key, value in result.items() if key not in ambiguous}
        except Exception as error:
            logger.debug(f"   已公布答案讀取失敗（{type(error).__name__}）")
            return {}

    def _flush_answer_evidence(self, evidence):
        """Use the per-machine evidence key, outside distributable config and source."""
        try:
            from pathlib import Path
            import threading
            key_path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AutoLearningBot" / "ecpa_evidence.key"
            if not key_path.exists():
                return
            key = key_path.read_text(encoding="utf-8").strip()
            if not re.fullmatch(r"[a-f0-9]{64}", key):
                return
            url = self.config.get("gas_url", self._GAS_DB_URL)
            if not url:
                return
            def send():
                try:
                    count = evidence.flush(url, key, requests.post)
                    if count:
                        logger.info(f"   ☁️ 雲端已接受 {count} 筆平台正解證據")
                except Exception as error:
                    logger.debug(f"   正解同步保留待重試（{type(error).__name__}）")
            threading.Thread(target=send, daemon=False, name="ECPA-Evidence-Sync").start()
        except Exception as error:
            logger.debug(f"   正解同步尚未啟用（{type(error).__name__}）")

    def _report_missing_questions(self, course, missing, reason="missing"):
        """Report missing or failed-exam questions through the existing GAS contract."""
        _missing = missing
        if _missing:
            # 去重（同一次考試同一題可能出現多次）
            _seen = set()
            _missing_dedup = []
            for _m in _missing:
                _key = _m.get("question", "")
                if _key not in _seen:
                    _seen.add(_key)
                    _missing_dedup.append(_m)

            _GAS_URL = self.config.get(
                "gas_url",
                "https://script.google.com/macros/s/AKfycbzYUNM--zLlS8El6YR6lIiKerBIz1M6rL2gM8nTGicmEjfh_1TNiBo12YcVsb37J7Cl/exec"
            )
            _course_name = course.get("caption", "未知課程")
            _username = (
                self.config.get("name")
                or self.config.get("account")
                or "匿名"
            )
            if not _GAS_URL:
                return None

            # Reserve before starting the worker: retries and concurrent accounts
            # share receipts, without storing account data or touching questions.db.
            from utils.question_report_cache import QuestionReportCache
            receipt_cache = None
            receipt_owner = None
            try:
                receipt_cache = QuestionReportCache()
                receipt_owner, pending = receipt_cache.reserve(_GAS_URL + "#" + reason, _course_name, _missing_dedup)
                skipped = len(_missing_dedup) - len(pending)
                _missing_dedup = pending
                if skipped:
                    logger.debug(f"   缺題回報略過 {skipped} 題近期已收件或處理中的題目")
                if not pending:
                    return None
            except Exception as error:
                receipt_cache = None
                logger.warning(f"   缺題回報去重紀錄暫時不可用（{type(error).__name__}），仍繼續回報")

            _payload = {
                "course": _course_name,
                "username": _username,
                "missing": [{key: item.get(key) for key in ("question", "options", "type")} for item in _missing_dedup],
                "reason": reason,
            }

            import threading as _threading, requests as _req

            def _post_gas(url, payload):
                accepted = False
                try:
                    response = _req.post(url, json=payload, timeout=20)
                    response.raise_for_status()
                    acknowledgement = response.json()
                    if not isinstance(acknowledgement, dict) or not (
                        acknowledgement.get("ok") is True or acknowledgement.get("status") == "ok"
                    ):
                        raise ValueError("GAS 未確認接受缺題回報")
                    accepted = True
                    logger.info(
                        f"   📨 GAS 已接受 {len(payload['missing'])} 題缺題"
                        f"（正式新增 {acknowledgement.get('added', 0)} 題；待查 {acknowledgement.get('pending', 0)} 題）"
                    )
                except Exception as _e:
                    logger.warning(f"   ⚠️ 缺題回報失敗（{type(_e).__name__}），尚未確認送達 GAS")

                finally:
                    if receipt_cache is not None:
                        try:
                            receipt_cache.finish(receipt_owner, accepted)
                        except Exception as error:
                            logger.warning(f"   缺題回報收件紀錄寫入失敗（{type(error).__name__}）")

            report_thread = _threading.Thread(
                target=_post_gas, args=(_GAS_URL, _payload), daemon=False,
                name="ECPA-Missing-Report",
            )
            report_thread.start()
            logger.info(f"   📨 缺題回報排入背景處理（{len(_missing_dedup)} 題），等待 GAS 確認")
            return report_thread


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
        # AI candidates remain separate from confirmed answers.
        _ai_answered = {}
        # Whole-exam failure is not evidence that every existing answer is wrong.
        force_ai = False
        evidence = None
        attempt_id = None
        observed_rows = []
        cloud_candidates_loaded = False
        try:
            from utils.answer_evidence import AnswerEvidence
            evidence = AnswerEvidence()
            self._flush_answer_evidence(evidence)
        except Exception as error:
            logger.warning(f"   作答證據紀錄暫時不可用（{type(error).__name__}）")
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
            _missing = []  # 題庫無答案的題目，考試後回報 GAS
            _exam_questions = []

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

                    # Image-only true/false choices have no text. Read the actual
                    # form values rather than guessing from the number of choices.
                    radio_values = [(r.get_attribute("value") or "").upper() for r in radios]
                    is_true_false = len(radios) == 2 and set(radio_values) == {"T", "F"}
                    if is_true_false:
                        option_texts = ["正確" if value == "T" else "錯誤" for value in radio_values]

                    question_payload = {
                        "type": "多選" if checkboxes else ("是非" if is_true_false else "單選"),
                        "question": q_text,
                        "options": option_texts,
                    }
                    _exam_questions.append(question_payload)
                    answer_source = "legacy_db" if ans is not None else "guess"
                    stored_answer = None
                    if evidence:
                        try:
                            stored_answer = evidence.lookup(course_id, question_payload, verified_only=True)
                            if ans is None and not stored_answer:
                                if not cloud_candidates_loaded:
                                    cloud_candidates_loaded = True
                                    # One bounded read per exam, only when the bank lacks an answer.
                                    import hashlib
                                    digest = hashlib.sha256(str(course.get("caption", "未知課程")).encode("utf-8")).hexdigest()
                                    try:
                                        response = requests.get(
                                            "https://raw.githubusercontent.com/waynelord0628-beep/auto-learning-bot/main/review/ecpa/" + digest + ".json",
                                            timeout=5,
                                        )
                                        if response.status_code == 200:
                                            evidence.import_cloud(course_id, response.json())
                                    except Exception:
                                        logger.debug("   雲端候選暫時不可用，繼續本機補答")
                                stored_answer = evidence.lookup(course_id, question_payload)
                            if stored_answer:
                                # Bypass legacy fuzzy/positional answer mapping below.
                                answer_source = stored_answer["source"]
                        except Exception as error:
                            logger.debug(f"   候選查詢略過（{type(error).__name__}）")
                    observed_rows.append((question_payload, row))
                    question_payload["source"] = answer_source
                    if ans is None:
                        _missing.append(question_payload)  # AI補答後仍需補進共用題庫。

                    # 題庫找不到時，或不及格重試強制用 AI 補答
                    if stored_answer:
                        from utils.answer_evidence import text
                        answers_to_select = set(stored_answer["answers"])
                        inputs = checkboxes or radios
                        if len(inputs) == len(option_texts):
                            for option, control in zip(option_texts, inputs):
                                wanted = text(option) in answers_to_select
                                if control.is_selected() != wanted and (checkboxes or wanted):
                                    self.driver.execute_script("arguments[0].click();", control)
                            answered += 1
                            continue
                        stored_answer = None
                        question_payload["source"] = "legacy_db" if ans is not None else "guess"
                    if ans is None or force_ai:
                        radios_count = len(row.find_elements(By.CSS_SELECTOR, "input[type='radio']"))
                        ai_options = option_texts if any(option_texts) else (
                            ["正確（是）", "錯誤（否）"] if radios_count == 2 else []
                        )
                        if ai_options:
                            ai_ans = self._ai_find_answer(q_text, ai_options)
                            if ai_ans:
                                ans = ai_ans
                                # Keep the option text, never a position that can change.
                                _ai_answered[q_text] = ai_options[int(ai_ans) - 1]
                                question_payload["source"] = "local_ai_unverified"
                                if evidence:
                                    try:
                                        evidence.candidate(course_id, question_payload, [_ai_answered[q_text]])
                                    except Exception:
                                        logger.debug("   AI 候選暫存失敗，保留本次作答")

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
                            ans_lower = ans_norm.lower()

                            # AI 新格式會只回 1/2/3/4；題庫也可能存 A/B/C/D 或「2. 答案」。
                            m = re.fullmatch(r"\s*(\d+)(?:[.、)）:：]\s*[^\d\s].*)?\s*", ans_norm)
                            if m:
                                n = int(m.group(1))
                                if 1 <= n <= len(radios):
                                    idx = n - 1

                            if idx is None:
                                letter_map = {chr(ord("a") + i): i for i in range(len(radios))}
                                token = re.sub(r"[^a-zA-Z]", "", ans_norm).lower()
                                if token in letter_map:
                                    idx = letter_map[token]

                            if idx is None and is_true_false:
                                truth_tokens = {"正確": "T", "對": "T", "是": "T", "TRUE": "T", "T": "T", "O": "T",
                                                "不正確": "F", "錯誤": "F", "錯": "F", "否": "F", "FALSE": "F", "F": "F", "X": "F"}
                                truth_value = truth_tokens.get(ans_norm.upper())
                                if truth_value is not None:
                                    idx = radio_values.index(truth_value)

                            if idx is None and len(radios) == 2:
                                ans_upper = ans_norm.upper()
                                if ans_upper in ("O", "T", "TRUE", "A"):
                                    idx = 0
                                elif ans_upper in ("X", "F", "FALSE", "B"):
                                    idx = 1
                                else:
                                    true_words = ["對", "是", "正確", "true"]
                                    false_words = ["錯", "否", "不正確", "錯誤", "false", "非"]
                                    if ans_lower in false_words:
                                        idx = 1
                                    elif ans_lower in true_words:
                                        idx = 0

                            if idx is None and option_texts:
                                def _choice_key(value):
                                    value = unicodedata.normalize("NFKC", str(value or "")).lower()
                                    # 去除選項前綴與所有標點空白，只保留可比對的中英數。
                                    value = re.sub(r"^[\s\(\[]*[a-zA-Z0-9一二三四五六七八九十]+[\s\)\]\.、:：-]+", "", value)
                                    return "".join(ch for ch in value if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")

                                ans_key = _choice_key(ans_norm)
                                ans_compact = re.sub(r"\s+", "", ans_norm)
                                for i, opt_text in enumerate(option_texts[: len(radios)]):
                                    opt_clean = str(opt_text).strip()
                                    opt_key = _choice_key(opt_clean)
                                    opt_compact = re.sub(r"\s+", "", opt_clean)
                                    if (
                                        ans_compact
                                        and opt_compact
                                        and (ans_compact in opt_compact or opt_compact in ans_compact)
                                    ) or (
                                        ans_key
                                        and opt_key
                                        and (ans_key in opt_key or opt_key in ans_key)
                                    ):
                                        idx = i
                                        break

                            if idx is None and len(radios) == 2:
                                idx = 0
                                logger.debug(
                                    f"   ⚠️ 是非題答案無法比對，預設選第一個：{q_text[:30]!r} ans={ans_norm!r}"
                                )
                        else:
                            if len(radios) == 2:
                                idx = 0
                                logger.info(
                                    f"   🎲 是非題無答案，預設猜正確：{q_text[:30]!r}"
                                )
                                _missing.append({"type": "是非", "question": q_text, "options": option_texts})
                            else:
                                idx = random.randrange(len(radios))
                                logger.info(
                                    f"   🎲 單選題隨機作答（無資料，稍後回報）：{q_text[:30]!r}"
                                )
                                _missing.append({"type": "單選", "question": q_text, "options": option_texts})

                        if idx is None and ans is not None and len(radios) > 2:
                            ans_compact = _normalize_q(ans_norm)
                            for i, opt_text in enumerate(option_texts):
                                opt_clean = (opt_text or "").strip()
                                opt_compact = _normalize_q(opt_clean)
                                if ans_norm and opt_clean and (
                                    ans_norm in opt_clean
                                    or opt_clean in ans_norm
                                    or (
                                        ans_compact
                                        and opt_compact
                                        and (
                                            ans_compact in opt_compact
                                            or opt_compact in ans_compact
                                        )
                                    )
                                ):
                                    idx = i
                                    break

                        if idx is not None and idx < len(radios):
                            self.driver.execute_script(
                                "arguments[0].click();", radios[idx]
                            )
                            answered += 1
                        else:
                            logger.debug(
                                f"   ⚠️ 單選答案無法比對，略過：{q_text[:30]!r} ans={ans!r} options={option_texts!r}"
                            )
                            skipped += 1

                except Exception as e:
                    logger.debug(f"   ⚠️ 作答某題時發生錯誤: {e}")
                    skipped += 1

            logger.info(f"   📝 作答完成：{answered} 題已答，{skipped} 題略過")

            self._report_missing_questions(course, _missing)

            if evidence:
                try:
                    for question, observed_row in observed_rows:
                        controls = observed_row.find_elements(By.CSS_SELECTOR, "input[type='radio'], input[type='checkbox']")
                        question["selection_observed"] = len(controls) == len(question["options"])
                        question["selected"] = [question["options"][i] for i, control in enumerate(controls)
                                                if i < len(question["options"]) and control.is_selected()]
                    attempt_id = evidence.prepare(course_id, _exam_questions)
                    logger.info("   🧾 已記錄送出前實際選項；等待平台結果確認")
                except Exception as error:
                    logger.warning(f"   作答證據記錄失敗（{type(error).__name__}），不影響原作答流程")

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
                    initially_missing = {item["question"] for item in _missing}
                    self._report_missing_questions(
                        course, [item for item in _exam_questions if item["question"] not in initially_missing], reason="exam_failed"
                    )
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
                    # Passing the exam does not validate every generated answer.
                    if _ai_answered:
                        logger.info(f"   ℹ️ {len(_ai_answered)} 題 AI 答案尚無逐題正誤確認，不寫入正式題庫")
                else:
                    logger.info("   📝 無法判斷成績，請自行確認")
            except Exception:
                pass

            # Record observed outcomes without requesting answer disclosure.
            if evidence and attempt_id:
                try:
                    disclosed = self._harvest_correct_answers(self.driver.current_url)
                    outcome = evidence.finish(attempt_id, body_text, disclosed)
                    evidence.queue_confirmed(attempt_id, course.get("caption", "未知課程"))
                    self._flush_answer_evidence(evidence)
                    logger.info(f"   🧾 作答结果已記錄；逐題確認 {outcome['confirmed']} 題，其餘保持原狀")
                except Exception as error:
                    logger.warning(f"   結果證據記錄失敗（{type(error).__name__}）")

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

    def _get_driver_path(self):
        """取得 ChromeDriver 路徑，子類可覆寫此方法實作不同策略。"""
        return os.path.abspath(download_best_chromedriver())

    def init_engine(self):
        self.kill_orphan_drivers()
        try:
            driver_path = self._get_driver_path()
            if not os.path.exists(driver_path):
                logger.error(f"找不到驅動程式檔案: {driver_path}")
                return False

            logger.info(f"🚀 正在啟動輔助引擎...")
            options = Options()
            options.add_argument("--mute-audio")
            # 加速啟動
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            if self.config.get("disable_gpu", False):
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
                options.add_argument("--headless=new")
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
                pid = self._driver_service.process.pid
                self._managed_process_times[pid] = psutil.Process(pid).create_time()
                self._managed_pids.add(pid)

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

    @staticmethod
    def _is_logout_text(text) -> bool:
        text = str(text or "")
        return any(kw in text for kw in ("閒置", "重新登入", "登出", "登入後再學習"))

    def _accept_alert_if_present(self) -> str:
        try:
            alert = self.driver.switch_to.alert
            alert_text = alert.text
            alert.accept()
            return alert_text or ""
        except NoAlertPresentException:
            return ""
        except Exception as e:
            return str(e)

    def fetch_course_list(self, year=None):
        year = year or time.strftime("%Y")
        courses = []
        for page in range(1, 20):
            payload = f"year={year}&keyword=&course_type=single&page={page}&orderby=&sort="
            resp = self.http_session.post(
                self.api_url, data=payload, verify=False, timeout=10
            )
            data = resp.json().get("data", [])
            courses.extend(data)
            if len(data) < 50:
                break
        return courses

    def fetch_course_list_checked(self, year=None):
        courses = self.fetch_course_list(year)
        count = len(courses)
        should_retry = 0 < count < 5
        if self._last_course_count and 0 < count < self._last_course_count:
            should_retry = True

        if should_retry:
            logger.warning(
                f"⚠️ 課程 API 只回 {count} 筆"
                + (
                    f"（前次 {self._last_course_count} 筆）"
                    if self._last_course_count
                    else ""
                )
                + "，先同步 session 後重抓。"
            )
            try:
                self.sync_session()
            except Exception:
                pass
            time.sleep(2)
            try:
                retry_courses = self.fetch_course_list(year)
            except Exception:
                retry_courses = courses
            if len(retry_courses) > count:
                logger.info(f"✅ 課程 API 重抓成功：{count} → {len(retry_courses)} 筆")
                courses = retry_courses
                count = len(courses)

        if count > self._last_course_count:
            self._last_course_count = count
        return courses

    def _is_open_course(self, course):
        course_type = str(course.get("course_type", "") or "").strip()
        if course_type:
            return course_type == "開放式"
        course_type_cd = str(course.get("course_type_cd", "") or "").strip().lower()
        if course_type_cd:
            return course_type_cd in {"single", "open"}
        return True

    def recover_login_session(self, reason="session 失效") -> bool:
        logger.warning(f"🔄 {reason}，嘗試重新登入並同步 API session...")
        try:
            self._accept_alert_if_present()
        except Exception:
            pass
        try:
            self.driver.get(self.stat_url)
            self.safe_sleep(2)
        except Exception:
            pass
        if not self.login():
            logger.error("❌ 重新登入失敗，無法恢復 API session。")
            return False
        if not self.sync_session():
            logger.error("❌ 重新登入後 session 同步失敗。")
            return False
        logger.info("✅ 重新登入並同步 session 完成。")
        return True

    def find_classroom_window(self):
        """Return the browser window that owns the course frame tree."""
        if not self.driver:
            return None
        try:
            handles = list(self.driver.window_handles)
        except Exception:
            return None

        for handle in reversed(handles):
            try:
                self.driver.switch_to.window(handle)
                self.driver.switch_to.default_content()
                self.driver.switch_to.frame("s_catalog")
                self.driver.switch_to.frame("pathtree")
                self.driver.switch_to.default_content()
                return handle
            except Exception:
                try:
                    self.driver.switch_to.default_content()
                except Exception:
                    pass
                continue
        return None

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
        if not self._is_open_course(course):
            logger.info(
                f"⏭️ 略過非開放式課程：{course.get('caption', course.get('course_id', '未知課程'))}"
            )
            return "SKIP"

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

            classroom_h = self.find_classroom_window()
            if not classroom_h:
                logger.warning("   ⚠️ 找不到課程教室主視窗（s_catalog/pathtree），嘗試重新登入後重試。")
                return "RELOGIN"
            self.driver.switch_to.window(classroom_h)

            attempted = set()
            active_unit = None
            playback_start_checks = 0
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

                try:
                    self.driver.switch_to.window(classroom_h)
                    self.driver.switch_to.default_content()
                except Exception:
                    new_classroom_h = self.find_classroom_window()
                    if new_classroom_h:
                        classroom_h = new_classroom_h
                        self.driver.switch_to.window(classroom_h)
                        self.driver.switch_to.default_content()

                try:
                    keep_current_video = bool(active_unit) and (
                        start_unstarted_video(self.driver, inspect_only=True) == "playing"
                    )
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
                    target = None
                    if keep_current_video:
                        target = next((link for link in links if
                            (link.text.strip(), link.get_attribute("href") or "") == active_unit), None)
                    if target is None:
                        target = next(
                            (link for link in links if link.text not in attempted),
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
                        unit_key = (u_name, target.get_attribute("href") or "")
                        if unit_key != active_unit:
                            logger.info(f"   📍 進入單元：{u_name[:20]}...")
                            self.driver.execute_script("arguments[0].click();", target)
                            active_unit = unit_key
                            playback_start_checks = 3
                        else:
                            logger.debug(f"   ▶ 維持目前單元：{u_name[:20]}，避免重載播放器")

                        w_time = self.config.get("residence_time", 75)
                        def commit_progress():
                            nonlocal playback_start_checks
                            self.driver.switch_to.window(classroom_h)
                            if playback_start_checks > 0:
                                playback_start_checks -= 1
                                state = start_unstarted_video(
                                    self.driver, muted=self.config.get("headless", True)
                                )
                                if state == "playing":
                                    playback_start_checks = 0
                                elif state == "requested":
                                    logger.info("   ▶ 已向播放器請求正常播放")
                            self.driver.execute_script(
                                "function deepCommit(win){ try{if(win.API)win.API.LMSCommit('');}catch(e){} if(win.frames){for(let i=0;i<win.frames.length;i++)deepCommit(win.frames[i]);}} deepCommit(window);"
                            )

                        if not wait_with_heartbeat(
                            w_time, lambda: self.running, commit_progress,
                            interval=self.config.get("playback_poll_interval", 5),
                        ):
                            logger.info("🛑 使用者手動停止（停留中）")
                            return "STOP"
                    else:
                        for _ in range(30):
                            # ⭐ 檢查點 9（無目標課程時）
                            if not self.running:
                                logger.info("🛑 使用者手動停止（無課程可選）")
                                return "STOP"
                            time.sleep(1)
                except Exception as e:
                    active_unit = None  # Recovery must reopen the unit when page state is uncertain.
                    # 優先攔截殘留 alert（如閒置登出），避免後續操作全部失敗
                    alert_text = self._accept_alert_if_present()
                    err_text = f"{alert_text} {e}"
                    if alert_text:
                        logger.warning(f"   ⚠️ frame 切換時偵測到 Alert：{alert_text}")
                    if self._is_logout_text(err_text):
                        logger.warning("🔄 帳號閒置被登出，停止當前教室並觸發重新登入。")
                        return "RELOGIN"
                    new_classroom_h = self.find_classroom_window()
                    if new_classroom_h and new_classroom_h != classroom_h:
                        logger.warning("   🔄 目前視窗不是教室主視窗，已切回含課程選單的教室視窗。")
                        classroom_h = new_classroom_h
                        frame_fail_count = 0
                        continue
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
            if resp.status_code != 200:
                logger.debug(f"版本檢查失敗（HTTP {resp.status_code}）")
                return
            latest = resp.text.strip()
            if not latest or not latest.startswith("V"):
                logger.debug(f"版本檢查失敗（回應格式不符：{latest!r}）")
                return
            if _is_newer_version(latest, self.version):
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
        if self.config.get("login_type") == "taipei_eda":
            self._start_keep_awake()
            try:
                logger.info("🏫 啟動臺北E大平台流程...")
                from taipei_eda_course import run_taipei_eda

                ok = run_taipei_eda(
                    config_override=self.config,
                    should_continue=lambda: self.running,
                    log_callback=self.log_callback,
                    owner=self,
                )
                if ok:
                    logger.info("🏆 臺北E大所有任務完成！")
                else:
                    logger.warning("⚠️ 臺北E大流程未完整完成，請查看 taipei_eda_course.log")
            except ImportError as e:
                logger.error(f"❌ 臺北E大模組載入失敗，請確認依賴已安裝: {e}")
            except Exception as e:
                logger.error(f"⚠️ 臺北E大流程發生錯誤: {e}")
                logger.debug(traceback.format_exc())
            finally:
                self._cleanup()
            return

        self._start_keep_awake()
        print(
            f"\n{Fore.CYAN}{'=' * 60}\n【行政效能領航員 - 數位研習輔助方案 {self.version}】\n{'=' * 60}{Style.RESET_ALL}"
        )
        # AI API 狀態提示
        provider = self.config.get("ai_provider", "OpenAI")
        ai_keys = self.config.get("ai_keys", {})
        ai_key = ai_keys.get(provider) or self.config.get("ai_api_key", "")
        if ai_key:
            base_url = self.config.get("ai_base_url", "https://api.openai.com/v1")
            model = self.config.get("ai_model", "gpt-4o-mini")
            logger.info(f"🤖 AI 補答已啟用（model: {model}，endpoint: {base_url}）")
        else:
            logger.info("📖 AI 補答未啟用，僅使用本地題庫作答")
        try:
            if not self.init_engine():
                if not self.log_callback and sys.stdin and sys.stdin.isatty():
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
                if not self.log_callback and sys.stdin and sys.stdin.isatty():
                    input(f"\n{Fore.RED}{msg} 按 Enter 退出...{Style.RESET_ALL}")
                return

            empty_api_count = 0
            all_tasks_done = False

            while self.running:
                try:
                    cur_y = time.strftime("%Y")
                    try:
                        courses = self.fetch_course_list_checked(cur_y)
                    except Exception as e:
                        logger.error(f"無法讀取列表，重試中... ({e})")
                        alert_text = self._accept_alert_if_present()
                        if self._is_logout_text(f"{alert_text} {e}"):
                            if self.recover_login_session("API 查詢時偵測到登出"):
                                continue
                            break
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

                    # API 回 0 筆通常是被登出或 cookie 失效，不可無限等待。
                    if len(courses) == 0:
                        empty_api_count += 1
                        logger.warning("⚠️ API 回傳 0 筆，先重新同步 session 後重查...")
                        self.sync_session()
                        time.sleep(3)
                        try:
                            courses = self.fetch_course_list(cur_y)
                        except Exception as e:
                            logger.error(f"重查失敗: {e}")
                            courses = []
                        logger.info(f"📋 重查後課程總數：{len(courses)} 筆")

                        if len(courses) == 0:
                            if not self.recover_login_session("API 連續回傳 0 筆，判定 session 可能已失效"):
                                break
                            try:
                                courses = self.fetch_course_list(cur_y)
                            except Exception as e:
                                logger.error(f"重新登入後重查失敗: {e}")
                                courses = []
                            logger.info(f"📋 重新登入後課程總數：{len(courses)} 筆")

                        if len(courses) == 0:
                            if empty_api_count >= 3:
                                logger.warning("🚀 API 連續 0 筆無法恢復，重啟輔助引擎後再試。")
                                self._cleanup(stop=False)
                                if not self.safe_sleep(5):
                                    break
                                if not self.init_engine() or not self.login():
                                    logger.error("❌ 引擎重啟或登入失敗，無法繼續。")
                                    break
                                empty_api_count = 0
                            else:
                                for _ in range(10):
                                    if not self.running:
                                        break
                                    time.sleep(1)
                            continue

                    empty_api_count = 0

                    pending = [
                        c
                        for c in courses
                        if self._is_open_course(c)
                        and to_sec(c.get("rss", "00:00:00"))
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
                        if not self._is_open_course(c):
                            return False
                        # 本次已成功處理過，跳過
                        if c_id in self._completed_in_session:
                            return False
                        hours_done = to_sec(c.get("rss", "00:00:00")) >= to_sec(
                            c.get("criteria_content_hour", "00:00:00")
                        ) * self.config.get("target_percentage", 1.0)
                        if not hours_done:
                            return False
                        # 有考試且未通過（exam_score 為 null/None 且 exam_exists=="1"）
                        # 或曾不及格但未達上限（exam_score 可能已非 None，仍需重試）
                        needs_exam = c.get("exam_exists") == "1" and (
                            c.get("exam_score") is None
                            or (
                                self._exam_fail_counts.get(c_id, 0) > 0
                                and self._exam_fail_counts.get(c_id, 0) < 3
                            )
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
                        all_tasks_done = True
                        break

                    # ── 第一步：先對時數已達標但考試/問卷未完成的課程執行 ──
                    # （初始使用者全部 pending 時，completed_hours 為空，此段直接跳過）
                    all_exam_done = True
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
                                    # 還有重試機會，立刻跳回迴圈頂部繼續重考，不去上課
                                    break
                                else:
                                    # 已達上限，本次不再重試，加入已處理集合
                                    self._completed_in_session.add(c_id)

                        if not self.running:
                            break

                        # 若全部達標課程都處理完，且無 pending，則全部完成
                        if all_exam_done and not pending:
                            break

                    # ── 第二步：處理時數未達標的課程（上課）──
                    # 若有考試還在重試中（all_exam_done=False），優先重考，不去上課
                    if pending and all_exam_done:
                        self.total_courses = len(pending) + (self.current_idx)
                        self.current_idx += 1
                        res = self.study_process(pending[0])

                        if res == "STOP":
                            logger.info("🛑 使用者已停止程式")
                            break

                        if res == "RELOGIN":
                            # 閒置登出後已重新登入，重試當前課程（退回 index）
                            logger.info("🔄 閒置登出重新登入成功，重試當前課程...")
                            self.sync_session()  # 確保 http_session 用最新 cookie
                            self.current_idx -= 1
                            continue

                        if res == "STALLED":
                            logger.warning("🚀 偵測到停滯，正在重新啟動輔助引擎...")
                            self._cleanup(stop=False)
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
                        self._cleanup(stop=False)
                        if not self.safe_sleep(5):
                            break
                        if not self.init_engine() or not self.login():
                            logger.error("❌ 引擎重啟或登入失敗，無法繼續。")
                            break
                        logger.info("✅ 引擎重建成功，從當前課程繼續...")
                    else:
                        self.safe_sleep(10)

            if all_tasks_done:
                logger.info(f"🏆 {Fore.GREEN}所有任務圓滿達成！{Style.RESET_ALL}")
            else:
                logger.warning("研習流程已中止，尚未確認所有任務完成。")
            if not self.log_callback and sys.stdin and sys.stdin.isatty():
                input(f"\n{Fore.GREEN}✓ 程式執行完畢，按 Enter 關閉。{Style.RESET_ALL}")

        except KeyboardInterrupt:
            print(
                f"\n{Fore.YELLOW}⚠️ 使用者中斷（Ctrl+C），正在安全退出...{Style.RESET_ALL}"
            )

        except Exception as e:
            logger.critical(f"🔥 程式發生致命錯誤: {e}")
            if not self.log_callback and sys.stdin and sys.stdin.isatty():
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
