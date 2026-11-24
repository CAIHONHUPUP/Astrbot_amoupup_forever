#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生产日志一次性脱敏（2026-11-22 task-26）。

背景：astrbot.log 里有 23 行明文 Bearer sk-…（provider 异常 traceback 把
headers 当 locals 打了出来）、1,061 行 open_id。运行中的 AstrBot 在 reload
之前还是会按旧代码打日志，所以这是一次性的历史清理；新日志靠
observability.install_log_redaction() 的进程级 logging.Filter。

规矩：
- 先备份（logs/backup/<文件名>.<时间戳>.bak），备份成功才动原文件；
- 输出只打印「命中行数 / 各规则次数 / sha256」，绝不打印密钥本身；
- 当前 astrbot.log 被 AstrBot 的 loguru 以 append 打开，Windows 上不能
  os.replace（会 PermissionError），此时退回原地重写：写完后把并发追加的
  尾部一起搬过来，尽量不丢新行。

用法（在插件仓库根目录）：
    python tools/redact_prod_logs.py --dry-run
    python tools/redact_prod_logs.py
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import time
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# REPO = <astrbot>/data/plugins/<plugin>，日志在 <astrbot>/data/logs
DEFAULT_LOGS_DIR = os.path.normpath(os.path.join(os.path.dirname(REPO), "..", "logs"))
REPORT_PATH = os.path.join(REPO, "work", "audit", "log_redaction_report.md")


def load_redactor():
    """按文件加载 observability.py（不触发 AstrBot 运行时的重量级 import）。"""
    if "astrbot" not in sys.modules:
        astrbot = types.ModuleType("astrbot")
        astrbot.__path__ = []
        api = types.ModuleType("astrbot.api")

        class _NullLogger:
            def __getattr__(self, _name):
                return lambda *args, **kwargs: None

        api.logger = _NullLogger()
        astrbot.api = api
        sys.modules["astrbot"] = astrbot
        sys.modules["astrbot.api"] = api
    path = os.path.join(REPO, "observability.py")
    spec = importlib.util.spec_from_file_location("amo_observability_redactor", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_hits(text, redactor):
    """逐行统计：命中行数 + 每条规则命中次数（只计数，不返回命中内容）。"""
    per_rule = {}
    changed = 0
    for line in text.splitlines():
        before = line
        after = redactor.redact_text(line, stats=per_rule)
        if after != before:
            changed += 1
    return changed, per_rule


def _write_in_place(path, new_bytes, old_size):
    """原地重写（文件被别的进程 append 打开时用）。先搬运并发追加的尾部。"""
    with open(path, "r+b") as fh:
        fh.seek(0)
        fh.write(new_bytes)
        fh.flush()
        os.fsync(fh.fileno())
        tail = b""
        if old_size:
            fh.seek(old_size)
            tail = fh.read()
        if tail:
            fh.seek(len(new_bytes))
            fh.write(tail)
            fh.flush()
            os.fsync(fh.fileno())
        fh.truncate(len(new_bytes) + len(tail))
    return len(new_bytes) + len(tail)


def redact_file(path, redactor, backup_dir, dry_run=False):
    name = os.path.basename(path)
    old_size = os.path.getsize(path)
    with open(path, "rb") as fh:
        raw = fh.read()
    text = raw.decode("utf-8", errors="surrogateescape")
    changed, per_rule = count_hits(text, redactor)
    fixed_text = redactor.redact_text(text)
    new_bytes = fixed_text.encode("utf-8", errors="surrogateescape")
    result = {
        "file": name,
        "bytes_before": old_size,
        "bytes_after": len(new_bytes),
        "lines_changed": changed,
        "hits": dict(sorted(per_rule.items())),
        "action": "dry-run" if dry_run else "",
        "backup": "",
        "backup_sha256": "",
        "after_sha256": "",
        "mode": "",
    }
    if dry_run:
        return result

    os.makedirs(backup_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = os.path.join(backup_dir, "%s.%s.bak" % (name, stamp))
    shutil.copy2(path, backup_path)
    result["backup"] = backup_path
    result["backup_sha256"] = sha256_file(backup_path)

    temp_path = path + ".redacting.tmp"
    with open(temp_path, "wb") as fh:
        fh.write(new_bytes)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        os.replace(temp_path, path)
        result["mode"] = "atomic-replace"
        result["bytes_after"] = len(new_bytes)
    except PermissionError:
        # 运行中的日志被 append 句柄占着，replace 不行：原地重写 + 搬运尾部。
        result["mode"] = "in-place-rewrite"
        try:
            result["bytes_after"] = _write_in_place(path, new_bytes, old_size)
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                # intentional: 配置/旧值/可选依赖/清理失败按默认值处理
                pass
    result["after_sha256"] = sha256_file(path)
    with open(path, "rb") as fh:
        remain_text = fh.read().decode("utf-8", errors="surrogateescape")
    remain_changed, remain_rules = count_hits(remain_text, redactor)
    result["remaining_lines"] = remain_changed
    result["remaining_hits"] = dict(sorted(remain_rules.items()))
    return result


def find_logs(logs_dir):
    names = []
    for entry in sorted(os.listdir(logs_dir)):
        lower = entry.lower()
        if not lower.endswith(".log"):
            continue
        names.append(os.path.join(logs_dir, entry))
    return names


def main(argv=None):
    parser = argparse.ArgumentParser(description="生产日志备份 + 一次性脱敏（不打印密钥）")
    parser.add_argument("--logs-dir", default=os.environ.get("AMO_LOGS_DIR") or DEFAULT_LOGS_DIR)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", help="额外输出 JSON 摘要")
    args = parser.parse_args(argv)

    redactor = load_redactor()
    logs_dir = os.path.abspath(args.logs_dir)
    backup_dir = os.path.join(logs_dir, "backup")
    files = find_logs(logs_dir)
    if not files:
        print("[redact] 没找到日志文件: %s" % logs_dir)
        return 2
    results = []
    for path in files:
        result = redact_file(path, redactor, backup_dir, dry_run=args.dry_run)
        results.append(result)
        print("[redact] %s 命中行 %d，规则 %s"
              % (result["file"], result["lines_changed"], result["hits"]))
        if not args.dry_run:
            print("[redact]   备份: %s (sha256=%s)"
                  % (result["backup"], result["backup_sha256"]))
            print("[redact]   模式: %s；大小 %d -> %d bytes；脱敏后 sha256=%s"
                  % (result["mode"], result["bytes_before"], result["bytes_after"],
                     result["after_sha256"]))
            print("[redact]   复检剩余命中行: %d %s"
                  % (result.get("remaining_lines", 0), result.get("remaining_hits", {})))
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
