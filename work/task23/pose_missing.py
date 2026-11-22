import os, sys, json, io
ROOT = r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever"
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import astrbot_stub; astrbot_stub.install()
import pose_library as P
data = json.load(open(os.path.join(os.path.dirname(os.path.dirname(ROOT)), "plugin_data", "Astrbot_amoupup_forever", "poses.json"), encoding="utf-8"))
poses = data.get("poses") or []
names = {str(p.get("name") or "") for p in poses}
out = io.TextIOWrapper(open(os.path.join(ROOT, "work", "task23", "pose_missing.txt"), "wb"), encoding="utf-8")
for name, room in P.APPROVED_POSES.items():
    if name not in names:
        print("MISSING from raw:", name, "|", room, file=out)
    else:
        pose = [p for p in poses if p.get("name")==name][0]
        if P.is_banned_pose(pose):
            print("BANNED by words:", name, "| banned:", [w for w in P.BANNED_WORDS if w in P.pose_text_of(pose)], file=out)
out.close()
print("done")
