# -*- coding: utf-8 -*-
"""共同活动：聊天约好 → 写当前小时日程 → 到点/打断收尾 → 场景事实。"""
import datetime
import unittest

import amo_shared_activity as sa


class DetectTests(unittest.TestCase):
    def test_together_watch(self):
        hit = sa.detect("那我们今晚一起看电视吧")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["kind"], "watch")
        self.assertFalse(hit["hedged"])

    def test_hedged_is_not_committed(self):
        hit = sa.detect("好啊，不过我可能看一会儿就去睡觉")
        self.assertIsNotNone(hit)
        self.assertTrue(hit["hedged"])

    def test_minutes_from_text(self):
        self.assertEqual(sa.detect("一起看两小时电影")["minutes"], 120)
        self.assertEqual(sa.detect("一起看半小时剧")["minutes"], 30)
        self.assertEqual(sa.detect("一起打游戏")["minutes"], sa.DEFAULT_MINUTES)

    def test_game_and_music(self):
        self.assertEqual(sa.detect("陪你一起打游戏吧")["kind"], "game")
        self.assertEqual(sa.detect("一块听歌")["kind"], "music")

    def test_plain_chat_not_detected(self):
        self.assertIsNone(sa.detect("今天上班好累"))
        self.assertIsNone(sa.detect("我自己看会儿书"))

    def test_compliment_is_not_an_agreement(self):
        # 2026-11-17 线上：「很好看，如果能换比基尼就更好看了」被当成约好一起看电视
        self.assertIsNone(sa.detect("很好看，如果能换比基尼就更好看了"))
        self.assertIsNone(sa.detect("哈哈哈，谁让我老婆好看"))
        self.assertIsNone(sa.detect("这件衣服真好看"))

    def test_user_own_activity_is_not_shared(self):
        self.assertIsNone(sa.detect("我很难过，我看电视去了"))
        self.assertIsNone(sa.detect("我在看动画片"))

    def test_real_invitation_still_detected(self):
        self.assertEqual("watch", sa.detect("好，那我们一起看剧吧")["kind"])
        self.assertEqual("watch", sa.detect("陪你一起看电影")["kind"])
        self.assertEqual("game", sa.detect("我们一起打游戏吧")["kind"])

    def test_stop_words(self):
        self.assertTrue(sa.stop_detected("我不看了，我去忙了"))
        self.assertTrue(sa.stop_detected("先睡了，明天再聊"))
        self.assertFalse(sa.stop_detected("这个剧情好看"))

    def test_expired(self):
        self.assertTrue(sa.expired({"ends_at": 10}, now_ts=20))
        self.assertFalse(sa.expired({"ends_at": 30}, now_ts=20))
        self.assertFalse(sa.expired({}, now_ts=20))

    def test_hour_detail_keeps_first_half(self):
        text = sa.hour_detail("在家翻翻手机，消磨时间", "看电视", "16:20")
        self.assertIn("前半段", text)
        self.assertIn("后半段", text)
        self.assertIn("和老公连麦看电视", text)

    def test_hour_detail_without_old_activity(self):
        text = sa.hour_detail("", "看电视", "16:20")
        self.assertNotIn("前半段", text)
        self.assertIn("和老公连麦看电视", text)

    def test_prompt_is_scene_bound(self):
        block = sa.prompt_block({"label": "看电视"})
        self.assertIn("同一段", block)
        self.assertIn("场景感", block)


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeState:
    def __init__(self, day="2026-11-15"):
        self.schedule = {day: [{
            "start": "16:00", "end": "17:00",
            "activity": "在家翻翻手机，消磨时间",
            "place": "家", "zone": "home", "category": "rest",
        }]}
        self.commits = []

    def commit_schedule_change(self, day, items, old_items=None, source="", reason=""):
        self.commits.append({"day": day, "items": items, "source": source, "reason": reason})
        self.schedule[day] = items
        return {"ok": True}


class FakePlugin(sa.SharedActivityMixin):
    def __init__(self):
        self.db = FakeDB()
        self.state = FakeState()


