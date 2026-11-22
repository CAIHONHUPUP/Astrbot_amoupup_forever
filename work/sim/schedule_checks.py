# -*- coding: utf-8 -*-
"""日程一致性检查器（只读、纯函数，供 work/sim/schedule_sim.py 调用）。

失败分类固定为六类：
    drift / overlap / gap / transition_missing / meal_over / meal_duplicate
每条问题 = {category, subtype, severity, detail, day, at, root_cause, severity}
- severity=fail 计入失败统计；severity=warn 只进警告区（设计取舍/低置信）。
- root_cause 指向 ROOT_CAUSES 里的键，报告按它聚合「疑似根因」。

不自带状态：输入都是当天的已落库块 + 生成前原始块 + 上下文 + 账本行。
"""
from __future__ import annotations

import datetime as dt
import re

import amo_food_audit
import amo_food_cost
import amo_location_rules as loc
from schedule_plan import CITY_POOL, PROVINCE_CITIES, activity_place_conflict

CATEGORIES = ("drift", "overlap", "gap", "transition_missing", "meal_over", "meal_duplicate")

ROOT_CAUSES = {
    "R1_place_normalize_restaurant": (
        "domains/schedule/place.py:46-47 normalize_place 把任何 zone=restaurant 的 place "
        "折叠成「楼下小馆」：商场餐厅/景区餐馆/外地小吃街全变家附近小馆"),
    "R2_transit_insert_tail_drop": (
        "amo_transition.py:1220-1278 insert_transit_blocks + :1181-1205 _push_after："
        "插通勤块后把后块整体顺延，越过 24:00 的块被直接丢弃（丢睡眠/收尾，尾部悬空）"),
    "R3_transit_before_normalize": (
        "amo_state_schedule.py:768-788 先插通勤块，:1294 才跑 _normalize_schedule_items "
        "（含 :1498 _enforce_zone_activity）：后置改写会作废刚插好的过渡块"),
    "R4_night_office_folded_home": (
        "amo_state_schedule.py:1516-1528 _enforce_zone_activity：18:00 之后的 office 块被整块"
        "折成 place=家/category=rest「在家休息」，且不补通勤块 → 办公室瞬间回家"),
    "R5_continuity_checker_blind": (
        "amo_state_schedule.py:317-368 place_continuity_problems 的 _area() 取「·」前段"
        "（通常是城市名）当大区域，且 place 含「楼下」直接放行 → 同城跨地点几乎从不报警"),
    "R6_category_word_rewrite": (
        "amo_state_schedule.py:1488-1492/1529-1536 _enforce_zone_activity 的 category 词表："
        "通勤块文案没有通勤词就被改成「在路上赶路」，丢失「到楼下便利店买早餐」这类细节"),
    "R7_event_precedence": (
        "schedule_skeleton.py:685-724 周末 outing/homebody 分支先于 rest/事件分支；"
        "而 _pick_event(:507-524) 仍会给休息日发 overtime/friend_dinner → 事件不落进日程"),
    "R8_multiday_trip_not_continued": (
        "出差只按当天生成（domains/schedule/rest_segments.py:6-30 结尾 23:00-24:00 还在酒店），"
        "次日 build_context 不继承昨天出差状态 → 跨天从外地酒店瞬移回家睡觉"),
    "R9_transition_text_stale_place": (
        "amo_transition.py:1140-1168 过渡文案用归一化前的地点名，:_normalize_schedule_items "
        "之后 place 被改写，过渡文案与相邻块地点不再一致"),
    "R10_same_complex_transit": (
        "amo_transition.py:1094-1119 transit_minutes 对同商圈/同楼（写字楼→写字楼附近、"
        "商场→商场·餐厅）也按 15-40 分钟插通勤块，推挤时间轴并触发 R2"),
    "R11_snack_block_no_guard": (
        "amo_food_cost.py:275-378 ensure_block_window 切块后没校验 24 小时覆盖；"
        "宵夜块把尾块顶出去时不拦"),
    "R12_friend_dinner_scenic_gap": (
        "domains/schedule/social_segments.py:29-33 景区版朋友聚餐：17:00 开车回家、"
        "18:00 又写「回家换衣服，出门赴约」，中间没有真正的到家块"),
    "R13_snack_place_home": (
        "amo_food_cost.py:302-305 ensure_block_window 把宵夜块 place/zone 写死「家/home」，"
        "并且直接切分覆盖块：出差住酒店/通勤路上也会切出「在家吃宵夜」，把一段通勤劈成两截"),
    "R14_unknown_zone_place_home": (
        "domains/schedule/place.py:52-54 normalize_place 对没枚举的 zone（playground 等）"
        "一律回落成「家」：游乐园/水上乐园的块 place=家，与同块 activity「在园里玩」自相矛盾"),
    "R15_snack_memory_overwrite": (
        "amo_food_cost.py:373-376 ensure_block_window 在 commit 之后又把 state.schedule[day] "
        "覆盖成提交前的 kept 列表，冲掉了 commit 侧 drop_short_blocks 的截断 → 内存 23 条/表 22 条"),
}

ROAD_CATS = ("road", "station")
HOME_CATS = ("home",)
TRANSIT_TEXT_WORDS = ("路上", "通勤", "地铁", "公交", "打车", "开车", "骑车", "走路", "赶路",
                      "下班", "回家", "出门", "去", "到", "站")
GENERIC_COMMUTE = ("在路上赶路",)
ANCHOR_CATS = (
    (("公司", "写字楼", "单位", "办公室", "工位"), "office"),
    (("商场", "步行街", "购物中心"), "mall"),
    (("饭店", "餐厅", "饭馆", "小馆", "食堂", "咖啡店", "便利店", "小吃街", "火锅", "烧烤"), "restaurant"),
    (("酒店", "宾馆", "民宿", "旅馆", "客栈"), "hotel"),
    (("医院", "诊所", "卫生院"), "hospital"),
    (("学校", "学校", "图书馆", "教室", "宿舍"), "school"),
    (("景区", "公园", "山", "湖", "海", "沙滩", "古镇"), "scenic"),
    (("车站", "北站", "机场", "高铁站", "火车站"), "station"),
    (("路上",), "road"),
)
_SELF_CITIES = None


