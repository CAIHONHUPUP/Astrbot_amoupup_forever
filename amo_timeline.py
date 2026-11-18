# -*- coding: utf-8 -*-
"""Timeline：日程块身份 / 读写 / 查询的唯一实现。

重构目标（第 1 阶段：日程块身份收敛）：
- 块身份只在这里算（block_id），amo_state_common 只做转发，旧调用方不受影响；
- 切块只走显式 split_schedule_block（父 id 由调用方指定哪段继承），启发式只作 fallback；
- 块读取只走 load/blocks，统一「排序 → 去重 → 空 end 防御 → 跨 0 点修复」；
- 块写入只走 save，内部仍然调用 commit_schedule_change，绝不绕过唯一写入口；
- 查询 current / last_ended / between 给消费者用，不再各自手搓分钟比较。

这一层是**纯规则 + 薄 IoC**：只认 plugin/state 上已有的 schedule / db /
commit_schedule_change，不 import 任何状态机模块（避免循环导入）。包内相对导入
失败时退回绝对导入，测试里 sys.path.insert(ROOT) 直接按文件加载也能用。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

try:  # Phase 11：分钟/时钟纯函数唯一实现在 amo_numbers
    from .amo_numbers import clock_of, minutes_of
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_numbers import clock_of, minutes_of

import hashlib
import re
import uuid
from typing import Any

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed

try:  # 纯规则模块：类别优先级（重叠块取谁的既有语义）
    from .photo_rules import CATEGORY_PRIORITY
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_rules import CATEGORY_PRIORITY

#: 一天结束（24:00 的分钟数）；块绝不允许越过这里。
DAY_END_MINUTES = 24 * 60
#: 空 end / 坏 end 的兜底时长，沿用旧代码 parse_hhmm(end) or begin + 60 的口径。
DEFAULT_BLOCK_MINUTES = 60

_START_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def clock_text(value: Any) -> str:
    """统一成 HH:MM：9:00 与 09:00 是同一个块，24:00 保持 24:00。

    解析不了的原样返回（去空格），交给调用方按脏数据处理。
    """
    body = str(value or "").strip()[:5]
    hour_text, _, minute_text = body.partition(":")
    try:
        hour = int(hour_text)
        minute = int(minute_text or 0)
    except (TypeError, ValueError):
        return body
    if hour < 0 or hour > 24 or minute < 0 or minute > 59:
        return body
    return "%02d:%02d" % (hour, minute)


# minutes_of / clock_of 已迁到 amo_numbers（顶部 import）。


#: 事件身份前缀：一个 id 贯穿日程块 / 账本 / 块日记 / 记忆。
EVENT_ID_PREFIX = "ev_"
#: event_id 负载的十六进制长度（ev_ + 16 = 19 字符，和旧 16 位内容哈希一眼可分）
EVENT_ID_HEX = 16


def event_id(day, item) -> str:
    """读块上已持久化的 event_id；没有返回空串（旧数据 / 纯规则假件）。"""
    row = item if isinstance(item, dict) else {}
    return str(row.get("event_id") or "").strip()


def mint_event_id(day, item) -> str:
    """按 day+start+end 确定性生成 event_id：迁移/回填的唯一口径。

    同一天同一时段只可能有一个块（日程不允许重叠），所以这个键唯一；日期参与
    哈希，跨天的同一时段是两个不同事件。旧行缺 event_id 时统一用它补齐。
    """
    row = item if isinstance(item, dict) else {}
    raw = "%s|%s|%s" % (str(day or ""), clock_text(row.get("start")), clock_text(row.get("end")))
    return EVENT_ID_PREFIX + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:EVENT_ID_HEX]


def new_event_id() -> str:
    """新块的新身份：创建时分配一次，之后随块持久化，不再由内容推导。"""
    return EVENT_ID_PREFIX + uuid.uuid4().hex[:EVENT_ID_HEX]


def ensure_event_id(day, item) -> str:
    """取事件身份：已持久化就用它；旧行缺失时按 day+start+end 确定性补（不写回）。"""
    value = event_id(day, item)
    if value:
        return value
    return mint_event_id(day, item)


def legacy_block_id(day, item) -> str:
    """旧的内容哈希身份（兼容未迁移数据和纯规则测试）。

    块改内容或改时间 → 这个 id 会变；新数据一律用 event_id，不再走这里。
    日期不参与哈希（block_diary 的 day 列区分），所以同内容块跨天仍是同一 id。
    """
    row = item if isinstance(item, dict) else {}
    start = clock_text(row.get("start"))
    end = clock_text(row.get("end"))
    activity = str(row.get("activity") or "").strip()
    place = str(row.get("place") or "").strip()
    raw = "%s-%s|%s|%s" % (start, end, activity, place)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def block_id(day, item) -> str:
    """日程块的稳定身份：优先 event_id，没有才退回旧内容哈希。

    新写入口在 commit 时给每块分配并持久化 event_id，所以编辑/切块后父块仍是
    同一个 id；block_diary 用同一个 id 当 block_id 列，四本账（日程/账本/块日记/
    记忆）说的是同一个事件。
    """
    return event_id(day, item) or legacy_block_id(day, item)


def _minute_point(value, default=None):
    """把 datetime / HH:MM / 分钟数统一成当天分钟数。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return max(0, min(DAY_END_MINUTES, value))
    if hasattr(value, "hour") and hasattr(value, "minute"):
        try:
            return int(value.hour) * 60 + int(value.minute)
        except Exception:
            return default
    parsed = minutes_of(value)
    return default if parsed is None else parsed


