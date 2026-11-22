# -*- coding: utf-8 -*-
"""静态扫描：生产代码里硬编码的「样例句」是 docstring/注释，还是参与行为判断。

输出 work/audit/holdout/static_scan.json / static_scan.txt
只读，不 import 生产模块（纯 ast/tokenize）。
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import tokenize

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
HERE = os.path.dirname(os.path.abspath(__file__))
SKIP_DIRS = {"tests", "work", "__pycache__", ".git", "persona_eval", "docs",
             "templates", "outfit_refs", "photo_cache", ".mypy_cache", ".pytest_cache"}
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
SENT_PUNCT = "。！？，、；：…"
NAMED = ("妈，我地铁上", "就是985", "你美丽的老婆", "4072", "649",
         "柳如烟", "方圆", "季博达", "收到你后来", "你们男人", "晾这儿")
GUARD_HINT = re.compile(r"(guard|pipeline|consistency|persona|addressee|question|tone|"
                        r"place|purchase|memory|share|message|proactive|prompt_registry)")


def is_docstring(node, docstring_ids):
    return id(node) in docstring_ids


def collect_py():
    files = []
    for base, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in names:
            if name.endswith(".py") and not name.startswith("test_"):
                files.append(os.path.join(base, name))
    return sorted(files)


def docstring_nodes(tree):
    ids = set()

    def add(node):
        if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) \
                and isinstance(node.body[0].value.value, str):
            ids.add(id(node.body[0].value))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node)
    return ids


def enclosing_container(node, parents):
    """返回（容器名, 容器类型）——用于判断字符串是输出池还是匹配池。"""
    cur = node
    while id(cur) in parents:
        parent = parents[id(cur)]
        if isinstance(parent, (ast.List, ast.Tuple, ast.Set)):
            name = None
            grand = parents.get(id(parent))
            if isinstance(grand, ast.Assign) and grand.targets and isinstance(grand.targets[0], ast.Name):
                name = grand.targets[0].id
            elif isinstance(grand, ast.AnnAssign) and isinstance(grand.target, ast.Name):
                name = grand.target.id
            return name, type(parent).__name__
        cur = parent
    return None, None


def classify_code_strings(path, tree, doc_ids):
    rows = []
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        value = node.value
        if is_docstring(node, doc_ids):
            continue
        if not CJK_RE.search(value):
            continue
        if len(value.strip()) < 4:
            continue
        container, ctype = enclosing_container(node, parents)
        # regex?
        is_regex = False
        parent = parents.get(id(node))
        if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute) \
                and parent.func.attr in ("compile", "search", "match", "sub", "findall", "fullmatch"):
            is_regex = True
        if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name) and parent.func.id == "compile":
            is_regex = True
        sentence_like = (len(value) >= 10 and any(ch in value for ch in SENT_PUNCT))
        rows.append({
            "node": node, "value": value, "lineno": getattr(node, "lineno", 0),
            "container": container, "ctype": ctype, "is_regex": is_regex,
            "sentence_like": sentence_like,
            "named": [name for name in NAMED if name in value],
        })
    return rows


def scan():
    files = collect_py()
    summary = {}
    named_hits = []
    suspicious = []
    totals = {"files": len(files), "code_strings_cjk": 0, "sentence_like": 0,
              "in_containers": 0, "regex_like": 0, "docstrings_cjk": 0, "comments_cjk": 0}
    for path in files:
        rel = os.path.relpath(path, ROOT)
        try:
            source = open(path, encoding="utf-8").read()
            tree = ast.parse(source)
        except Exception as exc:
            summary[rel] = {"error": repr(exc)[:120]}
            continue
        doc_ids = docstring_nodes(tree)
        # docstrings
        try:
            docs = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and is_docstring(n, doc_ids)
                    and isinstance(n.value, str) and CJK_RE.search(n.value)]
        except Exception:
            docs = []
        # comments
        comments = []
        try:
            for tok in tokenize.generate_tokens(io.StringIO(source).readline):
                if tok.type == tokenize.COMMENT and CJK_RE.search(tok.string):
                    comments.append({"line": tok.start[0], "text": tok.string[:300]})
        except Exception:
            pass
        rows = classify_code_strings(rel, tree, doc_ids)
        totals["code_strings_cjk"] += len(rows)
        totals["sentence_like"] += sum(1 for r in rows if r["sentence_like"])
        totals["in_containers"] += sum(1 for r in rows if r["container"])
        totals["regex_like"] += sum(1 for r in rows if r["is_regex"])
        totals["docstrings_cjk"] += len(docs)
        totals["comments_cjk"] += len(comments)
        file_rows = []
        for comment in comments:
            for name in NAMED:
                if name in comment["text"]:
                    named_hits.append({"file": rel, "line": comment["line"], "kind": "comment",
                                       "text": comment["text"].strip()[:240], "name": name})
        for doc in docs:
            for name in NAMED:
                if name in doc:
                    named_hits.append({"file": rel, "line": 0, "kind": "docstring",
                                       "text": doc.strip()[:240], "name": name})
        for row in rows:
            value = row["value"]
            named = row["named"]
            entry = {"file": rel, "line": row["lineno"], "value": value[:300],
                     "container": row["container"], "is_regex": row["is_regex"],
                     "sentence_like": row["sentence_like"], "named": named,
                     "kind": "regex" if row["is_regex"] else ("container" if row["container"] else "code")}
            for name in named:
                named_hits.append({"file": rel, "line": row["lineno"], "kind": entry["kind"],
                                   "text": value[:240], "name": name})
            # 可疑：参与行为的字符串里带具体样例句特征（数字/具体人名/整句）
            looks_sample = (row["sentence_like"] or any(ch.isdigit() for ch in value)
                            or any(name in value for name in ("柳如烟", "方圆", "季博达", "4072", "985", "649")))
            is_behavior = row["is_regex"] or row["container"] or GUARD_HINT.search(rel)
            if looks_sample and is_behavior and GUARD_HINT.search(rel):
                suspicious.append(entry)
                file_rows.append(entry)
        if file_rows:
            summary[rel] = {"strings": len(rows), "sentence_like": sum(1 for r in rows if r["sentence_like"]),
                            "suspicious": len(file_rows)}
    # 去重 named hits
    uniq = {}
    for hit in named_hits:
        key = (hit["file"], hit["line"], hit["kind"], hit["name"], hit["text"][:60])
        uniq[key] = hit
    named_hits = list(uniq.values())
    report = {"totals": totals, "files_with_suspicious": summary,
              "named_sample_hits": named_hits, "suspicious_strings": suspicious}
    with open(os.path.join(HERE, "static_scan.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
    out = io.StringIO()
    out.write("totals %s\n" % json.dumps(totals, ensure_ascii=False))
    out.write("\n== 指定样例句命中（按 位置/类型）==\n")
    for hit in sorted(named_hits, key=lambda h: (h["name"], h["file"], h["line"])):
        out.write("%-10s %-42s:%-5s %-10s %s\n" % (hit["name"], hit["file"], hit["line"],
                                                   hit["kind"], hit["text"].replace("\n", " ")[:180]))
    out.write("\n== 可疑：生产代码里的整句/带例子数字字符串（参与行为）==\n")
    for item in suspicious:
        out.write("%-42s:%-5s %-10s named=%-12s %s\n" % (
            item["file"], item["line"], item["kind"], ",".join(item["named"]),
            item["value"].replace("\n", " ")[:200]))
    open(os.path.join(HERE, "static_scan.txt"), "w", encoding="utf-8").write(out.getvalue())
    print(json.dumps(totals, ensure_ascii=False))
    print("wrote static_scan.json / static_scan.txt")


if __name__ == "__main__":
    scan()