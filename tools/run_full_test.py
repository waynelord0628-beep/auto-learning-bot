# -*- coding: utf-8 -*-
"""
Full integration run test - patches out input() calls so it doesn't block.
Logs everything to debug.log and run_full_test.log
"""

import json, time, sys, builtins

# Patch input() so it doesn't block
_original_input = builtins.input


def _no_input(prompt=""):
    try:
        safe = prompt.encode("ascii", errors="replace").decode("ascii")
    except Exception:
        safe = repr(prompt)
    print(f"[AUTO-SKIP input] {safe}")
    return ""


builtins.input = _no_input

from app import AdminEfficiencyPilot
from utils.helpers import to_sec

with open("config.json", encoding="utf-8") as f:
    cfg = json.load(f)
acc = cfg["accounts"][0]
settings = cfg.get("settings", {})
full_config = acc.copy()
full_config.update(settings)
full_config["headless"] = True  # must be headless for unattended run

pilot = AdminEfficiencyPilot(config_override=full_config)
pilot.run()
