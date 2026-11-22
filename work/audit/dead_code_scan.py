# -*- coding: utf-8 -*-
"""Read-only dead-code scanner for the four AstrBot plugins (task-31).

Collects module import graph + top-level def/class/method references via AST.
No imports of the target code, no writes outside the JSON path passed as argv.
"""
import ast
import json
import os
import sys

ROOT = r"D:\dafeiyu\astrbot\data\plugins"
PLUGINS = {
    "forever": os.path.join(ROOT, "Astrbot_amoupup_forever"),
    "watch": os.path.join(ROOT, "Astrbot_amoupup_watch"),
    "data": os.path.join(ROOT, "Astrbot_amoupup_data"),
    "photo": os.path.join(ROOT, "Astrbot_amoupup_photo"),
}
EXCLUDE_DIRS = {".git", "__pycache__", ".mypy_cache", ".pytest_cache", "work",
                "tests", ".test-tmp", "photo_cache", "outfit_refs",
                "persona_eval", "persona_packs", "templates", "views", "docs",
                "dist-publish", ".github"}
KEEP_MODULES = {"main", "__init__"}


def is_test_file(fn):
    return fn.startswith("test_") or fn.endswith("_test.py") or fn.startswith("conftest")


def iter_prod_files(base):
    for dirpath, dirnames, filenames in os.walk(base):
        rel = os.path.relpath(dirpath, base)
        parts = [] if rel == "." else rel.split(os.sep)
        if "work" in parts or "tests" in parts:
            continue
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            if fn.endswith(".py") and not is_test_file(fn):
                yield os.path.join(dirpath, fn)


def iter_aux_files(plugin, base):
    tests = os.path.join(base, "tests")
    work = os.path.join(base, "work")
    if os.path.isdir(tests):
        for dirpath, dirnames, filenames in os.walk(tests):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".test-tmp")]
            for fn in filenames:
                if fn.endswith(".py"):
                    yield "test", os.path.join(dirpath, fn)
    if os.path.isdir(work):
        for dirpath, dirnames, filenames in os.walk(work):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".test-tmp")]
            for fn in filenames:
                if fn.endswith(".py"):
                    yield "work", os.path.join(dirpath, fn)
    if plugin != "forever":
        for fn in os.listdir(base):
            p = os.path.join(base, fn)
            if os.path.isfile(p) and fn.endswith(".py") and is_test_file(fn):
                yield "test", p


def dotted_of(plugin, path):
    base = PLUGINS[plugin]
    rel = os.path.relpath(path, base)
    rel = rel[:-3] if rel.endswith(".py") else rel
    rel = rel.replace(os.sep, ".")
    if rel.endswith(".__init__"):
        rel = rel[: -len(".__init__")]
    return rel


class Mod:
    __slots__ = ("plugin", "path", "key", "dotted", "basename", "tree", "defs",
                 "imports", "strings", "all_names", "kind")

    def __init__(self, plugin, path, dotted, tree, kind="prod"):
        self.plugin = plugin
        self.path = path
        self.dotted = dotted
        self.key = plugin + ":" + dotted
        self.basename = os.path.basename(path)[:-3]
        self.tree = tree
        self.defs = []
        self.imports = []
        self.strings = []
        self.all_names = set()
        self.kind = kind


def iter_defs(tree, mod):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            mod.defs.append({"name": node.name, "kind": "func", "line": node.lineno,
                             "decorated": bool(node.decorator_list), "class": ""})
        elif isinstance(node, ast.ClassDef):
            mod.defs.append({"name": node.name, "kind": "class", "line": node.lineno,
                             "decorated": bool(node.decorator_list), "class": ""})
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    mod.defs.append({"name": sub.name, "kind": "method", "line": sub.lineno,
                                     "decorated": bool(sub.decorator_list), "class": node.name})
                elif isinstance(sub, ast.ClassDef):
                    mod.defs.append({"name": sub.name, "kind": "class", "line": sub.lineno,
                                     "decorated": bool(sub.decorator_list), "class": node.name})
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id.isupper():
                    mod.defs.append({"name": tgt.id, "kind": "const", "line": node.lineno,
                                     "decorated": False, "class": ""})
                if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                    try:
                        vals = ast.literal_eval(node.value)
                        if isinstance(vals, (list, tuple)):
                            mod.all_names = set(x for x in vals if isinstance(x, str))
                    except Exception:  # cleanup: 兜底/降级，保持原行为
                        pass
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id.isupper():
            mod.defs.append({"name": node.target.id, "kind": "const", "line": node.lineno,
                             "decorated": False, "class": ""})


