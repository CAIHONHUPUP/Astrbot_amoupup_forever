# -*- coding: utf-8 -*-
"""候选优先的出站二次校验（task-40，2026-11-22）。

生产回放发现：守卫把「完整候选」整条换成固定兜底（还是老样子。/我在休息。/
我试了试，挺喜欢的，还没买。），多池拼装（哈哈哈，挺喜欢的，还没买，笑死我了。），
改写后留下语法破损（坐回沙发，端着面）/看衣服服/儿半天了）。

本模块只做两件事，不选句、不写文案：
1) 记住本轮「进入装饰链前的原始候选」（由 amo_runtime_reply._deco_soft_guards 登记）；
2) 在整条回复的出口（apply_chat_safety / validate_reply_assembly）把候选过一遍安全
   改写（调用方传入 amo_persona_guard.sanitize_candidate：旁白/技术词/禁语/金额/人名/
   收件人），再和现行文本比：现行文本出现固定兜底覆盖、多池串味、语法破损、指代
   错改、单句删除时，用安全改写过的候选替换——候选优先于兜底/删句。

只对 source=chat 且同一会话、最近 180 秒内登记的候选生效；判不出来就保持现行文本，
绝不把回复弄丢。
"""
from __future__ import annotations

import logging
import re
import time

logger = logging.getLogger("amo.rewrite_safety")

#: 候选有效期（秒）：发送链同上一条消息之间的间隔
CANDIDATE_TTL = 180.0
#: 候选优先里「完整消息」的最小归一化长度
SUBSTANTIVE_MIN = 8

_OPEN = "（("
_CLOSE = "）)"
_HAN = re.compile(r"[\u4e00-\u9fff]")
_SENT_SPLIT = re.compile(r"(?<=[。！？!?…~～\n])")
_CLAUSE_SPLIT = re.compile(r"(?<=[，,、；;：:。！？!?…])")
#: 句首残片：删禁语/删分句后只留下这些字开头，正常中文句子不会这么起头
_FRAGMENT_HEAD = re.compile(r"^(?:儿|了|的|们)(?=[\u4e00-\u9fff])")
#: 行为层追加的固定接话词（正文后面突然多出来的）
_APPEND_MARKERS = ("哈哈哈，笑死我了。", "笑死我了。", "哈哈哈，", "哈哈哈")
#: 地点/状态被改写的指代锚点：候选说 A，现行改成了 B 且候选里没有 B
_PLACE_SWAPS = (
    ("上班", "休息"), ("上班", "忙活"), ("工位", "家"), ("工位", "书桌"),
    ("工位", "屋里"), ("公司", "家"),
    ("地铁", "家"), ("地铁", "楼下"), ("商场", "家"), ("商场", "外面"),
    ("餐厅", "家"), ("小馆", "家"), ("食堂", "家"),
    ("在吃饭", "待会儿吃饭"), ("在吃饭", "还没吃"),
)
#: 改完会拼出病句的固定插入
_BAD_INSERTS = ("只就是", "就就是", "都就是", "也刚才那个", "的就是刚才")


def _guard():
    try:
        from .domains.reply import repetition_guard as guard
    except ImportError:  # 直接按文件加载时
        from domains.reply import repetition_guard as guard
    return guard


def _store(plugin):
    if plugin is None:
        return None
    data = getattr(plugin, "_amo_turn_candidate", None)
    if not isinstance(data, dict):
        data = {}
        try:
            plugin._amo_turn_candidate = data
        except Exception:  # cleanup: 插件禁止写属性时放弃登记
            return None
    return data


def remember_candidate(plugin, umo, text):
    """登记本轮原始候选（进入装饰链前的文本）。"""
    body = str(text or "").strip()
    if not body:
        return
    data = _store(plugin)
    if data is None:
        return
    data[str(umo or "")] = {"text": body, "ts": time.time()}


