# -*- coding: utf-8 -*-
"""发布回滚工具（Phase 19）：manifest / verify / plan / apply，默认只做 dry-run。

安全边界（任何一条不满足都只拒绝，不做写入）：
- 根目录自动识别：默认本脚本所在插件的父目录；如果路径不在其中，再向上查找同级
  `backup/` 目录，把备份目录及其父目录也当候选。所以默认参数就能处理
  `<备份根>/**`，不必每次显式 --root；
- 显式 --root 时以 --root 为准；所有目录必须落在根目录之内；
- apply 必须先通过清单校验（sha256 全量比对），清单不匹配直接拒绝；
- apply 先把当前目标目录整体备份成 <target>.rollback-<时间戳>，再覆盖；
- 覆盖后抽样 py_compile，编译不过报失败并给出备份目录，不假装成功。

清单格式（schema=2，兼容旧 schema=1）：
    {"schema": 2, "root": <插件根绝对路径>, "backup": <备份目录绝对路径>,
     "plugin_root": <相对备份目录的插件根，"." 表示备份目录就是插件根>,
     "created_at": "...", "count": N, "digest": "...", "files": {相对路径: sha256}}

容器布局：备份目录可以是 `<backup>/<plugin>/...`、`<backup>/plugins/<plugin>/...`
或直接 `<backup>/...`；manifest 记 plugin_root，verify/plan/apply 都按它找文件。

纯标准库、无第三方依赖；三插件同一份（sha256 一致）。
用法：
    python tools/rollback.py manifest --dir <备份目录> [--out <备份目录>/manifest.json]
    python tools/rollback.py verify   --dir <备份目录>
    python tools/rollback.py plan     --backup <备份目录> --target <插件目录>
    python tools/rollback.py apply    --backup <备份目录> --target <插件目录> [--confirm <token>]
    python tools/make_manifest.py --backup <目录> [--backup <目录> ...]  # 批量生成
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(TOOL_DIR)
DEFAULT_BASE = os.path.dirname(PLUGIN_ROOT)

MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA = 2
BACKUP_DIRNAME = "backup"
SKIP_DIRS = ("__pycache__", ".git", ".pytest_cache", ".mypy_cache",
             ".ruff_cache", ".coverage")
SKIP_NAMES = ("manifest.json", ".rollback_manifest.json", ".DS_Store")
PLUGIN_MARKERS = ("main.py", "metadata.yaml", "__init__.py")
SAMPLE_COMPILE = 20


class Refused(Exception):
    """越界 / 清单不匹配 / 参数不对：只拒绝，不做任何写操作。"""


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def walk_files(root):
    """目录下所有普通文件（相对路径，/ 分隔，排序）。"""
    found = []
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(names):
            if name in SKIP_NAMES or name.endswith(".pyc"):
                continue
            path = os.path.join(base, name)
            if os.path.isfile(path):
                found.append(os.path.relpath(path, root).replace("\\", "/"))
    return sorted(found)


def manifest(root):
    """给目录生成 sha256 清单：{root, count, digest, files:{相对路径: sha256}}。"""
    files = {}
    for rel in walk_files(root):
        files[rel] = _sha256_file(os.path.join(root, rel))
    payload = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return {"root": os.path.abspath(root), "count": len(files),
            "digest": digest, "files": files}


def write_manifest(root, out_path, backup_dir=None):
    """生成清单并落盘（schema=2：记录备份目录与容器布局的相对插件根）。"""
    root = os.path.abspath(root)
    backup = os.path.abspath(backup_dir or root)
    data = manifest(root)
    try:
        rel = os.path.relpath(root, backup)
    except ValueError:
        rel = "."
    if rel.startswith(".."):
        rel = "."
    data.update ({
        "schema": MANIFEST_SCHEMA,
        "backup": backup,
        "plugin_root": rel.replace("\\", "/"),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    })
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return data


def load_manifest(path):
    if not os.path.isfile(path):
        raise Refused("清单不存在：%s（先跑 manifest 生成清单）" % path)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        raise Refused("清单格式不对：%s" % path)
    return data


def manifest_root(data):
    """清单里的插件根：schema=2 按 backup+plugin_root 还原，schema=1 退回 root。"""
    try:
        schema = int(data.get("schema") or 1)
    except (TypeError, ValueError):
        schema = 1
    if schema >= 2 and "plugin_root" in data and data.get("backup"):
        return os.path.normpath(os.path.join(os.path.abspath(str(data["backup"])),
                                             str(data.get("plugin_root") or ".")))
    return os.path.abspath(str(data.get("root") or "."))


def resolve_inside(path, base):
    """把 path 规范成绝对路径，并确认它在 base 之内（不能等于 base）。"""
    target = os.path.abspath(path)
    base_abs = os.path.abspath(base)
    if os.path.normcase(target) == os.path.normcase(base_abs):
        raise Refused("不能直接操作插件父目录本身：%s" % target)
    try:
        common = os.path.commonpath([target, base_abs])
    except ValueError:
        raise Refused("路径不在允许范围内：%s（基准 %s）" % (target, base_abs))
    if os.path.normcase(common) != os.path.normcase(base_abs):
        raise Refused("路径越界：%s 不在插件父目录 %s 之内" % (target, base_abs))
    return target


def base_candidates():
    """默认根候选：插件父目录 + 向上找到的同级 backup/ 目录及其父目录。"""
    seen, out = set(), []

    def _add(path):
        path = os.path.abspath(path)
        key = os.path.normcase(path)
        if key not in seen:
            seen.add(key)
            out.append(path)

    _add(DEFAULT_BASE)
    current = os.path.abspath(DEFAULT_BASE)
    for _ in range(8):
        parent = os.path.dirname(current)
        if parent == current:
            break
        backup = os.path.join(parent, BACKUP_DIRNAME)
        if os.path.isdir(backup):
            _add(backup)
            _add(parent)
            break
        current = parent
    return out


def pick_base(paths, base=""):
    """显式 base 优先；否则选一个能同时容纳所有路径的候选根。"""
    text = str(base or "").strip()
    if text and text.lower() != "auto":
        return os.path.abspath(text)
    wanted = [os.path.abspath(p) for p in paths if str(p or "").strip()]
    candidates = base_candidates()
    for candidate in candidates:
        try:
            for path in wanted:
                resolve_inside(path, candidate)
        except Refused:
            continue
        return candidate
    raise Refused("找不到能同时容纳这些路径的根目录：%s（可用 --root 指定；候选 %s）"
                  % (wanted, candidates))


def _has_markers(path):
    """目录里有没有插件标记文件（main.py / metadata.yaml / __init__.py）。"""
    try:
        names = os.listdir(path)
    except OSError:
        return False
    return any(name in PLUGIN_MARKERS for name in names)


def _looks_plugin(path):
    try:
        names = os.listdir(path)
    except OSError:
        return False
    if any(name in PLUGIN_MARKERS for name in names):
        return True
    if not any(str(name).endswith(".py") for name in names):
        return False
    # 只有 .py、没有标记文件时，如果下面还套着带标记的插件目录，这层是容器而不是插件根
    # （data-p1-pre-* 就是这种：根上放了一个 compare_tree.py，插件其实在子目录里）
    for name in names:
        child = os.path.join(path, name)
        if os.path.isdir(child) and _has_markers(child):
            return False
    return True


def _plugin_dirs(container):
    try:
        names = sorted(os.listdir(container))
    except OSError:
        return []
    return [name for name in names
            if name not in SKIP_DIRS and os.path.isdir(os.path.join(container, name))]


def _match_dir(container, target):
    """按 target 的目录名在容器里找同一插件（大小写不敏感，允许包含匹配）。"""
    subs = _plugin_dirs(container)
    if not subs or not target:
        return ""
    want = os.path.normcase(os.path.basename(os.path.abspath(target)))
    for name in subs:
        if os.path.normcase(name) == want:
            return name
    for name in subs:
        low = os.path.normcase(name)
        if want in low or low in want:
            return name
    return ""


def _descend(path, target=None, depth=0):
    """容器再套容器时继续下钻到真正的插件根。

    优先按 target 目录名匹配（data-p2-pre-* 的 <bk>/<plugins 快照>/<插件>/），
    其次取唯一带标记文件的子目录（phase1-timeline 的 <bk>/plugin/）。
    下钻最多 4 层，找不到就停在原处，绝不猜。
    """
    if depth >= 4 or _has_markers(path):
        return path
    marked = [name for name in _plugin_dirs(path)
              if _has_markers(os.path.join(path, name))]
    want = os.path.normcase(os.path.basename(os.path.abspath(target))) if target else ""
    if want:
        for name in marked:
            low = os.path.normcase(name)
            if low == want or want in low or low in want:
                return _descend(os.path.join(path, name), target, depth + 1)
    if len(marked) == 1:
        return _descend(os.path.join(path, marked[0]), target, depth + 1)
    return path


def layout_root(backup, target=None):
    """备份目录 → 真正的插件根（兼容容器布局与扁平布局）。"""
    backup = os.path.abspath(backup)
    if not os.path.isdir(backup):
        return backup
    if _looks_plugin(backup):
        return _descend(backup, target)
    plugins = os.path.join(backup, "plugins")
    if os.path.isdir(plugins):
        subs = _plugin_dirs(plugins)
        if len(subs) == 1:
            return _descend(os.path.join(plugins, subs[0]), target)
        chosen = _match_dir(plugins, target)
        if chosen:
            return _descend(os.path.join(plugins, chosen), target)
        return plugins
    chosen = _match_dir(backup, target)
    if chosen:
        return _descend(os.path.join(backup, chosen), target)
    subs = _plugin_dirs(backup)
    if len(subs) == 1:
        child = os.path.join(backup, subs[0])
        if _looks_plugin(child):
            return _descend(child, target)
    return _descend(backup, target)


def plugin_roots(backup):
    """备份里能独立当插件根的目录（容器布局里可能有多个插件）。"""
    backup = os.path.abspath(backup)
    if not os.path.isdir(backup):
        return []
    if _looks_plugin(backup):
        return [backup]
    found = []
    plugins = os.path.join(backup, "plugins")
    if os.path.isdir(plugins):
        found = [os.path.join(plugins, name) for name in _plugin_dirs(plugins)
                 if _looks_plugin(os.path.join(plugins, name))]
        if found:
            return found
    for name in _plugin_dirs(backup):
        child = os.path.join(backup, name)
        if _looks_plugin(child):
            found.append(child)
    return found


def manifest_targets(backup):
    """(备份目录, 插件根, 清单路径)：单插件写在备份根，多插件各写各的目录。"""
    backup = os.path.abspath(backup)
    roots = plugin_roots(backup)
    if len(roots) > 1:
        return [(root, root, os.path.join(root, MANIFEST_NAME)) for root in roots]
    root = roots[0] if roots else layout_root(backup)
    return [(backup, root, os.path.join(backup, MANIFEST_NAME))]


def diff_files(source, destination):
    """source 相对 destination 的差异（apply 视角：source=备份，destination=当前）。"""
    added = sorted(set(source) - set(destination))
    extra = sorted(set(destination) - set(source))
    changed = sorted(rel for rel in set(source) & set(destination)
                     if source[rel] != destination[rel])
    return {"added": added, "extra": extra, "changed": changed}


def verify(backup, manifest_path, base):
    """清单校验：备份目录当前内容必须和 manifest 完全一致。"""
    backup = resolve_inside(backup, base)
    saved = load_manifest(os.path.abspath(manifest_path))
    root = resolve_inside(manifest_root(saved), base)
    current = manifest(root)
    diff = diff_files(saved["files"], current["files"])
    ok = saved.get("digest") == current.get("digest")
    return {"ok": ok, "backup": backup, "root": root, "count": current["count"],
            "saved_digest": str(saved.get("digest") or ""),
            "current_digest": current["digest"], "diff": diff}


def plan(backup, target, base):
    """dry-run：只读对比备份与当前目录，指出会新增/覆盖/保留哪些文件。"""
    backup = resolve_inside(backup, base)
    target = resolve_inside(target, base)
    if os.path.normcase(backup) == os.path.normcase(target):
        raise Refused("备份目录与目标目录不能是同一个：%s" % target)
    if not os.path.isdir(backup):
        raise Refused("备份目录不存在：%s" % backup)
    if not os.path.isdir(target):
        raise Refused("目标目录不存在：%s" % target)
    root = layout_root(backup, target)
    if not os.path.isdir(root):
        raise Refused("备份里找不到插件目录：%s" % backup)
    if os.path.normcase(root) == os.path.normcase(target):
        raise Refused("备份插件目录与目标目录不能是同一个：%s" % target)
    source = manifest(root)
    destination = manifest(target)
    diff = diff_files(source["files"], destination["files"])
    return {"ok": True, "backup": backup, "root": root, "target": target,
            "dry_run": True, "token": source["digest"][:12],
            "source_count": source["count"], "target_count": destination["count"],
            "diff": diff, "add": len(diff["added"]),
            "overwrite": len(diff["changed"]), "keep_extra": len(diff["extra"])}


def _compile_sample(root, files):
    targets = [rel for rel in files if rel.endswith(".py")][:SAMPLE_COMPILE]
    if not targets:
        return {"ok": True, "checked": 0, "detail": "没有需要编译的 .py"}
    command = [sys.executable, "-m", "py_compile"] + [os.path.join(root, rel) for rel in targets]
    result = subprocess.run(command, capture_output=True, text=True)
    detail = (result.stderr or result.stdout or "").strip()[-400:]
    return {"ok": result.returncode == 0, "checked": len(targets), "detail": detail}


def apply(backup, target, confirm, manifest_path, base):
    """恢复备份：校验清单 -> 备份当前 -> 覆盖 -> py_compile 抽样。默认 dry-run。"""
    backup = resolve_inside(backup, base)
    target = resolve_inside(target, base)
    if os.path.normcase(backup) == os.path.normcase(target):
        raise Refused("备份目录与目标目录不能是同一个：%s" % target)
    if not os.path.isdir(backup) or not os.path.isdir(target):
        raise Refused("备份或目标目录不存在")
    checked = verify(backup, manifest_path, base)
    token = checked["saved_digest"][:12]
    if not confirm:
        return {"ok": bool(checked["ok"]), "dry_run": True, "token": token,
                "verify": checked, "plan": plan(backup, target, base)}
    if confirm != token:
        raise Refused("确认串不对：应传 %s（先不传 --confirm 看 dry-run）" % token)
    if not checked["ok"]:
        raise Refused("备份清单校验失败，拒绝覆盖：%s" % checked["diff"])
    root = checked["root"]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    shelter = target + ".rollback-" + stamp
    serial = 1
    while os.path.exists(shelter):
        serial += 1
        shelter = target + ".rollback-" + stamp + "-" + str(serial)
    shutil.copytree(target, shelter, ignore=shutil.ignore_patterns(*SKIP_DIRS))
    saved = load_manifest(os.path.abspath(manifest_path))
    planned = diff_files(saved["files"], manifest(target)["files"])
    copied = []
    for rel in sorted(saved["files"]):
        source = os.path.join(root, rel)
        destination = os.path.join(target, rel)
        parent = os.path.dirname(destination)
        if parent:
            os.makedirs(parent, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(rel)
    compiled = _compile_sample(target, copied)
    return {"ok": compiled["ok"], "dry_run": False, "token": token,
            "shelter": shelter, "root": root, "copied": len(copied),
            "add": len(planned["added"]), "overwrite": len(planned["changed"]),
            "keep_extra": len(planned["extra"]), "compile": compiled}


def _print_manifest(data):
    print("[清单] 目录：%s" % data["root"])
    print("       文件：%d 个，总指纹：%s" % (data["count"], data["digest"][:12]))


def _cmd_manifest(args):
    out = os.path.abspath(args.out) if args.out else ""
    base = pick_base([args.dir] + ([out] if out else []), args.root)
    backup = resolve_inside(args.dir, base)
    targets = manifest_targets(backup)
    if out and len(targets) != 1:
        raise Refused("这份备份含多个插件，--out 只能配合单个插件使用：%s" % backup)
    if out:
        targets = [(backup, targets[0][1], out)]
    for backup_dir, root, manifest_path in targets:
        if not os.path.isdir(root):
            raise Refused("备份里找不到插件目录：%s" % backup)
        resolve_inside(manifest_path, base)
        data = write_manifest(root, manifest_path, backup_dir=backup_dir)
        _print_manifest(data)
        print("       插件根：%s（相对备份 %s）" % (root, data["plugin_root"]))
        print("       清单已写入：%s" % manifest_path)
    return 0


def _cmd_verify(args):
    manifest_path = args.manifest or os.path.join(args.dir, MANIFEST_NAME)
    base = pick_base([args.dir, manifest_path], args.root)
    if not os.path.isfile(manifest_path):
        # 多插件容器：每个插件目录各有一份清单，逐个校验并汇总。
        results = []
        for root in plugin_roots(resolve_inside(args.dir, base)):
            plugin_manifest = os.path.join(root, MANIFEST_NAME)
            if os.path.isfile(plugin_manifest):
                results.append((root, verify(root, plugin_manifest, base)))
        if not results:
            print("[拒绝] 清单不存在：%s（先跑 manifest 生成清单）" % manifest_path)
            return 2
        failed = False
        for root, result in results:
            if result["ok"]:
                print("[校验] 通过：%s（%d 个文件，指纹 %s）"
                      % (root, result["count"], result["current_digest"][:12]))
            else:
                failed = True
                print("[校验] 失败：%s 和清单不一致" % root)
                print("       新增：%s" % (result["diff"]["added"][:6] or "无"))
                print("       缺失：%s" % (result["diff"]["extra"][:6] or "无"))
                print("       变化：%s" % (result["diff"]["changed"][:6] or "无"))
        return 1 if failed else 0
    result = verify(args.dir, manifest_path, base)
    if result["ok"]:
        print("[校验] 通过：%s（%d 个文件，指纹 %s）"
              % (result["backup"], result["count"], result["current_digest"][:12]))
        return 0
    print("[校验] 失败：备份内容和清单不一致")
    print("       新增：%s" % (result["diff"]["added"][:6] or "无"))
    print("       缺失：%s" % (result["diff"]["extra"][:6] or "无"))
    print("       变化：%s" % (result["diff"]["changed"][:6] or "无"))
    return 1


def _cmd_plan(args):
    base = pick_base([args.backup, args.target], args.root)
    result = plan(args.backup, args.target, base)
    print("[计划] dry-run（不写任何文件）")
    print("       备份：%s（%d 个文件）" % (result["backup"], result["source_count"]))
    print("       插件根：%s" % result["root"])
    print("       目标：%s（%d 个文件）" % (result["target"], result["target_count"]))
    print("       将新增 %d、覆盖 %d、目标多出但保留 %d"
          % (result["add"], result["overwrite"], result["keep_extra"]))
    print("       确认串：%s" % result["token"])
    return 0


def _resolve_manifest_path(backup, target, explicit):
    """清单定位：备份根 → 容器里匹配 target 的插件目录，顺序找 manifest.json。"""
    if explicit:
        return explicit
    default = os.path.join(backup, MANIFEST_NAME)
    if os.path.isfile(default):
        return default
    root = layout_root(backup, target)
    candidate = os.path.join(root, MANIFEST_NAME)
    if os.path.isfile(candidate):
        return candidate
    return default


def _cmd_apply(args):
    manifest_path = _resolve_manifest_path(args.backup, args.target, args.manifest)
    base = pick_base([args.backup, args.target, manifest_path], args.root)
    result = apply(args.backup, args.target, args.confirm, manifest_path, base)
    if result.get("dry_run"):
        print("[恢复] dry-run（未确认，不动任何文件）")
        print("       清单校验：%s" % ("通过" if result["verify"]["ok"] else "失败"))
        print("       将新增 %d、覆盖 %d" % (result["plan"]["add"], result["plan"]["overwrite"]))
        print("       确认串：%s（通过 --confirm 传入才会真正执行）" % result["token"])
        return 0 if result["ok"] else 1
    print("[恢复] 已覆盖 %d 个文件；当前目录已备份到：%s" % (result["copied"], result["shelter"]))
    print("[恢复] 新增 %d、覆盖 %d、目标多出但保留 %d"
          % (result["add"], result["overwrite"], result["keep_extra"]))
    compile_info = result["compile"]
    print("[恢复] py_compile 抽样 %d 个文件：%s"
          % (compile_info["checked"], "通过" if compile_info["ok"] else "失败"))
    if not compile_info["ok"]:
        print("       %s" % compile_info["detail"])
        print("       可用上面的备份目录手工回退")
        return 1
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="rollback.py", description="发布回滚工具（默认 dry-run）")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("manifest", help="给目录生成 sha256 清单")
    one.add_argument("--dir", required=True)
    one.add_argument("--out", default="")
    one.add_argument("--root", default="", help="允许根目录；缺省自动识别")
    two = sub.add_parser("verify", help="校验目录与清单是否一致")
    two.add_argument("--dir", required=True)
    two.add_argument("--manifest", default="")
    two.add_argument("--root", default="", help="允许根目录；缺省自动识别")
    three = sub.add_parser("plan", help="dry-run 对比备份与当前目录")
    three.add_argument("--backup", required=True)
    three.add_argument("--target", required=True)
    three.add_argument("--root", default="", help="允许根目录；缺省自动识别")
    four = sub.add_parser("apply", help="恢复备份（先校验清单，再备份当前）")
    four.add_argument("--backup", required=True)
    four.add_argument("--target", required=True)
    four.add_argument("--confirm", default="")
    four.add_argument("--manifest", default="")
    four.add_argument("--root", default="", help="允许根目录；缺省自动识别")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    handlers = {"manifest": _cmd_manifest, "verify": _cmd_verify,
                "plan": _cmd_plan, "apply": _cmd_apply}
    try:
        return handlers[args.command](args)
    except Refused as exc:
        print("[拒绝] %s" % exc)
        return 2
    except OSError as exc:
        print("[失败] 文件操作失败：%s" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
