# -*- coding: utf-8 -*-
import json, os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.environ.get("AMO_DATA_DIR") or os.path.join(os.path.dirname(os.path.dirname(ROOT)), "data")
data = json.load(open(os.path.join(DATA, "cmd_config.json"), encoding="utf-8-sig"))
def walk(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            walk(v, path + "/" + str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, path + "[%d]" % i)
    elif isinstance(node, str) and "sk-" in node:
        print("PATH", path, "len", len(node), "mask", node[:6] + "..." + node[-4:])
walk(data)
print("TOP", list(data.keys())[:20])
print("PROVIDERS", json.dumps(data.get("provider") or data.get("providers") or {}, ensure_ascii=False)[:800])
