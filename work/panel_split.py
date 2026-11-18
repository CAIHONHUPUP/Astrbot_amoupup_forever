# -*- coding: utf-8 -*-
"""把 views/dashboard/index.html 拆成 index.html + assets/{panel.css,api-client.js,panel.js,panel-views.js}。

一次性构建脚本：源文件用备份里的原版 index.html（3 个 <style> + 1 个内联 <script>）。
渲染零变化的依据：
- CSS 三块按原顺序原样导出，只在分段处加注释（<style id=...> 标记以注释保留，供既有断言定位）；
- JS 按声明边界原样搬移，不重新缩进（模板字符串里的空白不受影响）；
- 页体只做 HTML 规范允许的空白折叠：块级标签之间的纯空白节点删除，其余纯空白折叠为一个空格，
  <pre>/<textarea> 内原样保留；最后在标签边界按 200 列换行（换行只落在纯空白节点处）。
自检：标签序列、非空白文本、<pre> 文本、原 JS 每个非空行都还在。
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH = os.path.join(ROOT, "views", "dashboard")
ASSETS = os.path.join(DASH, "assets")
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DASH, "index.html")

WRAP = 200
BLOCK = {
    "div", "section", "header", "footer", "nav", "main", "form", "table",
    "thead", "tbody", "tfoot", "tr", "td", "th", "ul", "ol", "li", "p",
    "h1", "h2", "h3", "h4", "h5", "h6", "article", "aside", "figure",
    "figcaption", "details", "summary", "dl", "dt", "dd", "blockquote",
    "pre", "hr",
}
API_EXPORT = """window.PanelApi = {
  PLUGIN_NAME: PLUGIN_NAME,
  officialBridge: officialBridge,
  createInlineBridge: createInlineBridge,
  inlineBridge: inlineBridge,
  bridge: bridge,
  actionTimeout: actionTimeout,
  withTimeout: withTimeout,
  routeCandidates: routeCandidates,
  bridgeCall: bridgeCall,
  httpCall: httpCall,
  call: call,
  safeDetail: safeDetail,
  friendlyError: friendlyError,
  toast: toast,
  normalize: normalize,
  WRITE_ACTIONS: WRITE_ACTIONS,
};
"""
LEGACY_EXPORT = "".join(
    "window.%s = %s;\n" % (name, name)
    for name in ("PLUGIN_NAME", "WRITE_ACTIONS", "call", "httpCall", "bridgeCall",
                 "routeCandidates", "withTimeout", "actionTimeout", "safeDetail",
                 "friendlyError", "toast", "normalize")
)


def read_lines(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read().replace("\r\n", "\n").replace("\r", "\n")
    return text.splitlines(keepends=True)


def find_line(lines, pred, start=0):
    for i in range(start, len(lines)):
        if pred(lines[i].strip()):
            return i
    raise SystemExit("找不到目标行: %r" % pred)


def locate(lines):
    at = {}
    at["style1_open"] = find_line(lines, lambda s: s == "<style>")
    at["style1_close"] = find_line(lines, lambda s: s == "</style>", at["style1_open"])
    at["sky_open"] = find_line(lines, lambda s: 'id="amo-skyblue-theme"' in s, at["style1_close"])
    at["sky_close"] = find_line(lines, lambda s: s == "</style>", at["sky_open"])
    at["ward_open"] = find_line(lines, lambda s: 'id="amo-wardrobe-theme"' in s, at["sky_close"])
    at["ward_close"] = find_line(lines, lambda s: s == "</style>", at["ward_open"])
    at["head_close"] = find_line(lines, lambda s: s == "</head>", at["ward_close"])
    at["body_open"] = find_line(lines, lambda s: s == "<body>", at["head_close"])
    at["script_open"] = find_line(lines, lambda s: s == "<script>", at["body_open"])
    at["script_close"] = find_line(lines, lambda s: s == "</script>", at["script_open"])
    at["body_close"] = find_line(lines, lambda s: s == "</body>", at["script_close"])
    return at


def build_css(css1, css2, css3):
    return (
        "/* 面板样式：从 index.html 的三个内联 <style> 按原顺序抽出，选择器与顺序保持不变。 */\n"
        + "/* ===== 基础 / 组件（原第 1 个 <style>） ===== */\n"
        + css1.strip("\n") + "\n\n"
        + '/* ===== <style id="amo-skyblue-theme"> ===== */\n'
        + css2.strip("\n") + "\n\n"
        + '/* ===== <style id="amo-wardrobe-theme"> ===== */\n'
        + css3.strip("\n") + "\n"
    )


def split_scripts(js):
    """按声明边界切成 api-client / 核心页 / 展示页三份，原样保留缩进。"""
    def line_start(index):
        return js.rfind("\n", 0, index) + 1

    i_apply = line_start(js.index("function applyTheme(theme, persist)"))
    i_api = line_start(js.index("function officialBridge()"))
    i_load = line_start(js.index("async function load() {"))
    assert i_apply < i_api < i_load
    header, apply_block, api_block, panel_rest = js[:i_apply], js[i_apply:i_api], js[i_api:i_load], js[i_load:]

    api_decl, kept = [], []
    for line in header.splitlines(keepends=True):
        if line.startswith("    const PLUGIN_NAME") or line.startswith("    let toastTimer"):
            api_decl.append(line)
        else:
            kept.append(line)
    assert len(api_decl) == 2, api_decl

    api_js = (
        "/* 面板 API 客户端：从 index.html 内联脚本抽出（桥接 / HTTP 兜底 / 错误文案 / 通知）。 */\n"
        + "".join(api_decl) + "\n" + api_block
        + "\n/* ---- 对外出口：新代码用 window.PanelApi，旧全局名保持不变（避免改调用点） ---- */\n"
        + API_EXPORT + LEGACY_EXPORT
    )
    panel_js = (
        "/* 面板渲染与交互：从 index.html 内联脚本抽出；通信层在 assets/api-client.js（window.PanelApi）。 */\n"
        + "".join(kept) + apply_block + panel_rest
    )
    i_photo = panel_js.index("    let photoItems = [];")
    panel_views = (
        "/* 面板展示页：照片 / 设置 / 用户 / 关系图谱；通信层在 assets/api-client.js。 */\n"
        + panel_js[i_photo:]
    )
    return api_js, panel_js[:i_photo], panel_views


def verify_scripts(js, api_js, panel_core, panel_views):
    orig = Counter(line for line in js.splitlines() if line.strip())
    new_all = Counter(
        line for line in (api_js + panel_core + panel_views).splitlines() if line.strip()
    )
    missing = orig - new_all
    assert not missing, "JS 丢行: %r" % list(missing)[:5]


def tokenize(fragment):
    """返回 [(kind, text)]；kind 为 tag/text。注释按 tag 处理。"""
    tokens = []
    i, n = 0, len(fragment)
    while i < n:
        if fragment.startswith("<!--", i):
            j = fragment.find("-->", i + 4)
            if j < 0:
                raise SystemExit("注释未闭合")
            tokens.append(("tag", fragment[i:j + 3]))
            i = j + 3
        elif fragment[i] == "<":
            j = i + 1
            quote = None
            while j < n:
                ch = fragment[j]
                if quote:
                    if ch == quote:
                        quote = None
                elif ch in ("'", '"'):
                    quote = ch
                elif ch == ">":
                    break
                j += 1
            if j >= n:
                raise SystemExit("标签未闭合: %r" % fragment[i:i + 60])
            tokens.append(("tag", fragment[i:j + 1]))
            i = j + 1
        else:
            j = fragment.find("<", i)
            if j < 0:
                j = n
            tokens.append(("text", fragment[i:j]))
            i = j
    return tokens


def tag_name(tag):
    m = re.match(r"</?\s*([A-Za-z][A-Za-z0-9-]*)", tag)
    return m.group(1).lower() if m else ""


def is_closing(tag):
    return tag.startswith("</")


def analyze(fragment):
    """返回 (tags, nonws_texts, pre_texts, drops)；drops 只允许出现在块级标签之间。"""
    tags, nonws, pre_texts, drops = [], [], [], []
    pre_depth = 0
    pending_ws = None
    prev_tag = None
    for kind, text in tokenize(fragment):
        if kind == "tag":
            if pending_ws is not None:
                prev_block = prev_tag is not None and tag_name(prev_tag) in BLOCK
                if prev_block and tag_name(text) in BLOCK:
                    drops.append((prev_tag, text))
                pending_ws = None
            tags.append(text)
            prev_tag = text
            if tag_name(text) == "pre":
                pre_depth = pre_depth + (-1 if is_closing(text) else 1)
        else:
            if pre_depth > 0:
                pre_texts.append(text)
                continue
            collapsed = re.sub(r"[ \t\r\n\f]+", " ", text)
            if collapsed.strip() == "":
                pending_ws = True
            else:
                nonws.append(collapsed)
    return tags, nonws, pre_texts, drops


def minify(fragment):
    """按 HTML 空白规则压缩，再按 200 列换行。"""
    pieces = []
    pre_depth = 0
    prev_tag = None
    pending_ws = False
    for kind, text in tokenize(fragment):
        if kind == "tag":
            sep, br = "", False
            if pending_ws:
                if prev_tag is not None and tag_name(prev_tag) in BLOCK and tag_name(text) in BLOCK:
                    sep, br = "", True
                else:
                    sep, br = " ", True
                pending_ws = False
            pieces.append((sep, text, br))
            prev_tag = text
            if tag_name(text) == "pre":
                pre_depth = pre_depth + (-1 if is_closing(text) else 1)
        else:
            if pre_depth > 0:
                pieces.append(("", text, False))
                continue
            collapsed = re.sub(r"[ \t\r\n\f]+", " ", text)
            if collapsed.strip() == "":
                pending_ws = True
            else:
                pieces.append(("", collapsed, False))

    lines, line = [], ""
    for sep, tok, br in pieces:
        if line and br and len(line) + len(sep) + len(tok) > WRAP:
            lines.append(line)
            line = tok
        else:
            line += sep + tok
    if line:
        lines.append(line)
    return "\n".join(lines) + "\n"


def verify_body(body, minified):
    old_tags, old_nonws, old_pre, _old_drops = analyze(body)
    new_tags, new_nonws, new_pre, new_drops = analyze(minified)
    assert old_tags == new_tags, "标签序列不一致"
    assert old_nonws == new_nonws, "文本内容不一致"
    assert old_pre == new_pre, "pre 内容不一致"
    assert all(tag_name(a) in BLOCK and tag_name(b) in BLOCK for a, b in new_drops), "非块级空白删除"


def build_index(lines, at, minified):
    return (
        "".join(lines[:at["style1_open"]])
        + '  <link rel="stylesheet" href="assets/panel.css" />\n'
        + lines[at["head_close"]]
        + lines[at["body_open"]]
        + minified
        + '  <script src="assets/api-client.js"></script>\n'
        + '  <script src="assets/panel.js"></script>\n'
        + '  <script src="assets/panel-views.js"></script>\n'
        + "".join(lines[at["script_close"] + 1:at["body_close"]])
        + lines[at["body_close"]]
        + lines[at["body_close"] + 1]
    )


def write_outputs(index_out, css_out, api_js, panel_core, panel_views):
    os.makedirs(ASSETS, exist_ok=True)
    outputs = (
        (os.path.join(DASH, "index.html"), index_out),
        (os.path.join(ASSETS, "panel.css"), css_out),
        (os.path.join(ASSETS, "api-client.js"), api_js),
        (os.path.join(ASSETS, "panel.js"), panel_core),
        (os.path.join(ASSETS, "panel-views.js"), panel_views),
    )
    for path, text in outputs:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print("%-28s %5d lines %8d bytes" % (
            os.path.relpath(path, ROOT).replace("\\", "/"),
            len(text.splitlines()), len(text.encode("utf-8")),
        ))


def main():
    lines = read_lines(SRC)
    at = locate(lines)
    css1 = "".join(lines[at["style1_open"] + 1:at["style1_close"]])
    css2 = "".join(lines[at["sky_open"] + 1:at["sky_close"]])
    css3 = "".join(lines[at["ward_open"] + 1:at["ward_close"]])
    js = "".join(lines[at["script_open"] + 1:at["script_close"]])
    body = "".join(lines[at["body_open"] + 1:at["script_open"]])

    api_js, panel_core, panel_views = split_scripts(js)
    verify_scripts(js, api_js, panel_core, panel_views)
    minified = minify(body)
    verify_body(body, minified)
    write_outputs(build_index(lines, at, minified), build_css(css1, css2, css3),
                  api_js, panel_core, panel_views)


if __name__ == "__main__":
    main()