def peek_candidate(plugin, umo):
    """取仍有效的候选；过期/没有返回空串。"""
    data = _store(plugin)
    if not data:
        return ""
    entry = data.get(str(umo or ""))
    if not isinstance(entry, dict):
        return ""
    try:
        age = time.time() - float(entry.get("ts") or 0)
    except (TypeError, ValueError):
        return ""
    if age < 0 or age > CANDIDATE_TTL:
        data.pop(str(umo or ""), None)
        return ""
    return str(entry.get("text") or "")


def clear_candidate(plugin, umo):
    data = _store(plugin)
    if data:
        data.pop(str(umo or ""), None)


def _norm(text):
    return _guard().compare_body(text)


def _filled(text):
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", str(text or ""))


def _paren_balance(text):
    body = str(text or "")
    return body.count("(") - body.count(")") + body.count("（") - body.count("）")


def _dup_runs(text):
    """句中重复字连写（「看衣服服」「行行行」）的片段集合。"""
    body = str(text or "")
    runs = set()
    index = 0
    while index < len(body):
        char = body[index]
        if char not in _OPEN and char not in _CLOSE and _HAN.match(char):
            end = index + 1
            while end < len(body) and body[end] == char:
                end += 1
            if end - index >= 2:
                runs.add(body[index:end])
            index = end
        else:
            index += 1
    return runs


def _sentences(text):
    return [part for part in _SENT_SPLIT.split(str(text or "")) if part.strip()]


def _clauses(text):
    return [part for part in _CLAUSE_SPLIT.split(str(text or "")) if str(part or "").strip()]


def _remove_unit(candidate, unit):
    body = str(candidate or "")
    if not unit or unit not in body:
        return body
    out = body.replace(unit, "", 1)
    # 删句后可能留下空分句/重复标点，归一化后再比
    out = re.sub(r"[，,；;：:]\s*([。！？!?])", r"\1", out)
    out = re.sub(r"^[，,；;：:\s]+", "", out)
    return out.strip()


def _unit_dropped(candidate, current):
    """现行文本是不是候选删掉一个分句/整句后的结果（内容丢失）。"""
    cand = str(candidate or "").strip()
    cur = _norm(current)
    if not cand or not cur:
        return False
    if _norm(cand) == cur:
        return False
    units = list(_clauses(cand)) + list(_sentences(cand))
    if not units:
        return False
    for unit in units:
        rest = _remove_unit(cand, unit)
        if rest and _norm(rest) == cur:
            return True
    return False


def _place_swap(candidate, current):
    """候选说的地点/状态被改成了另一类，且候选里本来没有那个说法（指代错改）。"""
    cand = str(candidate or "")
    cur = str(current or "")
    for said, changed in _PLACE_SWAPS:
        if said not in cand or said in cur:
            continue
        if not changed:
            continue
        if changed in cur and changed not in cand:
            return True
    return False


#: 被删掉就留下病句的地点/状态词（「在工位上」→「在」，「地铁上」→「的」）
_REMOVABLE_WORDS = ("工位上", "工位", "地铁上", "地铁", "公交", "商场", "餐厅", "小馆",
                    "食堂", "公司", "上班", "路上", "外面")
#: 家属称呼被换成工作/外人的说法（妈妈→同事）
_FAMILY_WORDS = ("妈妈", "爸爸", "我妈", "我爸")
_FAMILY_SWAPS = ("同事", "朋友", "有人")


def _word_removed(candidate, current):
    """现行文本 == 候选把某个地点/状态词整个删掉（留下「在憋不住」「我在回去的了」）。"""
    cand = str(candidate or "")
    cur = _norm(current)
    if not cand or not cur:
        return False
    for word in _REMOVABLE_WORDS:
        if word not in cand:
            continue
        rest = cand.replace(word, "")
        if rest != cand and _norm(rest) == cur:
            return True
    return False


#: 地点词 + 方位后缀（「地铁上」→「楼下上」这种只换词、后缀留下的病句）
_LOCAL_SUFFIXES = ("上", "里", "中", "里面", "上面", "这边", "那边")


