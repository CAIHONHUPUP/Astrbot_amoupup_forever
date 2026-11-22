import json, os, sys
root = r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever"
sys.path.insert(0, root); sys.path.insert(0, os.path.join(root, "tests"))
import astrbot_stub; astrbot_stub.install()
import test_architecture_ratchet as A
names = A._module_names()
fanout, violations = A._collect(names)
baseline = {rel: sorted(targets) for rel, targets in sorted(fanout.items())}
path = A.BASELINE_PATH
with open(path, encoding="utf-8") as fh:
    old = json.load(fh)
changed = {rel: sorted(set(fanout.get(rel, [])) - set(old.get(rel, []))) for rel in fanout if set(fanout.get(rel, [])) - set(old.get(rel, []))}
with open(path, "w", encoding="utf-8", newline="\n") as fh:
    json.dump(baseline, fh, ensure_ascii=False, indent=2, sort_keys=True)
    fh.write("\n")
print("baseline files", len(baseline), "changed entries", len(changed))
for rel, extra in sorted(changed.items()):
    print(rel, "->", extra)
print("violations", violations)
