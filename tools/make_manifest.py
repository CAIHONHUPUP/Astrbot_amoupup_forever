# -*- coding: utf-8 -*-
"""批量生成备份清单（audit-fixes P0）。

给备份目录生成 manifest.json：文件列表 + sha256 + 相对路径 + 生成时间，
自动识别容器布局（<backup>/plugins/<plugin>/... / <backup>/<plugin>/... / <backup>/...）。

用法：
    python tools/make_manifest.py --backup <backup-root>/photo-bg-20261117011923
    python tools/make_manifest.py --backup A --backup B --check
    python tools/make_manifest.py --scan <backup-root> --filter photo- [--force]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rollback as rb


def _targets(args):
    if args.scan:
        root = os.path.abspath(args.scan)
        if not os.path.isdir(root):
            raise rb.Refused("扫描目录不存在：%s" % root)
        found = []
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if not os.path.isdir(path) or name in rb.SKIP_DIRS:
                continue
            if args.filter and args.filter not in name:
                continue
            manifest_path = os.path.join(path, rb.MANIFEST_NAME)
            if os.path.isfile(manifest_path) and not args.force:
                continue
            try:
                root_files = rb.walk_files(rb.layout_root(path))
            except OSError:
                root_files = []
            if not root_files:
                continue
            found.append(path)
        return found
    return [os.path.abspath(path) for path in (args.backup or [])]


def generate(backup, root, out, base="", check=False):
    """给一个备份目录（或容器里的一个插件目录）生成 manifest.json，返回摘要。"""
    backup = os.path.abspath(backup)
    root = os.path.abspath(root)
    if not os.path.isdir(backup):
        raise rb.Refused("备份目录不存在：%s" % backup)
    if not os.path.isdir(root):
        raise rb.Refused("备份里找不到插件/文件根：%s" % backup)
    data = rb.write_manifest(root, out, backup_dir=backup)
    info = {"backup": backup, "root": root, "plugin_root": data["plugin_root"],
            "count": data["count"], "digest": data["digest"][:12], "manifest": out}
    if check:
        info["verify"] = rb.verify(backup, out, rb.pick_base([backup], base))["ok"]
    return info


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="make_manifest.py", description="给备份目录批量生成 manifest.json")
    parser.add_argument("--backup", action="append", default=[], help="备份目录，可重复")
    parser.add_argument("--scan", default="", help="扫描该目录下的一级子目录")
    parser.add_argument("--filter", default="", help="只处理名字包含该子串的备份")
    parser.add_argument("--force", action="store_true", help="已有 manifest 也重写")
    parser.add_argument("--check", action="store_true", help="生成后立刻 verify")
    parser.add_argument("--root", default="", help="允许根目录；缺省自动识别")
    args = parser.parse_args(argv)
    try:
        targets = _targets(args)
        if not targets:
            raise rb.Refused("没有可处理的备份目录（--backup / --scan 至少给一个）")
        failed = 0
        for backup in targets:
            try:
                plans = rb.manifest_targets(backup)
            except rb.Refused as exc:
                print("[拒绝] %s" % exc)
                failed += 1
                continue
            for backup_dir, root, out in plans:
                try:
                    info = generate(backup_dir, root, out, args.root, check=args.check)
                except rb.Refused as exc:
                    print("[拒绝] %s" % exc)
                    failed += 1
                    continue
                print("[清单] %s -> %s（插件根 %s，%d 个文件，指纹 %s）"
                      % (os.path.basename(backup_dir), info["manifest"],
                         info["plugin_root"], info["count"], info["digest"]))
                if args.check:
                    passed = bool(info.get("verify"))
                    print("       校验：%s" % ("通过" if passed else "失败"))
                    if not passed:
                        failed += 1
        return 1 if failed else 0
    except rb.Refused as exc:
        print("[拒绝] %s" % exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