def cities():
    global _SELF_CITIES
    if _SELF_CITIES is None:
        names = {"东莞", "贵阳", "成都", "北京", "上海", "广州", "深圳", "杭州", "南京", "武汉",
                 "西安", "重庆", "长沙", "苏州"}
        names.update(str(x) for x in (CITY_POOL or []) if str(x))
        names.update(str(x) for x in (PROVINCE_CITIES or {}).values() if str(x))
        _SELF_CITIES = names
    return _SELF_CITIES


def hm(text):
    """'HH:MM' -> 分钟；'24:00' -> 1440；非法返回 None。"""
    body = str(text or "").strip()[:5]
    if body in ("24:00", "2400"):
        return 24 * 60
    try:
        hour, minute = body.split(":")
        hour, minute = int(hour), int(minute)
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 24 and 0 <= minute < 60):
        return None
    return hour * 60 + minute


def span(row):
    if not isinstance(row, dict):
        return None
    begin, finish = hm(row.get("start")), hm(row.get("end"))
    if begin is None or finish is None or finish <= begin:
        return None
    return begin, finish


#: 日程块以 zone 为准（place 只是文案，归一化会把它洗成笼统词）
ZONE_CATS = {"home": "home", "office": "office", "mall": "mall",
             "restaurant": "restaurant", "scenic": "scenic", "playground": "scenic",
             "hotel": "hotel", "hospital": "hospital", "health": "hospital",
             "school": "school", "road": "road", "street": "street",
             "station": "station", "transit": "road", "commute": "road"}


def category(row):
    zone = str(row.get("zone") or "").strip()
    if zone in ZONE_CATS:
        return ZONE_CATS[zone]
    return loc.resolve(row.get("place"), zone)


def is_transit(row):
    if not isinstance(row, dict):
        return False
    if str(row.get("source") or "") == "transit_block":
        return True
    return category(row) in ROAD_CATS or str(row.get("zone") or "") in ROAD_CATS


def strip_city(place):
    text = str(place or "").strip()
    if "·" not in text:
        return text
    parts = [part for part in text.split("·") if part]
    if len(parts) >= 2 and parts[0] in cities():
        return "·".join(parts[1:])
    return text


def area_key(place):
    return strip_city(place).replace("·", "")


def same_area(left, right):
    a, b = area_key(left), area_key(right)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def anchor_cat(text):
    value = str(text or "")
    for words, cat in ANCHOR_CATS:
        if any(word in value for word in words):
            return cat
    return ""


def make_issue(day, category_name, subtype, detail, severity="fail", at="", root_cause="", block=None):
    issue = {"day": str(day), "category": category_name, "subtype": subtype,
             "severity": severity, "detail": str(detail or "")[:400], "at": str(at or ""),
             "root_cause": str(root_cause or "")}
    if isinstance(block, dict):
        issue["block"] = {key: block.get(key) for key in
                          ("start", "end", "activity", "place", "zone", "category")}
    return issue


# ---------------------------------------------------------------- 原始块对齐
def match_raw(rows, raw_rows):
    """把落库块对回生成前原始块：先按 activity 全等，再按同 activity 最近起点。"""
    used = set()
    matched = {}
    pool = [row for row in (raw_rows or []) if isinstance(row, dict)]
    for index, row in enumerate(rows or []):
        if not isinstance(row, dict):
            continue
        activity = str(row.get("activity") or "")
        if not activity or str(row.get("source") or "") == "transit_block":
            continue
        best = None
        for ridx, raw in enumerate(pool):
            if ridx in used or str(raw.get("activity") or "") != activity:
                continue
            if best is None:
                best = ridx
                continue
            delta = abs((hm(raw.get("start")) or 0) - (hm(row.get("start")) or 0))
            best_delta = abs((hm(pool[best].get("start")) or 0) - (hm(row.get("start")) or 0))
            if delta < best_delta:
                best = ridx
        if best is not None:
            used.add(best)
            matched[index] = pool[best]
    return matched


