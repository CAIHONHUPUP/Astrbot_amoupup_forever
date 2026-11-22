# -*- coding: utf-8 -*-
import json, re, io, collections
j = json.load(open("work/audit/holdout/replay_prod.json", encoding="utf-8"))
rows = j["samples"]["clean_changed"] + j["samples"]["fixed"] + j["samples"]["persistent"]
# replay_prod.json samples include only first N; but samples dict stores all rows in memory; file has limited?
print("rows in samples", {k: len(v) for k, v in j["samples"].items()})
def issues(text):
    out = []
    if not text:
        return ["empty"]
    if text.count("（") != text.count("）") or text.count("(") != text.count(")"):
        out.append("unbalanced_paren")
    if re.search(r"([\u4e00-\u9fff])\1", text):
        for m in re.finditer(r"([\u4e00-\u9fff])\1", text):
            pair = m.group(0)
            if pair not in ("哈哈", "哈哈", "嘿嘿", "呵呵", "嘻嘻", "呜呜", "嗯嗯", "看看", "想想", "谢谢", "慢慢", "说说", "走走", "好好", "天天"):
                out.append("dup_char:" + pair)
                break
    if text.startswith(("儿", "了", "的", "着", "嘛", "呢", "吧", "啊", "呀")) and len(text) < 12:
        out.append("dangling_prefix")
    return out
cnt = collections.Counter()
samples = collections.defaultdict(list)
for r in rows:
    for item in issues(r["final"]):
        cnt[item] += 1
        samples[item].append((r["id"], r["raw"][:60], r["final"][:60]))
print(json.dumps(cnt, ensure_ascii=False, indent=1))
out = io.StringIO()
for key, items in samples.items():
    out.write("== %s (%d) ==\n" % (key, len(items)))
    for item in items[:12]:
        out.write("  #%s raw=%r fin=%r\n" % item)
open("work/audit/holdout/replay_corruption.txt", "w", encoding="utf-8").write(out.getvalue())