def _swapped_with_suffix(candidate, current):
    """候选「地铁上了」被换成「楼下上了」：词换了但方位后缀没处理，语法破损。"""
    cand = str(candidate or "")
    cur = str(current or "")
    for said, changed in _PLACE_SWAPS:
        if not changed or said not in cand or said in cur:
            continue
        for suffix in _LOCAL_SUFFIXES:
            if said + suffix in cand and changed + suffix in cur:
                return True
    return False


def _family_swap(candidate, current):
    """候选里的妈妈/爸爸被改成了同事/朋友/有人（task-40 指代错改）。"""
    cand = str(candidate or "")
    cur = str(current or "")
    for word in _FAMILY_WORDS:
        if word not in cand or word in cur:
            continue
        if any(item in cur and item not in cand for item in _FAMILY_SWAPS):
            return True
    return False


def _appended_fixed(candidate, current):
    """完整正文后面被拼了一截固定接话词（多池串味的入口）。"""
    cand = _norm(candidate)
    cur = str(current or "").strip()
    for marker in _APPEND_MARKERS:
        if not cur.endswith(marker):
            continue
        if _norm(marker) in cand:
            continue
        return True
    return False


def _comparable(text):
    """比较口径：动作旁白是本来就该剥的，不算内容差异（回放 #2006）。"""
    body = str(text or "")
    try:
        try:
            from .amo_persona_guard import strip_action_narration
        except ImportError:
            from amo_persona_guard import strip_action_narration
        return str(strip_action_narration(body) or "").strip()
    except Exception:  # cleanup: 判据不可用就按原文
        return body.strip()


def damage_reasons(candidate, current, allow_shrink=True):
    """现行文本相对候选有哪些「不该发生的改写」。返回原因列表（空=没伤）。"""
    cand = str(candidate or "").strip()
    cur = str(current or "").strip()
    if not cand or not cur or cand == cur:
        return []
    guard = _guard()
    reasons = []
    if not guard.substantive_reply(cand, SUBSTANTIVE_MIN):
        # 候选本身也是固定句，但比现行兜底信息多（align.swim_outside 这类带内容的
        # 兜底句被换成「还是老样子」）：仍允许还原，只是换回信息更多的那一句。
        if not (guard.is_fixed_rewrite(cand)
                and len(_filled(cand)) >= len(_filled(cur)) + 6):
            return []
        reasons.append("fixed_cover")
    # 内容比较先剥掉动作旁白（旁白本来就要剥，不算改写伤害）
    cand_cmp = _comparable(cand) or cand
    cur_cmp = _comparable(cur) or cur
    if guard.is_fixed_rewrite(cur_cmp) or guard.is_multi_pool(cur_cmp):
        reasons.append("fixed_cover")
    else:
        cur_norm = _norm(cur_cmp)
        cand_norm = _norm(cand_cmp)
        for fixed_body in guard.fixed_rewrite_bodies():
            if len(fixed_body) >= 6 and cur_norm.startswith(fixed_body) \
                    and not cand_norm.startswith(fixed_body):
                reasons.append("fixed_cover")
                break
    if guard.is_multi_pool(cur_cmp) and not guard.is_multi_pool(cand_cmp):
        reasons.append("multi_pool")
    if _paren_balance(cand) == 0 and _paren_balance(cur) != 0:
        reasons.append("unbalanced_paren")
    known = _dup_runs(cand_cmp)
    if any(run not in known and run not in cand_cmp for run in _dup_runs(cur_cmp)):
        reasons.append("dup_char")
    for sentence in _sentences(cur_cmp):
        head = sentence.strip().lstrip("\"“”‘’ ")
        if _FRAGMENT_HEAD.match(head):
            reasons.append("fragment")
            break
    if _place_swap(cand_cmp, cur_cmp):
        reasons.append("place_swap")
    if _word_removed(cand_cmp, cur_cmp):
        reasons.append("word_removed")
    if _swapped_with_suffix(cand_cmp, cur_cmp):
        reasons.append("word_swapped")
    if _family_swap(cand_cmp, cur_cmp):
        reasons.append("family_swap")
    if any(word in cur_cmp and word not in cand_cmp for word in _BAD_INSERTS):
        reasons.append("bad_insert")
    if _appended_fixed(cand_cmp, cur_cmp):
        reasons.append("appended_fixed")
    if _unit_dropped(cand_cmp, cur_cmp):
        reasons.append("unit_dropped")
    if allow_shrink:
        cand_len = len(_filled(cand_cmp))
        cur_len = len(_filled(cur_cmp))
        if cand_len and cur_len < 0.6 * cand_len and cand_len - cur_len >= 6:
            reasons.append("big_shrink")
    return reasons


