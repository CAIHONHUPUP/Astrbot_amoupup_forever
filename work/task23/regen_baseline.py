import json, os, shutil, sys, difflib
root = r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever"
sys.path.insert(0, root); sys.path.insert(0, os.path.join(root, "tests"))
import astrbot_stub; astrbot_stub.install()
import test_photo_plan as T

pairs = [(os.path.join(root, "tests", "data", "photo_plan_ab_baseline.json"), None),
         (os.path.join(root, "tests", "data", "photo_plan_ab_baseline_room_off.json"), False)]
report = []
for path, room_refs in pairs:
    with open(path, encoding="utf-8") as fh:
        old = json.load(fh)
    engine = T.FakeEngine()
    new = {}
    for name, activity, kind, selfie in T.SCENARIOS:
        new[name] = T.capture(engine, activity, kind, selfie, room_refs=room_refs)
    for name in sorted(old):
        for field in T.ABParityTests.FIELDS:
            a = old[name].get(field); b = new.get(name, {}).get(field)
            if a != b:
                report.append((os.path.basename(path), name, field, a, b))
    shutil.copy2(path, path + ".bak-task23")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(new, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")

out = []
for file, name, field, a, b in report:
    out.append("--- %s %s.%s" % (file, name, field))
    out.append("OLD: " + str(a)[:1500])
    out.append("NEW: " + str(b)[:1500])
open(os.path.join(root, "work", "task23", "baseline_diff.txt"), "w", encoding="utf-8").write("\n".join(out))
print("changed fields:", len(report))