# ---------------------------------------------------------------- 1) 时间轴
def check_timeline(day, rows):
    issues = []
    if not rows:
        return [make_issue(day, "gap", "empty_day", "当天没有任何日程块", "fail")]
    spans = []
    for index, row in enumerate(rows):
        item = span(row)
        if item is None:
            issues.append(make_issue(day, "gap", "bad_time", "时间不可解析或 end<=start: %r"
                                     % {k: row.get(k) for k in ("start", "end", "activity")},
                                     "fail", root_cause=""))
            continue
        spans.append((item[0], item[1], index, row))
    if not spans:
        return issues
    spans.sort()
    if spans[0][0] > 0:
        issues.append(make_issue(day, "gap", "head_gap", "00:00-%s 没有块" % _hhmm(spans[0][0]),
                                 "fail", block=spans[0][3]))
    if spans[-1][1] < 24 * 60:
        issues.append(make_issue(day, "gap", "tail_gap",
                                 "%s-24:00 没有块（尾块丢失/悬空）" % _hhmm(spans[-1][1]),
                                 "fail", root_cause="R2_transit_insert_tail_drop",
                                 block=spans[-1][3]))
    cursor = 0
    for begin, finish, index, row in spans:
        if begin < cursor:
            issues.append(make_issue(day, "overlap", "block_overlap",
                                     "%s 与前一块重叠 %d 分钟" % (_hhmm(begin), cursor - begin),
                                     "fail", at=_hhmm(begin), block=row))
        elif begin > cursor:
            issues.append(make_issue(day, "gap", "block_gap",
                                     "%s-%s 空档 %d 分钟" % (_hhmm(cursor), _hhmm(begin), begin - cursor),
                                     "fail", at=_hhmm(cursor), block=row))
        cursor = max(cursor, finish)
    if cursor > 24 * 60:
        issues.append(make_issue(day, "gap", "beyond_midnight", "排到 24:00 之后: %s" % _hhmm(cursor), "fail"))
    # 尾块被通勤块推挤：睡眠被压成碎片 / 整块丢掉（不悬空但当天没睡觉）
    sleep_minutes = 0
    sleep_rows = [row for row in rows if str(row.get("category") or "") == "sleep" and span(row)]
    for row in sleep_rows:
        begin, finish = span(row)
        sleep_minutes += max(0, finish - begin)
    if sleep_rows:
        last_sleep = max(sleep_rows, key=lambda row: span(row)[1])
        begin, finish = span(last_sleep)
        if finish >= 24 * 60 and finish - begin < 30:
            issues.append(make_issue(day, "gap", "tail_compressed_sleep",
                                     "收尾睡眠块只剩 %d 分钟（%s-%s），被顺延挤掉"
                                     % (finish - begin, _hhmm(begin), _hhmm(finish)),
                                     "fail", at=_hhmm(begin),
                                     root_cause="R2_transit_insert_tail_drop", block=last_sleep))
    elif span(spans[-1][3])[1] >= 24 * 60:
        issues.append(make_issue(day, "gap", "no_sleep_block",
                                 "当天没有任何睡眠块（尾块丢失，收尾是「%s」）"
                                 % str(spans[-1][3].get("activity") or ""),
                                 "fail", at=_hhmm(spans[-1][0]),
                                 root_cause="R2_transit_insert_tail_drop", block=spans[-1][3]))
    elif sleep_minutes < 240:
        issues.append(make_issue(day, "gap", "sleep_short",
                                 "当天睡眠合计只有 %d 分钟" % sleep_minutes, "warn",
                                 root_cause="R2_transit_insert_tail_drop"))
    return issues