def should_recover(candidate, current, allow_shrink=True):
    return bool(damage_reasons(candidate, current, allow_shrink))


#: 他这一句在问「你现在在哪/在干嘛/跟谁」：候选里的事实是答案，必须和此刻场景/同伴一致
_ASK_PLACE = re.compile(
    r"在哪|哪里|哪儿|在干嘛|干嘛呢|干嘛呀|做什么|干啥|忙什么|忙啥|在吗|"
    r"跟谁|和谁|一个人|谁在一起|有人吗|旁边有|身边有|回不回来|到家了吗|"
    r"吃的什么|吃了吗|吃了没|吃饭了吗|中午吃|晚上吃|中午吃|吃药|"
    r"干嘛去了|忙完了没|修完了没|在公司|到公司")


def _candidate_unsafe(plugin, safe, probe, user_text=""):
    """候选本身是不是「不该原样还原」：角色反转/地点冲突被守卫收成了短场景句。"""
    if not probe:
        return True
    # 角色反转（B1「你发的视频…再发我一遍？」）：删掉反转说法后整条不剩人话
    try:
        try:
            from .amo_state_consistency import _strip_role_reversal
        except ImportError:
            from amo_state_consistency import _strip_role_reversal
        stripped = "".join(_strip_role_reversal(item) for item in _sentences(safe))
        if safe and not _filled(stripped):
            return True
    except Exception:  # cleanup: 判据不可用就按守卫形态判
        pass

    # 地点冲突：不管他有没有问地点，候选里的地点断言都必须和此刻场景一致。
    # task-40 第一版只在 _ASK_PLACE 时检查，结果非问句轮把「商场/公司」候选整条
    # 还原回居家/景区场景，日常 holdout 从 50/50 掉到 27-40/50。
    try:
        try:
            from .amo_state_snapshot import blacklist_hit as _blacklist_hit
        except ImportError:
            from amo_state_snapshot import blacklist_hit as _blacklist_hit
        try:
            activity = plugin.state.current_activity() or {}
        except Exception:
            activity = {}
        zone = str((activity or {}).get("zone") or "")
        if zone and zone != "unknown" and _blacklist_hit(safe, zone):
            return True
    except Exception as exc:  # cleanup: 场景判据不可用就跳到其它判据
        logger.debug("[amo] 候选地点冲突检查失败: %s", exc)
    if _ASK_PLACE.search(str(user_text or "")):
        # 他问的就是这类事实、而事实守卫必须改写候选才能成立 → 候选不能原样还原
        # （日常 holdout G14：问「你在哪呀」候选「我在公司呢」在病假居家场景被还原）
        if _norm(probe) != _norm(safe):
            return True
    return False


def _fresh_question(umo, user_text=""):
    """取本轮他真正问的那句（登记表优先，避免 send 阶段拿到上一轮的旧问句）。"""
    key = str(umo or "").strip()
    if key:
        try:
            try:
                from .amo_reply_pipeline import _turn_question
            except ImportError:
                from amo_reply_pipeline import _turn_question
            fresh = str(_turn_question(key) or "").strip()
            if fresh:
                return fresh
        except Exception:  # cleanup: 取不到就退回传入文本
            pass
    return str(user_text or "").strip()


def _current_is_fallback(text, guard_mod):
    """现行文本是否只是固定兜底句（不是正常候选）。"""
    try:
        pools = getattr(guard_mod, "FALLBACKS", {}) or {}
        wanted = {_norm(item) for pool in pools.values() for item in (pool or ())}
        return _norm(text) in wanted
    except Exception:  # cleanup: registry 不可用时保守判定
        return False


