# -*- coding: utf-8 -*-
"""schedule_engine.py 拆分回归锁（门面 + schedule_plan/skeleton/slots/diary）。

2026-11-24 方案 B（docs/persona_roadmap.md T3）：删掉基于 SOURCE_HASHES 的源码 sha256
哈希锁，只留「不依赖哈希」的模块归属断言和 behavior golden（tests/golden_split.json
只保留 behavior 段，按当前代码重新生成）。锁四件事：

1. 旧 import 路径不变：schedule_engine 里原来的名字全部还在（门面 re-export）；
2. 方法归属：每个业务方法都在 owner mixin 模块，门面自身不定义业务方法；
3. 行为等价：纯函数的输入输出与 golden 一致（test_behavior_*）；
4. 依赖单向：子模块不 import 门面；新文件都在 1500 行以内、没有裸 except: pass。
"""
import ast
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

BEHAVIOR = GOLDEN["behavior"]
NEW_MODULES = ["schedule_plan.py", "schedule_skeleton.py", "schedule_slots.py", "schedule_diary.py"]
MIXIN_CLASSES = [schedule_skeleton.ScheduleSkeletonMixin, schedule_slots.ScheduleSlotsMixin, schedule_diary.ScheduleDiaryMixin]
FACADE = schedule_engine
OWNER = {'schedule_plan': 'schedule_plan', 'schedule_skeleton': 'schedule_skeleton', 'schedule_slots': 'schedule_slots', 'schedule_diary': 'schedule_diary'}

LEGACY_NAMES = (
    "ADMIN_BANK",
    "Any",
    "BREAKFAST_BANK",
    "CAR_BRANDS",
    "CAR_POOL",
    "CATEGORY_MOOD",
    "CITY_POOL",
    "COMMUTE_BANK",
    "COMPANY_POOL",
    "CREATIVE_BANK",
    "DINNER_BANK",
    "DISTRICT_MAP",
    "HOME_LIFE_BANK",
    "HOME_STYLES",
    "HOTEL_BANK",
    "INDOOR_BANK",
    "JIT_HOURS",
    "LUNCH_BANK",
    "MED_BANK",
    "MOOD_BASE",
    "MOOD_KEYS",
    "NEGATIVE_LABELS",
    "NIGHT_LIFE_BANK",
    "OCCUPATION_BANKS",
    "OFFICE_WORK_BANK",
    "POSITIVE_LABELS",
    "PROVINCE_CITIES",
    "REST_BANK",
    "ROAD_BANK",
    "SAFE_EVENT",
    "SALES_BANK",
    "SCENIC_BANK",
    "SERVICE_BANK",
    "STATION_BANK",
    "STUDENT_BANK",
    "SWIM_DETAILS",
    "SWIM_PLACES",
    "ScheduleEngine",
    "TAXI_BANK",
    "TEACHER_BANK",
    "TECH_WORK_BANK",
    "TRAVEL_KEYWORDS",
    "TYPE_CHECKING",
    "_FOOD_ACTIVITY_WORDS",
    "_MEETING_DETAIL_WORDS",
    "_SLEEP_ACTIVITY_WORDS",
    "_WORK_DETAIL_WORDS",
    "_amo_clock",
    "_balanced",
    "_brief_world",
    "_city_of",
    "_clock_now",
    "_clock_today",
    "_diary_material_lines",
    "_mood_text",
    "_note_swallowed",
    "_place_of",
    "_scenic_text",
    "_strip_fences",
    "_weather_text",
    "_with_city",
    "amusement_segments",
    "annotations",
    "asyncio",
    "business_trip_segments",
    "clamp",
    "date",
    "datetime",
    "detail_conflicts",
    "diary_presence_rules",
    "fmt_hhmm",
    "hashlib",
    "homebody_segments",
    "indoor_rest_segments",
    "is_long_holiday",
    "jit_plan",
    "json",
    "json_extract",
    "logger",
    "md5_int",
    "occupation_kind",
    "outing_segments",
    "overtime_segments",
    "parse_hhmm",
    "period_rest_segments",
    "pick_amusement_spot",
    "pick_holiday_plan",
    "random",
    "re",
    "scenic_segments",
    "shopping_segments",
    "sick_rest_segments",
    "swim_segments",
    "timedelta",
    "travels_for_work",
    "work_bank_for",
    "workday_segments",
)


def _own_methods(cls):
    """类自己定义的方法名（不含 dunder / 继承来的）。"""
    return {name for name, value in vars(cls).items()
            if callable(value) and not name.startswith("__")}


#: 归属断言用的方法清单：直接从 mixin 类定义里取，不依赖任何源码哈希。
METHOD_NAMES = tuple(sorted(set().union(*(_own_methods(cls) for cls in MIXIN_CLASSES))))


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
        # 不读源码哈希：只看每个方法对象的 __module__ 是否落在 owner mixin 里。
        self.assertTrue(METHOD_NAMES, "没有从 mixin 取到任何方法，归属断言形同虚设")
        wrong = []
        for name in METHOD_NAMES:
            owner = getattr(schedule_engine.ScheduleEngine, name).__module__
            if owner not in OWNER:
                wrong.append((name, owner))
        self.assertEqual([], wrong)
        # 反向兜底：门面自己不许定义业务方法（否则 MRO 前面的门面实现会盖住 mixin）。
        facade_own = sorted(_own_methods(schedule_engine.ScheduleEngine))
        self.assertEqual([], facade_own)


class BehaviorTests(unittest.TestCase):
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