def collect_strings(tree, mod):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            mod.strings.append((node.value, getattr(node, "lineno", 0)))


def parse_mods():
    mods = {}
    for plugin, base in PLUGINS.items():
        for path in iter_prod_files(base):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    tree = ast.parse(fh.read())
            except Exception as exc:
                print("PARSE FAIL", path, exc, file=sys.stderr)
                continue
            m = Mod(plugin, path, dotted_of(plugin, path), tree)
            iter_defs(tree, m)
            collect_strings(tree, m)
            mods[m.key] = m
        for kind, path in iter_aux_files(plugin, base):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    tree = ast.parse(fh.read())
            except Exception:
                continue
            rel = os.path.relpath(path, base).replace(os.sep, "/")
            key = "%s:%s:%s" % (plugin, kind, rel)
            if key in mods:
                continue
            m = Mod(plugin, path, os.path.basename(path)[:-3], tree, kind=kind)
            m.key = key
            iter_defs(tree, m)
            collect_strings(tree, m)
            mods[key] = m
    return mods


def build_index(mods):
    by_key = {}   # plugin -> {dotted: key}
    by_base = {}  # plugin -> {basename: [key]}
    full = {}
    for key, m in mods.items():
        if m.kind != "prod":
            continue
        full[key] = m
        by_key.setdefault(m.plugin, {})[m.dotted] = key
        by_base.setdefault(m.plugin, {}).setdefault(m.basename, []).append(key)
    return by_key, by_base, full


def resolve(plugin, name, cur_dotted, by_key, by_base, full):
    """Resolve dotted import name relative to cur_dotted; returns module key."""
    if not name:
        return None
    dots = len(name) - len(name.lstrip("."))
    rest = name[dots:]
    if dots:
        cur = cur_dotted.split(".") if cur_dotted else []
        pkg = cur[:-1]
        up = dots - 1
        if up:
            pkg = pkg[: len(pkg) - up]
        cand = ".".join(pkg + ([rest] if rest else []))
        table = by_key.get(plugin, {})
        if cand in table:
            return table[cand]
        # flat root fallback
        last = rest.split(".")[-1] if rest else ""
        if last:
            hits = by_base.get(plugin, {}).get(last, [])
            if len(hits) == 1:
                return hits[0]
        if not cand and not rest:
            return None
    table = by_key.get(plugin, {})
    if name in table:
        return table[name]
    # same-plugin unique basename fallback (dual import style)
    last = name.split(".")[-1]
    hits = by_base.get(plugin, {}).get(last, [])
    if len(hits) == 1:
        return hits[0]
    # cross-plugin unique exact / basename
    for p, t in by_key.items():
        if name in t:
            return t[name]
    cross = []
    for p, bb in by_base.items():
        if p == plugin:
            continue
        cross.extend(bb.get(last, []))
    if len(cross) == 1:
        return cross[0]
    return None


