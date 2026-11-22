# -*- coding: utf-8 -*-
"""为 amo_grounding_critic 用审计 pin 作为改动前快照，然后重算 diff。"""
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIN = os.path.join(ROOT, "work", "audit", "_scratch", "repo_pin", "amo_grounding_critic.py")
BEFORE = os.path.join(ROOT, "work", "task29", "before")
with io.open(PIN, encoding="utf-8") as fh:
    content = fh.read()
with io.open(os.path.join(BEFORE, "amo_grounding_critic.py"), "w", encoding="utf-8") as fh:
    fh.write(content)
print("copied pin baseline:", len(content), "bytes")
