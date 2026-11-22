# -*- coding: utf-8 -*-
import json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "tests")):
    if path not in sys.path:
        sys.path.insert(0, path)
import prompt_fixtures as fx
OUT = os.path.join(ROOT, "tests", "golden", "prompts_golden.json")
with open(OUT, encoding="utf-8") as handle:
    data = json.load(handle)
fx.freeze_clock()
data["constants"] = {"photo_request_rule": fx.amo_llm.PHOTO_REQUEST_RULE,
                     "action_ownership_rules": fx.amo_llm.ACTION_OWNERSHIP_RULES}
data["private_parts"] = fx.private_parts()
data["private_system"] = fx.system_private("")
data["private_system_note"] = fx.system_private("刚才在忙，补一句。")
data["group_parts"] = fx.group_parts("", "")
data["group_system"] = fx.system_group("", "")
data["group_system_direct"] = fx.system_group("bot", "")
data["group_system_interject"] = fx.system_group("other", "想插一句")
data["date_blocks"] = fx.date_blocks()
data["short_blocks"] = fx.short_blocks()
data["group_scene_blocks"] = fx.group_scene_blocks()
with open(OUT, "w", encoding="utf-8", newline="\n") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=1, sort_keys=True)
    handle.write("\n")
print("WROTE", os.path.getsize(OUT))
