# -*- coding: utf-8 -*-
"""生成 task-29 的精确 diff：用「当前文件 - 本轮新增片段」反推改动前快照。

不依赖 git（工作树没有 git）。每个替换对都断言命中且只命中一次，
命中失败就直接报错，避免产出不可信的 diff。
"""
import difflib
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "work", "task29")
BEFORE = os.path.join(OUT, "before")
AFTER = os.path.join(OUT, "after")

FILES = ("amo_reply_pipeline.py", "amo_state_consistency.py",
         "amo_question_guard.py", "amo_grounding_critic.py")

#: 有审计 pin 且与改动前逐字节一致的文件，直接用 pin 当 before
PIN_BEFORE = {
    "amo_grounding_critic.py": os.path.join(
        ROOT, "work", "audit", "_scratch", "repo_pin", "amo_grounding_critic.py"),
}

#: 反向替换：当前片段 -> 改动前片段（每对必须唯一命中）
REVERSE = {
    "amo_question_guard.py": [
        (
            "def answer(text, plugin, reply, candidate=\"\"):\n"
            "    \"\"\"答非所问时用当前状态兜底；本轮候选已经答到这类问题，就不许整条覆盖它。\n"
            "\n"
            "    candidate = 进入破坏性改写前的原始候选（task-29 后置语义复检入口之一）：\n"
            "    上游守卫可能把候选删坏或换成通用句，状态模板只能在候选也答不到时才顶上去。\n"
            "    \"\"\"\n"
            "    category = _category(text)\n"
            "    if not category or _answered(category, reply):\n"
            "        return reply\n"
            "    candidate = str(candidate or \"\").strip()\n"
            "    if candidate and _answered(category, candidate):\n"
            "        return candidate\n"
            "    act = _act(plugin)",
            "def answer(text, plugin, reply):\n"
            "    category = _category(text)\n"
            "    if not category or _answered(category, reply):\n"
            "        return reply\n"
            "    act = _act(plugin)",
        ),
        (
            "    if category == \"doing\":\n"
            "        doing = doing or \"忙点事\"\n"
            "        if doing.startswith(\"在\"):\n"
            "            # 状态里的活动本身带「在」（「在酒店休息」），模板再套一层会成\n"
            "            # 「我在在酒店休息。」（审计 3.2.3 真实模型层样例）。\n"
            "            return \"我在%s。\" % doing[1:]\n"
            "        return _fallbacks.sentence(\"question.doing\", doing=doing)",
            "    if category == \"doing\":\n"
            "        return _fallbacks.sentence(\"question.doing\", doing=doing or \"忙点事\")",
        ),
    ],
    "amo_reply_pipeline.py": [
        (
            "    try:\n"
            "        category = _q_category(ask)\n"
            "        if category and _q_answered(category, body):\n"
            "            return True\n"
            "    except Exception as exc:  # cleanup: 判不出来就退回通用答案规则\n"
            "        logger.debug(\"[amo] chat_safety.intent 失败: %s\", exc)\n"
            "    # 问句守卫的答案词表偏窄（when 类只认「到家/路上/马上/…」），判不出来时\n"
            "    # 仍按通用答案规则再看一遍，别把「其实答到了」的候选判成没答到。\n"
            "    return _answers_question(body, ask)",
            "    try:\n"
            "        category = _q_category(ask)\n"
            "        if category:\n"
            "            return bool(_q_answered(category, body))\n"
            "    except Exception as exc:  # cleanup: 判不出来就退回通用答案规则\n"
            "        logger.debug(\"[amo] chat_safety.intent 失败: %s\", exc)\n"
            "    return _answers_question(body, ask)",
        ),
        (
            "    if _is_any_fallback(text) or not _answers_question(text, user_text):",
            "    if _is_generic_fallback(text) or not _answers_question(text, user_text):",
        ),
        (
            "        value = _state_rewrite(value, plugin, _context, umo=str(umo or \"\"))",
            "        value = _state_rewrite(value, plugin, _context)",
        ),
        (
            "        _TURN_USER_TEXT.clear()\n"
            "        _TURN_SOURCE.clear()\n"
            "    reset_semantic_recheck_stats()",
            "        _TURN_USER_TEXT.clear()\n"
            "        _TURN_SOURCE.clear()",
        ),
        (
            "    # 整句/状态去重是「整句删除」守卫：删完再判一次「还答不答得上他这一句」，\n"
            "    # 答不上就把本轮候选要回来（答案优先于不复读，且不许被「还是老样子」覆盖）。\n"
            "    return final_semantic_recheck(fixed or value, plugin, umo, user_text, original=value)",
            "    return fixed or value",
        ),
        (
            "        # 问句模板是「整条替换」：本轮候选已经答到这类问题（严格判据/通用答案\n"
            "        # 规则任一命中）时，模板不许顶上去——当前文本答不到就交给出口的语义复检\n"
            "        # 还原候选，而不是先用状态模板把候选盖掉。\n"
            "        _candidate = _media_clean(_turn_candidate(umo))\n"
            "        _candidate_answers = bool(_candidate) and _answered_intent(_candidate, user_text)\n"
            "        if not _candidate_answers:\n"
            "            value = _question_answer(\n"
            "                str(user_text or \"\"), plugin, value, candidate=_candidate)\n"
            "    except Exception as exc:\n"
            "        logger.debug(\"[amo] chat_safety.question 失败: %s\", exc)\n"
            "    # 唯一后置语义复检（task-29）收口：上面所有整句删除/替换（含其前的\n"
            "    # _align_arrival/_align_swim_where、其中的 state_consistency.rewrite 与\n"
            "    # question_guard.answer）都跑完了，再判一次「还答不答得上他这一句」。\n"
            "    value = final_semantic_recheck(value, plugin, umo, user_text)\n"
            "    return value.strip() or str(text or \"\")",
            "        value = _question_answer(str(user_text or \"\"), plugin, value)\n"
            "    except Exception as exc:\n"
            "        logger.debug(\"[amo] chat_safety.question 失败: %s\", exc)\n"
            "    return value.strip() or str(text or \"\")",
        ),
    ],
    "amo_state_consistency.py": [
        (
            "from __future__ import annotations\n"
            "\n"
            "import logging\n"
            "import re\n"
            "\n"
            "logger = logging.getLogger(\"amo.state_consistency\")\n"
            "\n"
            "_HOME_ACTION = re.compile(",
            "from __future__ import annotations\n"
            "\n"
            "import re\n"
            "\n"
            "_HOME_ACTION = re.compile(",
        ),
        (
            "def rewrite(text, plugin, user_text=\"\", umo=\"\"):",
            "def rewrite(text, plugin, user_text=\"\"):",
        ),
        (
            "    body = _attach_last_line(body, user, plugin)\n"
            "    if not body:\n"
            "        if scene[\"road\"]:\n"
            "            body = \"我还在路上呢，快到了。\"\n"
            "        elif scene[\"outside\"]:\n"
            "            body = \"我到了，等会儿说。\"\n"
            "        else:\n"
            "            body = \"我在家呢。\"\n"
            "    # 唯一后置语义复检（task-29）：本函数每一步都在整句删除/替换（场景分句、\n"
            "    # 分享补句、回落句），出口必须再判一次「还答不答得上他最后一句」；\n"
            "    # 答不上就保留本轮候选（候选优先于通用场景句）。\n"
            "    return _post_recheck(body, plugin, umo, _last_user_line(user))",
            "    body = _attach_last_line(body, user, plugin)\n"
            "    if body:\n"
            "        return body\n"
            "    if scene[\"road\"]:\n"
            "        return \"我还在路上呢，快到了。\"\n"
            "    if scene[\"outside\"]:\n"
            "        return \"我到了，等会儿说。\"\n"
            "    return \"我在家呢。\"",
        ),
        (
            "    if not body.strip():\n"
            "        return _post_recheck(_safe_short_reply(umo, history_bot_texts(plugin, umo)),\n"
            "                             plugin, umo, user_text)\n"
            "    refs = list(recent or [])\n"
            "    if not refs:\n"
            "        refs = history_bot_texts(plugin, umo)\n"
            "    body, _dropped = drop_repeated_clauses(body, refs)\n"
            "    if body.strip():\n"
            "        body = strip_current_scene_conflicts(body, plugin)\n"
            "    if not body.strip():\n"
            "        body = _safe_short_reply(umo, refs)\n"
            "    # 唯一后置语义复检（task-29）：媒体标记/自我复读/场景剥离三步都可能把答案\n"
            "    # 一起删掉，出口再判一次；原候选答得上就保留候选（别发通用短句）。\n"
            "    return _post_recheck(body, plugin, umo, user_text)",
            "    if not body.strip():\n"
            "        return _safe_short_reply(umo, history_bot_texts(plugin, umo))\n"
            "    refs = list(recent or [])\n"
            "    if not refs:\n"
            "        refs = history_bot_texts(plugin, umo)\n"
            "    body, _dropped = drop_repeated_clauses(body, refs)\n"
            "    if body.strip():\n"
            "        body = strip_current_scene_conflicts(body, plugin)\n"
            "    if not body.strip():\n"
            "        return _safe_short_reply(umo, refs)\n"
            "    return body",
        ),
    ],
}

