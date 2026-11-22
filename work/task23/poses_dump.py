
import json, io, sys, collections
sys.stdout = io.TextIOWrapper(open(r"D:\\dafeiyu\\astrbot\\data\\plugin_data\\Astrbot_amoupup_forever\\poses_report.txt", "wb"), encoding="utf-8")
p = json.load(open(r"D:\\dafeiyu\\astrbot\\data\\plugin_data\\Astrbot_amoupup_forever\\poses.json", encoding="utf-8"))
poses = p.get("poses") or []
print("total", len(poses), "keys", sorted(p.keys()))
notes = collections.Counter()
for x in poses:
    note = str(x.get("note") or "")
    group = note.split("：")[0] if "：" in note else note[:12]
    notes[group] += 1
print("groups:", notes.most_common())
for i, x in enumerate(poses):
    print(i, "|", x.get("name"), "|", x.get("note"), "|", x.get("usage"), "|", x.get("id"))
