# -*- coding: utf-8 -*-
import io, json, os
HERE = r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever"
report = json.load(io.open(os.path.join(HERE, "work", "sim", "real_llm_layer.json"), encoding="utf-8"))
replay = json.load(io.open(os.path.join(HERE, "work", "task29", "replay_after_fix.json"), encoding="utf-8"))


def intro(row):
    return sorted(set(row.get("sent_codes") or []) - set(row.get("cand_codes") or []))


print("report rows:", len(report["rows"]), "replay rows:", len(replay["rows"]))
print("report guard-introduced:", [(r["gid"], r["turn"], intro(r)) for r in report["rows"] if intro(r)])
print("replay guard-introduced:", [(r["gid"], r["turn"], intro(r)) for r in replay["rows"] if intro(r)])
for gid, turn in (("G47", 13), ("G33", 8)):
    print("=" * 60, gid, turn)
    old = next(r for r in report["rows"] if r["gid"] == gid and int(r["turn"]) == turn)
    new = next(r for r in replay["rows"] if r["gid"] == gid and int(r["turn"]) == turn)
    print("report:", json.dumps({k: old.get(k) for k in
          ("time", "probe", "snap", "candidate", "sent", "cand_codes", "sent_codes")},
          ensure_ascii=False, indent=1))
    print("replay:", json.dumps({k: new.get(k) for k in
          ("time", "probe", "snap", "candidate", "sent", "cand_codes", "sent_codes")},
          ensure_ascii=False, indent=1))