def _span_minutes(item):
    """块的 [begin, end) 分钟区间；坏区间返回 None。"""
    row = item if isinstance(item, dict) else {}
    begin = minutes_of(row.get("start"))
    finish = minutes_of(row.get("end"))
    if begin is None or finish is None or finish <= begin:
        return None
    return begin, finish


def stamp_event_ids(day, old_items, new_items):
    """写库前统一事件身份：返回 (带 event_id 的新列表, 统计)。

    规则（用户确认的语义）：
    - 已经带 event_id 的块原样保留；
    - 旧身份哈希相同 → 直接继承旧 event_id（整份重生成/重排不换 id）；
    - 与旧块重叠 ≥ 一半（编辑内容/改时间）→ 继承旧 event_id（父块保留）；
    - 一个旧块被切成多块时，最先开始的那一段继承父 id，其余拿新 id（新块新 id）；
    - 匹配不到任何旧块 → 分配全新 id。
    """
    candidates = []
    for row in old_items or []:
        if not isinstance(row, dict):
            continue
        candidates.append({
            "eid": ensure_event_id(day, row),
            "legacy": legacy_block_id(day, row),
            "span": _span_minutes(row),
            "used": False,
        })
    out = [dict(row) if isinstance(row, dict) else row for row in (new_items or [])]
    # 一个 event_id 只能属于一个块：切块时调用方常直接 copy 父块（event_id 也被复制），
    # 这里按「最早开始的那段继承父 id」去重，其余清空后重新匹配/分配。
    by_event: dict[str, Any] = {}
    for row in out:
        if not isinstance(row, dict):
            continue
        current = event_id(day, row)
        if current:
            by_event.setdefault(current, []).append(row)
    for rows_with_id in by_event.values():
        if len(rows_with_id) <= 1:
            continue

        def _parent_rank(row):
            begin = minutes_of(row.get("start"))
            span = _span_minutes(row)
            return (begin if begin is not None else DAY_END_MINUTES,
                    -(span[1] - span[0]) if span else 0)

        rows_with_id.sort(key=_parent_rank)
        for row in rows_with_id[1:]:
            row.pop("event_id", None)
    stats = {"kept": 0, "inherited": 0, "assigned": 0}
    pending = []
    for item in out:
        if not isinstance(item, dict):
            continue
        current = event_id(day, item)
        if current:
            for cand in candidates:
                if cand["eid"] == current:
                    cand["used"] = True
            stats["kept"] += 1
        else:
            pending.append(item)
    # 先按旧身份精确匹配：整份重生成时内容没变的块不能换 id。
    still = []
    for item in pending:
        legacy = legacy_block_id(day, item)
        match = None
        for cand in candidates:
            if not cand["used"] and cand["legacy"] == legacy:
                match = cand
                break
        if match is not None:
            match["used"] = True
            item["event_id"] = match["eid"]
            stats["inherited"] += 1
        else:
            still.append(item)
    # 再按重叠比例继承（编辑/切块）：按开始时间升序，父 id 落在最早那一段。
    still.sort(key=lambda row: minutes_of(row.get("start")) or 0)
    for item in still:
        span = _span_minutes(item)
        best, best_score = None, 0.0
        for cand in candidates:
            if cand["used"] or cand["span"] is None or span is None:
                continue
            overlap = min(span[1], cand["span"][1]) - max(span[0], cand["span"][0])
            if overlap <= 0:
                continue
            score = overlap / float(min(span[1] - span[0], cand["span"][1] - cand["span"][0]))
            if score >= 0.5 and score > best_score:
                best, best_score = cand, score
        if best is not None:
            best["used"] = True
            item["event_id"] = best["eid"]
            stats["inherited"] += 1
        else:
            item["event_id"] = new_event_id()
            stats["assigned"] += 1
    return out, stats


