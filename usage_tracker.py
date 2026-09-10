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
from utils.config_store import atomic_write_json


GAS_URL = "https://script.google.com/macros/s/AKfycbzYUNM--zLlS8El6YR6lIiKerBIz1M6rL2gM8nTGicmEjfh_1TNiBo12YcVsb37J7Cl/exec"
HEARTBEAT_SECONDS = 60


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _config_path() -> Path:
    return _base_dir() / "config.json"


_device_id_lock = threading.Lock()
_device_id = None


def get_device_id() -> str:
    global _device_id
    with _device_id_lock:
        if _device_id:
            return _device_id
        state_path = _base_dir() / "usage_state.json"
        # Read the legacy ID for continuity, but never write account configuration.
        for path in (state_path, _config_path()):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                value = data.get("usage_device_id") if isinstance(data, dict) else None
                if isinstance(value, str) and value.strip():
                    _device_id = value.strip()
                    break
            except (OSError, ValueError):
                continue
        if not _device_id:
            _device_id = uuid.uuid4().hex
        try:
            atomic_write_json(state_path, {"usage_device_id": _device_id})
        except OSError:
            pass  # Keep a stable in-memory ID when the directory is read-only.
        return _device_id


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
        self._lifecycle_lock = threading.Lock()
        self._stop_event = threading.Event()

    def update_context(self, screen: str = "", login_type: str = "") -> None:
        if screen:
            self.screen = screen
        if login_type:
            self.login_type = login_type

    def start(self) -> None:
        with self._lifecycle_lock:
            if self.running:
                return
            self.running = True
            # Each generation owns its cancellation event; restarting must not
            # revive a previous worker that is still waiting for HTTP to finish.
            self._stop_event = threading.Event()
            self._thread = threading.Thread(
                target=self._loop, args=(self._stop_event,), daemon=True,
                name="Usage-Heartbeat",
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            self.running = False
            self._stop_event.set()

    def _loop(self, stop_event) -> None:
        while not stop_event.is_set():
            stats = ping(self.version, self.screen, self.login_type)
            if stop_event.is_set():
                break
            if isinstance(stats, dict) and self.callback:
                try:
                    self.callback(stats)
                except Exception:
                    pass
            if stop_event.wait(HEARTBEAT_SECONDS):
                break