def collect_imports(m, by_key, by_base, full):
    out = []
    for node in ast.walk(m.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = resolve(m.plugin, alias.name, m.dotted, by_key, by_base, full)
                if target:
                    out.append((target, None, node.lineno, alias.asname or alias.name.split(".")[-1]))
        elif isinstance(node, ast.ImportFrom):
            level = node.level or 0
            modname = node.module or ""
            raw = "." * level + modname if (level or modname) else ""
            base_target = resolve(m.plugin, raw, m.dotted, by_key, by_base, full) if raw else None
            if base_target is None and level and not modname:
                cur = m.dotted.split(".") if m.dotted else []
                pkg = ".".join(cur[:-1])
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    cand = (pkg + "." + alias.name) if pkg else alias.name
                    hit = resolve(m.plugin, cand, m.dotted, by_key, by_base, full)
                    if hit:
                        out.append((hit, None, node.lineno, alias.asname or alias.name))
            elif base_target:
                for alias in node.names:
                    if alias.name == "*":
                        out.append((base_target, "*", node.lineno, "*"))
                        continue
                    sub = resolve(m.plugin, base_target.split(":")[-1] + "." + alias.name,
                                  m.dotted, by_key, by_base, full)
                    # base_target is full key; resolve submodule via dotted
                    base_dotted = full[base_target].dotted if base_target in full else ""
                    sub = resolve(m.plugin, base_dotted + "." + alias.name, m.dotted,
                                  by_key, by_base, full)
                    if sub:
                        out.append((sub, None, node.lineno, alias.asname or alias.name))
                        out.append((base_target, None, node.lineno, "__pkg__"))
                    else:
                        out.append((base_target, alias.name, node.lineno, alias.asname or alias.name))
                out.append((base_target, None, node.lineno, "__pkg__"))
    m.imports = out
    return out


def expr_module(node, alias_mod, alias_sym):
    if isinstance(node, ast.Name):
        if node.id in alias_mod:
            return alias_mod[node.id]
        if node.id in alias_sym:
            target, sym = alias_sym[node.id]
            return target if sym is None else None
        return None
    if isinstance(node, ast.Attribute):
        base = expr_module(node.value, alias_mod, alias_sym)
        return (base + "." + node.attr) if base else None
    return None


def walk_refs(m, full):
    refs = []
    alias_mod = {}
    alias_sym = {}
    for target, sym, line, bind in m.imports:
        if bind == "__pkg__":
            refs.append((target, None, line, "import"))
            continue
        if sym is None:
            alias_mod[bind] = target
        elif sym == "*":
            continue
        else:
            alias_sym[bind] = (target, sym)
        refs.append((target, sym, line, "import"))
    for node in ast.walk(m.tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in alias_sym:
                target, sym = alias_sym[node.id]
                refs.append((target, sym, node.lineno, "import_use"))
            elif node.id in alias_mod:
                refs.append((alias_mod[node.id], None, node.lineno, "module_use"))
            else:
                refs.append((m.key, node.id, node.lineno, "local"))
        elif isinstance(node, ast.Attribute):
            base = expr_module(node.value, alias_mod, alias_sym)
            if base and base in full:
                refs.append((base, node.attr, node.lineno, "module_attr"))
            elif isinstance(node.value, ast.Name) and node.value.id in ("self", "cls"):
                refs.append(("__method__", node.attr, node.lineno, "self_attr"))
            elif isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) \
                    and node.value.func.id == "super":
                refs.append(("__method__", node.attr, node.lineno, "super_attr"))
            else:
                refs.append(("__attr__", node.attr, node.lineno, "attr"))
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in ("getattr", "hasattr", "setattr") \
                    and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                    and isinstance(node.args[1].value, str):
                refs.append(("__dynattr__", node.args[1].value, node.lineno, "dynattr"))
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Call) \
                and isinstance(node.value.func, ast.Name) and node.value.func.id == "globals":
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                refs.append(("__dynattr__", sl.value, node.lineno, "globals"))
    return refs