def _hhmm(minute):
    try:
        minute = int(minute)
    except (TypeError, ValueError):
        return ""
    return "24:00" if minute >= 24 * 60 else "%02d:%02d" % (minute // 60, minute % 60)


# ---------------------------------------------------------------- 2) 地点跳变
def check_place_jumps(day, rows, raw_rows):
    """相邻块换地点却没有路上过渡块（同商圈/同楼按原始地点名放行）。"""
    issues = []
    matched = match_raw(rows, raw_rows)
    ordered = sorted((row for row in rows if span(row)), key=lambda row: span(row)[0])
    for index in range(1, len(ordered)):
        prev, row = ordered[index - 1], ordered[index]
        pspan, cspan = span(prev), span(row)
        if pspan[1] != cspan[0]:
            continue  # 时间不连续交给 timeline，避免重复计
        pcat, ccat = category(prev), category(row)
        if pcat == "unknown" or ccat == "unknown":
            continue
        same = pcat == ccat and same_area(prev.get("place"), row.get("place"))
        if same:
            continue
        if is_transit(prev) or is_transit(row):
            continue  # 已经有一段路上块
        # 原始地点同商圈（生成侧本来就不需要通勤，比如 写字楼→写字楼附近）
        raw_prev = matched.get(_index_of(rows, prev))
        raw_cur = matched.get(_index_of(rows, row))
        if raw_prev is not None and raw_cur is not None:
            if same_area(raw_prev.get("place"), raw_cur.get("place")):
                continue
        detail = "%s %s(%s) -> %s(%s) 之间没有过渡块" % (
            _hhmm(pspan[1]), prev.get("activity"), prev.get("place") or pcat,
            row.get("activity"), row.get("place") or ccat)
        if "宵夜" in str(prev.get("activity") or "") or "宵夜" in str(row.get("activity") or ""):
            root = "R13_snack_place_home"
        elif "office" in (pcat, ccat) and "home" in (pcat, ccat):
            root = "R4_night_office_folded_home"
        elif "restaurant" in (pcat, ccat):
            root = "R1_place_normalize_restaurant"
        else:
            root = "R5_continuity_checker_blind"
        issues.append(make_issue(day, "transition_missing", "place_change", detail, "fail",
                                 at=_hhmm(pspan[1]), root_cause=root, block=row))
    return issues


def _index_of(rows, target):
    for index, row in enumerate(rows):
        if row is target:
            return index
    return -1


def check_place_collapse(day, rows, raw_rows):
    """归一化把「商场·餐厅 / 外地小吃街」等折成楼下小馆：地点语义漂移。"""
    issues = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            conflict = activity_place_conflict(row.get("activity"), row.get("place"),
                                               row.get("zone"))
        except Exception:  # cleanup: 仿真容错
            conflict = False
        if conflict:
            issues.append(make_issue(day, "drift", "activity_place_conflict",
                                     "活动「%s」与地点「%s」/zone=%s 矛盾"
                                     % (str(row.get("activity") or "")[:26],
                                        row.get("place"), row.get("zone")),
                                     "fail", at=str(row.get("start") or ""),
                                     root_cause="R1_place_normalize_restaurant", block=row))
    matched = match_raw(rows, raw_rows)
    out_words = ("商场", "景区", "车站", "酒店", "外省", "客户公司", "医院", "学校", "古镇", "海边")
    for index, row in enumerate(rows):
        raw = matched.get(index)
        if raw is None:
            continue
        _zone = str(row.get("zone") or "")
        _raw_place = str(raw.get("place") or "")
        if (str(row.get("place") or "") == "家" and _zone not in ("home", "", "unknown")
                and _raw_place and not any(word in _raw_place for word in ("家", "楼下", "小区"))):
            issues.append(make_issue(day, "drift", "zone_fallback_home",
                                     "zone=%s 的块「%s」地点被回落成「家」（原始地点「%s」）"
                                     % (_zone, raw.get("activity"), _raw_place),
                                     "fail", at=str(row.get("start") or ""),
                                     root_cause="R14_unknown_zone_place_home", block=row))
            continue
        if str(row.get("place") or "") != "楼下小馆":
            continue
        if category(raw) != "restaurant" and str(raw.get("zone") or "") != "restaurant":
            continue
        raw_place = str(raw.get("place") or "")
        if not raw_place or any(word in raw_place for word in ("楼下", "家", "小区")):
            continue
        if not any(word in raw_place for word in out_words):
            continue
        issues.append(make_issue(day, "drift", "place_collapsed_to_home",
                                 "原始地点「%s」被归一化成「楼下小馆」（语义变成本小区餐馆）" % raw_place,
                                 "fail", at=str(row.get("start") or ""),
                                 root_cause="R1_place_normalize_restaurant", block=row))
    return issues


# ---------------------------------------------------------------- 3) 过渡块
def check_transitions(day, rows, raw_rows, ctx):
    issues = []
    matched = match_raw(rows, raw_rows)
    ordered = sorted((row for row in rows if span(row)), key=lambda row: span(row)[0])
    for index, row in enumerate(ordered):
        if not is_transit(row):
            continue
        act = str(row.get("activity") or "")
        item = span(row)
        prev = ordered[index - 1] if index else None
        nxt = ordered[index + 1] if index + 1 < len(ordered) else None
        if prev is None or nxt is None:
            issues.append(make_issue(day, "transition_missing", "floating_transit",
                                     "%s 过渡块没有前后邻块" % (_hhmm(item[0]) if item else "?"),
                                     "fail", at=_hhmm(item[0]) if item else "", block=row))
            continue
        if span(prev)[1] != item[0] or item[1] != span(nxt)[0]:
            issues.append(make_issue(day, "gap", "transit_not_contiguous",
                                     "%s 过渡块与前后块不首尾相接" % _hhmm(item[0]),
                                     "fail", at=_hhmm(item[0]), block=row))
        # A. 已经从家/公司出发，却再写一次「回家/去公司」
        prev_cat = category(prev)
        if prev_cat in HOME_CATS and re.search(r"(回家|回去|到家)", act):
            issues.append(make_issue(day, "drift", "transition_after_home",
                                     "上一块已经在家（%s），过渡却写「%s」" % (prev.get("place"), act),
                                     "fail", at=_hhmm(item[0]),
                                     root_cause="R4_night_office_folded_home", block=row))
        elif prev_cat == "office" and re.search(r"(去公司|到公司|上班)", act):
            issues.append(make_issue(day, "drift", "transition_after_office",
                                     "上一块已经在公司（%s），过渡却写「%s」" % (prev.get("place"), act),
                                     "fail", at=_hhmm(item[0]),
                                     root_cause="R4_night_office_folded_home", block=row))
        # B. 文案里的目的地 vs 下一个非过渡块（「从 X 去 Y」交给 C 精确比对）
        pair = re.search(r"从(.{1,14}?)去(.{1,14}?)[，,（(]", act)
        dest = "" if pair else destination_of(act)
        after = _next_real(ordered, index)
        if dest and after is not None:
            after_cat = category(after)
            raw_after = matched.get(_index_of(rows, after))
            target_place = (raw_after or after).get("place")
            if dest == "home":
                if after_cat not in HOME_CATS + ("hotel",):
                    issues.append(make_issue(day, "drift", "transition_unfulfilled_home",
                                             "过渡文案说回家（%s），下一块却是 %s(%s)，中间没到家"
                                             % (act[:28], after.get("activity"),
                                                after.get("place") or after_cat),
                                             "fail", at=_hhmm(item[0]),
                                             root_cause="R9_transition_text_stale_place", block=row))
            elif not _dest_matches(dest, after_cat, act, target_place):
                issues.append(make_issue(day, "drift", "transition_destination_mismatch",
                                         "过渡文案目的地「%s」与下一块 %s(%s) 不一致"
                                         % (dest, after.get("activity"), after.get("place") or after_cat),
                                         "fail", at=_hhmm(item[0]),
                                         root_cause="R9_transition_text_stale_place", block=row))
        # C. 「从 X 去 Y」X/Y 必须对得上前后原始地点
        if pair:
            from_text, to_text = pair.group(1), pair.group(2)
            raw_prev = matched.get(_index_of(rows, prev))
            raw_next = matched.get(_index_of(rows, after)) if after is not None else None
            if raw_prev is not None and not same_area(from_text, raw_prev.get("place")):
                issues.append(make_issue(day, "drift", "transition_from_mismatch",
                                         "过渡说「从%s」出发，上一块实际在 %s" % (from_text, raw_prev.get("place")),
                                         "fail", at=_hhmm(item[0]),
                                         root_cause="R9_transition_text_stale_place", block=row))
            if raw_next is not None and not same_area(to_text, raw_next.get("place")):
                if not _dest_matches(anchor_cat(to_text) or to_text, category(after),
                                     to_text, raw_next.get("place")):
                    issues.append(make_issue(day, "drift", "transition_to_mismatch",
                                             "过渡说「去%s」，下一块实际在 %s"
                                             % (to_text, raw_next.get("place")),
                                             "fail", at=_hhmm(item[0]),
                                             root_cause="R9_transition_text_stale_place", block=row))
        # D. 路上块却写「到家/到公司/在工位/窝在」这类终态
        if re.search(r"(到家了|到了公司|到公司了|在工位|在办公室|在公司了|窝在|瘫在|躺在)", act):
            issues.append(make_issue(day, "drift", "transit_terminal_claim",
                                     "还在路上却写终态：「%s」" % act[:30],
                                     "warn", at=_hhmm(item[0]),
                                     root_cause="R9_transition_text_stale_place", block=row))
    return issues


def destination_of(act):
    text = str(act or "")
    if re.search(r"(回家|到家|回去|往回走)", text):
        return "home"
    # 「从 X 去 Y」优先取 Y
    pair = re.search(r"从(.{1,14}?)去(.{1,14}?)[，,（(]", text)
    if pair:
        return anchor_cat(pair.group(2)) or ""
    match = re.search(r"(?:去|到|赶往|赶去)([^，,。；;（(]{1,16})", text)
    if match:
        return anchor_cat(match.group(1))
    if "赴约" in text or "出门" in text:
        return "date"
    return ""


def _dest_matches(dest, after_cat, text, target_place):
    if dest == "date":
        return after_cat in ("restaurant", "mall", "scenic", "home", "social", "date")
    if dest == after_cat:
        return True
    # 去酒店/公司/商场后先在附近吃饭是正常动线
    if after_cat == "restaurant" and dest in ("hotel", "office", "mall", "scenic", "station"):
        return True
    # 文案里的地名和下一块原始地点同商圈也算对得上
    if same_area(dest, str(target_place or "")):
        return True
    return anchor_cat(dest) == after_cat


def _next_real(ordered, index):
    for pos in range(index + 1, len(ordered)):
        if not is_transit(ordered[pos]):
            return ordered[pos]
    return None


def _prev_real(ordered, index):
    for pos in range(index - 1, -1, -1):
        if not is_transit(ordered[pos]):
            return ordered[pos]
    return None


# ---------------------------------------------------------------- 4) 关键过渡是否存在
def check_required_transitions(day, rows, ctx, raw_rows):
    """起床→通勤→到公司 / 下班→回家 / 吃饭→回家 三类过渡块是否齐全。"""
    issues = []
    ordered = sorted((row for row in rows if span(row)), key=lambda row: span(row)[0])
    matched = match_raw(rows, raw_rows)
    meta = ctx.get("meta") or {}
    event = ctx.get("event") or {}
    rest = bool(meta.get("rest"))
    # --- 起床：睡眠块之后必须是家里块（不是直接上路/上班）
    sleeps = [row for row in ordered if str(row.get("category") or "") == "sleep"]
    for sleep in sleeps:
        sspan = span(sleep)
        if sspan[1] < 5 * 60 or sspan[1] >= 12 * 60:
            continue
        after = [row for row in ordered if span(row)[0] == sspan[1]]
        if not after:
            continue
        wake = after[0]
        if category(wake) not in HOME_CATS and category(wake) not in ("hotel", "hospital"):
            issues.append(make_issue(day, "transition_missing", "wake_missing",
                                     "睡醒（%s）后直接 %s(%s)，没有起床/在家过渡"
                                     % (_hhmm(sspan[1]), wake.get("activity"), wake.get("place")),
                                     "fail", at=_hhmm(sspan[1]), block=wake))
        break
    # --- 早上出门：第一个工作块之前必须有路上块
    office_rows = [row for row in ordered if category(row) == "office"
                   and str(row.get("category") or "") in ("work", "commute", "rest", "life")]
    first_office = None
    for row in office_rows:
        if 6 * 60 <= span(row)[0] < 13 * 60:
            first_office = row
            break
    if first_office is not None and not rest and event.get("kind") != "business_trip":
        before = [row for row in ordered if span(row)[1] <= span(first_office)[0]]
        if not before or not is_transit(before[-1]):
            issues.append(make_issue(day, "transition_missing", "morning_outbound",
                                     "上班前一块是 %s(%s)，没有通勤过渡块"
                                     % (before[-1].get("activity") if before else "无",
                                        category(before[-1]) if before else "-"),
                                     "fail", at=_hhmm(span(first_office)[0]), block=first_office))
        # --- 下班回家：最后一个 office 块之后到第一个家块之间必须有路上块
    offices = [row for row in ordered if category(row) == "office"]
    if offices and not rest and event.get("kind") != "business_trip":
        last_office = max(offices, key=lambda row: span(row)[1])
        if 15 * 60 <= span(last_office)[1] <= 23 * 60 + 59:
            home_after = [row for row in ordered
                          if span(row)[0] >= span(last_office)[1] and category(row) in HOME_CATS]
            if home_after:
                home_row = home_after[0]
                road = [row for row in ordered
                        if is_transit(row) and span(last_office)[1] <= span(row)[0]
                        and span(row)[1] <= span(home_row)[0]]
                if not road:
                    issues.append(make_issue(day, "transition_missing", "evening_return",
                                             "下班后从 %s 直接到 %s，中间没有回家通勤块"
                                             % (last_office.get("place"), home_row.get("place")),
                                             "fail", at=_hhmm(span(last_office)[1]),
                                             root_cause="R2_transit_insert_tail_drop",
                                             block=home_row))
            elif not home_after:
                # 一整天没有回家块：只对非出差日提示
                if not event.get("kind"):
                    issues.append(make_issue(day, "transition_missing", "no_home_after_work",
                                             "最后一个公司块 %s 之后没有回家块"
                                             % _hhmm(span(last_office)[1]), "warn",
                                             at=_hhmm(span(last_office)[1]), block=last_office))
    # --- 外面吃饭→回家：相邻两块（外面餐馆 -> 家）之间必须有路上块
    for index in range(1, len(ordered)):
        prev, row = ordered[index - 1], ordered[index]
        if span(prev)[1] != span(row)[0]:
            continue
        if category(prev) == "restaurant" and str(prev.get("category") or "") in ("meal", "social", "date") \
                and category(row) in HOME_CATS:
            issues.append(make_issue(day, "transition_missing", "meal_to_home",
                                     "从餐馆（%s）直接到家，中间没有路上块" % prev.get("activity"),
                                     "fail", at=_hhmm(span(row)[0]),
                                     root_cause="R1_place_normalize_restaurant", block=row))
    return issues


# ---------------------------------------------------------------- 5) 事件一致性
def check_events(day, rows, ctx, raw_rows):
    issues = []
    event = ctx.get("event") or {}
    kind = str(event.get("kind") or "none")
    meta = ctx.get("meta") or {}
    day_type = str((ctx.get("day_type") or {}).get("id") or "")
    places = " ".join(str(row.get("place") or "") + str(row.get("activity") or "") for row in rows)
    if kind == "business_trip":
        city = str(event.get("city") or "")
        has_office_away = any(category(row) == "office"
                              and city and city in str(row.get("place") or "")
                              for row in rows)
        has_hotel = any(category(row) == "hotel" for row in rows)
        if not (has_office_away or has_hotel):
            issues.append(make_issue(day, "drift", "event_unreflected_trip",
                                     "事件=临时外派(%s)，日程里没有外地公司/酒店块" % city,
                                     "fail", root_cause="R8_multiday_trip_not_continued"))
    elif kind == "overtime":
        if not meta.get("rest"):
            late_office = [row for row in rows if category(row) == "office"
                           and span(row) and span(row)[0] >= 19 * 60]
            if not late_office:
                issues.append(make_issue(day, "drift", "event_unreflected_overtime",
                                         "事件=临时加班，但 19:00 后没有公司块（被折回家）",
                                         "fail", root_cause="R4_night_office_folded_home"))
        else:
            has_office = any(category(row) == "office" for row in rows)
            if not has_office:
                issues.append(make_issue(day, "drift", "event_unreflected_rest_overtime",
                                         "休息日事件=临时加班（文案说回不去），日程仍是%s日，没有公司块"
                                         % (day_type or "休息"),
                                         "warn", root_cause="R7_event_precedence"))
    elif kind == "friend_dinner":
        # A1：自动社交已下线；旧事件即使出现，日程里也不许再有社交块。
        social = [row for row in rows
                  if str(row.get("category") or "") in ("social", "date")]
        if social:
            issues.append(make_issue(day, "drift", "legacy_friend_dinner_social",
                                     "旧 friend_dinner 事件仍产生了社交块（A1 违规）",
                                     "fail", root_cause="A1_social_disabled"))
    scenic = ctx.get("scenic") or {}
    if isinstance(scenic, dict) and scenic.get("name"):
        name = str(scenic.get("name") or "")
        token = name[:3]
        if token and token not in places:
            issues.append(make_issue(day, "drift", "event_unreflected_scenic",
                                     "上下文选了景点「%s」，日程里找不到" % name,
                                     "warn", root_cause="R7_event_precedence"))
    return issues


# ---------------------------------------------------------------- 5.5) 生成侧改写
def check_rewrites(day, rows, raw_rows):
    """归一化/收口阶段把原始块改写掉的痕迹：通勤文案变空、夜间公司折回家。"""
    issues = []
    activities = {str(row.get("activity") or "") for row in rows if isinstance(row, dict)}
    ordered = sorted((row for row in rows if span(row)), key=lambda row: span(row)[0])
    for raw in raw_rows or []:
        if not isinstance(raw, dict):
            continue
        activity = str(raw.get("activity") or "")
        if not activity or activity in activities:
            continue
        rspan = span(raw)
        if rspan is None:
            continue
        if str(raw.get("zone") or "") == "road" or category(raw) in ROAD_CATS:
            for row in ordered:
                if str(row.get("activity") or "") not in GENERIC_COMMUTE:
                    continue
                span_row = span(row)
                if abs(span_row[0] - rspan[0]) <= 90:
                    issues.append(make_issue(day, "drift", "transit_text_flattened",
                                             "原始通勤块「%s」被改写成「%s」" % (activity, row.get("activity")),
                                             "fail", at=_hhmm(rspan[0]),
                                             root_cause="R6_category_word_rewrite", block=row))
                    break
        if category(raw) == "office" and rspan[0] >= 18 * 60:
            for row in ordered:
                span_row = span(row)
                if category(row) not in HOME_CATS:
                    continue
                if not (rspan[0] - 120 <= span_row[0] <= rspan[0] + 180):
                    continue
                issues.append(make_issue(day, "drift", "night_work_folded_to_home",
                                         "18:00 后的公司块「%s」被折成「%s」（%s）"
                                         % (activity, row.get("activity"), row.get("place")),
                                         "fail", at=_hhmm(rspan[0]),
                                         root_cause="R4_night_office_folded_home", block=row))
                break
    return issues


# ---------------------------------------------------------------- 5.6) 宵夜块落点
def check_snack_blocks(day, rows):
    """宵夜块：地点写死「家」、把通勤块劈成两截，都是 ensure_block_window 的副作用。"""
    issues = []
    ordered = sorted((row for row in rows if span(row)), key=lambda row: span(row)[0])
    for index, row in enumerate(ordered):
        activity = str(row.get("activity") or "")
        if "宵夜" not in activity and "夜宵" not in activity:
            continue
        raw_place = str(row.get("place") or "")
        before = ordered[index - 1] if index else None
        after = ordered[index + 1] if index + 1 < len(ordered) else None
        context = [item for item in (before, after) if item is not None]
        away = [item for item in context if category(item) not in HOME_CATS]
        if raw_place == "家" and away:
            issues.append(make_issue(day, "drift", "snack_place_home",
                                     "宵夜块写死在家（%s），但前后块在 %s"
                                     % (activity, "、".join(str(item.get("place")) for item in away)),
                                     "fail", at=str(row.get("start") or ""),
                                     root_cause="R13_snack_place_home", block=row))
        if before is not None and after is not None:
            same = (str(before.get("activity") or "") == str(after.get("activity") or "")
                    and is_transit(before) and is_transit(after))
            if same:
                issues.append(make_issue(day, "drift", "commute_interrupted_by_snack",
                                         "通勤块「%s」被宵夜块劈成两截（%s / %s）"
                                         % (before.get("activity"), before.get("start"),
                                            after.get("start")),
                                         "fail", at=str(row.get("start") or ""),
                                         root_cause="R13_snack_place_home", block=row))
    return issues


# ---------------------------------------------------------------- 6) 跨天
def check_cross_day(day, rows, ctx, prev_record, raw_rows):
    issues = []
    if not prev_record:
        return issues
    prev_rows = prev_record.get("rows") or []
    prev_ctx = prev_record.get("ctx") or {}
    ordered = sorted((row for row in rows if span(row)), key=lambda row: span(row)[0])
    prev_ordered = sorted((row for row in prev_rows if span(row)), key=lambda row: span(row)[0])
    if not ordered or not prev_ordered:
        return issues
    first = ordered[0]
    if span(first)[0] != 0:
        issues.append(make_issue(day, "gap", "cross_day_head", "跨天后 00:00 没有块", "fail"))
    prev_last = prev_ordered[-1]
    last_end = span(prev_last)[1]
    if last_end < 24 * 60:
        prev_penalty = 24 * 60 - last_end
        if last_end >= 23 * 60:
            issues.append(make_issue(day, "drift", "cross_day_tail", 
                                     "昨天(%s)在 %s 就断了（差 %d 分钟到 24:00），"
                                     "尾块丢失：%s"
                                     % (prev_record.get("day"), _hhmm(last_end), prev_penalty,
                                        prev_last.get("activity")),
                                     "warn", root_cause="R2_transit_insert_tail_drop"))
        else:
            issues.append(make_issue(day, "gap", "cross_day_tail",
                                     "昨天(%s)只排到 %s，尾部大段悬空"
                                     % (prev_record.get("day"), _hhmm(last_end)),
                                     "fail", root_cause="R2_transit_insert_tail_drop"))
    # 昨天睡眠被尾块丢掉：今天按「昨晚没睡」生成，是跨天连锁后果
    prev_sleep = 0
    for row in prev_rows:
        if str(row.get("category") or "") == "sleep" and span(row):
            begin, finish = span(row)
            prev_sleep += max(0, finish - begin)
    if prev_sleep < 300:
        issues.append(make_issue(day, "drift", "cross_day_sleep_short",
                                 "昨天(%s)睡眠合计只有 %d 分钟（尾块丢失的连锁）"
                                 % (prev_record.get("day"), prev_sleep), "warn",
                                 root_cause="R2_transit_insert_tail_drop"))
    # 跨午夜瞬移：昨天最后所在地 vs 今天第一块所在地
    prev_cat = category(prev_last)
    first_cat = category(first)
    if prev_cat not in ROAD_CATS and first_cat not in ROAD_CATS and prev_cat != first_cat:
        prev_event = (prev_ctx.get("event") or {}).get("kind")
        today_event = (ctx.get("event") or {}).get("kind")
        if prev_cat == "hotel" or prev_event == "business_trip":
            issues.append(make_issue(day, "drift", "cross_day_teleport",
                                     "昨天(%s)睡在 %s，今天 00:00 却在 %s（出差/外地场景没延续）"
                                     % (prev_record.get("day"), prev_last.get("place"),
                                        first.get("place") or first_cat),
                                     "fail", root_cause="R8_multiday_trip_not_continued"))
        elif not first_cat:
            pass
        else:
            issues.append(make_issue(day, "drift", "cross_day_teleport",
                                     "跨天瞬移：昨天最后 %s(%s) → 今天第一块 %s(%s)"
                                     % (prev_last.get("activity"), prev_last.get("place"),
                                        first.get("activity"), first.get("place")),
                                     "warn"))
    # 昨天专属场景残留：昨天出现、今天上下文没安排，却出现在今天日程里
    today_places = " ".join(str(raw.get("place") or "") + str(raw.get("activity") or "")
                            for raw in (raw_rows or []))
    today_ctx = " ".join(str((ctx.get(key) or {}).get("name") or "") if isinstance(ctx.get(key), dict) else ""
                         for key in ("scenic",))
    for row in prev_rows:
        if category(row) not in ("mall", "scenic", "hotel", "office", "hospital", "school"):
            continue
        token = area_key(row.get("place"))
        if len(token) < 2 or token in ("商场", "公司", "学校"):
            continue
        if token in today_places or token in today_ctx:
            continue
        for cur in rows:
            if token and token in str(cur.get("activity") or "") + str(cur.get("place") or ""):
                issues.append(make_issue(day, "drift", "cross_day_residue",
                                         "昨天场景「%s」残留到今天：%s"
                                         % (token, cur.get("activity")),
                                         "fail", root_cause="R8_multiday_trip_not_continued",
                                         block=cur))
                break
    # 昨天整天和今天整天块一样：复制漂移
    if prev_record.get("timeline_key") and prev_record.get("timeline_key") == timeline_key(rows):
        issues.append(make_issue(day, "drift", "cross_day_duplicate",
                                 "今天与昨天的块清单完全一样（复制/未重新生成）", "warn"))
    return issues


def timeline_key(rows):
    import hashlib
    import json
    body = [[str(row.get(key) or "") for key in ("start", "end", "activity", "place", "zone")]
            for row in rows or [] if isinstance(row, dict)]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- 7) 三餐
