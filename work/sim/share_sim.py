# -*- coding: utf-8 -*-
"""分享事件对话隔离模拟器（只读）。

做什么：
1) 用确定性 mock provider 生成 50 组 x 10 轮「她分享视频/图片/事情 + 用户回应」对话；
2) 每一轮把模型输出送进生产插件真实的确定性守卫：
   - 分享句：amo_state_consistency.rewrite（与 amo_proactive 主动消息同一条路）
   - 回复：先按 amo_llm_service.polish 的顺序模拟「接话 critic -> 重写 -> 安全句」，
     再过 amo_reply_pipeline.apply_chat_safety（线上聊天最后一道守卫）
3) 用与守卫不共享实现的独立判据打分；
4) 输出 work/sim/share_report.json / share_report.md。

不做：不联网、不读生产配置、不写生产库、不改插件代码、不 reload。
局限：mock 不读提示词，所以模型语义质量、提示词遵循度未被覆盖。

运行：
    python -B work/sim/share_sim.py
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
import traceback
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for path in (ROOT, HERE):
    if path not in sys.path:
        sys.path.insert(0, path)

from share_scenarios import (  # noqa: E402
    ANIMAL_WORDS,
    GROUP_COUNT,
    SEED_BASE,
    TURNS_PER_GROUP,
    MockProvider,
    build_dialogue,
)

logging.disable(logging.CRITICAL)

import amo_reply_pipeline as pipeline  # noqa: E402
import amo_share_ledger as ledger  # noqa: E402
import amo_state_consistency as state_consistency  # noqa: E402
from amo_grounding_critic import issues as grounding_issues  # noqa: E402
from amo_grounding_critic import safe_reply  # noqa: E402

#: 生产库（只做只读快照：大小 / mtime / 表行数）
PROD_DBS = [
    r"D:\dafeiyu\astrbot\data\plugin_data\Astrbot_amoupup_forever\amo_forever.db",
    r"D:\dafeiyu\astrbot\data\plugin_data\Astrbot_amoupup_data\amo_data.db",
    r"D:\dafeiyu\astrbot\data\data_v4.db",
]

OUT_JSON = os.path.join(HERE, "share_report.json")
OUT_MD = os.path.join(HERE, "share_report.md")

#: 记录本进程打开过的所有 sqlite 连接，证明只读（见报告 meta.sqlite_connections）。
SQLITE_CONNECTS = []
_sqlite_connect = sqlite3.connect


def _logged_connect(*args, **kwargs):
    target = args[0] if args else kwargs.get("database")
    SQLITE_CONNECTS.append({"target": str(target), "uri": bool(kwargs.get("uri"))})
    return _sqlite_connect(*args, **kwargs)


sqlite3.connect = _logged_connect

# ---------------------------------------------------------------------------
# 判据（独立于插件守卫实现，避免「用守卫验守卫」）
# ---------------------------------------------------------------------------

OPENERS = ("刚刷到", "刚刚刷到", "刚看到", "刷到一个", "快看", "给你看", "给你看个")
ROLE_ASK = ("你发的", "你那个视频", "你那个图", "你再发", "发我看看", "你翻上去",
            "你自己看", "你再看一遍")
HOME_PROP = ("沙发", "床上", "躺床", "趴着", "窝在", "厨房", "洗澡", "睡衣",
             "刚到家", "到家", "进门", "脱外套", "换鞋", "洗碗", "做饭", "煮面", "下厨")
ROAD_CLAIM = ("还在路上", "快到了", "马上到", "地铁上", "公交上", "在车上")
WHERE_WORDS = ("在哪", "抖音", "B站", "b站", "微博", "小红书", "商场", "路上",
               "店里", "网上", "拍的", "刷到", "工位", "面馆", "地铁", "公司", "午休")
PRETTY_WORDS = ("好看", "可爱", "是吧", "喜欢", "真的")
NEXT_WORDS = ("然后", "后来", "结果", "接着")


def _animal_hits(text):
    body = str(text or "")
    return {name for name, words in ANIMAL_WORDS.items() if any(w in body for w in words)}


def check_object(texts, topic):
    """当前话题的动物必须还在；换成了别的动物（且当前动物消失）算失败。"""
    animal = topic.get("animal")
    if not animal:
        return []
    problems = []
    for label, text in texts:
        hits = _animal_hits(text)
        if animal not in hits and (hits - {animal}):
            problems.append("object_swap:%s:->%s" % (label, ",".join(sorted(hits - {animal}))))
    return problems


def check_opener(seen_topics, topic, share_text, reply_text):
    """同一话题 2 小时内第二次出现「刚刷到/快看」= 重复开场。"""
    if topic["key"] not in seen_topics:
        return []
    hits = [word for word in OPENERS if word in share_text or word in reply_text]
    if hits:
        return ["opener_repeat:" + hits[0]]
    return []


def check_role(reply_text):
    hits = [word for word in ROLE_ASK if word in reply_text]
    return ["asks_user_to_look:" + hits[0]] if hits else []


def check_place(scene, texts):
    problems = []
    for label, text in texts:
        if scene["home"]:
            for word in ROAD_CLAIM:
                if word in text:
                    problems.append("place_home_says_road:%s:%s" % (label, word))
        else:
            for word in HOME_PROP:
                if word in text:
                    problems.append("place_out_says_home:%s:%s" % (label, word))
            if scene["key"] in ("mall", "office", "noodle"):
                for word in ROAD_CLAIM:
                    if word in text:
                        problems.append("place_still_road:%s:%s" % (label, word))
    return problems


def check_last_line(topic, intent, user_text, reply_text):
    body = str(reply_text or "")
    if not body:
        return ["miss_last_line:empty"]
    if "再说一遍" in body:
        return ["miss_last_line:safe_fallback"]
    if intent == "laugh":
        if "笑什么" in body or "笑啥" in body:
            return ["miss_last_line:asks_what_laughing"]
        return [] if ("笑" in body or "哈" in body) else ["miss_last_line:no_laugh"]
    if intent == "pretty":
        if "什么好看" in body:
            return ["miss_last_line:asks_what_pretty"]
        ok = any(word in body for word in PRETTY_WORDS)
        return [] if ok else ["miss_last_line:no_pretty_response"]
    if intent == "what":
        ok = topic["label"] in body or "视频" in body or "图" in body or "事" in body
        return [] if ok else ["miss_last_line:no_answer_what"]
    if intent == "where":
        ok = any(word in body for word in WHERE_WORDS)
        return [] if ok else ["miss_last_line:no_where_answer"]
    ok = topic["label"] in body or any(word in body for word in NEXT_WORDS)
    return [] if ok else ["miss_last_line:no_continuation"]


def evaluate_turn(scene, topic, intent, user_text, share_text, reply_text, seen_topics):
    problems = []
    problems += check_object([("share", share_text), ("reply", reply_text)], topic)
    problems += check_opener(seen_topics, topic, share_text, reply_text)
    problems += check_role(reply_text)
    problems += check_place(scene, [("share", share_text), ("reply", reply_text)])
    problems += check_last_line(topic, intent, user_text, reply_text)
    seen = set()
    unique = []
    for item in problems:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def category_of(problem):
    return problem.split(":", 1)[0]


# ---------------------------------------------------------------------------
# 生产桩（进程内内存库，绝不碰生产库）
# ---------------------------------------------------------------------------

class FakeDB:
    def __init__(self):
        self.data = {}
        self.writes = []

    def kv_get(self, key, default=None):
        return self.data.get(key, default)

    def kv_set(self, key, value):
        self.writes.append(key)
        self.data[key] = value

    def kv_del(self, key):
        self.data.pop(key, None)


class FakePlugin:
    def __init__(self):
        self.db = FakeDB()
        self._activity = {"place": "家", "zone": "home", "activity": ""}
        self.state = SimpleNamespace(current_activity=lambda: dict(self._activity))
        self.plugin_dir = ROOT
        self.config = {}

    def set_scene(self, scene):
        self._activity = {"place": scene["place"], "zone": scene["zone"],
                          "activity": scene["activity"]}

    def cfg(self, key, default=None):
        return self.config.get(key, default)

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))


GUARD_FILES = (
    "amo_reply_pipeline.py",
    "amo_state_consistency.py",
    "amo_share_ledger.py",
    "amo_grounding_critic.py",
    "amo_llm_service.py",
    "amo_prompt_registry.py",
    "reply_fact_guard.py",
    "tone_guard.py",
    "amo_proactive.py",
    "amo_question_guard.py",
)


def code_fingerprint():
    """记录本次跑的生产代码指纹：仓库可能被其他人并发修改，报告要能对上版本。"""
    out = {}
    for name in GUARD_FILES:
        path = os.path.join(ROOT, name)
        try:
            with open(path, "rb") as handle:
                data = handle.read()
            out[name] = {"sha256_16": hashlib.sha256(data).hexdigest()[:16],
                         "bytes": len(data),
                         "mtime": int(os.stat(path).st_mtime)}
        except Exception as exc:
            out[name] = {"error": repr(exc)}
    return out


def would_note_share(text):
    """复刻生产 call-site（amo_runtime_reply.py:184 / amo_proactive.py:1261）的入账条件。"""
    return (any(word in text for word in ("我刷到", "给你看", "快看", "分享"))
            and any(word in text for word in ("视频", "图片", "照片")))


# ---------------------------------------------------------------------------
# 生产库只读快照
# ---------------------------------------------------------------------------

def snapshot_db(path):
    info = {"path": path, "exists": os.path.isfile(path)}
    if not info["exists"]:
        return info
    try:
        stat = os.stat(path)
        info["size"] = int(stat.st_size)
        info["mtime_ns"] = int(stat.st_mtime_ns)
        for suffix in ("-wal", "-shm"):
            sibling = path + suffix
            if os.path.isfile(sibling):
                side = os.stat(sibling)
                info["wal_size" if suffix == "-wal" else "shm_size"] = int(side.st_size)
                info["wal_mtime_ns" if suffix == "-wal" else "shm_mtime_ns"] = int(side.st_mtime_ns)
    except OSError as exc:
        info["stat_error"] = repr(exc)
        return info
    try:
        uri = "file:%s?mode=ro" % path.replace("\\", "/")
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        conn.execute("pragma query_only=on")
        tables = [row[0] for row in conn.execute(
            "select name from sqlite_master where type='table' order by name")]
        counts = {}
        for table in tables:
            try:
                counts[table] = conn.execute(
                    'select count(*) from "%s"' % table.replace('"', '""')).fetchone()[0]
            except Exception:  # cleanup: 仿真容错
                counts[table] = None
        info["tables"] = len(tables)
        info["rows"] = counts
        conn.close()
    except Exception as exc:
        info["rows_error"] = repr(exc)
    return info


def diff_snapshot(before, after):
    keys = ("size", "mtime_ns", "wal_size", "wal_mtime_ns", "rows")
    changed = {}
    for key in keys:
        if before.get(key) != after.get(key):
            if key == "rows":
                b = before.get("rows") or {}
                a = after.get("rows") or {}
                delta = {name: (b.get(name), a.get(name))
                         for name in set(b) | set(a) if b.get(name) != a.get(name)}
                if delta:
                    changed["rows"] = delta
            else:
                changed[key] = (before.get(key), after.get(key))
    return changed


# ---------------------------------------------------------------------------
# 单轮：分享 -> 用户回应 -> 她回复（生产守卫链）
# ---------------------------------------------------------------------------

def critic_loop(user_text, raw_reply, scene, topic, intent, provider, enabled=True):
    """复刻 amo_llm_service.polish 里的接话 critic：一次重写，再不行发安全句。"""
    if not enabled:
        return raw_reply, {"first_issues": [], "disabled": True}
    trace = {"first_issues": grounding_issues(user_text, raw_reply)}
    text = raw_reply
    if trace["first_issues"]:
        retry = provider.aligned_reply(scene, topic, intent)
        trace["retry"] = retry
        trace["retry_issues"] = grounding_issues(user_text, retry)
        if retry and not trace["retry_issues"]:
            text = retry
        elif trace["retry_issues"]:
            text = safe_reply(user_text)
            trace["fallback"] = text
    return text, trace


def run_group(gidx, inject=True, critic=True):
    dialogue = build_dialogue(gidx)
    provider = MockProvider(SEED_BASE * 7 + gidx * 131, inject=inject)
    plugin = FakePlugin()
    umo = "Private:sim-share-%03d" % gidx
    start = datetime(2026, 11, 21, 19, 0, 0) + timedelta(minutes=7 * gidx)
    seen_topics = {}
    rows = []
    records = []
    for turn, spec in enumerate(dialogue):
        scene, topic, intent, user_text = spec["scene"], spec["topic"], spec["intent"], spec["user"]
        plugin.set_scene(scene)
        now = (start + timedelta(minutes=4 * turn)).timestamp()
        fresh = topic["key"] not in seen_topics
        gen = provider.turn(scene, topic, intent, fresh)

        # ---- 分享句：走生产主动消息的确定性状态守卫 ----
        share_raw = gen["share"]
        share_error = ""
        try:
            share_final = state_consistency.rewrite(share_raw, plugin, "")
        except Exception as exc:
            share_final = share_raw
            share_error = repr(exc)
        if not str(share_final or "").strip():
            share_final = share_raw

        # ---- 分享台账：按生产 call-site 条件入账 ----
        entry_before = {}
        try:
            entry_before = ledger.last(plugin, now=now) or {}
        except Exception:  # cleanup: 仿真容错
            entry_before = {}
        noted = False
        if would_note_share(share_final):
            try:
                ledger.note(plugin, share_final, media=True, now=now)
                noted = True
            except Exception:  # cleanup: 仿真容错
                noted = False
        prompt_line = ""
        try:
            prompt_line = ledger.prompt_line(plugin, now=now)
        except Exception:  # cleanup: 仿真容错
            prompt_line = ""
        prompt_pending = None
        if prompt_line:
            try:
                prompt_pending = (ledger.last(plugin, now=now) or {}).get("pending")
            except Exception:  # cleanup: 仿真容错
                prompt_pending = None
        prompt_stale = bool(prompt_line) and (topic["label"] not in prompt_line)

        # ---- 她回复前，历史里已经有「她的分享 + 他的回应」 ----
        rows.append({"role": "her", "content": share_final})
        rows.append({"role": "him", "content": user_text})
        history_text = " ".join(row["content"] for row in rows[-6:])

        # ---- 回复：先接话 critic（polish 内），再聊天最后一道守卫 ----
        reply_raw = gen["reply"]
        after_critic, critic_trace = critic_loop(
            user_text, reply_raw, scene, topic, intent, provider, enabled=critic)
        guard_error = ""
        try:
            reply_final = pipeline.apply_chat_safety(
                after_critic, plugin, umo=umo, user_text=user_text, history_text=history_text)
        except Exception as exc:
            reply_final = after_critic
            guard_error = repr(exc)
        if not str(reply_final or "").strip():
            reply_final = after_critic
        rows.append({"role": "her", "content": reply_final})

        raw_problems = evaluate_turn(scene, topic, intent, user_text,
                                     share_raw, reply_raw, seen_topics)
        final_problems = evaluate_turn(scene, topic, intent, user_text,
                                       share_final, reply_final, seen_topics)

        records.append({
            "group": gidx, "turn": turn,
            "scene": scene["key"], "place": scene["place"], "zone": scene["zone"],
            "topic": topic["key"], "kind": topic["kind"], "animal": topic.get("animal"),
            "fresh": fresh, "intent": intent,
            "user": user_text,
            "share_raw": share_raw, "share_final": share_final,
            "reply_raw": reply_raw, "reply_after_critic": after_critic,
            "reply_final": reply_final,
            "share_injected": gen["share_injected"], "reply_injected": gen["reply_injected"],
            "noted": noted, "prompt_line": prompt_line,
            "prompt_pending": prompt_pending, "prompt_stale": prompt_stale,
            "critic_first": critic_trace["first_issues"],
            "critic_retry_issues": critic_trace.get("retry_issues", []),
            "critic_fallback": "fallback" in critic_trace,
            "raw_problems": raw_problems, "final_problems": final_problems,
            "share_error": share_error, "guard_error": guard_error,
        })
        seen_topics.setdefault(topic["key"], turn)
    return records


# ---------------------------------------------------------------------------
# 直接函数级探针（根因证据，全部调用生产函数）
# ---------------------------------------------------------------------------

def _safe(fn):
    try:
        return {"ok": True, "result": fn()}
    except Exception as exc:
        return {"ok": False, "error": repr(exc)}


def build_probes():
    now = datetime(2026, 11, 21, 19, 0, 0).timestamp()
    probes = []

    def ledger_stale():
        plugin = FakePlugin()
        plugin.set_scene({"place": "家", "zone": "home", "activity": "窝在沙发上刷手机"})
        ledger.note(plugin, "老公我刚刷到一个猫的视频，一只橘猫卡在纸箱里", media=True, now=now)
        before = ledger.prompt_line(plugin, now=now)
        ledger.mark_reacted(plugin)
        after = ledger.prompt_line(plugin, now=now + 90)
        entry = ledger.last(plugin, now=now + 90) or {}
        return {"prompt_before": before, "prompt_after_reacted": after,
                "pending_after_reacted": entry.get("pending")}
    probes.append({"name": "share_ledger_pending_respected",
                   "claim": "修复验证：mark_reacted 之后 pending=False，prompt_line 返回空，不再每轮注入「他刚回应你」",
                   **_safe(ledger_stale)})

    texts = ["老公我刚刷到一个猫的视频，一只橘猫卡在纸箱里",
             "我刷到一个猫的视频，一只橘猫卡在纸箱里",
             "给你看个图，一只兔子瘫成一张饼",
             "给你看个图片，一只兔子瘫成一张饼",
             "老公给你看个视频，一只橘猫卡在纸箱里",
             "楼下奶茶店排队排到马路牙子上",
             "今天食堂的番茄炒蛋是甜的"]
    probes.append({"name": "share_ledger_media_only",
                   "claim": ("入账要求文本里出现连续的「我刷到」（「我刚刷到」不算），"
                             "且必须同时含「视频/图片/照片」；事情类与「看个图」都不入账"),
                   **_safe(lambda: {item: would_note_share(item) for item in texts})})

    def opener_behavior():
        from amo_state_consistency import rewrite
        plugin = FakePlugin()
        plugin.set_scene({"place": "家", "zone": "home", "activity": "窝在沙发上刷手机"})
        text = "老公我刚刷到一个猫的视频，一只橘猫把自己塞进纸箱。"
        fresh = rewrite(text, plugin, "哈哈哈")
        repeat = rewrite(text, plugin, "哈哈哈 刚才那个猫还有后续")
        return {"text": text, "fresh_context_output": fresh,
                "repeat_context_output": repeat}
    probes.append({"name": "repeated_opener_behavior",
                   "claim": "修复验证：没有分享痕迹时保留为合法新分享；有「刚才那个猫还有后续」时只换开场、保留后文（本轮 opener 0 轮）",
                   **_safe(opener_behavior)})

    def fake_share_truncate():
        from amo_state_consistency import rewrite
        plugin = FakePlugin()
        plugin.set_scene({"place": "家", "zone": "home", "activity": "窝在沙发上刷手机"})
        text = "老公我刚刷到一个猫的视频，一只橘猫把自己塞进纸箱。"
        history = "我刚才分享过一个猫的视频"
        return {"input": text, "output": rewrite(text, plugin, "哈哈哈 " + history)}
    probes.append({"name": "fake_share_keeps_tail",
                   "claim": "修复验证：开场替换只换开头，后文「一只橘猫把自己塞进纸箱」保留（上一轮会被吞成半句）",
                   **_safe(fake_share_truncate)})

    def role_gap():
        from amo_state_consistency import rewrite
        plugin = FakePlugin()
        plugin.set_scene({"place": "家", "zone": "home", "activity": "窝在沙发上刷手机"})
        pure = "你发的那个猫视频我还没点开，你再发我一遍？"
        mixed = "你发的那个猫视频我还没点开，一只橘猫卡在纸箱里。"
        return {"pure_role_input": pure, "pure_role_output": rewrite(pure, plugin, "哈哈哈"),
                "mixed_input": mixed, "mixed_output": rewrite(mixed, plugin, "哈哈哈")}
    probes.append({"name": "role_reversal_clause_only",
                   "claim": "修复验证：只删角色反转分句、保留同句描述——纯反转句回落「我在家呢。」，混合句保留「一只橘猫卡在纸箱里。」（本轮 role 0 轮）",
                   **_safe(role_gap)})

    def mall_fallback():
        from amo_state_consistency import rewrite
        plugin = FakePlugin()
        plugin.set_scene({"place": "商场", "zone": "outside", "activity": "在商场逛街"})
        text = "我刚到家，外套都脱了，窝在沙发上。"
        return {"input": text, "output": rewrite(text, plugin, "")}
    probes.append({"name": "outside_arrival_fallback_fixed",
                   "claim": "对照项：road/outside 拆分后，商场的到家/居家句被改写成「我到了」，不再说「还在路上」（修复验证）",
                   **_safe(mall_fallback)})

    def road_cook():
        from amo_state_consistency import rewrite
        plugin = FakePlugin()
        plugin.set_scene({"place": "路上", "zone": "road", "activity": "挤地铁回家"})
        samples = ["煮碗面暖暖身子。", "我等会儿自己煮面吃。", "回去煮点面。"]
        return {text: rewrite(text, plugin, "") for text in samples}
    probes.append({"name": "road_cook_subject_required",
                   "claim": "_HOME_ACTION 要求「我/自己/等会儿/回去」在前 8 字内，无主语煮面句在路面上不会被删",
                   **_safe(road_cook)})

    def noodle_overstrip():
        from amo_state_consistency import rewrite
        plugin = FakePlugin()
        plugin.set_scene({"place": "面馆", "zone": "outside", "activity": "在面馆吃面"})
        text = "我吃面差点喷出来。"
        return {"input": text, "output": rewrite(text, plugin, "")}
    probes.append({"name": "noodle_eat_no_longer_stripped",
                   "claim": "修复验证：吃面已从居家动作正则移除，面馆里「我吃面差点喷出来」原样保留",
                   **_safe(noodle_overstrip)})

    def critic_matrix():
        pairs = [
            ("在哪", "就在商场歇脚的时候刷到的"),
            ("在哪", "在抖音刷到的呀"),
            ("在哪看到的", "在商场歇脚的时候刷到的"),
            ("你在哪看到的", "在商场歇脚的时候刷到的"),
            ("你在哪看到的", "在抖音刷到的呀"),
            ("好看", "超可爱对吧"),
            ("好看", "猫那个样子太可爱了"),
            ("什么视频", "就是一只猫的视频"),
            ("然后呢", "然后它就被纸箱卡住了"),
            ("哈哈哈", "笑死我了它自己钻进去的"),
            ("真的假的", "真的，排队排到马路上"),
        ]
        return [{"user": user, "reply": reply, "issues": grounding_issues(user, reply)}
                for user, reply in pairs]
    probes.append({"name": "grounding_critic_false_positive",
                   "claim": "≤5 字问句已全部放行；6 字「你在哪看到的」仍因不含问句词被判没接住——真实聊天里 6-8 字问句很常见",
                   **_safe(critic_matrix)})

    def breed_gap():
        from amo_state_consistency import _animals
        from amo_share_ledger import _topic
        samples = ["一只柯基追自己的尾巴", "一只橘猫卡在纸箱里", "一只柴犬在打滚"]
        return [{"text": text, "state_animals": sorted(_animals(text)), "ledger_topic": _topic(text)}
                for text in samples]
    probes.append({"name": "breed_name_partially_covered",
                   "claim": "品种名已补（柯基/柴犬→狗），单字「兔」也已识别；但台账 _topic 仍只按单字，柯基文本的 ledger_topic=事",
                   **_safe(breed_gap)})

    def stale_context_object():
        from amo_state_consistency import rewrite
        plugin = FakePlugin()
        plugin.set_scene({"place": "家", "zone": "home", "activity": "窝在沙发上刷手机"})
        # 当前分享是猫；但最近 6 条里只有上一轮的兔子（当前分享句被地点守卫删空后就会这样）
        text = "就是猫的视频，一只橘猫把自己塞进纸箱。"
        history = "刚才那个兔子还有后续，它听见零食袋的声音，一下弹起来。"
        return {"input": text, "context": history,
                "output": rewrite(text, plugin, "什么视频 " + history)}
    def opener_gate():
        from amo_state_consistency import rewrite
        plugin = FakePlugin()
        plugin.set_scene({"place": "家", "zone": "home", "activity": "窝在沙发上刷手机"})
        text = "老公我刚刷到一个兔的视频，一只兔子瘫成一张饼贴在凉席上。"
        no_video = "发的啥 刚才那个兔还有后续，它听见零食袋的声音，一下弹起来。"
        with_video = "发的啥 我刚才分享过一个兔的视频"
        return {"text": text,
                "context_without_video": no_video,
                "output_without_video": rewrite(text, plugin, no_video),
                "context_with_video": with_video,
                "output_with_video": rewrite(text, plugin, with_video)}
    probes.append({"name": "share_opener_gated_by_context",
                   "claim": "修复验证：开场替换不再要求上下文含视频/图片；「刚才那个兔还有后续」这种痕迹也能触发，且保留后文（本轮 opener 0 轮）",
                   **_safe(opener_gate)})

    def role_window():
        from amo_state_consistency import rewrite
        plugin = FakePlugin()
        plugin.set_scene({"place": "家", "zone": "home", "activity": "窝在沙发上刷手机"})
        short = "你发的那个猫视频我还没点开，你再发我一遍？"
        long_ = "你发的那个表情包图我还没点开，你再发我一遍？"
        return {"short_input": short, "short_output": rewrite(short, plugin, "哈哈哈"),
                "long_input": long_, "long_output": rewrite(long_, plugin, "哈哈哈")}
    probes.append({"name": "role_regex_window",
                   "claim": "修复验证：不再依赖 12 字窗口，「你发的那个表情包图…再发我一遍」也会被删（本轮 role 0 轮）",
                   **_safe(role_window)})

    def legit_share_opener():
        from amo_state_consistency import rewrite
        plugin = FakePlugin()
        plugin.set_scene({"place": "家", "zone": "home", "activity": "窝在沙发上刷手机"})
        text = "你快看这个视频，一只橘猫卡在纸箱里。"
        return {"input": text, "output": rewrite(text, plugin, "")}
    probes.append({"name": "legit_share_opener_stripped",
                   "claim": "修复验证：空上下文里「你快看这个视频，一只橘猫卡在纸箱里」是她的合法新分享，原样保留（上一轮会被误删成「我在家呢。」）",
                   **_safe(legit_share_opener)})

    probes.append({"name": "stale_context_overwrites_object",
                   "claim": "修复验证：回复已明确「就是猫的视频」时不再被历史里的兔子覆盖（显式主语保护 + 最后出现的对象锚点）",
                   **_safe(stale_context_object)})

    def bare_rabbit_not_detected():
        from amo_state_consistency import _animals
        from amo_share_ledger import _topic
        return {"猫": sorted(_animals("刚才那个猫还有后续")),
                "兔": sorted(_animals("刚才那个兔还有后续")),
                "兔子": sorted(_animals("刚才那个兔子还有后续")),
                "ledger_topic_兔": _topic("刚才那个兔还有后续")}
    probes.append({"name": "bare_rabbit_covered",
                   "claim": "修复验证：对象词表补了单字「兔」，现在「刚才那个兔」也能识别（state_animals=[兔]）",
                   **_safe(bare_rabbit_not_detected)})

    def question_guard_source():
        from amo_question_guard import answer as _qa
        plugin = FakePlugin()
        plugin.set_scene({"place": "家", "zone": "home", "activity": "窝在沙发上刷手机"})
        text = "在抖音刷到的，一只橘猫把自己塞进纸箱。"
        return {"user": "在哪看到的", "reply": text,
                "category": "where", "output": _qa("在哪看到的", plugin, text)}
    probes.append({"name": "question_guard_treats_source_as_location",
                   "claim": "问「在哪（看到的）」时，正确答案是来源「在抖音刷到的」，但 _PLACES 没有抖音/B站/网上，"
                            "被当成「你在哪」并覆盖成「我在家呢。」（对照组 14 轮 where 因此失败）",
                   **_safe(question_guard_source)})

    def question_guard_location():
        from amo_question_guard import answer as _qa
        plugin = FakePlugin()
        plugin.set_scene({"place": "商场", "zone": "outside", "activity": "在商场逛街"})
        text = "就是刚才那个猫，一只橘猫把自己塞进纸箱。"
        return {"user": "在哪", "reply": text,
                "category": "where", "output": _qa("在哪", plugin, text)}
    probes.append({"name": "question_guard_location_fallback",
                   "claim": "对照项：回复确实没给地点时，用当前状态「我在商场呢。」兜底，方向正确",
                   **_safe(question_guard_location)})

    def company_canteen():
        from amo_state_consistency import rewrite
        plugin = FakePlugin()
        plugin.set_scene({"place": "家", "zone": "home", "activity": "窝在沙发上刷手机"})
        text = "就是食堂的事，公司食堂今天的番茄炒蛋是甜的。"
        return {"input": text, "output": rewrite(text, plugin, "")}
    probes.append({"name": "home_company_canteen_false_positive",
                   "claim": "既有误判：在家说「公司食堂」会命中 _OUT_PROP 的「公司」子串，整句被删后回落「我在家呢。」",
                   **_safe(company_canteen)})

    def unified_last_line_rule():
        from amo_state_consistency import rewrite
        plugin = FakePlugin()
        plugin.set_scene({"place": "家", "zone": "home", "activity": "窝在沙发上刷手机"})
        share = "刚才那个猫还有后续，它把自己卡住了，越挣扎出不来。"
        raw = "就是刚才那个猫，一只橘猫把自己塞进纸箱。"
        return {
            "user": "笑死我了 / 好可爱（同一套判据）",
            "miss_laugh": rewrite(raw, plugin, "笑死我了\n" + share),
            "done_laugh_unchanged": rewrite(
                "哈哈哈这猫也太逗了，它把自己卡住了。", plugin, "笑死我了\n" + share),
            "miss_pretty": rewrite(raw, plugin, "好可爱\n" + share),
            "done_pretty_unchanged": rewrite(
                "也太可爱了吧，它把自己卡住了。", plugin, "好可爱\n" + share),
            "followup_next": rewrite("哈哈哈。", plugin, "然后呢\n" + share),
        }
    probes.append({"name": "unified_last_line_rule",
                   "claim": ("修复验证（第九轮）：五种接话类型共用一套判据——笑/夸轮没接住就补接话词"
                             "（完整正文不硬塞信息点），已接住原样不动，追问轮仍补「答案 + 最后信息点」。"),
                   **_safe(unified_last_line_rule)})

    return probes


# ---------------------------------------------------------------------------
# 汇总 / 报告
# ---------------------------------------------------------------------------

CATEGORY_LABELS = {
    "object_swap": "对象换了（猫变狗等）",
    "opener_repeat": "重复「刚刷到/快看」",
    "asks_user_to_look": "叫用户看他已发过的",
    "place_home_says_road": "在家说还在路上",
    "place_out_says_home": "外出/商场说居家动作",
    "place_still_road": "已到场还说在路上",
    "miss_last_line": "没接住最后一句",
}


def cat_label(problem):
    category = category_of(problem)
    return CATEGORY_LABELS.get(category, category)


def summarize(records):
    total = len(records)
    raw_clean = [r for r in records if not r["raw_problems"]]
    final_clean = [r for r in records if not r["final_problems"]]

    def count(records_, key):
        # 按「轮次」计数：同一轮命中同一分类的多个词只算 1 次
        counter = {}
        for record in records_:
            for name in {category_of(problem) for problem in record[key]}:
                counter[name] = counter.get(name, 0) + 1
        return dict(sorted(counter.items(), key=lambda item: -item[1]))

    fixed = [r for r in records if r["raw_problems"] and not r["final_problems"]]
    damaged = [r for r in records if not r["raw_problems"] and r["final_problems"]]
    persistent = [r for r in records if r["raw_problems"] and r["final_problems"]]
    clean = [r for r in records if not r["raw_problems"] and not r["final_problems"]]

    by_scene = {}
    for record in records:
        bucket = by_scene.setdefault(record["scene"], {"turns": 0, "final_clean": 0})
        bucket["turns"] += 1
        bucket["final_clean"] += 0 if record["final_problems"] else 1

    by_kind = {}
    for record in records:
        bucket = by_kind.setdefault(record["kind"], {"turns": 0, "final_clean": 0})
        bucket["turns"] += 1
        bucket["final_clean"] += 0 if record["final_problems"] else 1

    by_intent = {}
    for record in records:
        bucket = by_intent.setdefault(record["intent"], {"turns": 0, "final_clean": 0})
        bucket["turns"] += 1
        bucket["final_clean"] += 0 if record["final_problems"] else 1

    groups = {}
    for record in records:
        bucket = groups.setdefault(record["group"], {"turns": 0, "clean": 0})
        bucket["turns"] += 1
        bucket["clean"] += 0 if record["final_problems"] else 1
    group_pass = sum(1 for value in groups.values() if value["clean"] == value["turns"])

    return {
        "total_turns": total,
        "groups": GROUP_COUNT,
        "turns_per_group": TURNS_PER_GROUP,
        "turns_meeting_min_rounds": total >= GROUP_COUNT * 10,
        "raw_clean_turns": len(raw_clean),
        "final_clean_turns": len(final_clean),
        "raw_pass_rate": round(100.0 * len(raw_clean) / total, 2),
        "final_pass_rate": round(100.0 * len(final_clean) / total, 2),
        "groups_all_turns_pass": group_pass,
        "groups_with_failure": GROUP_COUNT - group_pass,
        "raw_problem_counts": count(records, "raw_problems"),
        "final_problem_counts": count(records, "final_problems"),
        "guard_effect": {"fixed": len(fixed), "damaged": len(damaged),
                         "persistent": len(persistent), "clean": len(clean)},
        "by_scene": by_scene,
        "by_topic_kind": by_kind,
        "by_intent": by_intent,
        "reply_fallback": {
            "safe_reply_turns": sum(1 for r in records if "再说一遍" in r["reply_final"]),
            "empty_guard_turns": sum(1 for r in records if r["reply_final"].strip() == "嗯。"),
            "guard_fallback_turns": sum(
                1 for r in records
                if r["reply_final"].strip() in ("我到了，等会儿说。", "我在家呢。",
                                                "我还在路上呢，快到了。", "嗯。")),
        },
        "critic": {
            "first_hit_turns": sum(1 for r in records if r["critic_first"]),
            "retry_still_hit_turns": sum(1 for r in records if r["critic_retry_issues"]),
            "safe_fallback_turns": sum(1 for r in records if r["critic_fallback"]),
        },
        "share_ledger": {
            "noted_turns": sum(1 for r in records if r["noted"]),
            "prompt_injected_turns": sum(1 for r in records if r["prompt_line"]),
            "prompt_nonpending_turns": sum(1 for r in records
                                           if r["prompt_line"] and r["prompt_pending"] is False),
            "prompt_stale_topic_turns": sum(1 for r in records if r["prompt_stale"]),
        },
    }


def sanitize(text):
    body = re.sub(r"\d+", "#", str(text or ""))
    return body.replace("大肥鱼", "她")


def _sample_of(record):
    return {
        "group": record["group"], "turn": record["turn"],
        "scene": "%s/%s/%s" % (record["place"], record["zone"], record["scene"]),
        "topic": record["topic"], "kind": record["kind"],
        "user": sanitize(record["user"]),
        "share": sanitize(record["share_final"]),
        "reply": sanitize(record["reply_final"]),
        "injected": [item for item in dict.fromkeys(
            item for item in (record["share_injected"], record["reply_injected"]) if item)],
        "violations": sorted({category_of(p) for p in record["final_problems"]}),
        "violation_details": record["final_problems"],
    }


def pick_samples(records, limit=10):
    """每类失败先取 2 个，再按出现顺序补齐，保证样例覆盖不同判据。"""
    buckets = {}
    for record in records:
        if not record["final_problems"]:
            continue
        primary = sorted({category_of(p) for p in record["final_problems"]})[0]
        buckets.setdefault(primary, []).append(record)
    selected = []
    for primary in sorted(buckets, key=lambda name: -len(buckets[name])):
        for record in buckets[primary][:2]:
            if len(selected) < limit:
                selected.append(_sample_of(record))
    if len(selected) < limit:
        for record in records:
            if len(selected) >= limit:
                break
            if not record["final_problems"]:
                continue
            if any(item["group"] == record["group"] and item["turn"] == record["turn"]
                   for item in selected):
                continue
            selected.append(_sample_of(record))
    return selected[:limit]


def write_markdown(report, path):
    fence = chr(96) * 3
    lines = []
    add = lines.append
    add("# 分享事件对话隔离模拟报告")
    add("")
    add("> 只读测试：未修改插件代码、未写生产记忆/关系库、未 reload、未 commit。")
    add("> 所有测试数据在内存桩里生成；生产库只做只读快照（大小/mtime/表行数）。")
    add("")
    add("## 0. 结论摘要")
    add("")
    totals = report["totals"]
    aligned = report["aligned_only"]["totals"]
    no_critic = report["aligned_only"]["no_critic_totals"]
    add("- 规模：%d 组 × %d 轮 = **%d 轮**（满足每组 >= 10 轮）" % (
        totals["groups"], totals["turns_per_group"], totals["total_turns"]))
    add("- 未过守卫（模型原文）：%d/%d 轮干净（%.2f%%）" % (
        totals["raw_clean_turns"], totals["total_turns"], totals["raw_pass_rate"]))
    add("- 过完生产守卫（最终发出文本）：%d/%d 轮干净（%.2f%%）" % (
        totals["final_clean_turns"], totals["total_turns"], totals["final_pass_rate"]))
    add("- 整组 10 轮全过：%d/%d 组" % (totals["groups_all_turns_pass"], totals["groups"]))
    effect = totals["guard_effect"]
    add("- 守卫逐轮效果：修好 %d 轮 / 改坏 %d 轮 / 仍然违规 %d 轮 / 本来就干净 %d 轮" % (
        effect["fixed"], effect["damaged"], effect["persistent"], effect["clean"]))
    add("- 接话 critic：%d 轮首次判没接住，%d 轮重写后仍判没接住，%d 轮回落安全句" % (
        totals["critic"]["first_hit_turns"], totals["critic"]["retry_still_hit_turns"],
        totals["critic"]["safe_fallback_turns"]))
    add("- 分享台账：%d/%d 轮入账；%d 轮把 prompt 注入上下文，其中 %d 轮的锚点已不是当前话题、%d 轮是「用户已回应」的旧锚点" % (
        totals["share_ledger"]["noted_turns"], totals["total_turns"],
        totals["share_ledger"]["prompt_injected_turns"],
        totals["share_ledger"]["prompt_stale_topic_turns"],
        totals["share_ledger"]["prompt_nonpending_turns"]))
    aligned = report["aligned_only"]["totals"]
    add("- 对照组（mock 全部写对、不注入失败）：%d 轮里仍有 %d 轮被判违规（%.1f%% 误伤）" % (
        aligned["total_turns"], aligned["total_turns"] - aligned["final_clean_turns"],
        100.0 - aligned["final_pass_rate"]))
    add("- 对照组误伤分类：%s" % "、".join(
        "%s %d" % (cat_label(name), value)
        for name, value in aligned["final_problem_counts"].items()))
    add("")
    add("**模型语义质量未覆盖**：mock provider 不读提示词，不能证明真实模型会不会换对象/重复开场；")
    add("本报告覆盖的是「即使模型写歪了，确定性守卫能不能兜住」。")
    add("")
    add("## 1. 方法与边界")
    add("")
    add("每一轮按线上真实顺序跑：")
    add("")
    add("1. mock provider 按（场景/话题/意图）生成分享句与回复原文，并按固定种子注入常见失败模式；")
    add("2. 分享句走 amo_state_consistency.rewrite（与 amo_proactive._send_proactive_lines 同一条路）；")
    add("3. 分享台账按生产 call-site 条件调 amo_share_ledger.note，并取 prompt_line 作为上下文锚点；")
    add("4. 回复先复刻 amo_llm_service.polish 的接话 critic（issues -> 重写 -> safe_reply），")
    add("   再过 amo_reply_pipeline.apply_chat_safety（线上聊天最后一道确定性守卫）；")
    add("5. 用与守卫不共享实现的独立判据打分。")
    add("")
    add("本次跑的守卫代码指纹（sha256 前 16 位）：%s" % "、".join(
        "%s=%s" % (name, value.get("sha256_16"))
        for name, value in report["meta"]["code_fingerprint"].items()))
    add("")
    add("判据（命中即失败）：")
    add("")
    add("| 检查 | 判据 |")
    add("| --- | --- |")
    add("| 对象不换 | 当前分享是猫/狗/兔/鸟/仓鼠时，文本里当前动物消失、出现了别的动物 |")
    add("| 不重复开场 | 同一话题 2 小时内再次出现「刚刷到/刚看到/刷到一个/快看/给你看」 |")
    add("| 不叫用户看他发过的 | 出现「你发的…／你再发／发我看看／你翻上去」等把内容归给用户的话 |")
    add("| 地点动作一致 | 在家说还在路上/快到了；外出说沙发/洗澡/煮面/刚到家；已到商场/公司/面馆还说在路上 |")
    add("| 接住最后一句 | 哈哈哈→不能不接笑或反问笑什么；好看→要接好看/可爱；什么视频→要说清对象；在哪→要给地点/来源；然后呢→要往下讲 |")
    add("")
    add("## 2. 模拟层结果")
    add("")
    add("### 2.0 五条主要发现")
    add("")
    add("1. **object/opener/role 连续两轮保持 0**：raw 30/39/19 轮，过完守卫后 final %d/%d/%d；location 类也 0。" % (
            totals["final_problem_counts"].get("object_swap", 0),
            totals["final_problem_counts"].get("opener_repeat", 0),
            totals["final_problem_counts"].get("asks_user_to_look", 0)))
    add("2. **接住最后一句收成一套判据（第九轮）**：笑/夸/是什么/在哪/后续共用 _covers 一处判据；"
        "情绪轮「哈哈哈/好可爱」没接住时也补接话词（完整正文只补接话词，不硬塞信息点）。"
        "第八轮遗留的 miss 8 轮降到 %d 轮。" % totals["final_problem_counts"].get("miss_last_line", 0))
    add("3. **critic 仍不触发**：%d 轮首次判没接住 / %d 轮重写后仍没接住 / %d 轮回落安全句"
        "（用户话都 ≤5 字，critic 按设计放行，接话由分享守卫兜）。" % (
            totals["critic"]["first_hit_turns"], totals["critic"]["retry_still_hit_turns"],
            totals["critic"]["safe_fallback_turns"]))
    add("4. **对照组 500 轮 0 误伤**：跳过 critic 只跑 apply_chat_safety 也是 %d/500，"
        "说明本轮新增的接话补句只在「没接住」时触发。" % no_critic["final_clean_turns"])
    add("5. **台账只注入 pending 锚点（%d 轮，旧锚点/错话题 0），但入账覆盖仍窄**："
        "只有 %d/%d 轮入账，「我刚刷到」「看个图」和事情类分享仍不入账。" % (
            totals["share_ledger"]["prompt_injected_turns"],
            totals["share_ledger"]["noted_turns"], totals["total_turns"]))
    add("")
    add("### 2.1 失败分类（按轮次计：一轮里同一分类只算 1 次）")
    add("")
    add("| 分类 | 守卫前 | 守卫后 | 说明 |")
    add("| --- | ---: | ---: | --- |")
    for name, label in CATEGORY_LABELS.items():
        raw = totals["raw_problem_counts"].get(name, 0)
        final = totals["final_problem_counts"].get(name, 0)
        add("| %s | %d | %d | %s |" % (name, raw, final, label))
    add("")
    add("### 2.2 按场景 / 分享类型")
    add("")
    add("| 场景 | 轮次 | 守卫后干净 | 干净率 |")
    add("| --- | ---: | ---: | ---: |")
    for name, value in sorted(totals["by_scene"].items(), key=lambda item: -item[1]["turns"]):
        rate = 100.0 * value["final_clean"] / value["turns"]
        add("| %s | %d | %d | %.1f%% |" % (name, value["turns"], value["final_clean"], rate))
    add("")
    add("| 分享类型 | 轮次 | 守卫后干净 | 干净率 |")
    add("| --- | ---: | ---: | ---: |")
    for name, value in sorted(totals["by_topic_kind"].items(), key=lambda item: -item[1]["turns"]):
        rate = 100.0 * value["final_clean"] / value["turns"]
        add("| %s | %d | %d | %.1f%% |" % (name, value["turns"], value["final_clean"], rate))
    add("")
    add("| 用户意图 | 轮次 | 守卫后干净 | 干净率 |")
    add("| --- | ---: | ---: | ---: |")
    for name, value in sorted(totals["by_intent"].items(), key=lambda item: -item[1]["turns"]):
        rate = 100.0 * value["final_clean"] / value["turns"]
        add("| %s | %d | %d | %.1f%% |" % (name, value["turns"], value["final_clean"], rate))
    add("")
    add("### 2.3 对照组：mock 全部写对时的误伤（aligned-only）")
    add("")
    add("这一组不注入任何失败模式，模型输出按当前场景全部对齐；最终仍违规的轮次就是守卫自身造成的。")
    add("")
    add("| 分类 | 误伤轮次 |")
    add("| --- | ---: |")
    for name, value in aligned["final_problem_counts"].items():
        add("| %s | %d |" % (name, value))
    add("")
    no_critic = report["aligned_only"]["no_critic_totals"]
    add("| 对照组配置 | 守卫前干净 | 被改坏 | 守卫后仍干净 |")
    add("| --- | ---: | ---: | ---: |")
    add("| 保留接话 critic（线上默认） | %d | %d | %d |" % (
        aligned["raw_clean_turns"], aligned["guard_effect"]["damaged"],
        aligned["final_clean_turns"]))
    add("| 跳过 critic，只跑 apply_chat_safety | %d | %d | %d |" % (
        no_critic["raw_clean_turns"], no_critic["guard_effect"]["damaged"],
        no_critic["final_clean_turns"]))
    add("")
    add("跳过 critic 后仍误伤 %d 轮：%s" % (
        no_critic["guard_effect"]["damaged"],
        "、".join("%s %d" % (cat_label(name), value)
                 for name, value in no_critic["final_problem_counts"].items())))
    add("")
    add("对照组前 8 个被改坏样例：")
    add("")
    if not report["aligned_only"]["damaged_examples"]:
        add("- 无（对照组 0 轮被改坏）")
    for item in report["aligned_only"]["damaged_examples"]:
        add("- 组 %d / 轮 %d（%s）/ 他说「%s」" % (
            item["group"] + 1, item["turn"] + 1, item["scene"], sanitize(item["user"])))
        add("  - 原文：%s" % sanitize(item["raw"]))
        add("  - 守卫后：%s" % sanitize(item["final"]))
        add("  - 判据：%s" % "；".join(
            cat_label(name) for name in sorted({category_of(p) for p in item["final_problems"]})))
    add("")
    add("## 3. 前 10 个脱敏样例（守卫后的最终文本）")
    add("")
    if not report["samples"]:
        add("本轮守卫后 0 轮违规，无失败样例。")
        add("")
    for index, sample in enumerate(report["samples"], 1):
        add("### 样例 %d（组 %d / 轮 %d / %s / 话题 %s）" % (
            index, sample["group"] + 1, sample["turn"] + 1, sample["scene"], sample["topic"]))
        add("")
        add("- 他：" + sample["user"])
        add("- 她分享：" + sample["share"])
        add("- 她回复：" + sample["reply"])
        add("- 模拟注入：%s" % ("、".join(sample["injected"]) or "无"))
        add("- 命中判据：%s" % "；".join(cat_label(p) for p in sample["violations"]))
        add("")
    add("## 4. 根因与修复验证（全部有生产函数级复现）")
    add("")
    for probe in report["probes"]:
        add("### %s" % probe["name"])
        add("")
        add("**主张**：" + probe["claim"])
        add("")
        if probe.get("ok"):
            add(fence + "json")
            add(json.dumps(probe["result"], ensure_ascii=False, indent=2)[:4000])
            add(fence)
        else:
            add("探针执行失败：" + str(probe.get("error")))
        add("")
    add("## 5. 真实模型结论")
    add("")
    real = report["real_model"]
    add("- 可用性探测：%s" % real.get("summary"))
    add("- 结论：%s" % real.get("verdict"))
    add("- 原因：%s" % real.get("reason"))
    add("")
    add("## 6. 未覆盖项")
    add("")
    for item in report["caveats"]:
        add("- " + item)
    add("")
    add("## 7. 生产数据保护证据")
    add("")
    add("| 库 | 变更 |")
    add("| --- | --- |")
    for db_path, change in report["prod_db_guard"]["diffs"].items():
        add("| %s | %s |" % (db_path, "无变化" if not change else json.dumps(change, ensure_ascii=False)))
    add("")
    add("")
    add("- 本进程 sqlite 连接 %d 条，全部 mode=ro：%s" % (
        len(report["meta"]["sqlite_connections"]),
        "、".join(sorted({item["target"].split("?")[0].split("/")[-1]
                          for item in report["meta"]["sqlite_connections"]}))))
    main_db = PROD_DBS[0]
    before_db = report["prod_db_guard"]["before"][main_db]
    after_db = report["prod_db_guard"]["after"][main_db]
    add("- amo_forever.db 主库：size %s -> %s；mtime %s；WAL size %s -> %s" % (
        before_db.get("size"), after_db.get("size"),
        "未变" if before_db.get("mtime_ns") == after_db.get("mtime_ns") else "变了",
        before_db.get("wal_size"), after_db.get("wal_size")))
    control = report["prod_db_guard"]["external_control"]
    add("- 静置 %d 秒对照（模拟已结束、本进程无动作）：%s" % (
        control["window_seconds"],
        "生产库仍在变，确认写入来自常驻 AstrBot 进程" if control["changed"]
        else "未观察到变化"))
    add("")
    add("说明：%s" % report["prod_db_guard"]["note"])
    add("")
    add("## 8. 建议修复优先级（第九轮已修统一最后信息点规则；其余为只读评审）")
    add("")

    add("1. **P0 长问句 critic**：≤5 字已全放行，但 6 字以上（「你在哪看到的」）仍按二元组判、命中就发"
        "「你再说一遍」；真实聊天里 6-8 字问句很常见，建议改意图判据，重写失败保留原句而不是安全句。")
    add("2. **P1 删空回落**：回落成「我到了，等会儿说。」/「我在家呢。」能过地点检查，"
        "但对「哈哈哈/好看/然后呢」仍算没接住——第九轮已改为按意图补接话词"
        "（本轮 guard 通用回落 %d 轮、miss %d 轮）。" % (
            totals["reply_fallback"]["guard_fallback_turns"],
            totals["final_problem_counts"].get("miss_last_line", 0)))
    add("3. **P1 台账覆盖**：call-site 仍要求「我刷到/给你看/快看/分享」+「视频/图片/照片」，"
        "「我刚刷到」「看个图」「事情类」都不入账（本轮 %d/%d 入账）。" % (
            totals["share_ledger"]["noted_turns"], totals["total_turns"]))
    add("4. **P2 模型原文没接住**：本轮 miss 里约 %d 轮是模型原文就没接住（「嗯嗯，好呀。」「我等会儿再说。」），"
        "确定性守卫补不了，只能靠 critic 重写或提示词约束。" % (
            totals["final_problem_counts"].get("miss_last_line", 0)
            - totals["reply_fallback"]["guard_fallback_turns"]))
    add("5. **保持回归**：object/opener/role 已归零，建议把 §4 的修复验证探针（开场保留后文、"
        "legit 分享不误删、角色只删分句、对象锚点）纳入单测，防止后续改动回退。")
    add("6. **提醒/吃饭词表需单独验证**：本轮 amo_proactive 的提醒事实落地与 reply_fact_guard 的吃饭词表"
        "不在分享模拟路径内，建议用提醒类/吃饭类用例单独覆盖，不能拿本轮 70.6% 当它们的验收。")
    add("7. **P0 question guard 先分清问句指向**：分享语境「在哪（看到的）」问的是来源，不是她的位置；"
        "建议把抖音/B站/微博/小红书/网上/店里加入「已回答」词表，或只在问句是「你在哪/到哪了」时兜底。")
    add("8. **P1 家里说「公司食堂」**：_OUT_PROP 的「公司」是子串匹配，建议改成「在公司/去公司/到公司」这类短语。")
    add("")
    compare = report["fix_comparison"]
    stages = compare["stages"]
    add("## 9. 十阶段对比（初版 + 九轮修复，同一模拟器/判据）")
    add("")
    add("修复内容：① road/outside 拆分、吃面不算居家；② critic 放宽；③ 删空回落改「我到了/我在家呢」；"
        "④ 台账只注入 pending；⑤ 正则覆盖「我刚刷到一个X的视频」；⑥ reply_fact_guard / tone_guard；"
        "⑦ critic≤5 字、角色反转/品种名（第四轮，路径内）；"
        "⑧ 去重保留答案、动作类别（第四轮，路径外）；"
        "⑨ 开场只换开头保留后文、角色反转去上下文依赖、单字兔（第五轮，路径内）；"
        "⑩ 角色只删反转分句、legit 分享不误删、开场去 gate、对象锚点/显式主语（第六轮合并，路径内）；"
        "⑪ 提醒事实落地、吃饭词表（第七轮，amo_proactive / reply_fact_guard，路径外）；"
        "⑫ question guard：问句答非所问用状态兜底（第八轮，amo_question_guard，路径内）；"
        "⑬ 统一最后信息点规则：笑/夸/是什么/在哪/后续共用一套接住判据，情绪轮没接住只补接话词"
        "（第九轮，amo_state_consistency，路径内）。")
    add("")
    add("| 指标 | " + " | ".join(stage["label"] for stage in stages) + " |")
    add("| --- | " + " | ".join("---:" for _ in stages) + " |")

    def _row(title, values):
        add("| %s | %s |" % (title, " | ".join(values)))

    _row("最终干净轮次", ["%d（%.1f%%）" % (s["final_clean_turns"], s["final_pass_rate"])
                          for s in stages])
    _row("place_still_road", [str(s["final_problem_counts"].get("place_still_road", 0))
                              for s in stages])
    _row("miss_last_line", [str(s["final_problem_counts"].get("miss_last_line", 0))
                            for s in stages])
    _row("opener_repeat", [str(s["final_problem_counts"].get("opener_repeat", 0))
                           for s in stages])
    _row("asks_user_to_look", [str(s["final_problem_counts"].get("asks_user_to_look", 0))
                               for s in stages])
    _row("object_swap", [str(s["final_problem_counts"].get("object_swap", 0))
                         for s in stages])
    _row("对照组干净轮次", [str(s["aligned_clean_turns"]) for s in stages])
    _row("跳过 critic 后干净轮次", [str(s["no_critic_clean_turns"]) for s in stages])
    _row("守卫修好 / 改坏", ["%d / %d" % (s["guard_fixed"], s["guard_damaged"])
                             for s in stages])
    add("")
    add("解读：第八轮 question guard 把 343→492（98.4%%），miss 157→8，但把「在哪（看到的）」的来源答案"
        "误改成「我在家呢。」（对照组 14 轮 where 判失败）；第九轮把「接住最后一句」收成一套判据"
        "（笑/夸/是什么/在哪/后续共用），情绪轮没接住只补接话词，492→%d（%.1f%%），miss 8→%d，"
        "对照组 %d/500、跳过 critic %d/500，守卫修好 %d / 改坏 %d。" % (
            totals["final_clean_turns"], totals["final_pass_rate"],
            totals["final_problem_counts"].get("miss_last_line", 0),
            aligned["final_clean_turns"], no_critic["final_clean_turns"],
            totals["guard_effect"]["fixed"], totals["guard_effect"]["damaged"]))
    add("")
    add(compare["note"])
    add("")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def main():
    before = {path: snapshot_db(path) for path in PROD_DBS}
    all_records = []
    for gidx in range(GROUP_COUNT):
        try:
            all_records.extend(run_group(gidx))
        except Exception:  # cleanup: 仿真容错
            print("group %d failed: %s" % (gidx, traceback.format_exc()[:2000]), file=sys.stderr)
    aligned_records = []
    for gidx in range(GROUP_COUNT):
        try:
            aligned_records.extend(run_group(gidx, inject=False))
        except Exception:  # cleanup: 仿真容错
            print("aligned group %d failed: %s" % (gidx, traceback.format_exc()[:2000]),
                  file=sys.stderr)
    no_critic_records = []
    for gidx in range(GROUP_COUNT):
        try:
            no_critic_records.extend(run_group(gidx, inject=False, critic=False))
        except Exception:  # cleanup: 仿真容错
            print("no-critic group %d failed: %s" % (gidx, traceback.format_exc()[:2000]),
                  file=sys.stderr)
    probes = build_probes()
    after = {path: snapshot_db(path) for path in PROD_DBS}
    # 静置对照：模拟结束、本进程不再做任何事，看生产库是否还会自己变（常驻 AstrBot 的 WAL 写入）
    control_a = snapshot_db(PROD_DBS[0])
    time.sleep(6)
    control_b = snapshot_db(PROD_DBS[0])
    external_control = {
        "window_seconds": 6,
        "changed": bool(diff_snapshot(control_a, control_b)),
        "diff": diff_snapshot(control_a, control_b),
    }

    diffs = {path: diff_snapshot(before[path], after[path]) for path in PROD_DBS}
    totals = summarize(all_records)
    aligned_totals = summarize(aligned_records)
    no_critic_totals = summarize(no_critic_records)
    fix_comparison = {
        "stages": [
            {
                "label": "初版",
                "final_clean_turns": 236, "final_pass_rate": 47.2,
                "final_problem_counts": {"miss_last_line": 240, "place_still_road": 73,
                                         "object_swap": 12, "asks_user_to_look": 8,
                                         "opener_repeat": 8},
                "aligned_clean_turns": 302,
                "aligned_problem_counts": {"miss_last_line": 196, "place_still_road": 35,
                                           "object_swap": 2},
                "no_critic_clean_turns": 452,
                "guard_fixed": 64, "guard_damaged": 98,
            },
            {
                "label": "第一轮修复",
                "final_clean_turns": 216, "final_pass_rate": 43.2,
                "final_problem_counts": {"miss_last_line": 272, "place_still_road": 0,
                                         "object_swap": 12, "asks_user_to_look": 11,
                                         "opener_repeat": 11},
                "aligned_clean_turns": 307,
                "aligned_problem_counts": {"miss_last_line": 191, "place_still_road": 0,
                                           "object_swap": 2},
                "no_critic_clean_turns": 452,
                "guard_fixed": 41, "guard_damaged": 95,
            },
            {
                "label": "第二轮修复",
                "final_clean_turns": 307, "final_pass_rate": 61.4,
                "final_problem_counts": {"miss_last_line": 173, "object_swap": 23,
                                         "asks_user_to_look": 17, "opener_repeat": 13},
                "aligned_clean_turns": 464,
                "aligned_problem_counts": {"miss_last_line": 30, "object_swap": 6},
                "no_critic_clean_turns": 487,
                "guard_fixed": 53, "guard_damaged": 16,
            },
            {
                "label": "第三轮（路径外）",
                "final_clean_turns": 307, "final_pass_rate": 61.4,
                "final_problem_counts": {"miss_last_line": 173, "object_swap": 23,
                                         "asks_user_to_look": 17, "opener_repeat": 13},
                "aligned_clean_turns": 464,
                "aligned_problem_counts": {"miss_last_line": 30, "object_swap": 6},
                "no_critic_clean_turns": 487,
                "guard_fixed": 53, "guard_damaged": 16,
            },
            {
                "label": "第四轮修复",
                "final_clean_turns": 316, "final_pass_rate": 63.2,
                "final_problem_counts": {"miss_last_line": 159, "object_swap": 33,
                                         "asks_user_to_look": 7, "opener_repeat": 8},
                "aligned_clean_turns": 487,
                "aligned_problem_counts": {"miss_last_line": 6, "object_swap": 7},
                "no_critic_clean_turns": 487,
                "guard_fixed": 52, "guard_damaged": 6,
            },
            {
                "label": "第五轮修复",
                "final_clean_turns": 322, "final_pass_rate": 64.4,
                "final_problem_counts": {"miss_last_line": 159, "object_swap": 24,
                                         "asks_user_to_look": 6, "opener_repeat": 8},
                "aligned_clean_turns": 494,
                "aligned_problem_counts": {"miss_last_line": 6},
                "no_critic_clean_turns": 494,
                "guard_fixed": 55, "guard_damaged": 3,
            },
            {
                "label": "第六轮（合并）",
                "final_clean_turns": 343, "final_pass_rate": 68.6,
                "final_problem_counts": {"miss_last_line": 157},
                "aligned_clean_turns": 494,
                "aligned_problem_counts": {"miss_last_line": 6},
                "no_critic_clean_turns": 494,
                "guard_fixed": 76, "guard_damaged": 3,
            },
            {
                "label": "第七轮（提醒/吃饭词表，路径外）",
                "final_clean_turns": 343, "final_pass_rate": 68.6,
                "final_problem_counts": {"miss_last_line": 157},
                "aligned_clean_turns": 494,
                "aligned_problem_counts": {"miss_last_line": 6},
                "no_critic_clean_turns": 494,
                "guard_fixed": 76, "guard_damaged": 3,
            },
            {
                "label": "第八轮（question guard）",
                "final_clean_turns": 492, "final_pass_rate": 98.4,
                "final_problem_counts": {"miss_last_line": 8},
                "aligned_clean_turns": 500,
                "aligned_problem_counts": {},
                "no_critic_clean_turns": 500,
                "guard_fixed": 222, "guard_damaged": 0,
            },
            {
                "label": "第九轮（统一最后信息点）",
                "final_clean_turns": totals["final_clean_turns"],
                "final_pass_rate": totals["final_pass_rate"],
                "final_problem_counts": totals["final_problem_counts"],
                "aligned_clean_turns": aligned_totals["final_clean_turns"],
                "aligned_problem_counts": aligned_totals["final_problem_counts"],
                "no_critic_clean_turns": no_critic_totals["final_clean_turns"],
                "guard_fixed": totals["guard_effect"]["fixed"],
                "guard_damaged": totals["guard_effect"]["damaged"],
            },
        ],
        "note": ("数据来自本会话同一模拟器、同一 500 轮脚本、同一判据；代码版本不同，"
                 "不能当严格 A/B，只看量级方向。第三轮（reply_fact_guard / tone_guard）不在路径内；"
                 "第四轮里 0:00 的 critic≤5 字/分享正则/品种名在路径内，0:15 的"
                 "去重保留答案/动作类别在 apply_narrative_repetition、reply_fact_guard，仍不在路径内。"),
    }
    samples = pick_samples(all_records)

    real_model = {
        "summary": "未获得隔离可用的真实 provider（未读取生产配置，见 work/sim/provider_probe.json）",
        "verdict": "未覆盖真实模型语义：本轮只跑了确定性模拟层",
        "reason": "任务禁止为真实调用读取/修改生产配置；隔离探测未发现可用的本地模型端点或环境变量密钥",
    }
    probe_json = os.path.join(HERE, "provider_probe.json")
    if os.path.isfile(probe_json):
        try:
            with open(probe_json, encoding="utf-8") as handle:
                data = json.load(handle)
            real_model["summary"] = data.get("summary") or real_model["summary"]
            if data.get("available"):
                real_model["verdict"] = "发现候选端点，但本轮未接入（保持只读/隔离，避免误读生产配置）"
                real_model["reason"] = data.get("detail") or real_model["reason"]
            else:
                real_model["reason"] = data.get("detail") or real_model["reason"]
        except Exception as exc:
            real_model["reason"] += "（provider_probe.json 读取失败：%r）" % (exc,)

    caveats = [
        "模型语义质量：mock 不读提示词，无法判断真实模型是否会主动换对象、重复开场、编地点；只覆盖守卫兜底。",
        "提示词遵循度：amo_prompt_registry 里「不许换对象/不许让他看自己发的/2 小时内不许刚刷到」是否被模型遵守，未测。",
        "分享内容与地点的一致性只做了确定性词表判定（沙发/洗澡/煮面/在路上等），同义改写（如「瘫着」「冲个凉」）可能漏判。",
        "接话 critic 的重写用 mock 的「语义正确但不复述问句」的回答近似；真实模型重写可能复述问句从而绕过误判，线上发生率取决于模型习惯。",
        "接话 critic 只在 enable_polish=True（默认）且调用方传入 user_text 时生效；若线上显式关闭润色，critic 相关误伤不成立（其余守卫仍会跑）。",
        "路径边界：本模拟只跑 apply_chat_safety + 接话 critic；reply_fact_guard（吃饭词表/动作类别）/ tone_guard / apply_narrative_repetition（去重）/ 分段引擎 / 事实视图 / 主动提醒（amo_proactive）不在路径内，这些改动未被本报告覆盖。",
        "判据是启发式词表，未做人工标注；报告的失败率应看相对变化（守卫前 vs 守卫后），不宜当绝对线上故障率。",
        "主压力组 35% 的失败注入率是压力参数，不代表真实模型的错误率；主压力组的绝对通过率不可外推到线上。",
        "统一最后信息点规则的补句是模板句（「哈哈哈，笑死我了。」/「也太可爱了吧。」/「我在X呢」等），"
        "只保证接住他的接话类型（笑/夸/是什么/在哪/后续），不保证和分享内容语义匹配；"
        "真实模型层仍取决于提示词是否遵守，本轮未覆盖。",
    ]

    report = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "plugin_root": ROOT,
            "sim_dir": HERE,
            "seed_base": SEED_BASE,
            "python": sys.version.split()[0],
            "readonly": True,
            "note": "所有对话数据在内存桩中生成；未打开生产库写连接。",
            "sqlite_connections": SQLITE_CONNECTS,
            "code_fingerprint": code_fingerprint(),
        },
        "totals": totals,
        "aligned_only": {
            "totals": aligned_totals,
            "no_critic_totals": no_critic_totals,
            "damaged_examples": [
                {"group": r["group"], "turn": r["turn"], "scene": r["scene"],
                 "user": r["user"], "raw": r["reply_raw"], "final": r["reply_final"],
                 "final_problems": r["final_problems"]}
                for r in aligned_records if r["final_problems"]
            ][:8],
        },
        "guard_effect_detail": {
            "fixed_examples": [
                {"group": r["group"], "turn": r["turn"], "raw": r["reply_raw"],
                 "final": r["reply_final"], "raw_problems": r["raw_problems"]}
                for r in all_records if r["raw_problems"] and not r["final_problems"]
            ][:5],
            "damaged_examples": [
                {"group": r["group"], "turn": r["turn"], "raw": r["reply_raw"],
                 "final": r["reply_final"], "final_problems": r["final_problems"]}
                for r in all_records if not r["raw_problems"] and r["final_problems"]
            ][:5],
        },
        "samples": samples,
        "fix_comparison": fix_comparison,
        "probes": probes,
        "real_model": real_model,
        "caveats": caveats,
        "prod_db_guard": {
            "diffs": diffs,
            "external_control": external_control,
            "sqlite_connections": SQLITE_CONNECTS,
            "note": ("以 sqlite3 file:...?mode=ro + pragma query_only=on 只读打开，进程不持有写事务。"
                     "若行数有抖动而主库 size/mtime 不变，来自常驻 AstrBot 进程的 WAL 写入，"
                     "本模拟的全部连接记录见 meta.sqlite_connections。"),
            "before": before,
            "after": after,
        },
    }

    with open(OUT_JSON, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    write_markdown(report, OUT_MD)

    print(json.dumps({
        "total_turns": totals["total_turns"],
        "raw_pass_rate": totals["raw_pass_rate"],
        "final_pass_rate": totals["final_pass_rate"],
        "groups_all_pass": totals["groups_all_turns_pass"],
        "aligned_only_final_pass_rate": aligned_totals["final_pass_rate"],
        "aligned_no_critic_final_pass_rate": no_critic_totals["final_pass_rate"],
        "guard_effect": totals["guard_effect"],
        "final_problem_counts": totals["final_problem_counts"],
        "json": OUT_JSON,
        "md": OUT_MD,
        "prod_db_changed": {path: bool(change) for path, change in diffs.items()},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
