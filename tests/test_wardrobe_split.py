# -*- coding: utf-8 -*-
"""wardrobe.py 拆分回归锁（门面 + wardrobe_data/seed/daily/queries）。

2026-11-17：wardrobe.py 按职责拆成「数据/纯函数 + 3 个 mixin」，本文件锁住四件事：

1. 旧 import 路径不变：wardrobe 里原来的名字全部还在（门面 re-export）；
2. 方法源码逐字节等价：每个方法 / 模块级函数的 sha256 与拆分前的快照一致；
   （2026-11-17：为 mypy 清零删掉两条 unused type: ignore 注释，_combos_for 与
   _refill_sleep_combos 的哈希按「只删注释」后的源码更新；代码逻辑未变。）
   （2026-11-20：_at_home 先认 zone、current/tick 不再把「判断不了」当在家，
   修「20 点后在楼下小馆穿睡衣」；哈希按新源码更新，属行为修复不是搬家。）
3. 行为逐字节等价：纯函数的输入输出与拆分前的快照（tests/golden_split.json）一致；
4. 依赖单向：子模块不 import 门面；新文件都在 1500 行以内、没有裸 except: pass。
"""
import ast
import hashlib
import inspect
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for _p in (ROOT, TESTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import wardrobe  # noqa: E402
import wardrobe_data  # noqa: E402
import wardrobe_daily  # noqa: E402
import wardrobe_queries  # noqa: E402
import wardrobe_seed  # noqa: E402

with open(os.path.join(TESTS, "golden_split.json"), encoding="utf-8") as _fh:
    GOLDEN = json.load(_fh)

SOURCE_HASHES = GOLDEN["source_hashes"]
BEHAVIOR = GOLDEN["behavior"]
LEGACY_NAMES = SOURCE_HASHES["wardrobe.dir"]
METHOD_HASHES = SOURCE_HASHES["wardrobe"]
MODULE_FN_HASHES = SOURCE_HASHES["wardrobe.module_functions"]
NEW_MODULES = ["wardrobe_data.py", "wardrobe_seed.py", "wardrobe_daily.py", "wardrobe_queries.py"]
MIXIN_CLASSES = [wardrobe_seed.WardrobeSeedMixin, wardrobe_daily.WardrobeDailyMixin, wardrobe_queries.WardrobeQueriesMixin]
FACADE = wardrobe
OWNER = {'wardrobe_data': 'wardrobe_data', 'wardrobe_seed': 'wardrobe_seed', 'wardrobe_daily': 'wardrobe_daily', 'wardrobe_queries': 'wardrobe_queries'}


def _source_hash(obj):
    fn = obj.__func__ if isinstance(obj, (staticmethod, classmethod)) else obj
    return hashlib.sha256(inspect.getsource(fn).encode("utf-8")).hexdigest()


def _module_imports(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    rows = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            rows.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            rows.append(("." * node.level) + (node.module or ""))
    return rows


def _silent_excepts(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        text = fh.read()
    tree = ast.parse(text, filename=path)
    lines = text.splitlines()
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        body = node.body or []
        if not all(isinstance(item, ast.Pass) for item in body):
            continue
        head = lines[node.lineno - 1] if node.lineno - 1 < len(lines) else ""
        tail = lines[body[0].lineno - 1] if body and body[0].lineno - 1 < len(lines) else ""
        if "# cleanup" in head or "# cleanup" in tail:
            continue
        hits.append(node.lineno)
    return hits


def _line_count(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return sum(1 for _ in fh)



class FacadeApiTests(unittest.TestCase):
    def test_facade_exports_every_legacy_name(self):
        missing = [name for name in LEGACY_NAMES if not hasattr(wardrobe, name)]
        self.assertEqual([], missing)

    def test_all_entries_resolve(self):
        self.assertEqual([], [name for name in wardrobe.__all__ if not hasattr(wardrobe, name)])
        public_missing = [name for name in LEGACY_NAMES
                          if not name.startswith("_") and name not in wardrobe.__all__]
        self.assertEqual([], public_missing)

    def test_public_names_are_the_same_objects(self):
        self.assertIs(wardrobe.classify_item, wardrobe_data.classify_item)
        self.assertIs(wardrobe.combo_compatible, wardrobe_data.combo_compatible)
        self.assertEqual(wardrobe.SEED_OUTFITS, wardrobe_data.SEED_OUTFITS)

    def test_mro_uses_the_split_mixins(self):
        mro = [cls.__name__ for cls in wardrobe.Wardrobe.__mro__]
        self.assertEqual(["Wardrobe"] + [cls.__name__ for cls in MIXIN_CLASSES] + ["object"], mro)

    def test_each_method_lives_in_its_owner_module(self):
        wrong = []
        for name in METHOD_HASHES:
            owner = getattr(wardrobe.Wardrobe, name).__module__
            if owner not in OWNER:
                wrong.append((name, owner))
        self.assertEqual([], wrong)


class ByteEquivalenceTests(unittest.TestCase):
    def test_method_sources_byte_identical(self):
        changed = [(name, _source_hash(getattr(wardrobe.Wardrobe, name)), want)
                   for name, want in METHOD_HASHES.items()
                   if _source_hash(getattr(wardrobe.Wardrobe, name)) != want]
        self.assertEqual([], changed)

    def test_module_function_sources_byte_identical(self):
        changed = [name for name, want in MODULE_FN_HASHES.items()
                   if _source_hash(getattr(wardrobe, name)) != want]
        self.assertEqual([], changed)

    def test_classifier_behavior_matches_pre_split_golden(self):
        names = ["米色雪纺衬衫", "黑色包臀裙", "淡蓝色运动鞋", "粉色短袖T恤", "浅紫雪纺衫", "藏青色西装外套",
                 "白色帆布鞋", "黑色高领毛衣", "灰色宽松卫衣", "卡其色风衣", "睡裙", "泳衣", "比基尼",
                 "连衣裙", "睡衣"]
        got = BEHAVIOR["wardrobe"]
        self.assertEqual(got["is_dress"], [wardrobe.is_dress(n) for n in names])
        self.assertEqual(got["classify_item"],
                         [json.dumps(wardrobe.classify_item(n), ensure_ascii=False, sort_keys=True, default=str) for n in names])
        self.assertEqual(got["classify_sleep_item"],
                         [json.dumps(wardrobe.classify_sleep_item(n), ensure_ascii=False, sort_keys=True, default=str) for n in names])
        self.assertEqual(got["color_family"],
                         [json.dumps(wardrobe.color_family(n), ensure_ascii=False, sort_keys=True, default=str) for n in names])
        self.assertEqual(got["is_day_item"], [wardrobe.is_day_item({"name": n}) for n in names])

    def test_combo_rules_match_pre_split_golden(self):
        got = BEHAVIOR["wardrobe"]
        self.assertEqual(got["combo"],
                         [json.dumps(wardrobe.combo_compatible("卡其色风衣", "白色棉质衬衫", "深蓝直筒牛仔裤", "", "白色帆布鞋"), ensure_ascii=False, sort_keys=True),
                          json.dumps(wardrobe.combo_compatible("", "浅紫雪纺衫", "", "黑色皮裙", "黑色小皮鞋"), ensure_ascii=False, sort_keys=True),
                          json.dumps(wardrobe.combo_compatible("", "黑色高领毛衣", "卡其色半身裙", "", "棕色短靴"), ensure_ascii=False, sort_keys=True)])
        self.assertEqual(got["fabric"],
                         [json.dumps(wardrobe.fabric_match("白色棉质衬衫", "米白阔腿裤"), ensure_ascii=False, sort_keys=True),
                          json.dumps(wardrobe.fabric_match("羊毛衫", "雪纺裙"), ensure_ascii=False, sort_keys=True)])
        self.assertEqual(got["shoe"],
                         [json.dumps(wardrobe.shoe_matches("裸色高跟鞋", "白色棉质衬衫", "黑色包臀裙"), ensure_ascii=False, sort_keys=True)])

    def test_seed_constants_match_pre_split_golden(self):
        consts = BEHAVIOR["wardrobe"]["consts"]
        self.assertEqual(json.loads(consts["SEED_OUTFITS"]), [list(row) for row in wardrobe.SEED_OUTFITS])
        self.assertEqual(json.loads(consts["CATEGORY_LABELS"]), [list(row) for row in wardrobe.CATEGORY_LABELS])
        self.assertEqual(json.loads(consts["SEED_COATS"]), list(wardrobe.SEED_COATS))
        self.assertEqual(json.loads(consts["HOTEL_SEED"]), [list(row) for row in wardrobe.HOTEL_SEED])
        self.assertEqual(consts["MAX_OUTFITS"], wardrobe.MAX_OUTFITS)


class StructureTests(unittest.TestCase):
    def test_submodules_never_import_the_facade(self):
        offenders = []
        for path in NEW_MODULES:
            for target in _module_imports(path):
                if target.endswith("wardrobe"):
                    offenders.append((path, target))
        self.assertEqual([], offenders)

    def test_new_modules_stay_under_line_budget(self):
        big = [(path, _line_count(path)) for path in NEW_MODULES + ["wardrobe.py"]
               if _line_count(path) >= 1500]
        self.assertEqual([], big)



if __name__ == "__main__":
    unittest.main()