class FlowTests(unittest.TestCase):
    def setUp(self):
        self._old_now = sa._now
        sa._now = lambda: datetime.datetime(2026, 11, 15, 16, 20, 0)
        self.plugin = FakePlugin()

    def tearDown(self):
        sa._now = self._old_now

    def test_start_updates_schedule_and_action(self):
        result = self.plugin.shared_activity_on_message("那我们今晚一起看电视吧")
        self.assertEqual(result["action"], "start")
        act = self.plugin.db.kv_get("shared_activity")
        self.assertEqual(act["status"], "active")
        item = self.plugin.state.schedule["2026-11-15"][0]
        self.assertEqual(item["activity"], "和老公连麦看电视")
        self.assertEqual(item["zone"], "home")
        self.assertEqual(item["category"], "life")
        self.assertIn("前半段", item["detail"])
        action = self.plugin.db.kv_get("current_action") or {}
        self.assertEqual(action.get("action"), "和老公连麦看电视")
        self.assertIn("正在一起看电视", self.plugin.shared_activity_prompt())

    def test_hedged_does_not_touch_schedule(self):
        result = self.plugin.shared_activity_on_message("好啊，不过我可能看一会儿就去睡觉")
        self.assertEqual(result["action"], "hedged")
        self.assertIsNone(self.plugin.db.kv_get("shared_activity"))
        self.assertEqual(self.plugin.state.commits, [])
        self.assertEqual(self.plugin.state.schedule["2026-11-15"][0]["activity"],
                         "在家翻翻手机，消磨时间")

    def test_interrupt_finishes_activity(self):
        self.plugin.shared_activity_on_message("一起看电影吧")
        result = self.plugin.shared_activity_on_message("我不看了，我去忙了")
        self.assertEqual(result["action"], "finish")
        act = self.plugin.db.kv_get("shared_activity")
        self.assertEqual(act["status"], "done")
        self.assertTrue(act["interrupted"])
        last = self.plugin.db.kv_get("shared_activity_last") or {}
        self.assertIn("去忙", last.get("text") or "")
        action = self.plugin.db.kv_get("current_action") or {}
        self.assertIn("去忙", action.get("action") or "")
        self.assertEqual(self.plugin.shared_activity_prompt(), "")

    def test_tick_finishes_when_expired(self):
        self.plugin.shared_activity_on_message("一起看一小时剧")
        act = dict(self.plugin.db.kv_get("shared_activity"))
        act["ends_at"] = 1
        self.plugin.db.kv_set("shared_activity", act)
        self.assertEqual(self.plugin.shared_activity_tick(), "finished")
        done = self.plugin.db.kv_get("shared_activity")
        self.assertEqual(done["status"], "done")
        self.assertFalse(done["interrupted"])
        last = self.plugin.db.kv_get("shared_activity_last") or {}
        self.assertIn("看完", last.get("text") or "")

    def test_tick_keeps_running_before_end(self):
        self.plugin.shared_activity_on_message("一起看两小时剧")
        self.assertIsNone(self.plugin.shared_activity_tick())
        self.assertEqual(self.plugin.db.kv_get("shared_activity")["status"], "active")

    def test_start_without_existing_hour_row_creates_one(self):
        self.plugin.state.schedule["2026-11-15"] = []
        self.plugin.shared_activity_on_message("一起打游戏吧")
        items = self.plugin.state.schedule["2026-11-15"]
        self.assertTrue(items)
        self.assertEqual(items[0]["activity"], "和老公连麦打游戏")
        self.assertEqual(items[0]["start"], "16:00")



class NotHomeTests(unittest.TestCase):
    def setUp(self):
        self._old_now = sa._now
        sa._now = lambda: datetime.datetime(2026, 11, 16, 8, 2, 0)
        self.plugin = FakePlugin()
        self.plugin.state.schedule = {"2026-11-16": [{
            "start": "08:00", "end": "09:00", "activity": "下楼买早餐，边走边吃去地铁站",
            "place": "路上", "zone": "road", "category": "commute",
        }]}

    def tearDown(self):
        sa._now = self._old_now

    def test_does_not_merge_watch_into_commute(self):
        result = self.plugin.shared_activity_on_message("那我们今晚一起看电视吧")
        self.assertEqual(result["action"], "ignored")
        self.assertIsNone(self.plugin.db.kv_get("shared_activity"))
        self.assertEqual(self.plugin.state.commits, [])


class MoodStoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_reads_recent_chat_and_shared_activity(self):
        import time
        from amo_commands import CommandMixin

        captured = []

        class LLM:
            async def chat_raw(self, umo, system, prompt):
                captured.append(prompt)
                return "独白：在家闲着，心里挺安稳。\n归因：刚刚和老公连麦看剧。"

        class P(CommandMixin):
            def __init__(self):
                self.db = FakeDB()
                self.llm = LLM()
                self.rows = [{"role": "user", "content": "我们一起看电视吧"}]

            def _recent(self, umo, n=6):
                return list(self.rows[-n:])

        plugin = P()
        plugin.db.kv_set("shared_activity", {
            "status": "active", "label": "看电视", "started_at": time.time(),
        })
        snap = {"mood": {"平静": 92}, "activity": {"place": "家", "activity": "看电视"},
                "health": {}, "period": {}, "mood_score": 92}
        narration, attribution = await plugin._mood_story(snap, "umo")
        self.assertTrue(captured)
        prompt = captured[0]
        self.assertIn("最近聊天", prompt)
        self.assertIn("一起看电视", prompt)
        self.assertIn("正在进行", prompt)
        self.assertIn("超过 2 小时", prompt)
        self.assertTrue(attribution)
        # 新聊天进来 → 缓存签名变化 → 会重新生成
        plugin.rows.append({"role": "user", "content": "我们一起打游戏吧"})
        await plugin._mood_story(snap, "umo")
        self.assertEqual(len(captured), 2)


if __name__ == "__main__":
    unittest.main()
