# -*- coding: utf-8 -*-
"""回滚演练：用上一个 7.6.x 版本的代码读/写「新引擎格式」的库副本。

方案 3 的回滚方式是 reload 回上一个 7.6.x 版本，因此必须证明：
1) 旧代码能读新格式数据（不崩、时间线/文本可渲染）——数据格式向后兼容一个版本；
2) 旧代码能继续写（reload 后不是只读僵尸）；
3) 新代码能再读旧代码写回的数据（来回都不丢字段）。

用法：
  python tools/rollback_drill.py --db <amo_forever.db> --old-worktree <旧版本 checkout> --out report.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

DAY = "2026-11-21"

OLD_READER = r'''
import json, os, sys
old_root = sys.argv[1]
db_path = sys.argv[2]
day = sys.argv[3]
sys.path.insert(0, old_root)
sys.path.insert(0, os.path.join(old_root, "tests"))
import astrbot_stub
astrbot_stub.install()
import importlib.util
spec = importlib.util.spec_from_file_location("spec_fixture",
    os.path.join(old_root, "tests", "test_spec_timeline_office.py"))
spec_fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spec_fixture)
from amo_human_state import HumanState
tmp = os.path.join(os.path.dirname(db_path), "drill_plugin_tmp")
os.makedirs(tmp, exist_ok=True)
plugin = spec_fixture.FakePlugin(tmp)
plugin.db.path = db_path
plugin.db._conn = None
state = HumanState(plugin)
plugin.state = state
out = {"ok": False}
try:
    rows = [dict(row) for row in (state.db.get_schedule(day) or [])]
    tree = state.schedule_tree(day)
    text = state.schedule_text_of(day)
    children = sum(len(block.get("children") or []) for block in (tree or []))
    out.update({"rows": len(rows), "tree_blocks": len(tree or []),
                "children": children, "text_lines": len(text.splitlines()),
                "text_head": text.splitlines()[:3]})
    # 旧代码写一次：证明 reload 回旧版本后仍可写入
    result = state.commit_schedule_change(day, rows, old_items=rows,
                                          source="chat", reason="rollback_drill")
    out["write"] = {"changed": bool(result.get("changed")),
                    "saved": bool(result.get("saved")),
                    "version": int(result.get("version") or 0),
                    "rejected": result.get("rejected")}
    out["ok"] = bool(rows) and children > 0 and bool(text.strip()) and out["write"]["saved"]
finally:
    try:
        plugin.db.conn.close()
    except Exception:
        pass
print(json.dumps(out, ensure_ascii=False))
'''


def _run_old(old_root, db_path):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                     encoding="utf-8") as handle:
        handle.write(OLD_READER)
        script = handle.name
    try:
        proc = subprocess.run([sys.executable, script, old_root, db_path, DAY],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=300)
    finally:
        try:
            os.remove(script)
        except OSError:
            # intentional: 配置/旧值/可选依赖/清理失败按默认值处理
            pass
    payload = {}
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                payload = json.loads(line)
            except ValueError:
                payload = {}
            break
    return {"returncode": proc.returncode, "reader": payload,
            "stderr_tail": (proc.stderr or "")[-800:]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="回滚演练（旧版本代码读/写新格式库）")
    parser.add_argument("--db", required=True)
    parser.add_argument("--old-worktree", required=True)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        # intentional: 配置/旧值/可选依赖/清理失败按默认值处理
        pass

    tmp_dir = tempfile.mkdtemp(prefix="amo-rollback-")
    drill_db = os.path.join(tmp_dir, "rollback_drill.db")
    src = sqlite3.connect("file:" + os.path.abspath(args.db) + "?mode=ro", uri=True)
    dst = sqlite3.connect(drill_db)
    src.backup(dst)
    dst.close()
    src.close()

    report = {"db": os.path.abspath(args.db), "old_worktree": os.path.abspath(args.old_worktree),
              "day": DAY, "drill_db": drill_db}
    old_result = _run_old(os.path.abspath(args.old_worktree), drill_db)
    report["old_version"] = old_result
    # 新代码再读旧代码写过的库（往返兼容）
    import schedule_observer as observer
    class _ReadOnly:
        def __init__(self, path):
            self.conn = sqlite3.connect(path)
            self.conn.row_factory = sqlite3.Row
        def get_schedule(self, day):
            row = self.conn.execute("SELECT items FROM schedule WHERE day=?",
                                    (str(day),)).fetchone()
            return json.loads(row["items"]) if row else None
        def kv_get(self, key, default=None):
            row = self.conn.execute("SELECT value FROM kv WHERE key=?",
                                    (str(key),)).fetchone()
            return json.loads(row["value"]) if row else default
        def kv_set(self, key, value):
            return None
        def kv_delete(self, key):
            return False
        def kv_keys(self, prefix="", limit=200, offset=0):
            return []
    db = _ReadOnly(drill_db)
    rows = db.get_schedule(DAY) or []
    view, defects = observer.view_rows(rows, DAY)
    report["new_reads_old_write"] = {
        "rows": len(rows), "view_rows": len(view),
        "defects": dict(__import__("schedule_engine_v2").count_defects(defects)),
    }
    report["ok"] = bool(old_result.get("reader", {}).get("ok")) and bool(rows)
    db.conn.close()
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