MEAL_SLOTS = ((5, 10, "breakfast"), (10, 15, "lunch"), (16, 21, "dinner"), (21, 29, "late"))


def slot_of(minute):
    if minute is None:
        return ""
    value = int(minute)
    if value < 5 * 60:
        value += 24 * 60
    for begin, finish, name in MEAL_SLOTS:
        if begin * 60 <= value < finish * 60:
            return name
    return ""


def meal_entries(rows):
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cat = str(row.get("category") or "")
        if cat not in ("meal", "social", "date"):
            continue
        activity = str(row.get("activity") or "")
        try:
            looks = amo_food_cost._looks_like_meal(activity, cat)
        except Exception:  # cleanup: 仿真容错
            looks = bool(activity)
        if not looks:
            continue
        slot = slot_of(hm(row.get("start")))
        if not slot:
            continue
        out.append({"slot": slot, "start": str(row.get("start") or ""),
                    "end": str(row.get("end") or ""),
                    "activity": activity, "place": str(row.get("place") or ""),
                    "category": cat, "source": "schedule"})
    return out


def ledger_meals(ledger_rows, day=None):
    out = []
    for row in ledger_rows or []:
        if not isinstance(row, dict):
            continue
        try:
            if int(row.get("delta_cents") or row.get("delta") or 0) >= 0:
                continue  # 入账不是吃饭
            if not amo_food_audit._meal_row(row):
                continue
            ts = float(row.get("ts") or 0)
            if ts <= 0:
                continue
            moment = dt.datetime.fromtimestamp(ts)
        except Exception:  # cleanup: 仿真容错
            continue
        if day and moment.strftime("%Y-%m-%d") != str(day):
            continue
        slot = slot_of(moment.hour * 60 + moment.minute)
        if not slot:
            continue
        out.append({"slot": slot, "start": moment.strftime("%H:%M"),
                    "activity": str(row.get("item_name") or row.get("reason") or ""),
                    "place": "", "category": "ledger", "source": "ledger"})
    return out


