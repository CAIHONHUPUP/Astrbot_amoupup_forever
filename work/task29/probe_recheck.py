# -*- coding: utf-8 -*-
"""task-29 手动探针：后置语义复检（只读/内存，不写库、不 reload）。

口径：
- 「历史守卫输出」= real_llm_layer.json 里真实发生过的那条错误发出文本，喂给
  apply_chat_safety 看能否还原候选；
- 「真实链」= 候选直接进 apply_chat_safety（没有历史坏输出），看最终发出文本
  还答不答得上他那句；
- answered = amo_reply_pipeline._answered_intent 或 grounding_critic.issues 为空。
"""
import os
import sqlite3
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import amo_grounding_critic as GC  # noqa: E402
import amo_question_guard as Q  # noqa: E402
import amo_reply_pipeline as P  # noqa: E402
import amo_state_consistency as SC  # noqa: E402

UMO = "lark:FriendMessage:recheck"


def plugin(place="家", zone="home", rows=None):
    class DB:
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.execute(
                "create table wallet_ledger (ts real, delta_cents int, item_name text)")
            self.conn.commit()

    p = SimpleNamespace()
    p.db = DB()
    p.state = SimpleNamespace(current_activity=lambda: {"place": place, "zone": zone})
    p._recent = lambda umo, n=8: list(rows or [])[-int(n):]
    return p


def answered(reply, question):
    if not str(reply or "").strip():
        return False
    box = {}
    box["strict"] = P._answered_intent(reply, question)
    box["grounding"] = not GC.issues(question, reply)
    return box


def restore(candidate, question, guarded, place="家", zone="home"):
    """历史守卫输出 -> apply_chat_safety，看候选能否还原。"""
    P.reset_repetition_memory()
    p = plugin(place, zone)
    P.apply_narrative_repetition(candidate, p, question, UMO)
    return P.apply_chat_safety(guarded, p, UMO, question)


def real_chain(candidate, question, place="家", zone="home"):
    """候选直接进 apply_chat_safety（真实装饰链：中间没有历史坏输出）。"""
    P.reset_repetition_memory()
    p = plugin(place, zone)
    P.apply_narrative_repetition(candidate, p, question, UMO)
    return P.apply_chat_safety(candidate, p, UMO, question)


#: (名称, 候选, 他这句, 历史真实发出文本)
CASES = [
    ("G13 when", "不回啦，我就在家歇着呢，刚到家没多久，今天真不想动了",
     "今晚回不回来？", "嗯，我在呢。"),
    ("G23 recall", "记得呀，那天你穿白衬衫，在奶茶店门口等我，我还迟到了",
     "你还记得那天我穿什么去接你的吗？", "我还在路上呢，快到了。"),
    ("G43 when", "17:27啦，我刚到家歇着呢，今晚不回了，明早还要早起",
     "今晚回不回来？", "我这儿一直在。"),
    ("G09 where", "没呢，刚下班走在回家的路上，今天累死了",
     "到公司了吗？", "还是老样子。"),
    ("G29 when", "刚练完器械，还在健身房呢，估计得七点多才能到家吧，今天练得有点久。",
     "几点到家呀？", "我还在外面呢，晚点就回去。"),
    ("G36 when", "在地铁上呢，晚点回去", "今晚回不回来？", "还是老样子。"),
    ("G17 doing", "刚洗完澡躺床上刷手机呢，酒店空调开得挺舒服的",
     "你在干嘛呢？", "嗯，我在呢。"),
    ("G47 doing", "刚洗完澡躺床上刷手机呢，酒店空调开得挺舒服的",
     "忙什么呢", "我在在酒店休息。"),
    ("G13-2 eating", "中午随便吃了碗云吞面，现在正走在回家路上，饿得不行了",
     "你中午吃的什么？", "还是老样子。"),
]

print("=== 1) 历史守卫输出还原（真实模型层已知误伤）===")
restored_exact = 0
answered_after = 0
for name, cand, q, sent in CASES:
    out = restore(cand, q, sent)
    exact = out.strip() == cand.strip()
    an = answered(out, q)
    restored_exact += exact
    answered_after += bool(an["strict"] or an["grounding"])
    print("%-14s %-8s | %s" % (name, "restored" if exact else "kept", out[:56]))
print("exact-restored %d/%d | 发出文本答到他这句 %d/%d"
      % (restored_exact, len(CASES), answered_after, len(CASES)))

print()
print("=== 2) 真实链（候选直入收口，看是否答到）===")
ok = 0
for name, cand, q, sent in CASES:
    out = real_chain(cand, q)
    an = answered(out, q)
    good = bool(an["strict"] or an["grounding"]) and bool(out.strip())
    ok += good
    print("%-14s %-6s | %s" % (name, "answer" if good else "MISS", out[:56]))
print("真实链答到 %d/%d" % (ok, len(CASES)))

print()
print("=== 3) 不覆盖/不误伤反例 ===")
P.reset_repetition_memory()
p = plugin(rows=[{"role": "bot", "content": "嗯嗯，我在家呢，想你"}])
out = P.apply_narrative_repetition("嗯嗯，我在家呢，想你", p, "想你", UMO)
print("emotion repeat（非问句）->", out, "| 仍去重:", out != "嗯嗯，我在家呢，想你")

P.reset_repetition_memory()
p = plugin()
P.apply_narrative_repetition("今天天气不错。", p, "你在哪？", UMO)
out = P.apply_chat_safety("嗯，我在呢。", p, UMO, "你在哪？")
print("候选没答到 ->", out, "| 没乱还原:", out != "今天天气不错。")

P.reset_repetition_memory()
p = plugin()
P.apply_narrative_repetition("我是DeepSeek大语言模型，有什么可以帮你？", p, "你是什么模型？", UMO)
out = P.apply_chat_safety("秘密，不告诉你。", p, UMO, "你是什么模型？")
print("候选带模型泄漏 ->", out, "| 安全拦截:", "DeepSeek" not in out)

P.reset_repetition_memory()
p = plugin()
P.apply_narrative_repetition("我回来啦！今天买了米白色针织开衫，花了649元。", p, "你买了吗？", UMO)
out = P.apply_chat_safety("我试了试，挺喜欢的，还没买。", p, UMO, "你买了吗？")
print("候选假消费 ->", out, "| 消费守卫优先:", "649" not in out)

print()
print("=== 4) rewrite / validate / question_guard 单点 ===")
P.reset_repetition_memory()
p = plugin()
P.apply_narrative_repetition("刚吃完，在沙发上瘫着呢", p, "吃了吗？", UMO)
print("rewrite 出口 ->", SC.rewrite("还是老样子。", p, "吃了吗？", umo=UMO))
P.reset_repetition_memory()
p = plugin()
P.apply_narrative_repetition("刚吃完，在沙发上瘫着呢", p, "吃了吗？", UMO)
print("validate 出口 ->", SC.validate_reply_assembly(
    "还是老样子。", p, umo=UMO, user_text="吃了吗？"))
print("question_guard 候选优先 ->", Q.answer(
    "你在哪？", plugin("公司", "office"), "今天天气还行。", candidate="我在公司呢。"))
print("question_guard doing 模板 ->", Q.answer(
    "你在干嘛？", plugin("公司", "office", ), "今天天气还行。"))
print()
print("stats:", P.semantic_recheck_stats())
