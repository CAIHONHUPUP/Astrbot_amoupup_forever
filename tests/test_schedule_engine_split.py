# -*- coding: utf-8 -*-
"""schedule_engine.py 拆分回归锁（门面 + schedule_plan/skeleton/slots/diary）。

2026-11-17：schedule_engine.py 按职责拆成「数据/纯函数 + 3 个 mixin」，本文件锁住四件事：

1. 旧 import 路径不变：schedule_engine 里原来的名字全部还在（门面 re-export）；
2. 方法源码逐字节等价：每个方法 / 模块级函数的 sha256 与拆分前的快照一致；
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

import schedule_diary  # noqa: E402
import schedule_engine  # noqa: E402
import schedule_plan  # noqa: E402
import schedule_skeleton  # noqa: E402
import schedule_slots  # noqa: E402

with open(os.path.join(TESTS, "golden_split.json"), encoding="utf-8") as _fh:
    GOLDEN = json.load(_fh)

SOURCE_HASHES = GOLDEN["source_hashes"]
BEHAVIOR = GOLDEN["behavior"]
LEGACY_NAMES = SOURCE_HASHES["schedule_engine.dir"]
METHOD_HASHES = SOURCE_HASHES["schedule_engine"]
MODULE_FN_HASHES = SOURCE_HASHES["schedule_engine.module_functions"]
NEW_MODULES = ["schedule_plan.py", "schedule_skeleton.py", "schedule_slots.py", "schedule_diary.py"]
MIXIN_CLASSES = [schedule_skeleton.ScheduleSkeletonMixin, schedule_slots.ScheduleSlotsMixin, schedule_diary.ScheduleDiaryMixin]
FACADE = schedule_engine
OWNER = {'schedule_plan': 'schedule_plan', 'schedule_skeleton': 'schedule_skeleton', 'schedule_slots': 'schedule_slots', 'schedule_diary': 'schedule_diary'}


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
        missing = [name for name in LEGACY_NAMES if not hasattr(schedule_engine, name)]
        self.assertEqual([], missing)

    def test_all_entries_resolve(self):
        self.assertEqual([], [name for name in schedule_engine.__all__ if not hasattr(schedule_engine, name)])
        public_missing = [name for name in LEGACY_NAMES
                          if not name.startswith("_") and name not in schedule_engine.__all__]
        self.assertEqual([], public_missing)

    def test_public_names_are_the_same_objects(self):
        self.assertIs(schedule_engine.jit_plan, schedule_plan.jit_plan)
        self.assertIs(schedule_engine.diary_presence_rules, schedule_plan.diary_presence_rules)
        self.assertIs(schedule_engine.fmt_hhmm, schedule_plan.fmt_hhmm)
        self.assertEqual(schedule_engine.MOOD_KEYS, schedule_plan.MOOD_KEYS)

    def test_mro_uses_the_split_mixins(self):
        mro = [cls.__name__ for cls in schedule_engine.ScheduleEngine.__mro__]
        self.assertEqual(["ScheduleEngine"] + [cls.__name__ for cls in MIXIN_CLASSES] + ["object"], mro)

    def test_each_method_lives_in_its_owner_module(self):
        wrong = []
        for name in METHOD_HASHES:
            owner = getattr(schedule_engine.ScheduleEngine, name).__module__
            if owner not in OWNER:
                wrong.append((name, owner))
        self.assertEqual([], wrong)


class ByteEquivalenceTests(unittest.TestCase):
    def test_method_sources_byte_identical(self):
        # 2026-11-20：_diary_prompt 的同席人提示词改成中性规则（示例人名会被模型照抄），
        # 哈希按新源码更新；属行为修复，不是搬家。
        changed = [(name, _source_hash(getattr(schedule_engine.ScheduleEngine, name)), want)
                   for name, want in METHOD_HASHES.items()
                   if _source_hash(getattr(schedule_engine.ScheduleEngine, name)) != want]
        self.assertEqual([], changed)

    def test_module_function_sources_byte_identical(self):
        changed = [name for name, want in MODULE_FN_HASHES.items()
                   if _source_hash(getattr(schedule_engine, name)) != want]
        self.assertEqual([], changed)

    def test_behavior_matches_pre_split_golden(self):
        combos = [(0, 0.0, 0, False, "", 0, None), (7, 0.3, 1200, False, "平静", 0, 0),
                  (9, 0.8, 50000, False, "开心", -200, 1), (12, 0.5, 9999, True, "疲惫", 300, 2),
                  (15, 0.2, 100000, False, "焦虑", -900, 3), (19, 0.9, 300, False, "生气", 0, 4),
                  (22, 0.4, 7000, True, "依恋", 50, 5), (23, 1.0, 0, False, "兴奋", -50, 6),
                  (3, 0.6, 200, False, "", 0, None), (18, 0.7, 2500, False, "郁闷", 120, 1)]
        got = [json.dumps(schedule_engine.jit_plan(*c), ensure_ascii=False, sort_keys=True, default=str)
               for c in combos]
        self.assertEqual(BEHAVIOR["jit_plan"], got)

    def test_fmt_hhmm_matches_pre_split_golden(self):
        got = [schedule_engine.fmt_hhmm(m) for m in (-5, 0, 5, 59, 60, 119, 720, 1439, 1440, 1500)]
        self.assertEqual(BEHAVIOR["fmt_hhmm"], got)

    def test_json_extract_matches_pre_split_golden(self):
        raw = ['ok {"a": 1, "b": [2, 3]} tail', 'no json', '[1, 2,]', '', '{"a": 1,}']
        got = [json.dumps(schedule_engine.json_extract(r), ensure_ascii=False, sort_keys=True, default=str)
               for r in raw]
        self.assertEqual(BEHAVIOR["json_extract"], got)

    def test_pure_rules_match_pre_split_golden(self):
        self.assertEqual(BEHAVIOR["diary_rules"],
                         [schedule_engine.diary_presence_rules(True),
                          schedule_engine.diary_presence_rules(False)])
        self.assertEqual(BEHAVIOR["detail_conflicts"],
                         [schedule_engine.detail_conflicts("加班写代码", "在厨房做早饭"),
                          schedule_engine.detail_conflicts("睡觉", "客厅看电视"),
                          schedule_engine.detail_conflicts("通勤", "地铁上补觉")])
        self.assertEqual(BEHAVIOR["occupation"],
                         [schedule_engine.occupation_kind({}),
                          schedule_engine.occupation_kind({"workplace": "学校", "summary": "老师"}),
                          schedule_engine.occupation_kind({"workplace": "医院", "summary": "护士"})])
        self.assertEqual(BEHAVIOR["work_bank"],
                         [schedule_engine.work_bank_for({"workplace": "公司", "summary": "程序员"}),
                          schedule_engine.work_bank_for({"workplace": "学校", "summary": "老师"})])

    def test_module_constants_match_pre_split_golden(self):
        consts = BEHAVIOR["consts"]
        self.assertEqual(json.loads(consts["MOOD_KEYS"]), list(schedule_engine.MOOD_KEYS))
        self.assertEqual(json.loads(consts["MOOD_BASE"]), schedule_engine.MOOD_BASE)
        self.assertEqual(json.loads(consts["PROVINCE_CITIES"]), schedule_engine.PROVINCE_CITIES)
        self.assertEqual(json.loads(consts["CATEGORY_MOOD"]), schedule_engine.CATEGORY_MOOD)
        self.assertEqual(json.loads(consts["SAFE_EVENT"]), schedule_engine.SAFE_EVENT)


class StructureTests(unittest.TestCase):
    def test_submodules_never_import_the_facade(self):
        offenders = []
        for path in NEW_MODULES:
            for target in _module_imports(path):
                if target.endswith("schedule_engine"):
                    offenders.append((path, target))
        self.assertEqual([], offenders)

    def test_new_modules_stay_under_line_budget(self):
        big = [(path, _line_count(path)) for path in NEW_MODULES + ["schedule_engine.py"]
               if _line_count(path) >= 1500]
        self.assertEqual([], big)

    def test_no_bare_silent_except(self):
        self.assertEqual([], [(path, _silent_excepts(path)) for path in NEW_MODULES if _silent_excepts(path)])


if __name__ == "__main__":
    unittest.main()