def recover(plugin, umo, current, sanitize, guard=None, source="chat",
            allow_shrink=True, user_text=""):
    """候选优先出口：现行文本被改坏时，用安全改写过的候选顶回去。

    sanitize(text) -> (safe_text, reason)：候选要先过一遍「该做的安全改写」
    （旁白/技术词/禁语/精确金额/人名/收件人），否则还原会把这些又带回来。
    guard(text) -> probe：候选的安全探针（事实守卫+场景/角色清洗）。候选本身
    角色反转/地点冲突时不许还原，改成守卫形态或保持现行文本（task-41 B1/C1）。
    """
    if str(source or "") != "chat" or not callable(sanitize):
        return current
    candidate = peek_candidate(plugin, umo)
    if not candidate:
        return current
    if _norm(candidate) == _norm(current):
        return current
    # 只在「现行文本确实是固定兜底/多池拼装」时还原候选。
    # 完整候选本身即使与场景冲突，也应交给场景守卫改写，不能靠候选优先顶回去
    # （崩溃前 task-40 第一版把所有损坏都还原，daily holdout 从 50/50 掉到 27-44）。
    guard_mod = _guard()
    if not _current_is_fallback(current, guard_mod) and not guard_mod.is_multi_pool(current):
        return current
    try:
        safe, safe_reason = sanitize(candidate)
    except Exception as exc:  # cleanup: 候选安全改写失败就不还原，保持现行文本
        logger.debug("[amo] 候选安全改写失败: %s", exc)
        return current
    safe = str(safe or "").strip()
    if not safe:
        return current
    probe = ""
    if callable(guard):
        try:
            probe = str(guard(safe) or "").strip()
        except Exception as exc:  # cleanup: 探针失败就跳过安全检查
            logger.debug("[amo] 候选安全探针失败: %s", exc)
            probe = safe
    if not probe:
        probe = safe
    guard_mod = _guard()
    question = _fresh_question(umo, user_text)
    if _candidate_unsafe(plugin, safe, probe, question):
        if guard_mod.is_multi_pool(current) and probe and _norm(probe) != _norm(current):
            # 现行文本是多池拼装：退而用守卫形态（单句场景话），内容丢但不串味
            if not _forbidden_by_guard(probe):
                logger.info("[amo] 候选不安全，改用守卫形态：%s -> %s",
                            str(current)[:40], probe[:40])
                return probe
        # 现行文本已经是守卫的安全产物（角色反转/地点冲突收成的场景句），保持
        return current
    if _norm(safe) == _norm(current):
        return current
    reasons = damage_reasons(candidate, current, allow_shrink=allow_shrink)
    if not reasons:
        # 候选带禁语/技术词时，安全改写后的形态才是「本应发出」的候选
        reasons = damage_reasons(safe, current, allow_shrink=allow_shrink)
    if not reasons:
        return current
    if _forbidden_by_guard(safe):
        logger.info("[amo] 候选优先还原被安全拦下: %s", safe[:40])
        return current
    logger.info("[amo] 候选优先还原（%s）：%s -> %s",
                ",".join(reasons), str(current)[:40], safe[:40])
    return safe


#: 安全改写后的候选仍然带这些内容时不许还原（后台话术/模型泄漏优先挡住）
_UNSAFE_MARKERS = ("服务器", "报错", "超时", "权限", "面板", "接口", "异常", "重试",
                   "数据库", "日志", "错误码", "traceback", "Traceback", "我发的照片",
                   "我发的语音")


def _forbidden_by_guard(text):
    body = str(text or "")
    return any(marker in body for marker in _UNSAFE_MARKERS)


__all__ = [
    "CANDIDATE_TTL", "SUBSTANTIVE_MIN", "remember_candidate", "peek_candidate",
    "clear_candidate", "damage_reasons", "should_recover", "recover",
]
