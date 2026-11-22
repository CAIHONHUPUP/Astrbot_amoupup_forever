# -*- coding: utf-8 -*-
"""50 组日常对话的「世界」生成器（确定性、可复现）。

每个 group = 一份日程（地点/zone/动作/同伴）+ 10~14 轮对话脚本：
- 时间单调前进，跨过日程块切换（用来测「状态切换后仍用旧场景」）；
- 每组至少 5 个探针（在哪/干嘛/跟谁/吃了吗/什么时候回），类型齐全；
- 组 4/9/14...（gi % 5 == 4）是「干净对照组」：不注入偏差，用来量守卫自己有没有改坏。

注意：这里只有日程与用户台词，没有模型输出——模型输出由 mock_provider 生成。
"""
from __future__ import annotations

import random

#: 各 group 的日程模板（start/end/zone/place/activity/verb/category/people/together）
PLANS = {
    "rest_home": {
        "health": {"status": "健康", "sick": False},
        "blocks": [
            ("08:00", "10:00", "home", "家", "窝在沙发上刷手机", "刷手机", "rest", [], False),
            ("10:00", "12:00", "home", "家", "收拾屋子", "收拾屋子", "life", [], False),
            ("12:00", "13:00", "home", "家", "做午饭", "做饭", "meal", [], False),
            ("13:00", "14:30", "home", "家", "午睡", "午睡", "sleep", [], False),
            ("14:30", "18:00", "home", "家", "追剧", "追剧", "rest", [], False),
            ("18:00", "19:00", "home", "家", "做晚饭", "做饭", "meal", [], False),
            ("19:00", "22:30", "home", "家", "敷面膜看书", "看书", "rest", [], False),
            ("22:30", "24:00", "home", "家", "准备睡觉", "洗漱", "sleep", [], False),
        ],
    },
    "workday": {
        "health": {"status": "健康", "sick": False},
        "blocks": [
            ("07:20", "08:00", "road", "地铁", "通勤去公司", "挤地铁", "commute", [], False),
            ("08:00", "12:00", "office", "公司", "在工位改方案", "改方案", "work", [], False),
            ("12:00", "12:40", "office", "公司", "在公司食堂吃午饭", "吃饭", "meal", [], False),
            ("12:40", "18:00", "office", "公司", "开会写周报", "写周报", "work", ["同事"], False),
            ("18:00", "18:40", "road", "地铁", "通勤回家", "挤地铁", "commute", [], False),
            ("18:40", "19:30", "home", "家", "做晚饭", "做饭", "meal", [], False),
            ("19:30", "22:30", "home", "家", "窝在沙发上追剧", "追剧", "rest", [], False),
            ("22:30", "24:00", "home", "家", "准备睡觉", "洗漱", "sleep", [], False),
        ],
    },
    "weekend_mall": {
        "health": {"status": "健康", "sick": False},
        "blocks": [
            ("09:00", "09:40", "road", "街上", "出门去商场", "走路", "commute", [], False),
            ("09:40", "12:00", "mall", "商场", "在商场试衣服", "试衣服", "play", [], False),
            ("12:00", "13:00", "restaurant", "餐厅", "在餐厅吃午饭", "吃饭", "meal", [], False),
            ("13:00", "15:30", "mall", "商场", "逛商场", "逛商场", "play", [], False),
            ("15:30", "16:10", "road", "地铁", "回家路上", "挤地铁", "commute", [], False),
            ("16:10", "18:00", "home", "家", "在家歇脚", "歇着", "rest", [], False),
            ("18:00", "19:00", "home", "家", "做晚饭", "做饭", "meal", [], False),
            ("19:00", "22:30", "home", "家", "窝在沙发上看剧", "看剧", "rest", [], False),
        ],
    },
    "sick_home": {
        "health": {"status": "发烧", "sick": True},
        "blocks": [
            ("07:00", "11:30", "home", "家", "裹着被子躺着", "躺着", "health", [], False),
            ("11:30", "12:30", "home", "家", "喝粥", "喝粥", "meal", [], False),
            ("12:30", "17:30", "home", "家", "睡觉", "睡觉", "sleep", [], False),
            ("17:30", "18:30", "home", "家", "吃药休息", "吃药", "health", [], False),
            ("18:30", "19:30", "home", "家", "喝粥", "喝粥", "meal", [], False),
            ("19:30", "23:00", "home", "家", "躺着刷手机", "躺着刷手机", "rest", [], False),
        ],
    },
    "hospital_day": {
        "health": {"status": "轻微不适", "sick": True},
        "blocks": [
            ("08:00", "08:30", "road", "路上", "去医院路上", "赶路", "commute", [], False),
            ("08:30", "11:00", "hospital", "医院", "在医院排队看诊", "排队看诊", "health", [], False),
            ("11:00", "11:40", "road", "街上", "回家路上", "走路", "commute", [], False),
            ("11:40", "12:30", "home", "家", "吃药休息", "吃药", "health", [], False),
            ("12:30", "18:00", "home", "家", "睡觉", "睡觉", "sleep", [], False),
            ("18:00", "19:00", "home", "家", "喝粥", "喝粥", "meal", [], False),
            ("19:00", "22:30", "home", "家", "躺着刷手机", "躺着刷手机", "rest", [], False),
        ],
    },
    "cafe_work": {
        "health": {"status": "健康", "sick": False},
        "blocks": [
            ("09:00", "09:30", "road", "地铁", "出门去咖啡店", "挤地铁", "commute", [], False),
            ("09:30", "12:00", "restaurant", "咖啡店", "在咖啡店改稿", "改稿", "work", [], False),
            ("12:00", "13:00", "restaurant", "咖啡店", "吃午饭", "吃饭", "meal", [], False),
            ("13:00", "17:00", "restaurant", "咖啡店", "在咖啡店写东西", "写东西", "work", [], False),
            ("17:00", "17:40", "road", "地铁", "回家路上", "挤地铁", "commute", [], False),
            ("17:40", "19:00", "home", "家", "做晚饭", "做饭", "meal", [], False),
            ("19:00", "22:30", "home", "家", "窝沙发刷手机", "刷手机", "rest", [], False),
        ],
    },
    "scenic_trip": {
        "health": {"status": "健康", "sick": False},
        "trip": {"phase": "traveling", "hotel": True},
        "blocks": [
            ("07:30", "09:00", "road", "路上", "去景区的路上", "赶路", "commute", [], False),
            ("09:00", "12:00", "scenic", "景区", "在景区爬山", "爬山", "play", [], False),
            ("12:00", "13:00", "restaurant", "景区餐厅", "吃午饭", "吃饭", "meal", [], False),
            ("13:00", "16:30", "scenic", "古镇", "逛古镇", "逛古镇", "play", [], False),
            ("16:30", "17:30", "road", "路上", "回酒店", "赶路", "commute", [], False),
            ("17:30", "22:00", "hotel", "酒店", "在酒店休息", "躺着", "rest", [], False),
        ],
    },
    "date_day": {
        "health": {"status": "健康", "sick": False},
        "blocks": [
            ("10:00", "12:00", "mall", "商场", "和他一起逛商场", "逛商场", "date", ["老公"], True),
            ("12:00", "13:30", "restaurant", "餐厅", "一起吃饭", "吃饭", "meal", ["老公"], True),
            ("13:30", "16:00", "mall", "商场", "一起看电影", "看电影", "play", ["老公"], True),
            ("16:00", "17:00", "scenic", "公园", "在公园散步", "散步", "date", ["老公"], True),
            ("17:00", "18:00", "road", "路上", "他送我回家", "坐车", "commute", ["老公"], True),
            ("18:00", "19:00", "home", "家", "做晚饭", "做饭", "meal", [], False),
            ("19:00", "22:30", "home", "家", "窝沙发上看剧", "看剧", "rest", [], False),
        ],
    },
    "gym_day": {
        "health": {"status": "健康", "sick": False},
        "blocks": [
            ("17:30", "18:00", "road", "路上", "去健身房路上", "赶路", "commute", [], False),
            ("18:00", "19:30", "gym", "健身房", "在健身房练器械", "练器械", "gym", [], False),
            ("19:30", "20:10", "road", "街上", "回家路上", "走路", "commute", [], False),
            ("20:10", "21:00", "home", "家", "洗澡", "洗澡", "life", [], False),
            ("21:00", "22:30", "home", "家", "窝在沙发上休息", "瘫着", "rest", [], False),
        ],
    },
    "office_early": {
        "health": {"status": "健康", "sick": False},
        "blocks": [
            ("06:40", "07:20", "home", "家", "赖床", "赖床", "sleep", [], False),
            ("07:20", "08:00", "road", "地铁", "通勤去公司", "挤地铁", "commute", [], False),
            ("08:00", "12:00", "office", "公司", "在工位上班", "写代码", "work", [], False),
            ("12:00", "13:00", "office", "公司", "吃午饭", "吃饭", "meal", ["同事"], False),
            ("13:00", "18:00", "office", "公司", "在工位上班", "改方案", "work", [], False),
            ("18:00", "19:00", "home", "家", "做晚饭", "做饭", "meal", [], False),
            ("19:00", "21:00", "home", "家", "追剧", "追剧", "rest", [], False),
            ("21:00", "23:00", "home", "家", "洗漱护肤", "护肤", "life", [], False),
        ],
    },
}

