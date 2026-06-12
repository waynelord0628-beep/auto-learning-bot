"""Anonymous usage ping for AdminEfficiencyPilot.

Only sends a random device id, version, platform and current screen/login type.
It deliberately does not send account, password, display name, course name or logs.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import threading
import time
import uuid
from pathlib import Path

import requests


GAS_URL = "https://script.google.com/macros/s/AKfycbzYUNM--zLlS8El6YR6lIiKerBIz1M6rL2gM8nTGicmEjfh_1TNiBo12YcVsb37J7Cl/exec"
HEARTBEAT_SECONDS = 60


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _config_path() -> Path:
    return _base_dir() / "config.json"


def get_device_id() -> str:
    path = _config_path()
    data = {}
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}

    device_id = str(data.get("usage_device_id") or "").strip()
    if device_id:
        return device_id

    device_id = uuid.uuid4().hex
    try:
        data["usage_device_id"] = device_id
        path.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")
    except Exception:
        pass
    return device_id


def _post(payload: dict) -> dict | None:
    try:
        resp = requests.post(GAS_URL, json=payload, timeout=6)
        if resp.status_code == 200:
            try:
                return resp.json()
            except Exception:
                return None
    except Exception:
        return None
    return None


def ping(version: str, screen: str = "entry", login_type: str = "") -> dict | None:
    payload = {
        "action": "usage_ping",
        "device_id": get_device_id(),
        "version": version,
        "screen": screen,
        "login_type": login_type or "",
        "platform": platform.system(),
        "platform_release": platform.release(),
        "ts": int(time.time()),
    }
    return _post(payload)


def fetch_stats(version: str = "") -> dict | None:
    payload = {
        "action": "usage_stats",
        "device_id": get_device_id(),
        "version": version,
        "ts": int(time.time()),
    }
    return _post(payload)


class UsageHeartbeat:
    def __init__(self, version: str, callback=None):
        self.version = version
        self.callback = callback
        self.running = False
        self.screen = "entry"
        self.login_type = ""
        self._thread = None

    def update_context(self, screen: str = "", login_type: str = "") -> None:
        if screen:
            self.screen = screen
        if login_type:
            self.login_type = login_type

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False

    def _loop(self) -> None:
        while self.running:
            stats = ping(self.version, self.screen, self.login_type)
            if isinstance(stats, dict) and self.callback:
                try:
                    self.callback(stats)
                except Exception:
                    pass
            for _ in range(HEARTBEAT_SECONDS):
                if not self.running:
                    break
                time.sleep(1)