def split_schedule_block(day, parent, parts, inherit_index=0):
    """显式切块：调用方给父块/父 id 和 parts，并指定哪一段继承父身份。

    - parent：父块 dict（用它的 event_id；缺 id 时按 day+start+end 确定性补），
      或直接给父 id 字符串；
    - parts：切出来的块（dict 列表）；返回顺序与 parts 一致（写库时再统一排序）；
    - inherit_index：继承父 id 的那一段下标（默认 0 = 最早那段；越界按 0）。

    一个 event_id 只能属于一个块：其余段复制来的父 id 会被换成新 id，显式带的
    其它 event_id 保留。低重叠也照常继承——这里不做重叠比例判断，那是旧启发式
    （stamp_event_ids）的 fallback 口径，只留给没有走显式 API 的调用方。
    返回 (rows, info)，info = {parent_id, inherited, dropped_parent_id, assigned}。
    """
    day_text = str(day or "")
    parent_row = parent if isinstance(parent, dict) else {}
    explicit = str(parent).strip() if isinstance(parent, str) else ""
    parent_id = explicit or ensure_event_id(day_text, parent_row)
    rows = [dict(row) for row in (parts or []) if isinstance(row, dict)]
    if not rows:
        return [], {"parent_id": parent_id, "inherited": 0,
                    "dropped_parent_id": 0, "assigned": 0}
    index = inherit_index if isinstance(inherit_index, int) and not isinstance(
        inherit_index, bool) else 0
    if index < 0 or index >= len(rows):
        index = 0
    dropped = assigned = 0
    for pos, row in enumerate(rows):
        current = event_id(day_text, row)
        if pos == index:
            row["event_id"] = parent_id
            continue
        if current and current not in ("", parent_id):
            continue  # 显式带了别的身份：保留
        if current == parent_id:
            dropped += 1
        assigned += 1
        row["event_id"] = new_event_id()
    info = {"parent_id": parent_id, "inherited": 1,
            "dropped_parent_id": dropped, "assigned": assigned}
    return rows, info


def _repair_row(row: dict):
    """返回 (item, begin, end)；脏 start 返回 None。

    统一语义（沿用旧代码，只是集中到一处）：
    - start 必须是 HH:MM，否则丢弃（旧 _sorted_blocks 的过滤口径）；
    - 空 end / 0 点 end → start + 60（旧 parse_hhmm(end) or begin + 60）；
    - end <= start（跨 0 点/倒置）→ start + 60，并夹到 24:00，**绝不留下跨 0 点块**。
    """
    start_text = str(row.get("start") or "").strip()
    if not _START_RE.match(start_text):
        return None
    begin = minutes_of(start_text)
    if begin is None or begin >= DAY_END_MINUTES:
        return None
    end = minutes_of(row.get("end"))
    if end is None or end <= 0:
        end = begin + DEFAULT_BLOCK_MINUTES
    if end <= begin:
        end = begin + DEFAULT_BLOCK_MINUTES
    end = min(DAY_END_MINUTES, max(begin + 1, end))
    item = dict(row)
    item["start"] = clock_of(begin)
    item["end"] = clock_of(end)
    return item, begin, end


