# -*- coding: utf-8 -*-
"""amo_shared.py 结构拆分回归（2026-11-17 structure-b）。

拆分前 amo_shared.py 2097 行；现在是门面 + 5 个子模块：

    amo_shared_deps.py    可选依赖兜底导入 / 历史兼容名
    amo_shared_time.py    取时、节假日、时段判断
    amo_shared_text.py    文本清洗/切分/情绪词/JSON 抽取
    amo_shared_config.py  插件常量/方言表/日程关系配置
    amo_shared_usage.py   token 用量/价格/余额/UID 合并

门面只做转发（from .x import ...），旧 from amo_shared import X 与
amo_shared.X 都保持可用；下面用逐字黄金值锁住拆分前后一致的文案/数值。
"""
from __future__ import annotations

import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_shared  # noqa: E402
import amo_shared_config  # noqa: E402
import amo_shared_deps  # noqa: E402
import amo_shared_text  # noqa: E402
import amo_shared_time  # noqa: E402
import amo_shared_usage  # noqa: E402

NEW_MODULES = (
    "amo_shared_deps.py", "amo_shared_time.py", "amo_shared_text.py",
    "amo_shared_config.py", "amo_shared_usage.py",
)


class SharedSplitTests(unittest.TestCase):
    #: 这些名字只在"全部导入失败"的兜底分支里才会绑定（原 amo_shared 也是如此，
    #: 拆分前 dir(amo_shared) 同样没有它们）；__all__ 保留历史写法，这里显式记录。
    CONDITIONAL_ONLY = {
        "importlib", "_engine_dir", "_load_engine", "_importlib_util",
        "_schedule_path", "_schedule_spec", "_schedule_module",
    }

    def test_every_public_name_still_importable(self):
        missing = [name for name in amo_shared.__all__ if not hasattr(amo_shared, name)]
        self.assertEqual(set(missing), self.CONDITIONAL_ONLY,
                         "门面缺的名字和拆分前的兜底名不一致：%r" % missing)
        self.assertEqual(len(amo_shared.__all__), 169)

    def test_facade_only_reexports(self):
        for module, name in (
            (amo_shared_time, "_now"),
            (amo_shared_time, "day_meta"),
            (amo_shared_text, "segment_text"),
            (amo_shared_config, "dialect_for_city"),
            (amo_shared_config, "STYLE_HINTS"),
            (amo_shared_usage, "summarize_token_usage"),
            (amo_shared_deps, "logger"),
        ):
            self.assertIs(getattr(amo_shared, name), getattr(module, name),
                          "%s 应当是 %s 的转发" % (name, module.__name__))

    def test_all_head_unchanged(self):
        head = ["_now", "Comp", "logger", "AstrMessageEvent", "filter", "Context", "Star"]
        self.assertEqual(list(amo_shared.__all__)[:len(head)], head)

    def test_segment_text_golden(self):
        self.assertEqual(amo_shared.segment_text("我们明天去海边玩吧然后晚上回来吃火锅怎么样呀"),
                         ["我们明天去海边玩吧然后晚上回来吃火锅怎么样呀"])
        self.assertEqual(amo_shared.segment_text("嗯。好的。"), ["嗯。好的。"])
        self.assertEqual(amo_shared.segment_text("第一段，第二段；第三段！第四段？"),
                         ["第一段，第二段；第三段！第四段？"])

    def test_clean_reply_and_strip_golden(self):
        self.assertEqual(amo_shared.clean_reply("（笑）你好呀～"), "（笑）你好呀～")
        self.assertEqual(amo_shared.clean_reply("【动作】她看了看你"), "【动作】她看了看你")
        self.assertEqual(amo_shared.clean_reply("*抱抱* 晚安"), "*抱抱* 晚安")
        self.assertLessEqual(len(amo_shared.clean_reply("好的" * 80)), 300)
        self.assertEqual(amo_shared.strip_chat_format("（笑）你好"), "（笑）你好")
        self.assertEqual(amo_shared.strip_chat_format("[图片] 在呢"), "[图片] 在呢")
        self.assertEqual(amo_shared.strip_chat_format("**粗体**"), "**粗体**")

    def test_sentiment_and_tokens_golden(self):
        self.assertEqual(amo_shared.sentiment("今天好开心啊"),
                         {"pos": 1, "neg": 0, "angry": 0, "sad": 0, "aff": 0,
                          "insult": False, "score": 1.0})
        self.assertEqual(amo_shared.sentiment("气死了讨厌"),
                         {"pos": 0, "neg": 2, "angry": 1, "sad": 0, "aff": 0,
                          "insult": False, "score": -1.0})
        self.assertEqual(amo_shared.sentiment(""),
                         {"pos": 0, "neg": 0, "angry": 0, "sad": 0, "aff": 0,
                          "insult": False, "score": 0.0})
        self.assertEqual(sorted(amo_shared.tokens("你好世界 hello world")),
                         sorted(["hello", "world", "世界", "你好", "你好世", "好世", "好世界"]))

    def test_day_meta_golden(self):
        self.assertEqual(amo_shared.day_meta("2026-10-02"), {
            "date": "2026-10-02", "weekday": "五", "weekend": False, "holiday": "国庆",
            "solar_term": "", "holiday_days": 7, "rest": True,
            "holiday_source": "CN_HOLIDAYS", "label": "国庆 · 7天假期"})
        self.assertEqual(amo_shared.day_meta("2028-01-01"), {
            "date": "2028-01-01", "weekday": "六", "weekend": True, "holiday": "元旦",
            "solar_term": "", "holiday_days": 1, "rest": True,
            "holiday_source": "holiday_calendar", "label": "元旦"})

    def test_config_constants_golden(self):
        self.assertEqual(amo_shared.PLUGIN_NAME, "Astrbot_amoupup_forever")
        self.assertEqual(amo_shared.SCHEDULE_VERSION, "backbone-v6")
        self.assertEqual(amo_shared.STYLE_HINTS, {
            "soft": "整体语气温柔、软一点。",
            "cool": "整体语气清淡、克制一点。",
            "lively": "整体语气活泼、有精神：多用语气词（呀/啦/嘛/诶/哼哼）、短句、反问和玩笑，"
                      "偶尔撒个小娇或吐槽；不要平铺直叙，也不要每句都完整规矩。",
            "mature": "整体语气成熟、稳一点。"})
        self.assertEqual(sorted(amo_shared.DIALECT_SAMPLES),
                         sorted(["东北话", "上海话", "台湾腔", "四川话", "天津话",
                                 "广东话", "河南话", "贵州话"]))
        self.assertEqual(len(amo_shared.MOOD_KEYS), 10)
        self.assertEqual(len(amo_shared.SICK_STATUSES), 10)
        self.assertEqual(amo_shared.dialect_for_city("东莞"), "广东话")
        self.assertEqual(amo_shared.dialect_for_city(""), "")

    def test_usage_helpers_golden(self):
        self.assertEqual(amo_shared.usage_cost({"prompt_tokens": 1000, "completion_tokens": 500}), 0.0)
        self.assertEqual(amo_shared.clean_ui_value({"a": 1, "b": [1, 2]}), {"a": 1, "b": [1, 2]})
        self.assertEqual(amo_shared.in_minute_range(600, 500, 700), True)
        self.assertEqual(amo_shared.in_minute_range(600, 700, 800), False)
        self.assertEqual(sorted(amo_shared.schedule_category_mood()),
                         sorted(["commute", "life", "meal", "play", "rest", "sleep",
                                 "social", "study", "work"]))

    def test_new_modules_do_not_import_facade(self):
        for name in NEW_MODULES:
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=name)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    self.assertNotEqual(mod.split(".")[-1], "amo_shared",
                                        "%s 不许 import 门面" % name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotEqual(alias.name.split(".")[-1], "amo_shared",
                                            "%s 不许 import 门面" % name)




if __name__ == "__main__":
    unittest.main()