#: 整段插入（键 = 起止标记，值 = 删除的整段起止定位）：helper 块整体回退
CUTS = {
    "amo_reply_pipeline.py": (
        "\n\n# ---------------------------------------------------------------------------\n"
        "# 唯一后置语义复检（task-29，2026-11-22）",
        "\n\ndef _answer_food_first(",
        "\n\ndef _answer_food_first(",
    ),
    "amo_state_consistency.py": (
        "\n\n\ndef _post_recheck(text, plugin, umo=\"\", user_text=\"\"):",
        None,
        "",
    ),
}


def rebuild(name, text):
    pairs = REVERSE.get(name, ())
    for new, old in pairs:
        count = text.count(new)
        if count != 1:
            raise SystemExit("replace %s: matched %d times for: %r" % (name, count, new[:60]))
        text = text.replace(new, old, 1)
    cut = CUTS.get(name)
    if cut:
        start_marker, end_marker, replacement = cut
        start = text.find(start_marker)
        if start < 0:
            raise SystemExit("cut start not found: " + name)
        if end_marker:
            end = text.find(end_marker, start)
            if end < 0:
                raise SystemExit("cut end not found: " + name)
            text = text[:start] + replacement + text[end + len(end_marker):]
        else:
            text = text[:start] + replacement
    return text


