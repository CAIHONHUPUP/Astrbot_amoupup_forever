# -*- coding: utf-8 -*-
"""社会见闻引擎（2026-11-14）：她刷热搜、形成自己的看法、聊天时自然提起。

设计要点（都是纯函数，方便单测）：

- 抓取：微博热搜 / 知乎热榜 / 百度风云榜，只要标题 + 摘要，抓不到就当今天没刷；
- 安全：政治敏感、色情暴力、血腥自杀类直接丢弃（正则兜底）；
- 认知边界：观点只能基于抓到的标题/摘要，禁止她编国家大事；
- 记忆：每次抓取合并成 **一条** 记忆，带 #社会见闻 #社会见闻_过期 标签，3 天过期；
- 检索限流：聊天注入最多最近 2 条、24 小时内；
- 幂等：同一天同一时段只跑一次；同一条热搜中午晚上都抓到只留一次；
- 时间戳：每条见闻都带 ts / day / slot，过期自动清理。

实机由 amo 后台 tick 在 12:30 / 20:00 触发，结果注入 <amo_state> 的【社会见闻】。
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import re
import urllib.request

try:
    from .clock import now as _now
except ImportError:  # 直接按文件加载时
    from clock import now as _now

SLOTS = ((12, 30, "noon"), (20, 0, "night"))
WINDOW_MINUTES = 30
TTL_DAYS = 3
MAX_ITEMS = 5
INJECT_LIMIT = 2
INJECT_MAX_AGE_HOURS = 24
RETRY_SECONDS = 300

KV_ENTRIES = "social_learn:entries"
KV_SEEN = "social_learn:seen"
KV_LAST_TRY = "social_learn:last_try"
KV_SLOT = "social_learn:slot:"

TAG_LINE = "#社会见闻 #社会见闻_过期"

_BANNED = re.compile(
    r"习近平|共产党|中共|六四|法轮|台独|港独|疆独|藏独|民运|政变|"
    r"色情|裸体|性侵|强奸|嫖娼|卖淫|"
    r"血腥|碎尸|尸体|屠杀|灭门|割喉|割腕|自杀|跳楼|枪击|爆炸|恐怖袭击|"
    r"惨死|虐杀|分尸|杀妻|杀母|"
    r"加沙|以色列|巴勒斯坦|俄乌|乌克兰|俄罗斯|美国|特朗普|拜登|普京|军演|导弹|空袭|战争|"
    r"外交部|国防部|APEC|G20|联合国|北约|中东|台海"
)

#: 观点里出现、但标题里没有的“国家大事”词：视为她编造
_POLITICAL = ("国家", "政府", "党中央", "主席", "总统", "战争", "政策", "外交部", "国务院")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

#: 她真正会关心的话题：民生 / 职场 / 钱 / 生活，优先于纯国家大事
INTEREST_WORDS = (
    "加班", "工资", "打工人", "职场", "上班", "下班", "房租", "房价", "物价",
    "消费", "省钱", "攒钱", "贷款", "存款", "理财", "基金", "股票", "失业",
    "裁员", "招聘", "教育", "学生", "高考", "医疗", "看病", "医院", "养老",
    "结婚", "相亲", "生育", "孩子", "电影", "电视剧", "综艺", "明星", "网红",
    "美食", "外卖", "奶茶", "咖啡", "旅游", "交通", "地铁", "高铁", "天气",
    "台风", "降温", "宠物", "猫", "狗", "快递", "租房", "买房", "装修", "搬家",
    "猪肉", "菜价", "油价", "电价", "天然气", "社保", "公积金", "年终奖", "加班费",
    "打工", "老板", "公司", "同事", "面试", "简历", "A股", "股市", "金价", "存款利率",
)

WEIBO_URL = "https://weibo.com/ajax/side/hotSearch"
ZHIHU_URL = "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=10"
BAIDU_URL = "https://top.baidu.com/api/board?platform=wise&tab=realtime"
TOUTIAO_URL = "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc"


def _text(value, limit=160):
    body = re.sub(r"\s+", " ", str(value or "")).strip()
    return body[:limit]


def parse_weibo(payload):
    rows = (((payload or {}).get("data") or {}).get("realtime")) or []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _text(row.get("word") or row.get("note"), 60)
        summary = _text(row.get("note") or row.get("word"), 120)
        if title:
            out.append({"title": title, "summary": summary, "source": "微博"})
    return out


def parse_zhihu(payload):
    rows = (payload or {}).get("data") or []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        target = row.get("target") or {}
        title = _text(target.get("title") or row.get("title"), 60)
        summary = _text(target.get("excerpt") or row.get("excerpt"), 120)
        if title:
            out.append({"title": title, "summary": summary, "source": "知乎"})
    return out


def parse_baidu(payload):
    """百度榜单的卡片是嵌套结构，递归找带 word/query 的条目。"""
    out = []

    def walk(node):
        if isinstance(node, dict):
            title = node.get("word") or node.get("query")
            if title:
                out.append({"title": _text(title, 60),
                            "summary": _text(node.get("desc") or "", 120),
                            "source": "百度"})
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk((payload or {}).get("data") or {})
    return out


def parse_toutiao(payload):
    rows = (payload or {}).get("data") or []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _text(row.get("Title") or row.get("title"), 60)
        summary = _text(row.get("Abstract") or row.get("LabelDesc") or "", 120)
        if title:
            out.append({"title": title, "summary": summary, "source": "头条"})
    return out


def filter_safe(items):
    """安全过滤：敏感/色情/暴力/血腥一律不进她的世界。"""
    out = []
    seen_titles = set()
    for item in items or []:
        title = _text((item or {}).get("title"), 60)
        summary = _text((item or {}).get("summary"), 120)
        if len(title) < 4 or title in seen_titles:
            continue
        if _BANNED.search(title) or _BANNED.search(summary):
            continue
        seen_titles.add(title)
        out.append({"title": title, "summary": summary,
                    "source": _text((item or {}).get("source"), 10)})
    return out


def interest_score(item):
    body = _text((item or {}).get("title"), 60) + _text((item or {}).get("summary"), 120)
    return sum(1 for word in INTEREST_WORDS if word in body)


def rank_items(items):
    """民生/职场/钱的新闻排前面，纯时政排后面（不删除，只排序）。"""
    return sorted(list(items or []), key=interest_score, reverse=True)


def dedupe(items, seen=None, day=""):
    """同一条热搜只留第一次；seen 记录标题 -> 日期。"""
    seen = dict(seen or {})
    out = []
    for item in items or []:
        title = _text((item or {}).get("title"), 60)
        if not title:
            continue
        if day and seen.get(title) == day:
            continue
        seen[title] = day or seen.get(title, "")
        out.append(item)
    return out, seen


def due_slot(now=None):
    """现在是不是 12:30 / 20:00 这一档（半小时窗口）。"""
    now = now or _now()
    for hour, minute, slot in SLOTS:
        if now.hour == hour and minute <= now.minute < minute + WINDOW_MINUTES:
            return slot
    return ""


def build_opinion_prompt(items, state_line=""):
    numbered = "\n".join(
        "%d. %s%s" % (idx + 1, item.get("title", ""),
                      ("（%s）" % item.get("summary")) if item.get("summary") else "")
        for idx, item in enumerate(items or [])
    )
    system = ("你就是这个角色本人，一个普通打工女孩。写自己的看法，不写旁白，"
              "不写“作为AI”。")
    prompt = (
        "今天刷到的热搜：\n" + numbered + "\n\n"
        + (("你现在的状态：%s\n" % state_line) if state_line else "")
        + "结合你自己的生活（上班、房租、钱、感情），写一段 50 字左右的看法或吐槽，"
          "像跟老公聊天那样口语化、有情绪；事实只来自上面的标题和摘要。只输出这段话。"
    )
    return system, prompt


def clean_opinion(text, limit=120):
    body = _text(text, limit)
    body = body.strip("“”\"'‘’ ")
    body = re.sub(r"^(我觉得|我认为|说实话|说真的)[，,：:]\s*", "", body)
    return body[:limit]


def fact_guard(opinion, items):
    """观点只能基于抓到的内容：不能带敏感词，也不能编标题里没有的国家大事。"""
    body = _text(opinion, 200)
    if not body or len(body) > 120:
        return False
    if _BANNED.search(body):
        return False
    source = " ".join(
        (_text(item.get("title"), 60) + " " + _text(item.get("summary"), 120))
        for item in items or []
    )
    for word in _POLITICAL:
        if word in body and word not in source:
            return False
    return True


def aggregate(rows, now=None):
    """3-5 条见闻 + 她的看法 → 一条记忆文本（带标签和时间戳）。"""
    now = now or _now()
    kept = [row for row in (rows or []) if _text(row.get("opinion"), 120)]
    if not kept:
        return ""
    stamp = now.strftime("%m-%d %H:%M")
    lines = []
    for row in kept:
        title = _text(row.get("title"), 60)
        opinion = _text(row.get("opinion"), 120)
        lines.append("%s：%s" % (title, opinion))
    return "[今日见闻 %s] %s %s" % (stamp, TAG_LINE, "；".join(lines))


def is_expired(entry, now=None, days=TTL_DAYS):
    now = now or _now()
    try:
        ts = float((entry or {}).get("ts") or 0)
    except Exception:
        return True
    return (now.timestamp() - ts) > days * 86400


def cleanup_entries(entries, now=None, days=TTL_DAYS):
    now = now or _now()
    return [entry for entry in (entries or []) if not is_expired(entry, now, days)]


def recent_block(entries, now=None, limit=INJECT_LIMIT, max_age_hours=INJECT_MAX_AGE_HOURS):
    """聊天注入：最近 24 小时、最多 2 条，带一句“话题合适再聊”。"""
    now = now or _now()
    fresh = []
    for entry in entries or []:
        try:
            ts = float((entry or {}).get("ts") or 0)
        except Exception:
            continue
        if (now.timestamp() - ts) > max_age_hours * 3600:
            continue
        text = _text((entry or {}).get("text"), 400)
        if text:
            fresh.append((ts, text))
    fresh.sort(key=lambda pair: pair[0], reverse=True)
    picked = [text for _, text in fresh[:max(0, int(limit))]]
    if not picked:
        return ""
    return ("【社会见闻】你今天刷到的：" + " ｜ ".join(picked)
            + "。聊天话题合适时自然提起；他在说别的事就先听他说。")


class SocialLearnEngine:
    """抓热搜 → 生成她的看法 → 合并成一条临时记忆 → 聊天注入。"""

    def __init__(self, plugin, fetcher=None):
        self.p = plugin
        self.fetcher = fetcher
        self.last_error = ""

    # ---------- 小工具 ----------
    def _enabled(self):
        try:
            return bool(self.p.cfg_bool("social_learn_enabled", True))
        except Exception:
            return True

    def _kv_get(self, key, default):
        try:
            return self.p.db.kv_get(key, default)
        except Exception:
            return default

    def _kv_set(self, key, value):
        try:
            self.p.db.kv_set(key, value)
        except Exception:
            return

    def _http_json(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                                   "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    # ---------- 抓取 ----------
    async def collect(self):
        if self.fetcher is not None:
            data = self.fetcher()
            if asyncio.iscoroutine(data):
                data = await data
            return rank_items(filter_safe(data))
        items = []
        for url, parser in ((TOUTIAO_URL, parse_toutiao), (BAIDU_URL, parse_baidu),
                            (WEIBO_URL, parse_weibo)):
            try:
                payload = await asyncio.to_thread(self._http_json, url)
                items.extend(parser(payload))
            except Exception as exc:
                self.last_error = "%s: %s" % (url, exc)
        safe = filter_safe(items)
        seen = set()
        unique = []
        for item in safe:
            if item["title"] in seen:
                continue
            seen.add(item["title"])
            unique.append(item)
        by_source = {}
        for item in unique:
            by_source.setdefault(item.get("source") or "", []).append(item)
        picked = []
        for source in ("头条", "百度", "微博"):
            rows = sorted(by_source.get(source) or [], key=interest_score, reverse=True)
            picked.extend(rows[:2])
        for item in unique:
            if len(picked) >= MAX_ITEMS:
                break
            if item not in picked:
                picked.append(item)
        return rank_items(picked)[:MAX_ITEMS]

    # ---------- 记忆 ----------
    async def _write_memory(self, text, umo="", uid=""):
        writer = getattr(self.p, "_write_memory_checked", None)
        if writer is None or not uid:
            return False
        try:
            return bool(await writer(uid, text, kind="social_news", tier=0, subject="bot"))
        except Exception:
            return False

    # ---------- 主流程 ----------
    async def run(self, now=None, umo="", uid=""):
        now = now or _now()
        if not self._enabled():
            return {"ok": False, "reason": "disabled"}
        slot = due_slot(now)
        if not slot:
            return {"ok": False, "reason": "not_due"}
        day = now.strftime("%Y-%m-%d")
        slot_key = KV_SLOT + day + ":" + slot
        if self._kv_get(slot_key, False):
            return {"ok": False, "reason": "done"}
        last_try = float(self._kv_get(KV_LAST_TRY, 0) or 0)
        if now.timestamp() - last_try < RETRY_SECONDS:
            return {"ok": False, "reason": "backoff"}
        self._kv_set(KV_LAST_TRY, now.timestamp())
        items = await self.collect()
        seen = self._kv_get(KV_SEEN, {}) or {}
        items, seen = dedupe(items, seen, day)
        if not items:
            return {"ok": False, "reason": "no_items", "error": self.last_error}
        state_line = ""
        try:
            state_line = self.p.state.snapshot()["activity"].get("activity", "")
        except Exception:
            state_line = ""
        system, prompt = build_opinion_prompt(items[:MAX_ITEMS], state_line)
        raw = ""
        llm = getattr(self.p, "llm", None)
        if llm is not None:
            try:
                raw = await asyncio.wait_for(
                    llm.chat_raw(umo or getattr(self.p, "_last_umo", "") or "", system, prompt),
                    timeout=60)
            except Exception:
                raw = ""
        opinion = clean_opinion(raw)
        if not fact_guard(opinion, items):
            opinion = ""
        rows = [{"title": item["title"], "summary": item.get("summary", ""),
                 "opinion": opinion} for item in items[:MAX_ITEMS]]
        text = aggregate(rows, now)
        if not text:
            return {"ok": False, "reason": "no_opinion"}
        entries = cleanup_entries(self._kv_get(KV_ENTRIES, []) or [], now)
        entries.append({"ts": now.timestamp(), "day": day, "slot": slot, "text": text})
        self._kv_set(KV_ENTRIES, entries)
        self._kv_set(KV_SEEN, {title: day for title, day in seen.items() if day == day})
        self._kv_set(slot_key, True)
        wrote = await self._write_memory(text, umo, uid)
        return {"ok": True, "items": len(rows), "text": text, "memory": wrote}

    async def tick(self, umo="", uid=""):
        if not self._enabled():
            return {"ok": False, "reason": "disabled"}
        now = _now()
        self._kv_set(KV_ENTRIES, cleanup_entries(self._kv_get(KV_ENTRIES, []) or [], now))
        return await self.run(now, umo, uid)

    def injection(self, now=None):
        if not self._enabled():
            return ""
        entries = self._kv_get(KV_ENTRIES, []) or []
        return recent_block(entries, now or _now())