def _drop_mirrored_ledger(schedule_entries, ledger_entries):
    """同一次宵夜/外卖在日程块和账本各出现一次，只算一顿（账本侧去重）。"""
    kept = []
    for entry in ledger_entries:
        text = str(entry.get("activity") or "")
        mirrored = False
        for sched in schedule_entries:
            if sched.get("slot") != entry.get("slot"):
                continue
            if sched.get("activity") and (sched.get("activity") in text or text in sched.get("activity")):
                mirrored = True
                break
            if any(word in sched.get("activity", "") for word in re.findall(r"[一-龥]{2,}", text)):
                mirrored = True
                break
        if not mirrored:
            kept.append(entry)
    return kept


def _sessionize(entries):
    """同一餐别里时间连续的块算同一顿（2 小时午饭被切成两个整点块不算两顿）。"""
    slots = {}
    for entry in entries:
        slots.setdefault(entry["slot"], []).append(entry)
    sessions = {}
    for slot, items in slots.items():
        ordered = sorted(items, key=lambda item: hm(item.get("start")) or 0)
        groups = []
        for item in ordered:
            begin = hm(item.get("start"))
            joined = False
            if groups:
                last = groups[-1][-1]
                last_begin = hm(last.get("start"))
                last_end = hm(last.get("end")) or last_begin
                if begin is not None and last_end is not None and begin - last_end <= 15:
                    groups[-1].append(item)
                    joined = True
            if not joined:
                groups.append([item])
        sessions[slot] = groups
    return sessions


