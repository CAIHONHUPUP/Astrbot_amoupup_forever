import os, sys, json, io
ROOT = r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever"
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import astrbot_stub; astrbot_stub.install()
import pose_library as P

out = io.TextIOWrapper(open(os.path.join(ROOT, "work", "task23", "pose_check.txt"), "wb"), encoding="utf-8")
print("approved whitelist names:", len(P.APPROVED_POSES), file=out)
print("banned words:", len(P.BANNED_WORDS), file=out)
data = json.load(open(os.path.join(os.path.dirname(os.path.dirname(ROOT)), "plugin_data", "Astrbot_amoupup_forever", "poses.json"), encoding="utf-8"))
poses = data.get("poses") or []
allowed = P.approved_poses(poses)
print("raw", len(poses), "-> allowed", len(allowed), file=out)
from collections import Counter
print("rooms:", Counter(p["room"] for p in allowed), file=out)
# check no banned names in allowed
bad = [p["name"] for p in allowed if P.is_banned_pose(p)]
print("banned leaked:", bad, file=out)
# trim
trimmed, removed = P.trim_poses_data(data)
print("trimmed count", len(trimmed["poses"]), "removed", len(removed), file=out)
print("trim names sample", [p["name"] for p in trimmed["poses"]][:5], file=out)

# fake library for random checks
class FakePlugin: pass
class Lib(P.PoseLibrary):
    def __init__(self):
        self._items = [dict(p) for p in allowed]
        self._last_pose_name = ""
        self._lock = __import__("threading").RLock()
    def poses(self):
        return [dict(p) for p in self._items]
    def allowed_poses(self):
        return P.approved_poses(self.poses())
lib = Lib()
for zone in ("home_sofa", "home_bed", "home_bath", "mall", "road_subway", "home_generic"):
    picks = [lib.random_pose(zone) for _ in range(20)]
    names = {p["name"] for p in picks if p}
    rooms = {p["room"] for p in picks if p}
    print(zone, "->", len(names), "names", "rooms", rooms, file=out)
    if zone in ("home_sofa","home_generic"):
        print("  living sample:", sorted(names)[:8], file=out)
out.close()
print("done")