PROBES = {
    "where": ["你在哪呢？", "你现在在哪呀？", "你在家吗？", "到公司了吗？"],
    "doing": ["你在干嘛呢？", "忙什么呢？", "你是不是在偷懒？"],
    "who": ["你旁边有人吗？", "你跟谁在一起呢？", "一个人吗？"],
    "eaten": ["吃了吗？", "吃饭了没？", "你中午吃的什么？"],
    "when_return": ["你什么时候回来？", "几点到家呀？", "今晚回不回来？"],
}

TOPICS = {
    "work_complain": ["今天又被领导说了，烦死了。", "感觉这份工作好没意思。", "我同事今天摸鱼一整天。",
                      "你说我要不要换个工作？"],
    "food": ["晚上吃啥呀？", "好想吃火锅。", "今天中午吃什么好呢？", "你会做饭吗？"],
    "miss": ["有点想你了。", "你什么时候能多陪陪我。", "今天特别想你。", "抱抱。"],
    "drama": ["我在看那个新剧。", "推荐个剧呗。", "昨晚那集太虐了。", "这个男主好帅。"],
    "clothes": ["想买件外套，你帮我看看。", "今天穿什么好？", "新买的裙子到了。", "这件衣服好看吗？"],
    "sleep": ["我有点困了。", "昨晚没睡好。", "你几点睡？", "好想睡个懒觉。"],
    "family": ["我妈又催我了。", "我爸今天打电话来。", "家里人说想我了。", "我妈问我们什么时候回去。"],
    "travel": ["周末去哪玩好？", "想去海边。", "你上次说的那个古镇叫什么？", "等放假我们出去玩吧。"],
    "gym": ["今天练腿好累。", "你说我能坚持下来吗？", "最近是不是胖了？", "想办张健身卡。"],
    "pet": ["刚看到一只猫，好可爱。", "想养只狗。", "那只橘猫胖乎乎的。", "刷到个仓鼠视频，笑死。"],
    "game": ["打了一晚上游戏。", "你玩不玩这个？", "这关我过了三遍。", "队友太菜了。"],
    "movie": ["最近有什么好电影？", "想去看电影。", "那个片子评分好高。", "你陪我去看呗。"],
    "friend": ["朋友约我晚上吃饭。", "我闺蜜失恋了。", "我朋友说想见见你。", "她们都在问我你呢。"],
    "weather": ["今天好冷啊。", "外面下雨了。", "这天气说变就变。", "你那边冷不冷？"],
    "commute": ["今天地铁好挤。", "路上堵死了。", "我讨厌挤公交。", "每天通勤太累了。"],
    "gossip": ["我们公司新来了个同事。", "听说楼下开了家新店。", "我们部门要换领导了。", "你猜我今天碰到谁了？"],
    "memory": ["你还记得我们第一次见面吗？", "以前我们常去那家店。", "那时候你都不爱说话。", "时间过得好快。"],
    "coquetry": ["抱抱。", "亲一下。", "你今天有没有想我？", "哼，不理你了。"],
    "daily": ["在吗？", "嗯嗯。", "好吧。", "晚安。"],
}

