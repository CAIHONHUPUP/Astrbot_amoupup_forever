# -*- coding: utf-8 -*-
"""打包发版：生成「安装包」和「源码包」两个 zip（对应 docs/RELEASING.md 第二节）。

用法：
    python tools/pack_release.py              # 输出到 dist/
    python tools/pack_release.py --out D:/x   # 指定输出目录
    python tools/pack_release.py --dry        # 只打印会打包哪些文件

安装包：只含运行时文件（排除 tests/ tools/ .github/ .coveragerc mypy.ini
        requirements-dev.txt run_tests.py dist/ 以及 __pycache__/*.pyc/*.db*/*.log/.git）；
源码包：运行时 + 测试 + 工具 + CI（同样排除运行时产物）。

两个包一定要大小不同；一样大说明过滤失效。
"""
from __future__ import annotations

import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RUNTIME_SKIP_DIRS = {".git", "__pycache__", "dist", ".pytest_cache", ".mypy_cache",
                     "tests", "tools", ".github", "work", "_test_tmp", ".test-tmp"}
RUNTIME_SKIP_FILES = {".coveragerc", "mypy.ini", "requirements-dev.txt", "run_tests.py",
                      ".coverage", "mypy.ini"}
SKIP_SUFFIX = (".pyc", ".pyo", ".log", ".db", ".db-wal", ".db-shm", ".sqlite")
#: 带日期的备份/临时库副本（如 amo_forever.db.bak-cleanfake-20261120-232833）。
#: 这类名字不以 .db 结尾，后缀匹配不到，所以另外用「包含式」标记拦掉：真库备份不许进发布包。
SKIP_INFIX = (".db.bak", ".sqlite.bak")

#: 隐私：人物三视图与姿势/穿搭参考图绝不进任何发布包（2026-11-22 用户明确要求）。
#: 本地保留文件供拍照参考；包里只发代码，缺参考图由 photo_engine 自动降级。
PRIVATE_IMAGE_SUFFIX = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
PRIVATE_IMAGE_DIRS = ("templates/", "outfit_refs/", "photo_cache/",
                      "outfit_refs/角色三视图/")
PRIVATE_IMAGE_FILES = (
    "templates/amo_persona_front.png",
    "templates/amo_persona_side.png",
    "templates/amo_persona_full.png",
)


def _is_private_asset(rel):
    """发布包里必须排除的私有图片：三视图、姿势/穿搭参考图（含 work 下的快照副本）。"""
    rel = str(rel or "").replace(os.sep, "/").lstrip("./")
    if rel in PRIVATE_IMAGE_FILES:
        return True
    if not rel.lower().endswith(PRIVATE_IMAGE_SUFFIX):
        return False
    parts = rel.split("/")
    return any(name in parts for name in ("templates", "outfit_refs", "photo_cache"))


#: 任何发布包都不许带的敏感文件：本机配置（可能含 key）与模型探针凭据。
SENSITIVE_NAMES = frozenset({"cmd_config.json", "provider.env", ".env"})


def _is_sensitive(rel):
    rel = str(rel or "").replace(os.sep, "/").lstrip("./")
    return os.path.basename(rel) in SENSITIVE_NAMES or rel.endswith(".env")


SOURCE_SKIP_DIRS = {".git", "__pycache__", "dist", ".pytest_cache", ".mypy_cache",
                    "_test_tmp", ".test-tmp", "_scratch",
                    # work/ 是审计/仿真/旧代码快照（含真库备份与生产文件副本），只本地用：
                    # 安装包从来不含它，源码包也不再带（2026-11-23）。
                    "work"}


def project_name():
    meta = os.path.join(ROOT, "metadata.yaml")
    with open(meta, encoding="utf-8") as fh:
        text = fh.read()
    name = re.search(r"^name:\s*(\S+)\s*$", text, re.M)
    version = re.search(r"^version:\s*([0-9.]+)\s*$", text, re.M)
    if not name or not version:
        raise SystemExit("metadata.yaml 缺 name/version")
    return name.group(1), version.group(1)


def _walk(skip_dirs, skip_files):
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in skip_dirs and not d.startswith("."))
        for name in sorted(files):
            if name in skip_files:
                continue
            if name.endswith(SKIP_SUFFIX) or any(mark in name for mark in SKIP_INFIX):
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            if _is_private_asset(rel) or _is_sensitive(rel):
                continue
            yield rel, path


def build(kind, out_dir, dry=False):
    name, version = project_name()
    if kind == "install":
        entries = list(_walk(RUNTIME_SKIP_DIRS, RUNTIME_SKIP_FILES))
        suffix = ""
    else:
        entries = list(_walk(SOURCE_SKIP_DIRS, {".coverage"}))
        suffix = "-source"
    target = os.path.join(out_dir, "%s-%s%s.zip" % (name, version, suffix))
    if dry:
        print("[dry] %s -> %d files" % (os.path.basename(target), len(entries)))
        return target, len(entries)
    os.makedirs(out_dir, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, path in entries:
            zf.write(path, arcname="%s/%s" % (name, rel))
    print("%s: %d files, %.1f KB" % (os.path.basename(target), len(entries),
                                     os.path.getsize(target) / 1024.0))
    return target, len(entries)


def main(argv):
    out_dir = os.path.join(ROOT, "dist")
    dry = "--dry" in argv
    if "--out" in argv:
        out_dir = argv[argv.index("--out") + 1]
    install, n1 = build("install", out_dir, dry)
    source, n2 = build("source", out_dir, dry)
    if not dry:
        size1 = os.path.getsize(install)
        size2 = os.path.getsize(source)
        if size1 == size2:
            print("WARN: 两个包一样大，过滤可能失效")
        if n2 <= n1:
            print("WARN: 源码包文件数没有多于安装包")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
