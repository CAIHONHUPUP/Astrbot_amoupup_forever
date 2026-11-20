# -*- coding: utf-8 -*-
"""测试辅助：把 index.html 与它外链的本地 CSS/JS 拼成「面板全源」。

Phase 10 把 views/dashboard 拆成 index.html + assets/*.css/js；
既有的 7 个静态 grep 测试统一改读这里，断言本身不变。
"""
from __future__ import annotations

import os
import re

DASHBOARD = ("views", "dashboard")
_LINK_RE = re.compile(r'<link\b[^>]*\brel="stylesheet"[^>]*\bhref="([^"]+)"[^>]*>', re.I)
_SCRIPT_RE = re.compile(r'<script\b[^>]*\bsrc="([^"]+)"[^>]*>\s*</script>', re.I)


def dashboard_dir(root):
    return os.path.join(root, *DASHBOARD)


def _local_asset(dash, ref):
    """只接受 page root 内的相对资源：拒绝 http(s)/协议相对/绝对路径/跳出 dashboard。"""
    if re.match(r"^(?:[a-z]+:)?//", ref, re.I) or ref.startswith(("/", "\\")):
        raise AssertionError("面板资源必须是相对路径: %s" % ref)
    path = os.path.normpath(os.path.join(dash, ref))
    if os.path.commonpath([path, os.path.normpath(dash)]) != os.path.normpath(dash):
        raise AssertionError("面板资源跳出了 views/dashboard: %s" % ref)
    return path


def _read_asset(dash, ref):
    with open(_local_asset(dash, ref), encoding="utf-8") as fh:
        return fh.read()


def panel_source(root):
    """按文档顺序把外链 CSS/JS 内联回 index.html，得到拆分前的「面板全源」。"""
    dash = dashboard_dir(root)
    with open(os.path.join(dash, "index.html"), encoding="utf-8") as fh:
        html = fh.read()
    html = _LINK_RE.sub(lambda m: "<style>\n%s\n</style>" % _read_asset(dash, m.group(1)), html)
    html = _SCRIPT_RE.sub(lambda m: "<script>\n%s\n</script>" % _read_asset(dash, m.group(1)), html)
    return html
