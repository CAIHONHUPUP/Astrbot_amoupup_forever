# -*- coding: utf-8 -*-
"""人设自进化 A 阶段：A1 检索 / A2 采集 / A3 过滤 / A4 注入 / A5 池上限与衰减。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_persona_evolve as evolve  # noqa: E402
import amo_persona_pack as pack  # noqa: E402
import amo_persona_store as store  # noqa: E402
from support import make_temp_dir  # noqa: E402


class FakeDB:
    def __init__(self, root):
        self.path = os.path.join(root, "plugin_data", "Astrbot_amoupup_forever", "amo.db")
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeState:
    def __init__(self):
        self.activity = {"zone": "home_bed", "place": "家"}
        self.mood_name = "开心"
        self.mood_value = 70

    def current_activity(self):
        return dict(self.activity)

    def dominant_mood(self):
        return self.mood_name

    def mood_score(self):
        return self.mood_value


class FakePlugin:
    def __init__(self, cfg=None):
        self.root = make_temp_dir("amo-persona-")
        os.makedirs(os.path.dirname(FakeDB(self.root).path), exist_ok=True)
        self.db = FakeDB(self.root)
        self.state = FakeState()
        self.cfg_map = dict(cfg or {})
        self.recent = []

    def cfg_bool(self, key, default=False):
        return bool(self.cfg_map.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.cfg_map.get(key, default))

    def _recent(self, umo, limit=10):
        return list(self.recent)[-limit:]


def _plugin(**cfg):
    values = {"persona_auto_evolve": True}
    values.update(cfg)
    return FakePlugin(values)


def _corpus_pack():
    return {"corpus": [
        {"scene": "home_bed", "mood": "依恋", "text": "床上再赖五分钟"},
        {"scene": "home_bed", "mood": "困", "text": "灯关了，你过来"},
        {"scene": "office", "mood": "压力", "text": "会开完了，我喘口气"},
        {"scene": "office", "mood": "责任", "text": "表格我发你了"},
        {"scene": "quarrel", "mood": "委屈", "text": "你先别凶我"},
        {"scene": "sick", "mood": "示弱", "text": "药好苦，不想吃"},
        {"scene": "general", "mood": "", "text": "我先忙一会儿，等下找你"},
        {"scene": "manual", "mood": "", "text": "你回来路上慢点"},
    ]}


# ---- A1 检索 ----

def test_a1_generic_scores_one_and_lands_third():
    for scene in ("home_bed", "quarrel", "sick", "office"):
        rows = pack.retrieve_with_sources(_corpus_pack(), scene=scene, limit=3)
        assert len(rows) == 3, scene
        generic = [row for row in rows if row["generic"]]
        assert len(generic) == 1, (scene, rows)
        assert rows[-1]["generic"], (scene, rows)
        assert generic[0]["scene"] in pack.GENERIC_SCENES


def test_a1_generic_base_score_is_one():
    ranked = pack._corpus_ranked(_corpus_pack(), scene="home_bed")
    by_text = {row["text"]: row["score"] for row in ranked}
    assert by_text["我先忙一会儿，等下找你"] == 1
    assert by_text["你回来路上慢点"] == 1
    assert by_text["床上再赖五分钟"] == 2
    assert by_text["会开完了，我喘口气"] == 0


def test_a1_live_pack_acceptance_four_scenes():
    live = pack.load_default()
    live["corpus"] = list(live["corpus"]) + [
        {"scene": "manual", "mood": "", "text": "你回来路上慢点"},
        {"scene": "general", "mood": "", "text": "我先忙一会儿，等下找你"},
    ]
    for scene in ("home_bed", "quarrel", "sick", "office"):
        rows = pack.retrieve_with_sources(live, scene=scene, limit=3)
        generic = [row for row in rows if row["generic"]]
        assert len(rows) == 3, scene
        assert len(generic) == 1, (scene, rows)
        assert rows[2]["generic"], (scene, rows)


def test_a1_last_used_pushes_recent_generic_back():
    live = pack.load_default()
    live["corpus"] = list(live["corpus"]) + [
        {"scene": "manual", "mood": "", "text": "你回来路上慢点"},
        {"scene": "general", "mood": "", "text": "我先忙一会儿，等下找你"},
    ]
    rows = pack.retrieve_with_sources(live, scene="home_bed", limit=3,
                                      last_used={"你回来路上慢点": 9999999999})
    generic = [row for row in rows if row["generic"]]
    assert len(generic) == 1
    assert generic[0]["text"] == "我先忙一会儿，等下找你"


# ---- A2 采集 ----

def test_a2_only_private_chat_success_is_pending():
    plugin = _plugin()
    assert evolve.record_pending(plugin, "普通私聊回复", "qq:FriendMessage:1",
                                 source="chat")["ok"]
    state = store.load_state(plugin)
    assert state["pending_review"]["text"] == "普通私聊回复"
    assert state["pending_review"]["scene"] == "home_bed"
    assert state["pending_review"]["mood"] == "开心"

    assert not evolve.record_pending(plugin, "群聊不该学", "qq:GroupMessage:2",
                                     source="chat")["ok"]
    assert not evolve.record_pending(plugin, "主动消息", "qq:FriendMessage:1",
                                     source="proactive")["ok"]
    assert not evolve.record_pending(plugin, "[图片]", "qq:FriendMessage:1",
                                     source="chat", kind="photo")["ok"]


def test_a2_record_chat_success_private_only():
    plugin = _plugin()

    class FakeEvent:
        unified_msg_origin = "qq:FriendMessage:1"

        def is_private_chat(self):
            return True

    assert evolve.record_chat_success(plugin, FakeEvent(), "私聊聊天回复")["ok"]
    assert store.load_state(plugin)["pending_review"]["text"] == "私聊聊天回复"

    class GroupEvent(FakeEvent):
        unified_msg_origin = "qq:GroupMessage:2"

        def is_private_chat(self):
            return False

    assert not evolve.record_chat_success(plugin, GroupEvent(), "群聊不该学")["ok"]


def test_a2_chat_hook_is_wired_in_decorating_result():
    import inspect
    import amo_runtime_reply
    src = inspect.getsource(amo_runtime_reply.RuntimeReplyMixin.on_decorating_result)
    assert "_record_persona_pending" in src
    assert src.count("_record_persona_pending") >= 3


def test_a2_switch_off_writes_nothing():
    plugin = FakePlugin({"persona_auto_evolve": False})
    assert not evolve.record_pending(plugin, "关掉就不采集", "qq:FriendMessage:1")["ok"]
    assert store.load_state(plugin)["pending_review"] is None


def test_a2_judge_reaction_rules():
    now = 1000000
    base = {"text": "x", "at": now - 10}
    assert evolve.judge_reaction(base, "哈哈可爱", now=now) == "positive"
    assert evolve.judge_reaction(base, "滚", now=now) == "negative"
    assert evolve.judge_reaction(base, "哦", now=now) == "negative"
    assert evolve.judge_reaction(base, "今天下班好晚啊", now=now) == "positive"
    assert evolve.judge_reaction(base, "嗯？", now=now) == "negative"
    assert evolve.judge_reaction(base, "哈哈", now=now + 601) == "timeout"


def test_a2_positive_review_adds_to_pool_negative_does_not():
    plugin = _plugin()
    evolve.record_pending(plugin, "你先忙，我等你回来", "qq:FriendMessage:1")
    out = evolve.review_pending(plugin, "哈哈好可爱")
    assert out["ok"], out
    state = store.load_state(plugin)
    # 正向只进「待通过」，要点通过才进池（2026-11-24 审批）
    assert state["style_pool"] == []
    assert [row["text"] for row in state["style_pending"]] == ["你先忙，我等你回来"]
    assert state["style_pending"][0]["scene"] == "home_bed"
    assert state["style_pending"][0]["source"] == "auto"

    evolve.record_pending(plugin, "你烦不烦啊真是", "qq:FriendMessage:1")
    assert not evolve.review_pending(plugin, "滚")["ok"]
    assert len(store.load_state(plugin)["style_pending"]) == 1


# ---- A3 过滤器 ----

def _filter(plugin, text, **kw):
    state = store.load_state(plugin)
    args = {"scene": "home_bed", "mood": "", "mood_score": 70, "umo": "qq:FriendMessage:1"}
    args.update(kw)
    return evolve.filter_reason(plugin, state, text, args["scene"], args["mood"],
                                args["mood_score"], args["umo"])


def test_a3_length_bounds():
    plugin = _plugin()
    # 2026-11-24：她平时就 4-6 个字，下限从 6 放到 4；
    # 但「嗯嗯」这种纯语气仍然不收。
    assert _filter(plugin, "嗯嗯") == "length"
    assert _filter(plugin, "嗯嗯嗯") == "length"
    assert _filter(plugin, "这" * 41) == "length"
    assert _filter(plugin, "今天下班我走得有点晚") == ""
    assert _filter(plugin, "刚到家呢") == ""
    # 但「嗯」在 pack 的禁用词里：她拿「嗯……」开口的句子仍然不收。
    assert _filter(plugin, "嗯，去吧。") == "forbidden"


def test_a3_forbidden_and_tech_and_template():
    plugin = _plugin()
    assert _filter(plugin, "你美丽的老婆今天不想理你") == "forbidden"
    assert _filter(plugin, "作为一个人工智能我做不到") == "tech"
    assert _filter(plugin, "【今天真的好累啊】") == "template"
    # 动作括号是她说话的一部分（不是模板）：整句一起学，不剥
    assert evolve.spoken_text("（扒了口饭，看着手机）嗯，去吧。") == "嗯，去吧。"
    assert _filter(plugin, "（叹气）今天真的好累啊") == ""
    assert _filter(plugin, "（裹着毛衣在沙发上坐着，笑了一下）行啊，那你先到。") == ""


def test_a3_pool_stores_spoken_only():
    plugin = _plugin()
    state = store.load_state(plugin)
    out = evolve.add_to_pool(plugin, "（笑着摇头，但没驳他）行啊，饭桌得先擦一遍。", scene="home", state=state)
    assert out["ok"], out
    fresh = store.load_state(plugin)
    assert fresh["style_pool"][-1]["text"] == "行啊，饭桌得先擦一遍。"
    # 整句只有动作 → 剥完是空的，不入池
    only = evolve.add_to_pool(plugin, "（把碗放下）", scene="home")
    assert not only["ok"]


def test_a3_similar_to_fixed_or_his_words():
    plugin = _plugin()
    sample = pack.load_style_examples()[0]["text"]
    assert _filter(plugin, sample) == "similar"
    plugin.recent = [{"role": "user", "content": "我今天在公司被领导说了很久"}]
    assert _filter(plugin, "我今天在公司被领导说了") == "similar"


def test_a3_extreme_mood_not_learned():
    plugin = _plugin()
    assert _filter(plugin, "我真的受够了这种日子", mood="生气") == "extreme_mood"
    assert _filter(plugin, "我真的受够了这种日子", mood_score=8) == "extreme_mood"


def test_a3_daily_cap_three():
    plugin = _plugin()
    texts = ["今天下班我走得有点晚", "晚饭给你留了在锅里", "外面下雨记得带伞"]
    for text in texts:
        assert evolve.add_to_pool(plugin, text, scene="home_bed")["ok"], text
    assert evolve.add_to_pool(plugin, "我洗完澡先躺一会儿", scene="home_bed")["reason"] == "daily_cap"


# ---- A5 池上限/衰减/snapshot ----

def test_a5_trim_per_intent_and_snapshot():
    plugin = _plugin()
    state = store.load_state(plugin)
    state["style_pool"] = [
        {"text": "同意图句子%d" % i, "intent": "care", "score": 1.0 + i,
         "at": 100 + i, "last_used": 0} for i in range(8)
    ]
    evolve.trim_pool(plugin, state)
    assert len(state["style_pool"]) == 6
    assert len(state["style_archive"]) == 2
    out = evolve.add_to_pool(plugin, "今天下班我走得有点晚", scene="home_bed")
    assert out["ok"], out
    snaps = store.load_state(plugin)["snapshots"]
    assert snaps and snaps[-1]["reason"] == "before_style_pool"


def test_a5_decay_and_archive():
    plugin = _plugin()
    state = store.load_state(plugin)
    now = 2000000000
    state["style_pool"] = [
        {"text": "四十天没用的句子", "intent": "care", "score": 1.0,
         "at": now - 40 * 86400, "last_used": 0},
        {"text": "一百天没用的句子", "intent": "care", "score": 1.0,
         "at": now - 100 * 86400, "last_used": 0},
    ]
    archived = evolve.decay_pool(state, now=now, decay_days=30)
    assert archived == 1
    left = state["style_pool"][0]
    assert left["text"] == "四十天没用的句子"
    assert abs(left["score"] - (0.98 ** 10)) < 1e-9
    assert state["style_archive"][0]["text"] == "一百天没用的句子"
    # 同一天重复调用不重复衰减
    evolve.decay_pool(state, now=now, decay_days=30)
    assert abs(state["style_pool"][0]["score"] - (0.98 ** 10)) < 1e-9


# ---- A4 注入 ----

def test_a4_empty_pool_falls_back_to_fixed_33():
    plugin = _plugin()
    picked = evolve.learned_style_samples(plugin, "今天好累啊", limit=2)
    assert len(picked) == 2
    assert all(item["source"] == "fixed" for item in picked)


def test_a4_pool_first_and_prompt_budget():
    plugin = _plugin()
    state = store.load_state(plugin)
    now = evolve._now()
    state["style_pool"] = [
        {"text": "你先忙，我等你回来", "intent": "care", "score": 9.0,
         "at": now, "last_used": 0, "source": "auto"},
        {"text": "药吃了没，别硬扛", "intent": "care", "score": 8.0,
         "at": now, "last_used": 0, "source": "auto"},
    ]
    store.save_state(plugin, store.active_id(plugin), state)
    picked = evolve.learned_style_samples(plugin, "今天好累啊", limit=2)
    assert [item["text"] for item in picked] == ["你先忙，我等你回来", "药吃了没，别硬扛"]
    assert all(item["source"] == "pool" for item in picked)
    block = evolve.learned_style_block(plugin, "今天好累啊", limit=2)
    assert block.startswith("【你以前这样说过")
    assert len(block) <= 120
    # 抽中 +1 分：上面 samples 抽了一次、block 又抽了一次，共 +2。
    state = store.load_state(plugin)
    assert state["style_pool"][0]["score"] == 11.0


def test_a4_recently_used_rotates_first_sentence():
    plugin = _plugin()
    state = store.load_state(plugin)
    now = evolve._now()
    state["style_pool"] = [
        {"text": "你先忙，我等你回来", "intent": "care", "score": 1.0,
         "at": now, "last_used": 0},
        {"text": "药吃了没，别硬扛", "intent": "care", "score": 1.0,
         "at": now, "last_used": 0},
        {"text": "累了就靠一会儿，我在", "intent": "care", "score": 1.0,
         "at": now, "last_used": 0},
        {"text": "别熬太晚，明天还要早起", "intent": "care", "score": 1.0,
         "at": now, "last_used": 0},
    ]
    store.save_state(plugin, store.active_id(plugin), state)
    first = evolve.learned_style_block(plugin, "今天好累啊", limit=2)
    second = evolve.learned_style_block(plugin, "今天好累啊", limit=2)
    assert first != second
    assert first.split("\n")[1] != second.split("\n")[1]


class ApiPlugin(FakePlugin, __import__("amo_api_persona").PersonaApiMixin):
    pass


def test_a6_status_adds_fields_without_breaking_old():
    plugin = ApiPlugin({"persona_auto_evolve": True})
    state = store.load_state(plugin)
    state["candidates"] = [{"text": "自动候选", "source": "auto", "scene": "home_bed"},
                           {"text": "手动候选"}]
    store.save_state(plugin, store.active_id(plugin), state)
    out = plugin._persona_status()
    # 旧字段还在且仍是纯文本列表
    assert out["candidates"] == ["自动候选", "手动候选"]
    # 新字段只增不改
    assert out["candidate_rows"][0]["source"] == "auto"
    assert out["candidate_rows"][1]["source"] == "manual"
    for key in ("auto_evolve", "auto_approve", "today_added", "pool_count",
                "archive_count", "pending_review", "recent_auto"):
        assert key in out, key
    assert out["auto_evolve"] is True


def test_edit_pool_entry_updates_pool_and_log():
    plugin = _plugin()
    state = store.load_state(plugin)
    state["style_pool"] = [{"text": "今天下班有点晚", "at": 100, "intent": "daily", "score": 1.0}]
    state["evolve_log"] = [{"text": "今天下班有点晚", "at": 100, "scene": "home"}]
    store.save_state(plugin, store.active_id(plugin), state)

    fixed = "（把包一放）今天下班晚啦，累死了"
    spoken = "今天下班晚啦，累死了"
    out = evolve.edit_pool_entry(plugin, 0, fixed)
    assert out["ok"], out
    fresh = store.load_state(plugin)
    assert fresh["style_pool"][0]["text"] == spoken
    assert fresh["evolve_log"][-1]["text"] == spoken

    # 改坏了就拒绝，且不动原文
    assert evolve.edit_pool_entry(plugin, 0, "嗯")["reason"] == "length"
    assert evolve.edit_pool_entry(plugin, 0, "嗯，知道了")["reason"] == "forbidden"
    assert evolve.edit_pool_entry(plugin, 9, "随便改一句")["reason"] == "empty"
    assert store.load_state(plugin)["style_pool"][0]["text"] == spoken


def test_auto_approve_goes_straight_to_pool():
    plugin = _plugin(persona_auto_approve=True)
    evolve.record_pending(plugin, "你先忙，我等你回来", "qq:FriendMessage:1")
    out = evolve.review_pending(plugin, "哈哈好可爱")
    assert out["ok"], out
    state = store.load_state(plugin)
    assert len(state["style_pool"]) == 1
    assert state["style_pending"] == []


def test_manual_candidate_joins_the_same_queue():
    plugin = _plugin()
    out = store.add_candidate(plugin, "你回来路上慢点", scene="general")
    assert out["ok"], out
    state = store.load_state(plugin)
    assert state["candidates"] == []
    assert [row["text"] for row in state["style_pending"]] == ["你回来路上慢点"]
    assert state["style_pending"][0]["source"] == "manual"


def test_card_action_line_follows_switch():
    data = {"card": {"core": "认定你", "language": {"rules": ["不写动作旁白", "句子要短"]}}}
    on = pack.card_text(data, allow_action=True)
    off = pack.card_text(data, allow_action=False)
    # 动作只由开关说一次：开着的时候卡里不许再留「不写动作」
    assert "不写动作旁白" not in on
    assert "可以带一句很短的括号动作" in on
    assert "不写动作旁白" in off
    assert "可以带一句很短的括号动作" not in off
    # 别的语言规则照旧
    assert "句子要短" in on and "句子要短" in off


def test_pending_needs_approval_before_pool():
    plugin = _plugin()
    state = store.load_state(plugin)
    out = evolve.add_to_pool(plugin, "行啊，饭桌得先擦一遍。", scene="home", state=state,
                             pending=True)
    assert out["ok"], out
    fresh = store.load_state(plugin)
    assert len(fresh["style_pending"]) == 1
    assert len(fresh["style_pool"]) == 0
    st = evolve.status(plugin)
    assert len(st["pending_pool"]) == 1
    assert st["pending_pool"][0]["log_index"] == 0

    # 未通过前不参与注入（池子空 → 回落固定例句，不是我们自己这条）
    before = evolve.learned_style_samples(plugin, "晚饭吃了吗", mark=False)
    assert all(row.get("source") != "pool" for row in before)

    assert evolve.approve_pending(plugin, 0)["ok"]
    fresh = store.load_state(plugin)
    assert fresh["style_pending"] == []
    assert len(fresh["style_pool"]) == 1
    picked = evolve.learned_style_samples(plugin, "晚饭吃了吗", mark=False)
    assert picked and picked[0]["text"] == "行啊，饭桌得先擦一遍。"


def test_learned_style_block_uses_pool():
    plugin = _plugin()
    state = store.load_state(plugin)
    # at 必须是近期：池子按天数衰减，1970 年的时间戳会被当成过期归档
    state["style_pool"] = [{"text": "行啊，饭桌得先擦一遍。",
                            "at": int(__import__("time").time()),
                            "intent": pack.style_intent("晚饭吃了吗"), "score": 1.0}]
    store.save_state(plugin, store.active_id(plugin), state)
    block = evolve.learned_style_block(plugin, "晚饭吃了吗")
    assert "你以前这样说过" in block
    assert "饭桌得先擦一遍" in block
    assert len(block) <= 120


def test_style_pool_is_wired_into_chat_prompt():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "amo_llm.py"), encoding="utf-8") as fh:
        src = fh.read()
    # 正常聊天的入口必须自己取例句池，否则池子只是陈列柜
    assert "amo_llm.on_llm_request.style_pool" in src
    # 2026-11-24：两条路共用 _style_block_for，不再各写一份
    assert src.count("_style_block_for(") >= 3
    assert "learned_style_block as _learned" in src


def test_old_state_missing_new_fields_gets_defaults():
    plugin = _plugin()
    folder = store.ensure_profile(plugin)
    with open(os.path.join(folder, "state.json"), "w", encoding="utf-8") as fh:
        fh.write('{"version": 1, "candidates": []}')
    state = store.load_state(plugin)
    for key in ("pending_review", "style_pool", "style_archive", "last_used",
                "used_seq", "evolve_stats", "evolve_log"):
        assert key in state, key