def check_meals(day, rows, ledger_rows, snack_info=None):
    issues = []
    schedule_entries = meal_entries(rows)
    entries = schedule_entries + _drop_mirrored_ledger(schedule_entries, ledger_meals(ledger_rows, day))
    sessions = _sessionize(entries)
    total = sum(len(groups) for groups in sessions.values())
    if total > 4:
        issues.append(make_issue(day, "meal_over", "too_many_meals",
                                 "当天共 %d 顿（%s），超过三餐+宵夜 4 顿上限"
                                 % (total, "/".join("%s×%d" % (slot, len(groups))
                                                    for slot, groups in sorted(sessions.items()))),
                                 "fail"))
    for slot, groups in sessions.items():
        if len(groups) <= 1:
            continue
        flat = [item for group in groups for item in group]
        friend = any(re.search(r"(朋友|闺蜜|聚餐)", str(item.get("activity") or "")) for item in flat)
        subtype = "friend_dinner_double" if friend else "slot_double"
        detail = "「%s」这一餐出现 %d 顿：%s" % (
            slot, len(groups), "；".join(" / ".join("%s %s" % (item.get("start"), item.get("activity"))
                                                    for item in group) for group in groups))
        issues.append(make_issue(day, "meal_duplicate", subtype, detail, "fail",
                                 root_cause="" if friend else ""))
    if snack_info and snack_info.get("eat"):
        # 宵夜次数本身：同一晚重复扣款
        if int(snack_info.get("charged_count") or 0) > 1:
            issues.append(make_issue(day, "meal_duplicate", "snack_double_charge",
                                     "同一晚宵夜扣款 %d 次：%s"
                                     % (int(snack_info.get("charged_count") or 0),
                                        snack_info.get("idems")),
                                     "fail", root_cause="R11_snack_block_no_guard"))
        try:
            audit = amo_food_audit.audit_meals(ledger_rows)
        except Exception as exc:
            audit = {"error": repr(exc)}
        if isinstance(audit, dict) and not audit.get("error"):
            if int(audit.get("too_many") or 0) > 0:
                issues.append(make_issue(day, "meal_over", "ledger_too_many",
                                         "钱包审计：%d 天超过四顿" % audit.get("too_many"), "fail"))
            if int(audit.get("duplicate_slot") or 0) > 0:
                issues.append(make_issue(day, "meal_duplicate", "ledger_duplicate_slot",
                                         "钱包审计：同餐别重复扣款 %d 笔"
                                         % audit.get("duplicate_slot"), "fail"))
    return issues, {"total": total,
                    "buckets": {key: len(groups) for key, groups in sessions.items()},
                    "entries": entries}