class Timeline:
    """绑定一个 plugin/state 的日程时间轴（读缓存 → 库，写回唯一入口）。"""

    def __init__(self, plugin=None):
        self.plugin = plugin

    # ---------- 身份 ----------
    @staticmethod
    def block_id(day, item) -> str:
        return block_id(day, item)

    @staticmethod
    def split(day, parent, parts, inherit_index=0):
        """显式切块（见模块级 split_schedule_block）。"""
        return split_schedule_block(day, parent, parts, inherit_index=inherit_index)

    # ---------- 存取 ----------
    @property
    def state(self):
        state = getattr(self.plugin, "state", None)
        return state if state is not None else self.plugin

    @property
    def db(self):
        db = getattr(self.state, "db", None)
        return db if db is not None else getattr(self.plugin, "db", None)

    def raw(self, day) -> list:
        """原始日程行：先读内存缓存，再读库；两者都没有就是空。

        P2-1：同一次面板 GET 里 items 与 current_activity 各读一次；调用方可用
        state._schedule_read_scope = {"day": ...} 把这次同步 GET 的底层读取合并成
        一次（作用域只覆盖同步读，写完/越界都清掉，不会留下脏读）。
        """
        day_text = str(day or "")
        scope = getattr(self.state, "_schedule_read_scope", None)
        if isinstance(scope, dict) and scope.get("day") == day_text and "rows" in scope:
            cached_rows = scope.get("rows") or []
            return list(cached_rows) if isinstance(cached_rows, (list, tuple)) else []
        rows: list = []
        try:
            rows = (getattr(self.state, "schedule", {}) or {}).get(day_text) or []
        except Exception as _amo_exc:
            _note_swallowed("amo_timeline.Timeline.raw", _amo_exc)
            rows = []
        if not rows:
            getter = getattr(self.db, "get_schedule", None)
            if callable(getter):
                try:
                    rows = getter(day_text) or []
                except Exception as _amo_exc:
                    _note_swallowed("amo_timeline.Timeline.raw.db", _amo_exc)
                    rows = []
        rows = list(rows) if isinstance(rows, (list, tuple)) else []
        if isinstance(scope, dict) and scope.get("day") == day_text:
            scope["rows"] = list(rows)
        return rows

    def blocks(self, day) -> list:
        """规范化后的块列表：脏 start 丢弃、按 start 升序、按块身份去重、坏区间修复。"""
        day_text = str(day or "")
        return self.normalize(self.raw(day_text), day_text)

    @staticmethod
    def normalize(rows, day="") -> list:
        """把任意日程行整理成块列表（排序/去重/空 end 防御/跨 0 点修复）。"""
        prepared = []
        seen = set()
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            fixed = _repair_row(row)
            if fixed is None:
                continue
            item, begin, _end = fixed
            bid = block_id(day, item)
            if bid in seen:
                continue
            seen.add(bid)
            prepared.append((begin, item))
        prepared.sort(key=lambda pair: pair[0])
        return [item for _begin, item in prepared]

    def load(self, day) -> list:
        """读这一天的块；与 blocks 同义，保留给「读写」对称的调用方。"""
        return self.blocks(day)

    def save(self, day, rows, source="", reason="", old_items=None):
        """写这一天的块：内部仍走 commit_schedule_change（唯一写入口）。"""
        day_text = str(day or "")
        state = self.state
        commit = getattr(state, "commit_schedule_change", None)
        if not callable(commit):
            commit = getattr(self.plugin, "commit_schedule_change", None)
        if callable(commit):
            return commit(day_text, list(rows or []), old_items=old_items,
                          source=source, reason=reason)
        # 极简假件（只测纯查询）没有写入口：直接落库，保持可用。
        saver = getattr(self.db, "save_schedule", None)
        if callable(saver):
            saver(day_text, list(rows or []))
            return True
        return False

    # ---------- 查询 ----------
    def current(self, day, now=None):
        """覆盖 now 的那一块；重叠时按「最近开始 + 类别优先级」取后者（旧 _pick_covering）。"""
        point = _minute_point(now)
        if point is None:
            return None
        hits = [row for row in self.blocks(day)
                if _minute_point(row["start"]) <= point < _minute_point(row["end"])]
        if not hits:
            return None
        hits.sort(key=lambda row: (
            _minute_point(row["start"]),
            CATEGORY_PRIORITY.get(str(row.get("category") or ""), 0),
        ))
        return dict(hits[-1])

    def last_ended(self, day, now=None):
        """已经结束的块里最后结束的那一块（按结束时间，再按开始时间）。"""
        point = _minute_point(now)
        if point is None:
            return None
        ended = [row for row in self.blocks(day)
                 if _minute_point(row["end"]) <= point]
        if not ended:
            return None
        ended.sort(key=lambda row: (_minute_point(row["end"]), _minute_point(row["start"])))
        return dict(ended[-1])

    def between(self, day, a, b):
        """与 [a, b) 有重叠的块；返回按开始时间排序的副本。"""
        begin = _minute_point(a, 0)
        finish = _minute_point(b, DAY_END_MINUTES)
        if finish <= begin:
            return []
        return [dict(row) for row in self.blocks(day)
                if _minute_point(row["start"]) < finish and _minute_point(row["end"]) > begin]


# ---------- 模块级薄封装：load(plugin, day) / save(plugin, day, rows, ...) ----------

def for_plugin(plugin) -> Timeline:
    return Timeline(plugin)


def load(plugin, day) -> list:
    return Timeline(plugin).blocks(day)


def save(plugin, day, rows, source="", reason="", old_items=None):
    return Timeline(plugin).save(day, rows, source=source, reason=reason, old_items=old_items)
