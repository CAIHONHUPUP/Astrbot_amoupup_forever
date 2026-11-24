# -*- coding: utf-8 -*-
"""迁移前热备的校验 / 演练 / 恢复工具（event-id 加固 H1）。

安全边界（任何一条不满足都只拒绝，不做写入）：
- 备份必须带 manifest（<备份>.manifest.json）且 sha256/字节数对得上；
- 恢复前先 `PRAGMA integrity_check`，坏文件不恢复；
- 默认只做**副本演练**：把备份复制到临时目录校验，绝不碰目标库；
- 真实恢复必须显式 `--apply`，且会先把目标库用 sqlite backup API 热备成
  `<目标>.pre-restore-<时间戳>.db`，再覆盖目标并再次 integrity_check；
- 覆盖后清掉目标旁边的 -wal/-shm，避免旧 WAL 把恢复结果再"盖回去"。

用法：
    python tools/restore_db_backup.py list   [--backup-root DIR]
    python tools/restore_db_backup.py verify --backup <备份.db>
    python tools/restore_db_backup.py restore --backup <备份.db> [--target <库.db>] [--apply]

真实恢复前请先停掉 AstrBot（本工具不判断库是否被占用）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.parse

MANIFEST_SUFFIX = ".manifest.json"
BACKUP_DIR_ENV = "AMO_DB_BACKUP_DIR"
REQUIRED_FIELDS = ("schema", "source", "backup", "sha256", "bytes")


class Refused(Exception):
    """校验不通过 / 参数越界：只拒绝，不做任何写操作。"""


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_path_for(backup_path):
    return os.path.abspath(backup_path) + MANIFEST_SUFFIX


def load_manifest(backup_path):
    """读备份的 manifest；缺字段/不是对象都直接拒绝。"""
    path = manifest_path_for(backup_path)
    if not os.path.isfile(path):
        raise Refused("缺少 manifest：%s（没有清单的备份不校验，也不恢复）" % path)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Refused("manifest 读不出来：%s（%s）" % (path, exc))
    if not isinstance(data, dict):
        raise Refused("manifest 格式不对：%s" % path)
    missing = [name for name in REQUIRED_FIELDS if name not in data]
    if missing:
        raise Refused("manifest 缺字段 %s：%s" % (",".join(missing), path))
    return data


def _connect_readonly(path):
    uri = "file:%s?mode=ro" % urllib.parse.quote(os.path.abspath(path).replace("\\", "/"))
    return sqlite3.connect(uri, uri=True)


def integrity_check(path):
    """返回 (ok, message, user_version, tables)；坏文件不抛。"""
    try:
        conn = _connect_readonly(path)
    except sqlite3.Error as exc:
        return False, "%s: %s" % (type(exc).__name__, exc), 0, []
    try:
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            message = str((row[0] if row else "") or "")
        except sqlite3.Error as exc:
            return False, "%s: %s" % (type(exc).__name__, exc), 0, []
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
        except (sqlite3.Error, TypeError, ValueError):
            version = 0
        try:
            tables = [str(row[0]) for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        except sqlite3.Error:
            tables = []
        return message.lower() == "ok", message or "unknown", version, tables
    finally:
        conn.close()


def verify_backup(backup_path, deep=True):
    """校验 manifest + sha256 + 字节数（deep 时再跑 integrity_check）。"""
    backup = os.path.abspath(backup_path)
    if not os.path.isfile(backup):
        raise Refused("备份不存在：%s" % backup)
    manifest = load_manifest(backup)
    size = os.path.getsize(backup)
    if int(manifest.get("bytes") or -1) != size:
        raise Refused("备份字节数对不上：manifest %s / 实际 %d" % (manifest.get("bytes"), size))
    digest = sha256_file(backup)
    if str(manifest.get("sha256") or "") != digest:
        raise Refused("备份 sha256 对不上：manifest %s / 实际 %s"
                      % (str(manifest.get("sha256"))[:16], digest[:16]))
    report = {"backup": backup, "manifest": manifest_path_for(backup),
              "sha256": digest, "bytes": size,
              "from_version": manifest.get("from_version"),
              "to_version": manifest.get("to_version"),
              "source": manifest.get("source")}
    if deep:
        ok, message, version, tables = integrity_check(backup)
        report.update({"integrity": message, "ok": ok,
                       "user_version": version, "tables": len(tables)})
        if not ok:
            raise Refused("备份 integrity_check 不通过：%s" % message)
        if int(manifest.get("user_version") or 0) != version:
            raise Refused("备份的 user_version 和 manifest 不一致：manifest %s / 实际 %s"
                          % (manifest.get("user_version"), version))
    return report


def preflight(backup_path, work_dir=None, keep=False):
    """默认模式：复制到临时目录演练恢复，绝不碰线上库。"""
    verified = verify_backup(backup_path)
    base = work_dir or tempfile.mkdtemp(prefix="amo-restore-drill-")
    os.makedirs(base, exist_ok=True)
    copy_path = os.path.join(base, "restore-drill.db")
    shutil.copy2(verified["backup"], copy_path)
    ok, message, version, tables = integrity_check(copy_path)
    result = dict(verified, drill_copy=copy_path, integrity=message,
                  ok=ok, user_version=version, tables=len(tables), applied=False)
    if not keep:
        try:
            shutil.rmtree(base, ignore_errors=True)
        except OSError:
            # intentional: 配置/旧值/可选依赖/清理失败按默认值处理
            pass
    return result


def _hot_backup(source_path, dest_path):
    """用 sqlite backup API 给目标库做恢复前额外备份（WAL 安全）。"""
    src = sqlite3.connect(source_path)
    dst = sqlite3.connect(dest_path)
    try:
        src.backup(dst)
        check = dst.execute("PRAGMA quick_check").fetchone()
    finally:
        dst.close()
        src.close()
    if not check or str(check[0]).lower() != "ok":
        raise Refused("恢复前额外备份校验失败：%s" % (check[0] if check else "unknown"))
    return dest_path


def apply_restore(backup_path, target_path, force=False):
    """真实恢复：校验备份 → 额外备份目标 → 覆盖 → integrity_check。"""
    verified = verify_backup(backup_path)
    target = os.path.abspath(target_path)
    source = os.path.abspath(str(verified.get("source") or ""))
    if source and os.path.basename(source) != os.path.basename(target) and not force:
        raise Refused("备份来源库 %s 和目标库 %s 不是同一个文件名；确认无误请加 --force"
                      % (os.path.basename(source), os.path.basename(target)))
    parent = os.path.dirname(target)
    if not os.path.isdir(parent):
        raise Refused("目标目录不存在：%s" % parent)
    extra = None
    if os.path.isfile(target):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        extra = "%s.pre-restore-%s.db" % (target, stamp)
        _hot_backup(target, extra)
        extra = {"path": extra, "sha256": sha256_file(extra)}
    shutil.copy2(verified["backup"], target)
    for suffix in ("-wal", "-shm"):
        stale = target + suffix
        if os.path.exists(stale):
            os.remove(stale)
    ok, message, version, tables = integrity_check(target)
    if not ok:
        raise Refused("恢复后 integrity_check 不通过：%s（恢复前备份：%s）"
                      % (message, extra["path"] if extra else "无"))
    return dict(verified, target=target, integrity=message, ok=ok,
                user_version=version, tables=len(tables), applied=True,
                pre_restore_backup=(extra["path"] if extra else ""))


def find_backups(root):
    """目录下所有带 manifest 的备份（按文件名排序）。"""
    found = []
    if not os.path.isdir(root):
        return found
    for name in sorted(os.listdir(root)):
        if not name.endswith(".db"):
            continue
        path = os.path.join(root, name)
        if os.path.isfile(path) and os.path.isfile(path + MANIFEST_SUFFIX):
            found.append(path)
    return found


def _default_root(args):
    root = getattr(args, "backup_root", None) or os.environ.get(BACKUP_DIR_ENV, "")
    if not root:
        raise Refused("没给 --backup-root，也没有环境变量 %s" % BACKUP_DIR_ENV)
    return os.path.abspath(root)


def _print(report):
    for key in ("backup", "target", "sha256", "bytes", "from_version", "to_version",
                "user_version", "tables", "integrity", "drill_copy",
                "pre_restore_backup", "applied"):
        if key in report and report[key] not in (None, ""):
            print("%-20s %s" % (key + ":", report[key]))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="校验 / 演练 / 恢复 amo_forever.db 的迁移前热备（默认只演练）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出目录里带 manifest 的备份")
    p_list.add_argument("--backup-root", default=None)

    p_verify = sub.add_parser("verify", help="校验 manifest / sha256 / integrity_check")
    p_verify.add_argument("--backup", required=True)

    p_restore = sub.add_parser("restore", help="默认对副本演练；--apply 才真恢复")
    p_restore.add_argument("--backup", required=True)
    p_restore.add_argument("--target", default=None, help="真实恢复的目标库（--apply 必填）")
    p_restore.add_argument("--apply", action="store_true", help="真正覆盖目标库（会先额外备份）")
    p_restore.add_argument("--force", action="store_true", help="允许来源文件名与目标不同")
    p_restore.add_argument("--keep-copy", action="store_true", help="演练副本保留下来")

    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            root = _default_root(args)
            items = find_backups(root)
            if not items:
                print("没有找到带 manifest 的备份：%s" % root)
                return 0
            for path in items:
                manifest = load_manifest(path)
                print("%s  v%s→v%s  %s  %s" % (
                    path, manifest.get("from_version"), manifest.get("to_version"),
                    manifest.get("created_at"), str(manifest.get("sha256"))[:12]))
            print("共 %d 个备份：%s" % (len(items), root))
            return 0
        if args.command == "verify":
            _print(verify_backup(args.backup))
            print("verify: OK")
            return 0
        if not args.apply:
            report = preflight(args.backup, keep=args.keep_copy)
            _print(report)
            print("演练通过（未动任何线上库）；真实恢复请加 --apply --target <库.db>")
            return 0
        if not args.target:
            raise Refused("--apply 必须显式给 --target（默认只演练）")
        report = apply_restore(args.backup, args.target, force=args.force)
        _print(report)
        print("restore: OK")
        return 0
    except Refused as exc:
        print("拒绝：%s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
