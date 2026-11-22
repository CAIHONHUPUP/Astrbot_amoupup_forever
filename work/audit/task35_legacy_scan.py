# -*- coding: utf-8 -*-
import os, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
for _ in range(2):
    ROOT = os.path.dirname(ROOT)
LEGACY = ("astrbot_plugin_omnidraw","astrbot_plugin_Astrbot_amoupup_forever",
          "astrbot_plugin_Astrbot_Amo_forever","astrbot_plugin_amo_forever",
          "Astr_Amo_data","astrbot_plugin_amo_data","AstrAmoData")
hits = []
for path in __import__("pathlib").Path(ROOT).rglob("*.py"):
    if "__pycache__" in path.parts:
        continue
    rel = path.relative_to(ROOT).as_posix()
    if rel == "tests/test_naming_contract.py":
        continue
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    found = [name for name in LEGACY if name in text]
    if found:
        hits.append((rel, found))
print("total", len(hits))
for rel, found in hits:
    print(rel, found)
