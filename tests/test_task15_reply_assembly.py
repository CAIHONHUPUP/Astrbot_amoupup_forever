# -*- coding: utf-8 -*-
"""task-15：回复拼装 P0 回归（2026-11-22 10:32 真实记录）。

线上原文（amo_data.db messages #2287）：
「（窝在沙发上…）你又来了…[我发的照片] 自拍 老公，我在商场试了件米白色针织开衫…也太可爱了吧」

一条回复同时犯了四件事：模型复读自己上一条（#2285 商场试衣）和媒体占位符、当前在家却把
旧商场当成此刻、[我发的照片] 进了正文、分段引擎把两段拼装发出去。这里锁住统一收口：
- 媒体标记不进 LLM 可复读文本（_recent 整行剔除），出站剥离（segment_engine/photo caption）；
- 子句级自我复读检测（旧句被拆进新句子也能拦）；
- 拼装前统一场景校验：当前 home 时旧商场断言降级/剥离；
- 分段去重：后段和最近说过的话/本轮前面的段重复就整段不发。
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from astrbot.api.message_components import Plain  # noqa: E402

import amo_memory_context  # noqa: E402
import amo_message  # noqa: E402
import amo_state_consistency as consistency  # noqa: E402
from photo_share import PhotoShareMixin  # noqa: E402
from segment_engine import SegmentEngine  # noqa: E402

MARKER = "[我发的照片] 自拍"
OLD_MALL = "老公，我在商场试了件米白色针织开衫，你猜好不好看？"
#: 10:32:47 真实出站原文（含模型复读的两段）。
BAD_REPLY = (
    "（窝在沙发上，头发有点乱，笑了一声） 你又来了。这个真拍了也没地方穿啊。"
    "（笑）[我发的照片] 自拍 老公，我在商场试了件米白色针织开衫， "
    "你猜好不好看，也太可爱了吧。"
)


class _State:
    def __init__(self, place="家", zone="home"):
        self._activity = {"place": place, "zone": zone}

    def current_activity(self):
        return dict(self._activity)


class _Plugin:
    """验证用宿主：只需要拼装校验用到的读接口。"""

    name = "Astrbot_amoupup_forever"

    def __init__(self, rows=None, place="家", zone="home"):
        self.state = _State(place, zone)
        self.rows = list(rows or [])
        self.remembered = []
        self._spawn = lambda coro: coro.close()

    def _recent(self, umo, limit=10):
        return [dict(row) for row in self.rows[-int(limit or 10):]]

    def _remember_bot_message(self, umo, text):
        self.remembered.append((umo, text))

    def typing_seconds(self, text):
        return 0.0


def _p0_plugin(place="家", zone="home"):
    return _Plugin(
        rows=[
            {"role": "bot", "content": MARKER},
            {"role": "bot", "content": OLD_MALL},
            {"role": "user", "content": "你穿肯定好看"},
        ],
        place=place,
        zone=zone,
    )


class MediaMarkerTests(unittest.TestCase):
    def test_媒体记录与正文可区分(self):
        self.assertTrue(amo_message.is_media_record(MARKER))
        self.assertTrue(amo_message.is_media_record("[我发的语音] 一段语音"))
        self.assertFalse(amo_message.is_media_record(OLD_MALL))

    def test_出站剥离标记不人话(self):
        out = amo_message.strip_media_markers("（笑）" + MARKER + " 老公，你说呢")
        self.assertNotIn("[我发的照片]", out)
        self.assertNotIn("自拍", out)
        self.assertIn("老公", out)

    def test_recent_不把媒体占位喂给模型(self):
        plugin = _p0_plugin()
        rows = plugin._recent("u", 10)  # 真实 MessageMixin 的读侧过滤在下面单独测
        self.assertTrue(rows)  # 假的 _recent 只作对照

        class _DB:
            def recent_messages(self, umo, size):
                return []

        class _Mixin(amo_message.MessageMixin):
            def __init__(self):
                self._ctx_buffer = {"u": [
                    {"role": "bot", "content": MARKER},
                    {"role": "bot", "content": OLD_MALL},
                    {"role": "user", "content": "你穿肯定好看"},
                ]}
                self.db = _DB()
                self.state = _State()

            def _memory_hub(self):
                return None

        mixin = _Mixin()
        got = mixin._recent("u", 10)
        joined = "\n".join(str(row.get("content") or "") for row in got)
        self.assertNotIn("[我发的照片]", joined)
        self.assertNotIn("自拍", joined)
        self.assertIn("之前", joined)  # 旧商场断言降级成过去时
        self.assertIn("商场", joined)  # 内容不丢，只是不能当此刻
        self.assertIn("你穿肯定好看", joined)

    def test_记忆注入剔除媒体记录(self):
        lines = []
        self_ = SimpleNamespace(cfg_int=lambda key, default: default)
        amo_memory_context._recall_format(self_, None, lines, [
            {"source": "amo_data", "subject": "bot", "content": MARKER, "created_at": 0},
            {"source": "amo_data", "subject": "user", "content": "他说今天加班", "created_at": 0},
        ])
        joined = "\n".join(lines)
        self.assertNotIn("[我发的照片]", joined)
        self.assertIn("加班", joined)


class SceneAssemblyTests(unittest.TestCase):
    def test_旧商场断言降级为过去(self):
        plugin = _p0_plugin()
        out = consistency.demote_past_claims(OLD_MALL, plugin)
        self.assertNotEqual(OLD_MALL, out)
        self.assertIn("之前", out)
        self.assertIn("商场", out)

    def test_当前home剥离旧商场断言(self):
        plugin = _p0_plugin()
        out = consistency.strip_current_scene_conflicts("我还在商场试衣服呢。", plugin)
        self.assertNotIn("商场", out)

    def test_当前road剥离居家断言(self):
        plugin = _p0_plugin(place="路上", zone="road")
        out = consistency.strip_current_scene_conflicts("我窝在沙发上呢。", plugin)
        self.assertNotIn("沙发", out)

    def test_自身复读子句被删且新内容保留(self):
        fixed, dropped = consistency.drop_repeated_clauses(
            "你又来了。老公，我在商场试了件米白色针织开衫，也太可爱了吧。", [OLD_MALL])
        self.assertTrue(fixed.strip())
        self.assertNotIn("商场", fixed)
        self.assertIn("你又来了", fixed)
        self.assertTrue(any("商场" in item for item in dropped))

    def test_1032原文统一收口(self):
        plugin = _p0_plugin()
        out = consistency.validate_reply_assembly(
            BAD_REPLY, plugin, umo="lark:FriendMessage:u", user_text="你穿肯定好看")
        self.assertTrue(out.strip())
        self.assertNotIn("[我发的照片]", out)      # 媒体标记不进正文
        self.assertNotIn("商场", out)              # 当前在家，旧商场不能当此刻
        self.assertNotIn("你猜好不好看", out)      # 自己上一条整句复读
        self.assertIn("你又来了", out)             # 这轮新内容保留
        self.assertNotIn(OLD_MALL, out)

    def test_删空也不回退复读原文(self):
        plugin = _p0_plugin()
        out = consistency.validate_reply_assembly(
            OLD_MALL + MARKER, plugin, umo="u", user_text="你穿肯定好看")
        self.assertTrue(out.strip())
        self.assertNotIn("[我发的照片]", out)
        self.assertNotIn("商场", out)

    def test_分段去重(self):
        deduped = consistency.dedupe_reply_segments(
            ["老公，我在商场试了件米白色针织开衫。", "我在商场试了件米白色针织开衫，你猜好不好看？"],
            refs=[OLD_MALL])
        # 两段都是旧句复读：允许整段不发；若有保留，绝不能重复旧商场断言
        for item in deduped:
            self.assertNotIn("商场", item)


class SegmentEngineAssemblyTests(unittest.TestCase):
    def _engine(self, plugin):
        config = {
            "delay_type": "fixed",
            "fixed_delay_seconds": 0,
            "min_length": 12,
            "max_length": 40,
            "hard_max_limit": 60,
            "merge_short_tail": True,
            "split_symbols": ["\n\n", "\n", "。", "！", "？", "；", "……", "，"],
        }
        return SegmentEngine(SimpleNamespace(conversation_manager=None), config, plugin=plugin)

    class _Event:
        def __init__(self, text):
            self.unified_msg_origin = "lark:FriendMessage:u"
            self.message_str = "你穿肯定好看"
            self.message_obj = SimpleNamespace(message_id="m1")
            self.sent = []
            self._result = SimpleNamespace(chain=[Plain(text)])

        def get_result(self):
            return self._result

        def get_platform_name(self):
            return "lark"

        async def send(self, chain):
            self.sent.append("".join(
                comp.text for comp in getattr(chain, "chain", []) if isinstance(comp, Plain)))

    def test_拼装前统一校验后分段(self):
        plugin = _p0_plugin()
        event = self._Event(BAD_REPLY)
        handled = asyncio.run(self._engine(plugin).handle_segment_reply(event))
        self.assertTrue(handled)
        self.assertTrue(event.sent)
        joined = "\n".join(event.sent)
        self.assertNotIn("[我发的照片]", joined)
        self.assertNotIn("商场", joined)
        self.assertNotIn("你猜好不好看", joined)
        self.assertEqual(len(event.sent), len(set(event.sent)))  # 分段不重复
        stored = "\n".join(text for _umo, text in plugin.remembered)
        self.assertNotIn("[我发的照片]", stored)
        self.assertNotIn("商场", stored)

    def test_干净回复保持原发送路径(self):
        plugin = _p0_plugin()
        event = self._Event("嗯，我在呢，刚把汤煮上。")
        handled = asyncio.run(self._engine(plugin).handle_segment_reply(event))
        # 没有改动的一段：仍交回核心发（和改前行为一致），但记忆里是干净的
        self.assertFalse(handled)
        self.assertEqual(event.sent, [])
        self.assertIn("刚把汤煮上", plugin.remembered[-1][1])


class PhotoCaptionTests(unittest.TestCase):
    def test_图片走组件且caption不带占位符(self):
        class _PhotoPlugin:
            def __init__(self):
                self.sent = []

            async def _send_media_to_umo(self, umo, components, event_id="", source="photo"):
                self.sent.append(list(components))
                return True

        class _Photo(PhotoShareMixin):
            def __init__(self, plugin):
                self.plugin = plugin
                self._photo_event_binding = {"event_id": ""}

            def _image_component(self, item):
                return SimpleNamespace(kind="image", file=str(item))

        plugin = _PhotoPlugin()
        photo = _Photo(plugin)
        ok = asyncio.run(photo._send("lark:FriendMessage:u", {"path": "x"}, caption=MARKER))
        self.assertTrue(ok)
        components = plugin.sent[0]
        self.assertTrue(any(getattr(comp, "kind", "") == "image" for comp in components))
        texts = [str(getattr(comp, "text", "")) for comp in components
                 if hasattr(comp, "text")]
        self.assertNotIn("[我发的照片]", "".join(texts))
        self.assertNotIn("自拍", "".join(texts))


if __name__ == "__main__":
    unittest.main()
