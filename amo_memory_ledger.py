# -*- coding: utf-8 -*-
"""记忆事实账本（task-28 P0-1/P0-2/P0-3/P0-6，2026-11-22）。

线上问题（work/audit/memory_persona.md §3.3/§3.6/§3.7）：同一件米白色针织开衫
21 小时内出现 5 条互相矛盾的 active 记忆（买了 649 / 没买 / 花 649 买下 / 试穿 /
看中 985 已放回），全部 active；770 条 active 记忆里 82 条含服务器/报错/面板/
token 这类后台词，只要被召回就会再次进私聊。

本模块只做三件事，且都是确定性的：
1. 事实账本：subject/state/source/valid_from/supersedes/conflict_key；同一
   conflict_key 只保留一个 active 状态，新状态 supersede 旧状态；
2. 系统/运维/调试词：写入拦截 + 召回过滤（零进记忆、零进提示词）；
3. 记忆里的精确金额/余额/等待分钟：注入前改写成 approximate。
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field

logger = logging.getLogger("amo.memory_ledger")

# ---------------------------------------------------------------------------
# P0-2 系统 / 运维 / 调试词（零进记忆、零进聊天）
# ---------------------------------------------------------------------------

#: 审计口径（§3.6.2 + 用户点名）：任何一条记忆/一句话命中就不该出现在私聊里。
SYSTEM_WORDS = (
    "服务器", "报错", "面板", "数据库", "接口", "超时", "权限", "后台", "配置",
    "上下文", "日志", "质检", "重写", "软件崩", "崩了", "程序", "代码", "错误",
    "系统提示", "系统消息", "后台记录", "待重写", "详情在面板",
    "收到你后来", "收到你那句", "提示词", "模型", "插件",
    "记忆", "重启", "降级", "熔断", "异常", "调试", "报错日志",
)

#: 拉丁字母的后台词要按词边界判，避免把 rapid 里的 api 误伤。
_LATIN_WORDS = ("token", "api", "bug", "sql", "http", "json", "openid", "webhook")
#: 允许后缀（线上出现过 "tokenle" 这种被改坏的词），但不能吃掉普通英文词。
_LATIN_RE = re.compile(
    r"(?<![A-Za-z])(" + "|".join(_LATIN_WORDS) + r")[A-Za-z]{0,6}(?![A-Za-z])", re.I)


def system_word_hit(text):
    """返回命中的第一个系统/运维词；没命中返回空串。"""
    body = str(text or "")
    if not body:
        return ""
    for word in SYSTEM_WORDS:
        if word in body:
            return word
    match = _LATIN_RE.search(body)
    return match.group(1) if match else ""


def drop_system_text(text):
    """系统词零进记忆：命中整条丢弃（不删词改句，避免留下半句怪话）。"""
    body = str(text or "")
    hit = system_word_hit(body)
    if hit:
        logger.info("[amo] 记忆命中系统词 %s，丢弃：%s", hit, body[:50])
        return ""
    return body


# ---------------------------------------------------------------------------
# 文本卫生：重复前缀 / 单字叠字
# ---------------------------------------------------------------------------

_REPEAT = re.compile(r"([\u4e00-\u9fff]{2,6})\1+")
_LAUGH = frozenset({"哈哈", "嘿嘿", "呵呵", "嘻嘻", "呜呜", "啊啊", "哇哇"})
#: 「还在车上上」是确定性改写残留：只折叠这些方位/后缀字的重复。
_DUP_TAIL = re.compile(
    r"(?<=[\u4e00-\u9fff])([上下里外中前后边面])\1(?=[，。！？、；：\s了的地]|$)")


def collapse_repeats(text):
    """折叠重复前缀（米白色米白色→米白色）和改写残留（还在车上上→还在车上）。"""
    body = str(text or "")
    if not body:
        return ""
    previous = None
    while previous != body:
        previous = body
        body = _REPEAT.sub(
            lambda match: match.group(0)
            if (match.group(1) in _LAUGH or len(set(match.group(1))) == 1)
            else match.group(1),
            body,
        )
    body = _DUP_TAIL.sub(r"\1", body)
    return body


# ---------------------------------------------------------------------------
# P0-3 报账式数字 → approximate
# ---------------------------------------------------------------------------

_CN_NUM = {1: "一", 2: "两", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九"}


def approx_money(value, suffix="块"):
    """精确金额 → 口语大概数（649→六百多块，4072→四千多）。"""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "一些" + suffix
    if amount <= 0:
        return "没多少"
    if amount < 10:
        return "几" + suffix
    if amount < 20:
        return "十几" + suffix
    if amount < 100:
        tens = int(amount // 10)
        more = "多" if amount % 10 else ""
        return _CN_NUM.get(tens, "几") + "十" + more + suffix
    if amount < 1000:
        hundreds = int(amount // 100)
        more = "多" if amount % 100 else ""
        return _CN_NUM.get(hundreds, "几") + "百" + more + suffix
    if amount < 10000:
        thousands = int(amount // 1000)
        more = "多" if amount % 1000 else ""
        return _CN_NUM.get(thousands, "几") + "千" + more + suffix
    return "一万多" + suffix


_BALANCE = re.compile(r"(余额|卡里|卡上|卡内|还剩|剩下)([^\d\n]{0,6})(\d{2,7}(?:\.\d+)?)")
_AMOUNT = re.compile(r"(\d{2,7}(?:\.\d+)?)\s*(?:元|块钱|块)")
_DURATION = re.compile(
    r"(等了?|等了有|排队等|等了差不多)\s*([一二三四五六七八九十两\d]{1,3})\s*(?:分钟|分|个小时|小时)")
#: 「就是985」「要价649」这种不带单位的报价也要大概化。
_BARE_PRICE = re.compile(r"(就是|价格是|要价|卖|才)\s*(\d{3,5})(?![\d元块])")


def approximate_amounts(text):
    """把记忆/聊天里的精确余额、价格、等待分钟改成大概说法。"""
    body = str(text or "")
    if not body:
        return ""
    body = _BALANCE.sub(
        lambda match: match.group(1) + match.group(2) + approx_money(match.group(3), suffix=""),
        body,
    )
    body = _AMOUNT.sub(
        lambda match: approx_money(match.group(1)),
        body,
    )
    body = _DURATION.sub(r"\1好一会儿", body)
    body = _BARE_PRICE.sub(
        lambda match: match.group(1) + approx_money(match.group(2), suffix=""),
        body,
    )
    return body


def format_memory_text(text):
    """记忆注入前统一卫生：重复前缀 + 精确数字。"""
    return approximate_amounts(collapse_repeats(text))


# ---------------------------------------------------------------------------
# P0-1 记忆事实账本
# ---------------------------------------------------------------------------

@dataclass
class MemoryFact:
    """一条可裁决的事实：同一 conflict_key 只允许一个 active state。"""

    conflict_key: str
    subject: str
    state: str
    source: str = "chat"
    valid_from: int = 0
    supersedes: int = 0
    value: str = ""
    memory_id: int = 0
    text: str = ""

    def as_entry(self, entry_id=0, at=0):
        entry = asdict(self)
        entry.update({"entry_id": int(entry_id), "active": True, "at": int(at)})
        return entry


#: 服装/配饰/包：同一件商品的状态必须唯一。
_ITEM_WORDS = (
    "开衫", "毛衣", "毛衫", "外套", "大衣", "羽绒服", "棉服", "风衣", "西装",
    "裙子", "连衣裙", "半身裙", "裤子", "牛仔裤", "卫衣", "衬衫", "T恤", "背心",
    "泳衣", "泳装", "比基尼", "睡衣", "围巾", "帽子", "鞋子", "球鞋", "靴子",
    "包包", "袜子", "耳环", "项链", "香水", "衣服", "上衣",
)
_ITEM_RE = re.compile(r"([\u4e00-\u9fff]{0,8}(?:" + "|".join(_ITEM_WORDS) + r"))")
#: 颜色/材质修饰词：只把紧贴商品名前面的这些字算进商品名，
#: 避免把「今天买了…」整段动词吞成 subject。
_COLOR_CHARS = frozenset("米黑灰蓝红绿黄粉白紫驼杏奶茶咖藏青其燕深浅淡亮色")
_MATERIAL_WORDS = ("针织", "毛线", "羊绒", "羽绒", "牛仔", "棉", "真丝", "雪纺",
                   "西装", "长袖", "短袖", "碎花", "纯色", "加厚", "薄款")

_STATE_RULES = (
    ("not_bought", re.compile(r"没买|没有买|不买|没下手|没舍得|放回去|放回|退掉|退了|嫌贵|下次再买|再想想|没要|没付")),
    ("bought", re.compile(r"买了|买下|买好|买回来|入手|付款|付了|刷了|下单|带回家|拿下|买到了")),
    ("tried", re.compile(r"试穿|试了|试过|试完|看中|看上|喜欢这|试衣服|看好了")),
)
_PRICE_RE = re.compile(r"(\d{2,7}(?:\.\d+)?)\s*(?:元|块钱|块)")


def _trim_item(name):
    """把「今天买了米白色针织开衫」收敛成「米白色针织开衫」。"""
    body = collapse_repeats(str(name or "").strip())
    if not body:
        return ""
    # 1) 找到商品名后缀的起点（后缀一定在尾部）
    cut = len(body)
    for word in _ITEM_WORDS:
        if body.endswith(word) and len(word) < cut:
            cut = len(body) - len(word)
            break
    # 2) 从后缀往前，只保留紧贴的颜色/材质修饰，动词和称呼不要
    while cut > 0:
        moved = False
        for word in _MATERIAL_WORDS:
            start = cut - len(word)
            if start >= 0 and body[start:cut] == word:
                cut = start
                moved = True
                break
        if moved:
            continue
        if body[cut - 1] in _COLOR_CHARS:
            cut -= 1
            continue
        break
    item = body[cut:]
    return item if len(item) >= 2 else body


#: 泛称衣物：句子里只有「衣服」时，状态要挂到本句前面最近的具体商品上。
GENERIC_ITEMS = frozenset({"衣服", "上衣"})
_SENTENCE_END = re.compile(r"[。！？；\n!?;]")


def _sentence_at(text, pos):
    """返回 pos 所在句子的 [start, end)（句号/问号/分号/换行切句，逗号不算）。"""
    start = 0
    for match in _SENTENCE_END.finditer(text):
        if match.start() >= pos:
            return start, match.start()
        start = match.end()
    return start, len(text)


def _shopping_facts(text, source, now):
    """从一句话里抽「某件商品 = 买了/没买/试穿」的事实。

    状态只在本句内匹配：跨句会把「我买了开衫。他让我穿黑色比基尼」
    里的「买了」算成比基尼的状态（真实库 #1372 的误判）。
    """
    body = str(text or "")
    facts = []
    for match in _ITEM_RE.finditer(body):
        item = _trim_item(match.group(1))
        if len(item) < 2:
            continue
        start, end = _sentence_at(body, match.start())
        window = body[start:end][:300]
        candidates = []
        for state, pattern in _STATE_RULES:
            for hit in pattern.finditer(window):
                candidates.append((hit.start(), state))
        if not candidates:
            continue
        # 最后一个状态词代表最终结论：「试了…但没买」取没买
        strong = [row for row in candidates if row[1] in ("bought", "not_bought")]
        pool = strong or candidates
        state = max(pool, key=lambda row: row[0])[1]
        value = ""
        price = _PRICE_RE.search(window)
        if price:
            value = approx_money(price.group(1))
        facts.append(MemoryFact(
            conflict_key="item:" + item,
            subject=item,
            state=state,
            source=str(source or "chat"),
            valid_from=int(now or 0),
            value=value,
            text=body[:160],
        ))
    facts = _dedupe_facts(facts)
    specifics = [item for item in facts if item.subject not in GENERIC_ITEMS]
    generics = [item for item in facts if item.subject in GENERIC_ITEMS]
    if specifics and generics:
        target = specifics[-1]
        for generic in generics:
            if generic.state in ("bought", "not_bought"):
                target.state = generic.state
            if generic.value and not target.value:
                target.value = generic.value
        facts = [item for item in facts if item.subject not in GENERIC_ITEMS]
    return _dedupe_facts(facts)


def _dedupe_facts(facts):
    seen = set()
    out = []
    for fact in facts:
        key = (fact.conflict_key, fact.state)
        if key in seen:
            continue
        seen.add(key)
        out.append(fact)
    return out


_BALANCE_CLAIM = re.compile(r"(余额|卡里|卡上|卡内|还剩|剩下)[^\d\n]{0,6}\d{2,7}")
_MASTER_CLAIM = re.compile(r"主人|宠物|调教")


def extract_facts(text, source="chat", now=0):
    """从一条文本抽可裁决事实（目前覆盖：商品买没买、余额口径、旧关系称呼）。"""
    body = str(text or "")
    if not body:
        return []
    facts = _shopping_facts(body, source, now)
    if _BALANCE_CLAIM.search(body):
        facts.append(MemoryFact(
            conflict_key="wallet:cash",
            subject="钱包余额",
            state="claimed",
            source=str(source or "chat"),
            valid_from=int(now or 0),
            value="",
            text=body[:160],
        ))
    if _MASTER_CLAIM.search(body):
        facts.append(MemoryFact(
            conflict_key="relation:self",
            subject="关系称呼",
            state="old_master",
            source=str(source or "chat"),
            valid_from=int(now or 0),
            text=body[:160],
        ))
    return facts


LEDGER_KEY = "memory_fact_ledger:%s"
MAX_ENTRIES = 200


class FactLedger:
    """按用户持久化的记忆事实账本（plugin.db.kv，不写生产记忆库）。"""

    def __init__(self, plugin, uid):
        self.plugin = plugin
        self.uid = str(uid or "")
        self._entries = None

    def load(self):
        if self._entries is not None:
            return self._entries
        rows = []
        try:
            data = self.plugin.db.kv_get(LEDGER_KEY % self.uid, []) or []
            if isinstance(data, list):
                rows = [row for row in data if isinstance(row, dict)]
        except Exception:
            rows = []
        self._entries = rows
        return rows

    def save(self):
        try:
            self.plugin.db.kv_set(LEDGER_KEY % self.uid, list(self.load())[-MAX_ENTRIES:])
        except Exception:  # cleanup: 兜底/降级，保持原行为
            pass

    def next_id(self):
        rows = self.load()
        return max([int(row.get("entry_id") or 0) for row in rows] + [0]) + 1

    def active(self):
        return [row for row in self.load() if row.get("active", True)]

    def active_state(self, conflict_key):
        for row in reversed(self.active()):
            if str(row.get("conflict_key") or "") == str(conflict_key or ""):
                return str(row.get("state") or "")
        return ""

    def superseded_memory_ids(self):
        ids = set()
        for row in self.load():
            if row.get("active", True):
                continue
            try:
                mid = int(row.get("memory_id") or 0)
            except (TypeError, ValueError):
                mid = 0
            if mid:
                ids.add(mid)
        return ids

    def record(self, fact, memory_id=0, now=0):
        """登记新事实：同 conflict_key 的旧 active 状态全部 supersede。"""
        if not isinstance(fact, MemoryFact):
            return []
        rows = self.load()
        entry_id = self.next_id()
        superseded = []
        for row in rows:
            if not row.get("active", True):
                continue
            if str(row.get("conflict_key") or "") != fact.conflict_key:
                continue
            row["active"] = False
            row["superseded_by"] = entry_id
            superseded.append(dict(row))
        entry = fact.as_entry(entry_id=entry_id, at=now)
        entry["memory_id"] = int(memory_id or 0)
        rows.append(entry)
        self.save()
        return superseded


def ledger_for(plugin, uid):
    """同一插件/用户复用同一个账本对象（省 kv 读）。"""
    cache = getattr(plugin, "_memory_fact_ledgers", None)
    if not isinstance(cache, dict):
        cache = {}
        try:
            plugin._memory_fact_ledgers = cache
        except Exception:
            return FactLedger(plugin, uid)
    key = str(uid or "")
    ledger = cache.get(key)
    if ledger is None:
        ledger = FactLedger(plugin, uid)
        cache[key] = ledger
    return ledger


# ---------------------------------------------------------------------------
# 写入 / 召回接线
# ---------------------------------------------------------------------------

def guard_memory_write(plugin, uid, content, kind="fact", subject="user", source="chat"):
    """写记忆前的卫生检查：系统词整条丢弃；重复前缀折叠。返回 (text, info)。"""
    body = collapse_repeats(str(content or "")).strip()
    if not body:
        return "", {"drop": "empty"}
    hit = system_word_hit(body)
    if hit:
        logger.info("[amo] 记忆写入拦截（系统词 %s）：%s", hit, body[:50])
        return "", {"drop": "system_word", "word": hit}
    return body, {"drop": ""}


def record_memory_facts(plugin, uid, content, memory_id=0, source="chat", now=0):
    """记忆写成功后登记事实，同一件商品只留一个 active 状态。"""
    if not str(content or "").strip():
        return []
    facts = extract_facts(content, source=source, now=now)
    if not facts:
        return []
    superseded = []
    try:
        ledger = ledger_for(plugin, uid)
        for fact in facts:
            superseded.extend(ledger.record(fact, memory_id=memory_id, now=now))
    except Exception as exc:
        logger.debug("[amo] 事实账本登记失败: %s", exc)
        return []
    if superseded:
        logger.info(
            "[amo] 事实 supersede %d 条：conflict_key=%s state=%s",
            len(superseded), facts[0].conflict_key, facts[0].state,
        )
    return superseded


def _created(item):
    try:
        return int(item.get("created_at") or 0)
    except (AttributeError, TypeError, ValueError):
        return 0


def _newer(left, right):
    try:
        return int(left.get("created_at") or 0) >= int(right.get("created_at") or 0)
    except (TypeError, ValueError):
        return True


def filter_recalled(plugin, uid, memories):
    """召回后过滤：系统词不进提示词；账本已 supersede 的旧事实不再注入；
    同一件商品即使两条都还在库里，也只留最新那一条状态。"""
    rows = [item for item in (memories or []) if isinstance(item, dict)]
    if not rows:
        return []
    superseded_ids = set()
    try:
        superseded_ids = ledger_for(plugin, uid).superseded_memory_ids()
    except Exception:
        superseded_ids = set()
    kept = []
    annotated = []
    dropped_system = 0
    dropped_superseded = 0
    for item in rows:
        content = str(item.get("content") or "")
        if not content:
            continue
        if system_word_hit(content):
            dropped_system += 1
            continue
        try:
            memory_id = int(item.get("id") or item.get("memory_id") or 0)
        except (TypeError, ValueError):
            memory_id = 0
        if memory_id and memory_id in superseded_ids:
            dropped_superseded += 1
            continue
        conflict = ""
        for fact in extract_facts(content):
            if fact.conflict_key.startswith("item:") or fact.conflict_key == "wallet:cash":
                conflict = fact.conflict_key
                break
        annotated.append((item, conflict))
    # 同一 conflict_key 只留最新 created_at（列表顺序通常已是相关度/新旧混合）
    keep_index = {}
    for index, (item, conflict) in enumerate(annotated):
        if not conflict:
            continue
        current = keep_index.get(conflict)
        if current is None or _newer(item, annotated[current][0]):
            keep_index[conflict] = index
    # 泛称衣物（item:衣服/上衣）没有唯一商品身份：召回里只要还有更新的具体商品事实，
    # 泛称那条旧状态就不进提示词（线上 #1378「衣服没买」与 #1401「开衫已放回」并存）。
    generic_keys = {"item:" + name for name in GENERIC_ITEMS}
    newest_specific = 0
    for item, conflict in annotated:
        if conflict and conflict not in generic_keys:
            newest_specific = max(newest_specific, _created(item))
    dropped_conflict = 0
    for index, (item, conflict) in enumerate(annotated):
        if conflict and keep_index.get(conflict) != index:
            dropped_conflict += 1
            continue
        if (conflict in generic_keys and newest_specific
                and _created(item) < newest_specific):
            dropped_conflict += 1
            continue
        kept.append(item)
    if dropped_system or dropped_superseded or dropped_conflict:
        logger.info(
            "[amo] 记忆召回过滤：系统词 %d 条、已 supersede %d 条、同对象旧状态 %d 条",
            dropped_system, dropped_superseded, dropped_conflict,
        )
    return kept


__all__ = [
    "SYSTEM_WORDS", "system_word_hit", "drop_system_text",
    "collapse_repeats", "approx_money", "approximate_amounts", "format_memory_text",
    "MemoryFact", "FactLedger", "ledger_for", "extract_facts",
    "guard_memory_write", "record_memory_facts", "filter_recalled",
]