#: 注入偏差的配比：探针类型 -> 主要偏差类型
PROBE_DRIFT = {"where": "place", "doing": "action", "who": "companion",
               "eaten": "meal", "when_return": "irrelevant"}

SEED = 20261121


def _block_tuple(row):
    start, end, zone, place, activity, verb, category, people, together = row
    return {"start": start, "end": end, "zone": zone, "place": place,
            "activity": activity, "verb": verb, "category": category,
            "people": list(people or []), "together": bool(together)}


def _minutes(hhmm):
    hour, minute = str(hhmm).split(":")[:2]
    return int(hour) * 60 + int(minute)


def _hhmm(total):
    total = max(0, min(23 * 60 + 59, int(total)))
    return "%02d:%02d" % (total // 60, total % 60)


def _plan_names():
    return list(PLANS)


def build_groups(count=50, seed=SEED):
    names = _plan_names()
    groups = []
    for index in range(count):
        rng = random.Random(seed + index * 7919)
        plan_name = names[index % len(names)]
        plan = PLANS[plan_name]
        blocks = [_block_tuple(row) for row in plan["blocks"]]
        control = (index % 5 == 4)  # 每 5 组 1 组干净对照（共 10 组）
        turns_count = 10 + rng.randrange(5)
        first = _minutes(blocks[0]["start"]) + rng.randrange(0, 21)
        last = min(_minutes(blocks[-1]["end"]) - 10, 23 * 60 + 20)
        span = max(60, last - first)
        times = []
        for step in range(turns_count):
            moment = first + span * step / max(1, turns_count - 1) + rng.randrange(-8, 9)
            times.append(int(moment))
        times = sorted(times)
        for step in range(1, len(times)):  # 保证单调且至少隔 5 分钟
            times[step] = max(times[step], times[step - 1] + 5)

        probe_types = list(PROBES)
        rng.shuffle(probe_types)
        slots = list(range(1, turns_count - 1))
        rng.shuffle(slots)
        assign = {}
        for pos, ptype in enumerate(probe_types):
            assign[slots[pos]] = ptype
        extra = slots[len(probe_types)]  # 额外一个探针（可能重复类型）
        assign[extra] = rng.choice(list(PROBES))

        topics = rng.sample(list(TOPICS), 4)
        turns = []
        for step in range(turns_count):
            hhmm = _hhmm(times[step])
            probe = assign.get(step)
            topic = topics[step % len(topics)]
            user_text = rng.choice(PROBES[probe]) if probe else rng.choice(TOPICS[topic])
            drift = None
            if not control:
                if probe:
                    kind = PROBE_DRIFT[probe]
                    if rng.random() < 0.6:
                        drift = {"kind": kind, "tier": "edge" if rng.random() < 0.3 else "covered"}
                    elif kind != "irrelevant" and rng.random() < 0.15:
                        drift = {"kind": "irrelevant", "tier": "covered"}
                elif rng.random() < 0.12:
                    drift = {"kind": rng.choice(["place", "action", "meal", "health"]),
                             "tier": "edge" if rng.random() < 0.3 else "covered"}
            turns.append({"index": step + 1, "time": hhmm, "user": user_text,
                          "probe": probe, "topic": topic, "drift": drift})
        groups.append({
            "gid": "G%02d" % (index + 1),
            "plan": plan_name,
            "control": control,
            "health": dict(plan.get("health") or {"status": "健康", "sick": False}),
            "trip": dict(plan.get("trip") or {}),
            "blocks": blocks,
            "turns": turns,
        })
    return groups


def block_at(blocks, hhmm):
    """该时刻覆盖的日程块（与生产 current_activity_info 的 covering 语义一致）。"""
    current = _minutes(hhmm)
    covering = [item for item in blocks
                if _minutes(item["start"]) <= current < _minutes(item["end"])]
    if covering:
        return max(covering, key=lambda item: _minutes(item["start"]))
    past = [item for item in blocks if _minutes(item["start"]) <= current]
    return past[-1] if past else (blocks[0] if blocks else {})


def meal_ok(blocks, hhmm):
    """±90 分钟内有没有 meal 块（判「刚吃完」是否合理）。"""
    current = _minutes(hhmm)
    for item in blocks:
        if str(item.get("category") or "") != "meal":
            continue
        if _minutes(item["start"]) - 90 <= current <= _minutes(item["end"]) + 90:
            return True
    return False