def scan():
    mods = parse_mods()
    by_key, by_base, full = build_index(mods)
    for m in mods.values():
        collect_imports(m, by_key, by_base, full)
    refs = {m.key: walk_refs(m, full) for m in mods.values()}
    sym_refs = {}
    attr_refs = {}
    dyn_refs = {}
    importers = {}
    string_module_refs = {}
    dotted_map = {}
    base_map = {}
    for key, m in mods.items():
        if m.kind == "prod" and m.plugin not in dotted_map:
            dotted_map[m.plugin] = {m2.dotted: k2 for k2, m2 in mods.items()
                                    if m2.kind == "prod" and m2.plugin == m.plugin}
    for key, m in mods.items():
        if m.kind == "prod":
            base_map.setdefault(m.plugin, {}).setdefault(m.basename, []).append(key)
    def match_string_module(val, src):
        table = dotted_map.get(src.plugin, {})
        if val in table:
            return table[val]
        bm = base_map.get(src.plugin, {})
        if val in bm and len(bm[val]) == 1:
            return bm[val][0]
        last = val.split(".")[-1]
        hits = bm.get(last, [])
        if len(hits) == 1:
            return hits[0]
        for p, tab in dotted_map.items():
            if val in tab:
                return tab[val]
        return None
    for mkey, rows in refs.items():
        src = mods[mkey]
        for target, sym, line, kind in rows:
            if target in full and target != mkey:
                importers.setdefault(target, set()).add(mkey)
            if target == "__attr__" or target == "__method__":
                attr_refs.setdefault(sym, []).append((mkey, line, src.kind))
            elif target == "__dynattr__":
                dyn_refs.setdefault(sym, []).append((mkey, line, src.kind))
            elif sym is None:
                importers.setdefault(target, set()).add(mkey)
            else:
                sym_refs.setdefault((target, sym), []).append((mkey, line, kind, src.kind))
        for val, line in src.strings:
            hit = match_string_module(val, src)
            if hit:
                string_module_refs.setdefault(hit, []).append((mkey, line, src.kind))
    def_names = set()
    for m in mods.values():
        if m.kind == "prod":
            for d in m.defs:
                def_names.add(d["name"])
    str_refs = {}
    for m in mods.values():
        for val, line in m.strings:
            if val in def_names:
                str_refs.setdefault(val, []).append((m.key, line, m.kind))
    out = {"modules": [], "defs": []}
    for key, m in mods.items():
        if m.kind != "prod":
            continue
        imported = sorted(importers.get(key, []))
        out["modules"].append({
            "key": key, "plugin": m.plugin, "dotted": m.dotted, "path": m.path,
            "basename": m.basename, "def_count": len(m.defs),
            "imported_by_prod": [x for x in imported if mods[x].kind == "prod"],
            "imported_by_tests": [x for x in imported if mods[x].kind == "test"],
            "imported_by_work": [x for x in imported if mods[x].kind == "work"],
            "string_mentions": [(x[0], x[2]) for x in string_module_refs.get(key, [])][:8],
            "string_mentioned_prod": [x for x in string_module_refs.get(key, []) if x[2] == "prod"][:6],
            "all_names": sorted(m.all_names),
        })
    for key, m in mods.items():
        if m.kind != "prod":
            continue
        for d in m.defs:
            name = d["name"]
            srefs = sym_refs.get((key, name), [])
            arefs = attr_refs.get(name, [])
            drefs = dyn_refs.get(name, [])
            strs = str_refs.get(name, [])
            local = [r for r in srefs if r[0] == key]
            prod = [r for r in srefs if r[0] != key and r[3] == "prod"]
            tests = [r for r in srefs if r[0] != key and r[3] == "test"]
            work = [r for r in srefs if r[0] != key and r[3] == "work"]
            prod_attr = [r for r in arefs if r[2] == "prod"]
            test_attr = [r for r in arefs if r[2] == "test"]
            work_attr = [r for r in arefs if r[2] == "work"]
            dyn = [r for r in drefs if r[2] == "prod"]
            test_dyn = [r for r in drefs if r[2] == "test"]
            str_prod = [s for s in strs if s[2] == "prod" and s[0] != key]
            str_test = [s for s in strs if s[2] == "test"]
            str_work = [s for s in strs if s[2] == "work"]
            out["defs"].append({
                "module": key, "plugin": m.plugin, "file": m.path, "name": name,
                "kind": d["kind"], "class": d["class"], "line": d["line"],
                "decorated": d["decorated"], "exported": name in m.all_names,
                "local_refs": len(local),
                "prod_import_refs": len(prod), "prod_import_detail": prod[:6],
                "prod_attr_refs": len(prod_attr), "prod_attr_detail": prod_attr[:6],
                "prod_dyn_refs": len(dyn), "prod_str_refs": len(str_prod),
                "test_refs": len(tests) + len(test_attr) + len(test_dyn) + len(str_test),
                "test_ref_detail": (tests + test_attr)[:4],
                "work_refs": len(work) + len(work_attr) + len(str_work),
                "work_ref_detail": (work + work_attr)[:4],
            })
    return out


if __name__ == "__main__":
    data = scan()
    out_path = sys.argv[1] if len(sys.argv) > 1 else None
    text = json.dumps(data, ensure_ascii=False, indent=1)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("wrote", out_path, len(data["modules"]), "modules", len(data["defs"]), "defs")
    else:
        print(text)