def main():
    os.makedirs(BEFORE, exist_ok=True)
    os.makedirs(AFTER, exist_ok=True)
    chunks = []
    for name in FILES:
        path = os.path.join(ROOT, name)
        with io.open(path, encoding="utf-8") as fh:
            after = fh.read()
        pin = PIN_BEFORE.get(name)
        if pin:
            with io.open(pin, encoding="utf-8") as fh:
                before = fh.read()
        else:
            before = rebuild(name, after)
        # newline="" 保住原始换行，快照才能和改动前逐字节对得上
        with io.open(os.path.join(BEFORE, name), "w", encoding="utf-8",
                     newline="") as fh:
            fh.write(before)
        with io.open(os.path.join(AFTER, name), "w", encoding="utf-8",
                     newline="") as fh:
            fh.write(after)
        diff = list(difflib.unified_diff(
            before.splitlines(True), after.splitlines(True),
            fromfile="a/" + name, tofile="b/" + name, n=3))
        chunks.extend(diff)
        print("%-28s +%d -%d lines"
              % (name, len(after.splitlines()) - len(before.splitlines()),
                 sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))))
    # 新增测试文件整份
    test_name = os.path.join("tests", "test_post_rewrite_recheck.py")
    with io.open(os.path.join(ROOT, test_name), encoding="utf-8") as fh:
        test_lines = fh.readlines()
    chunks.extend(difflib.unified_diff([], test_lines, fromfile="/dev/null",
                                       tofile="b/" + test_name.replace(os.sep, "/"), n=3))
    out = os.path.join(OUT, "task29.diff")
    with io.open(out, "w", encoding="utf-8", newline="") as fh:
        fh.writelines(chunks)
    print("diff written:", out, "lines:", len(chunks))


if __name__ == "__main__":
    main()
