# -*- coding: utf-8 -*-
"""diary_checks：大日记 vs 当天事实的独立核对器（2026-11-22，只读测试）。

刻意不 import 生产守卫的判定逻辑：复用「地点词表思路」，但事实来源、命中、失败
分类都在本文件自建。输入：
- blocks：当天真实日程块（生产日程引擎生成、commit 后的最终 rows）；
- health_day / health_gen：当天 08:00 的身体状态、第二天早上生成日记时的身体状态；
- weather_day：当天 12:00 的生产模拟天气（state._simulate_weather）；
- date_windows：当天「面对面」窗口（生产 amo_date_facts；默认空 = 同城不同住）；
- ledger：当天账本（本仿真为空，保留接口）；
- prompt：生成这条日记时模型实际拿到的提示词（用于判断「事实有没有进素材包」）。

失败分类（task 要求）：
place / action / companion / meal / health / weather / fabricated / template / coverage
另加 order（时间顺序）、transition（过渡生硬）两个观察项。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- 地点词表
PLACE_GROUPS = (
    ("home", ("家里", "家中", "在家", "到家", "回家", "出租屋", "客厅", "卧室", "沙发",
              "厨房", "阳台", "被窝", "床上", "躺床", "卫生间", "浴室", "屋里", "家门")),
    ("office", ("写字楼", "办公室", "会议室", "工位", "公司", "单位", "办公桌")),
    ("mall", ("商场", "专柜", "逛街", "超市", "步行街", "试衣间", "购物袋", "专柜")),
    ("restaurant", ("咖啡店", "咖啡馆", "奶茶店", "小吃街", "餐厅", "饭店", "饭馆",
                    "食堂", "小馆", "面馆", "粉店", "火锅店", "牛肉粉", "咖啡")),
    ("road", ("车上", "车里", "打车", "出租车", "网约车", "地铁", "公交", "车厢",
              "路上", "开车", "电动车", "小电驴", "站台")),
    ("street", ("街上", "街边", "街头", "路边", "外面", "户外")),
    ("scenic", ("游乐园", "动物园", "植物园", "风景区", "爬山", "海边", "沙滩",
                "景区", "景点", "公园", "山上", "古镇")),
    ("hotel", ("酒店", "宾馆", "民宿", "旅馆", "客栈")),
    ("hospital", ("医院", "诊所", "挂号", "候诊", "输液", "药店")),
    ("gym", ("健身房", "跑步机")),
    ("school", ("图书馆", "教室", "学校", "宿舍", "操场")),
)
_WORD_GROUP = {}
for _group, _words in PLACE_GROUPS:
    for _word in _words:
        _WORD_GROUP.setdefault(_word, _group)
_ALL_WORDS = sorted(_WORD_GROUP, key=len, reverse=True)

#: 日程块 zone → 地点组（块里 zone 是主事实，place 文本是补充）
ZONE_GROUP = {
    "home": "home", "office": "office", "mall": "mall", "restaurant": "restaurant",
    "road": "road", "street": "street", "station": "road", "scenic": "scenic",
    "playground": "scenic", "hotel": "hotel", "hospital": "hospital", "gym": "gym",
    "school": "school",
}

FUTURE_MARKERS = ("明天", "后天", "下次", "改天", "以后", "将来", "要是", "如果",
                  "回头", "等有空", "等放假", "希望", "打算", "准备去", "有机会")
PAST_MARKERS = ("昨天", "前天", "以前", "之前", "上次", "那次", "当年", "小时候",
                "前阵子", "前两天", "去年")
CLAUSE_SPLIT = re.compile(r"[，,。！？!?；;、\n]+")
SENT_SPLIT = re.compile(r"(?<=[。！？!?\n])")


def _sentences(text):
    return [part.strip() for part in SENT_SPLIT.split(str(text or "")) if part.strip()]


def _clauses(text):
    return [part.strip() for part in CLAUSE_SPLIT.split(str(text or "")) if part.strip()]


def match_places(text):
    """返回文本命中的地点组集合（按词表）。"""
    value = str(text or "")
    groups = set()
    for word in _ALL_WORDS:
        if word in value:
            groups.add(_WORD_GROUP[word])
    return groups


def _future(clause):
    return any(marker in clause for marker in FUTURE_MARKERS)


def _past(clause):
    return any(marker in clause for marker in PAST_MARKERS)


def clean_place(place):
    text = str(place or "").strip()
    for prefix in ("东莞·", "东莞-"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text


def block_groups(block):
    """一个日程块的地点组：zone 优先，place 文本补充（商场里的餐厅算两处）。"""
    groups = set()
    zone = str(block.get("zone") or "")
    if group := ZONE_GROUP.get(zone):
        groups.add(group)
    place = clean_place(block.get("place"))
    if place == "家":
        groups.add("home")
    elif place == "路上":
        groups.add("road")
    else:
        groups |= match_places(place)
    return groups


#: 强动作词 → 需要的证据（在当天块的活动/地点/账本里出现即算有证据）
ACTION_EVIDENCE = {
    "work": ("上班", "加班", "开会", "工作", "改方案", "赶方案", "出差", "办公", "写周报",
             "公司", "写字楼", "工位", "单位", "办公室"),
    "hospital": ("医院", "挂号", "看诊", "看病", "输液", "打针", "诊所", "体温计"),
    "shopping": ("逛街", "逛商场", "专柜", "试衣", "购物", "超市", "买"),
    "scenic": ("景区", "公园", "爬山", "海边", "古镇", "景点", "游乐园", "旅游"),
    "hotel": ("酒店", "民宿", "房卡", "宾馆"),
    "cook": ("做饭", "炒", "煮", "下厨", "蒸", "煎", "洗碗"),
    "workout": ("健身", "跑步", "瑜伽", "游泳", "锻炼", "操场"),
    "movie": ("电影", "影院", "票房"),
    "social": ("同事", "朋友", "闺蜜", "聚餐", "聚会", "同学"),
    "date": ("约会", "面对面", "一起吃饭", "一起逛", "一起走"),
    "takeout": ("外卖", "点单", "配送"),
}
ACTION_LABEL = {
    "work": "上班/工作", "hospital": "看病/医院", "shopping": "逛街/买东西",
    "scenic": "景区/出游", "hotel": "住酒店", "cook": "做饭", "workout": "健身/运动",
    "movie": "看电影", "social": "同事朋友社交", "date": "面对面约会", "takeout": "点外卖",
}
#: 按块的活动/地点文本判强动作
ACTIVITY_CLAIM = {
    "work": re.compile(r"(?:上班|加班|开会|改方案|赶方案|出差|办公|工位|周报)"),
    "hospital": re.compile(r"(?:医院|挂号|看诊|看病|输液|打针|诊所|候诊)"),
    "shopping": re.compile(r"(?:逛街|逛商场|试衣|专柜|购物|超市)"),
    "scenic": re.compile(r"(?:景区|公园|爬山|海边|古镇|景点|游乐园|旅游)"),
    "hotel": re.compile(r"(?:酒店|民宿|房卡)"),
    "cook": re.compile(r"(?:做饭|炒|煮|下厨|蒸|煎)"),
    "workout": re.compile(r"(?:健身|跑步|瑜伽|游泳|锻炼)"),
    "movie": re.compile(r"(?:看电影|影院|电影票)"),
    "social": re.compile(r"(?:同事|朋友|闺蜜|聚餐|聚会|同学)"),
    "date": re.compile(r"(?:约会|面对面|一起吃饭|一起逛)"),
    "takeout": re.compile(r"(?:外卖|点单|配送)"),
}
#: 大日记里的强动作断言（只查这些高显著动作；在家发呆/刷手机等日常不查）
ACTION_CLAIM = {
    "work": re.compile(r"(?:在?公司|工位|办公室|开会|加班|上班|改方案|赶方案|出差|周报)"),
    "hospital": re.compile(r"(?:医院|挂号|看诊|看病|输液|打针|诊所|候诊|拿药)"),
    "shopping": re.compile(r"(?:逛街|逛商场|专柜|试衣间|购物|买了|超市)"),
    "scenic": re.compile(r"(?:景区|公园|爬山|海边|古镇|景点|游乐园|去旅游)"),
    "hotel": re.compile(r"(?:酒店|民宿|房卡|宾馆)"),
    "cook": re.compile(r"(?:做饭|炒菜|炒了|下厨|蒸了|煎了)"),
    "workout": re.compile(r"(?:健身|跑步|瑜伽|游泳|锻炼)"),
    "movie": re.compile(r"(?:看电影|影院|电影票)"),
    "social": re.compile(r"(?:同事|朋友|闺蜜|聚餐|聚会)"),
    "takeout": re.compile(r"(?:外卖)"),
}

#: 「加班/晚走」类断言：2026-11-22 日程修复后工作日不加班，日记再写加班就是动作不符。
#: 「忙到十点才出门」「熬到十点才睡」是生活作息、落点不是走/下班，不算加班断言
#: （2026-11-22 复跑误报：「收拾屋子、洗衣服，忙到十点才出门」被判成加班）。
OVERTIME_RE = re.compile(
    r"(?:加班|还在公司|晚下班|加到.{0,4}点|"
    r"熬到.{0,4}点(?:才走|才下班|才离开|才收工)|"
    r"忙到.{0,4}点(?:才走|才下班|才离开))")

SICK_WORDS = ("发烧", "低烧", "感冒", "生病", "咳嗽", "拉肚子", "头晕", "嗓子疼",
              "嗓子发炎", "胃疼", "肠胃炎", "浑身没劲", "不舒服", "病倒")
HEALTH_GOOD = ("精神很好", "生龙活虎", "一点事都没有", "完全好了", "身体没事", "没生病")

NO_MEAL_RE = re.compile(
    r"(?<!吃)(?:没|没有|还没|一直没|一天没)\s*(?:顾上)?\s*(?:吃(?!出|到|完|过|饱)|吃饭|吃东西|弄饭)")
NO_MEAL_BAND = {
    "早餐": re.compile(r"(?:没|没有|还没)\s*(?:吃|顾上吃)?\s*(?:早饭|早餐)"),
    "午餐": re.compile(r"(?:没|没有|还没)\s*(?:吃|顾上吃)?\s*(?:午饭|午餐|中饭)"),
    "晚餐": re.compile(r"(?:没|没有|还没)\s*(?:吃|顾上吃)?\s*(?:晚饭|晚餐)"),
}
EAT_RE = re.compile(r"(?:吃|煮|炒|做了|点了|下了|喝了|面|饭|粥|粉|菜|外卖|咖啡|奶茶|"
                    r"早餐|午餐|晚餐|宵夜|顿|饺子|包子|面包|豆浆|油条|便当|零食|水果)")

WEATHER_CLAIM = {
    "rain": re.compile(r"(?:下雨|下着雨|小雨|阵雨|暴雨|雨伞|淋雨|雨停)"),
    "clear": re.compile(r"(?:大晴天|晴天|出太阳|太阳很好|阳光|晒被子|万里无云|晴)"),
    "cloud": re.compile(r"(?:多云)"),
    "overcast": re.compile(r"(?:阴沉|阴天|天阴着)"),
    "snow": re.compile(r"(?:下雪|飘雪|积雪)"),
    "wind": re.compile(r"(?:刮风|风很大|风大|风吹)"),
    "cold": re.compile(r"(?:降温|天冷|天凉|凉下来|凉了|变冷|冷了|好冷|冷飕飕|冻手)"),
    "hot": re.compile(r"(?:很热|好热|闷热|热得|热浪|晒得)"),
    "vague": re.compile(r"(?:天气还行|天气不错|天气挺好|天气一般)"),
}
WEATHER_TEXT_TAG = {"晴": "clear", "多云": "cloud", "阴": "overcast", "小雨": "rain",
                    "阵雨": "rain", "雷阵雨": "rain", "小雪": "snow"}
WEATHER_TAG_LABEL = {"clear": "晴", "cloud": "多云", "overcast": "阴", "rain": "雨",
                     "snow": "雪", "storm": "雷阵雨"}


# ---------------------------------------------------------------- 事实表
def build_day_facts(day, kind, blocks, health_day, health_gen, weather_day, date_windows,
                    ledger=None, now_gen=""):
    """把生产日程块/健康/天气/约会窗口压成「当天事实表」。"""
    rows = [dict(item) for item in (blocks or []) if isinstance(item, dict)]
    rows.sort(key=lambda item: str(item.get("start") or ""))
    places, groups, zones = [], set(), set()
    meals, out_blocks, late_blocks = [], [], []
    for item in rows:
        place = clean_place(item.get("place"))
        if place and place not in places:
            places.append(place)
        groups |= block_groups(item)
        zones.add(str(item.get("zone") or ""))
        start = str(item.get("start") or "")
        end = str(item.get("end") or "")
        category = str(item.get("category") or "")
        activity = str(item.get("activity") or "")
        if category == "meal":
            meals.append({"start": start, "end": end, "text": activity, "place": place,
                          "band": meal_band(start)})
        if group := block_groups(item):
            if not group <= {"home"}:
                out_blocks.append({"start": start, "end": end, "text": activity,
                                   "place": place, "groups": sorted(group)})
        if start >= "22:00" and category != "sleep":
            late_blocks.append({"start": start, "text": activity})
    wake = ""
    for item in rows:
        if str(item.get("category") or "") != "sleep":
            continue
        end = str(item.get("end") or "")
        if end and "08:00" <= end <= "12:00" and int(end[:2]) >= 9:
            wake = end
    together = []
    for window in date_windows or []:
        together.append({"start": str(window.get("start") or ""),
                         "end": str(window.get("end") or ""),
                         "activity": str(window.get("activity") or "")})
    health = dict(health_day or {})
    gen_health = dict(health_gen or {})
    weather = dict(weather_day or {})
    return {
        "day": day, "kind": kind,
        "blocks": [{"start": str(item.get("start") or ""), "end": str(item.get("end") or ""),
                    "activity": str(item.get("activity") or ""),
                    "place": clean_place(item.get("place")),
                    "zone": str(item.get("zone") or ""),
                    "category": str(item.get("category") or "")} for item in rows],
        "block_count": len(rows),
        "places": places,
        "groups": sorted(groups),
        "zones": sorted(zones),
        "out": bool(out_blocks),
        "out_blocks": out_blocks,
        "home_only": all(block_groups(item) <= {"home"} for item in rows) if rows else False,
        "meals": meals,
        "meal_count": len(meals),
        "late_blocks": late_blocks,
        "wake": wake,
        "together": together,
        "together_any": bool(together),
        "health": {"status": str(health.get("status") or "健康"),
                   "sick": bool(health.get("sick")),
                   "note": str(health.get("note") or "")},
        "health_gen": {"status": str(gen_health.get("status") or "健康"),
                       "sick": bool(gen_health.get("sick")),
                       "note": str(gen_health.get("note") or "")},
        "weather": {"text": str(weather.get("text") or ""),
                    "tag": str(weather.get("tag") or ""),
                    "temp": weather.get("temp", ""),
                    "city": str(weather.get("city") or "")},
        "ledger": list(ledger or []),
        "now_gen": now_gen,
    }


def meal_band(start):
    text = str(start or "")
    try:
        hour = int(text[:2])
    except (TypeError, ValueError):
        return ""
    if hour < 10:
        return "早餐"
    if hour < 15:
        return "午餐"
    if hour < 22:
        return "晚餐"
    return "宵夜"


def _has_any(text, words):
    return any(word in str(text or "") for word in words)


# ---------------------------------------------------------------- 日记断言抽取
_REFLECTIVE = ("想起", "想到", "梦到", "听说", "记得", "回忆", "刷到", "看到", "梦见面")


_PLACE_CUE = re.compile(
    r"(?:在|去|到|回|进|逛|坐|待|躺|窝|睡|走|吃|喝|上班|下班)"
    r"[^，。！？]{0,6}(?:家|公司|商场|餐厅|饭馆|饭店|食堂|咖啡|小馆|超市|医院|酒店|"
    r"公园|景区|地铁|公交|车上|路上|街上|卧室|客厅|沙发|床|被窝|厨房|阳台|"
    r"写字楼|工位|专柜|便利店|学校|会议室|办公室|餐厅)")


def _place_self(clause):
    """只有「我+位移动词+地点」才算地点断言；「外面风挺大」这类环境句不算。"""
    return bool(_PLACE_CUE.search(str(clause or "")))


def _meal_self(clause):
    """「没吃」必须是自述；「他今天没吃」是他人的事，不算她的三餐矛盾。"""
    text = str(clause or "")
    if re.search(r"(我|俺)", text):
        return True
    return not bool(re.match(r"^(?:他|老公|她|朋友|同事|妈|爸|哥|姐)", text))


def _claim_context(text):
    """逐句过：跳过过去/未来/转述/回忆句，只留「今天/无标记」的自述句。"""
    for sentence in _sentences(text):
        for clause in _clauses(sentence):
            if not clause:
                continue
            if _future(clause) or _past(clause) or any(marker in clause for marker in _REFLECTIVE):
                # 未来/过去句只在「整句」层面跳过断言，不整篇跳过
                yield clause, sentence, True
                continue
            yield clause, sentence, False


def extract_claims(text):
    """从日记正文抽可核对断言：地点/动作/同伴/吃饭/健康/天气/编造。"""
    claims = {"places": [], "actions": [], "companions": [], "meals": [],
              "health": [], "weather": [], "fabricated": [], "times": [],
              "overtime": [], "sentences": _sentences(text)}
    for clause, sentence, muted in _claim_context(text):
        if muted:
            continue
        groups = match_places(clause)
        if groups and _place_self(clause):
            claims["places"].append({"groups": sorted(groups), "clause": clause,
                                     "sentence": sentence})
        for key, pattern in ACTION_CLAIM.items():
            if pattern.search(clause):
                claims["actions"].append({"action": key, "clause": clause,
                                          "sentence": sentence})
        if _companion_claim(clause):
            claims["companions"].append({"clause": clause, "sentence": sentence})
        if OVERTIME_RE.search(clause):
            claims["overtime"].append({"clause": clause, "sentence": sentence})
        if NO_MEAL_RE.search(clause) and _meal_self(clause):
            claims["meals"].append({"kind": "any", "clause": clause, "sentence": sentence})
        for band, pattern in NO_MEAL_BAND.items():
            if pattern.search(clause):
                claims["meals"].append({"kind": band, "clause": clause, "sentence": sentence})
        for word in SICK_WORDS:
            if word in clause:
                claims["health"].append({"kind": "sick", "word": word, "clause": clause,
                                         "sentence": sentence})
                break
        for word in HEALTH_GOOD:
            if word in clause:
                claims["health"].append({"kind": "good", "word": word, "clause": clause,
                                         "sentence": sentence})
                break
        for tag, pattern in WEATHER_CLAIM.items():
            if pattern.search(clause):
                claims["weather"].append({"tag": tag, "clause": clause, "sentence": sentence})
        for kind, pattern in FABRICATED_RE.items():
            if pattern.search(clause):
                claims["fabricated"].append({"kind": kind, "clause": clause,
                                             "sentence": sentence})
        for minute in _time_mentions(clause, sentence):
            claims["times"].append({"minute": minute, "clause": clause,
                                    "sentence": sentence})
    return claims


_COMPANION_PATTERNS = (
    re.compile(r"(?:老公|他)(?:就)?在(?:我)?(?:身边|旁边|对面|身后)"),
    re.compile(r"(?:老公|他)?(?:抱着我|搂着我|搂住我|牵着我|牵着我的手|亲了我|摸我的头)"),
    re.compile(r"(?:靠在他|靠进他|扑进他|依偎在他)"),
    re.compile(r"(?:他|老公)(?:陪我|陪着我|来看我|来找我|进门|回家陪我)"),
    re.compile(r"面对面(?:坐|吃|聊)?"),
    re.compile(r"(?:我们|我俩)(?:一起|两个人)(?:吃|走|逛|坐|看|回家)"),
    re.compile(r"(?:跟|和|与)(?:老公|他)(?:在|一起|一块|碰面|见面|吃|逛|走|坐)"),
    re.compile(r"(?:老公|他)(?:就)?坐在我(?:对面|旁边|边上)"),
    re.compile(r"坐在我对面|(?:他|老公)送我回家|帮我(?:涮菜|夹菜|剥)"),
)
_WISH = ("想", "要是", "如果", "希望", "以后", "下次", "好想", "真希望", "怀念", "以前", "那次")


def _companion_claim(clause):
    text = str(clause or "")
    for pattern in _COMPANION_PATTERNS:
        if not pattern.search(text):
            continue
        if any(marker in text for marker in _WISH):
            continue
        return True
    return False


FABRICATED_RE = {
    "phone_call": re.compile(r"(?:打电话|电话里|通电话|视频通话|语音通话|打视频)"),
    "visit_me": re.compile(r"(?:他来看我|他来我家|来家里找我|他突然出现|他站在我面前)"),
    "gift": re.compile(r"(?:收到.{0,6}(?:礼物|花|快递|外卖)|他送了我|给我买了)"),
    "transfer": re.compile(r"(?:转账|发红包|微信红包)"),
    "trip": re.compile(r"(?:出去旅游|去旅游了|旅行了)"),
    "party": re.compile(r"(?:聚餐|聚会|唱歌|KTV)"),
    "spend": re.compile(
        r"(?:花了(?:多少|[0-9一二三四五六七八九十百千]+|不少|很多|点)|花钱|账单|付款|"
        r"消费了|心疼[^，。]{0,6}钱)"),
}


def _time_mentions(clause, context=""):
    """句中时间词 → 分钟（只取明确的时刻/时段，用来查时间顺序）。

    中文口语「快六点」出现在晚上/下班语境时是 18:00，不是 06:00；否则会误报时间逆序。
    """
    value = str(clause or "")
    sentence = str(context or value)
    later = bool(re.search(r"(下午|傍晚|晚上|夜里|下班|天黑|快睡)", sentence))
    early = bool(re.search(r"(早上|上午|凌晨|中午前|天没亮)", sentence))
    def _adjust(hour):
        if hour is not None and hour <= 12 and later and not early:
            return hour + 12
        return hour
    out = []
    for hour, minute in re.findall(r"(\d{1,2})\s*[:：]\s*(\d{2})", value):
        try:
            out.append(_adjust(int(hour)) * 60 + int(minute))
        except (TypeError, ValueError):
            continue
    if not out:
        period = {"凌晨": 60, "早上": 480, "上午": 600, "中午": 720,
                  "下午": 900, "傍晚": 1110, "晚上": 1200, "夜里": 1380}
        for word, base in period.items():
            if word in value:
                out.append(base)
                break
    cn = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
          "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}
    for match in re.finditer(r"(\d{1,2}|十[一二]?|[一二两三四五六七八九])\s*点", value):
        raw = match.group(1)
        hour = int(raw) if raw.isdigit() else cn.get(raw)
        if hour is not None:
            hour = _adjust(hour)
        if hour is not None and 0 <= hour <= 23:
            out.append(hour * 60)
    return out


# ---------------------------------------------------------------- 核对
_ACTION_GROUP = {"work": "office", "shopping": "mall", "hospital": "hospital",
                 "scenic": "scenic", "workout": "gym", "movie": "mall"}


def _out_mentioned(facts, claims):
    out_groups = set()
    for block in facts.get("out_blocks") or []:
        out_groups |= set(block.get("groups") or [])
    if not out_groups:
        return False
    place_groups = set()
    for claim in claims.get("places") or []:
        place_groups |= set(claim.get("groups") or [])
    if place_groups & out_groups:
        return True
    for claim in claims.get("actions") or []:
        group = _ACTION_GROUP.get(claim.get("action"))
        if group and group in out_groups:
            return True
    return False


def evaluate(text, facts, prompt="", collect_claims=True):
    """返回 (issues, claims, hits)。issues 每条带 category/subtype/detail/evidence。"""
    body = str(text or "")
    claims = extract_claims(body) if collect_claims else {"sentences": _sentences(body)}
    visited_groups = set(facts.get("groups") or [])
    issues = []

    def add(category, subtype, detail, evidence="", prompt_has=None):
        item = {"category": category, "subtype": subtype, "detail": str(detail)[:240],
                "evidence": str(evidence)[:240]}
        if prompt_has is not None:
            item["prompt_has_fact"] = bool(prompt_has)
        issues.append(item)

    # ---- place
    for claim in claims.get("places", []):
        groups = set(claim["groups"])
        allowed = visited_groups | {"home"}
        bad = groups - allowed
        if bad and groups:
            add("place", "unvisited:" + ",".join(sorted(bad)),
                claim["clause"], "当天只在：" + "、".join(facts.get("places") or []),
                prompt_has=True)
    # 当天出过门，日记却断言「一直在家/一天没出门」
    home_lock = re.compile(r"(?:一直在家|一天没出门|没出过门|全天在家|一整天都在家|哪也没去|哪儿也没去)")
    for sentence in claims.get("sentences", []):
        if facts.get("out") and home_lock.search(sentence):
            add("place", "home_lock", sentence,
                "当天出过门：" + "、".join(facts.get("places") or []), prompt_has=True)
    if not facts.get("out") and not facts.get("together_any"):
        for sentence in claims.get("sentences", []):
            if re.search(r"(?:出了门|出门了|在外面逛|去了趟|去逛)", sentence) and not _future(sentence):
                add("place", "fake_out", sentence, "当天所有块都在家", prompt_has=True)

    # ---- action
    evidence_text = " ".join(
        str(block.get("activity") or "") + " " + str(block.get("place") or "") + " " +
        str(block.get("zone") or "") for block in facts.get("blocks") or [])
    ledger_text = " ".join(str(row.get("reason") or "") for row in facts.get("ledger") or [])
    ledger_text = " ".join([ledger_text, " ".join(str(row.get("text") or "") for row in facts.get("meals") or [])])
    for claim in claims.get("actions", []):
        action = claim["action"]
        words = ACTION_EVIDENCE.get(action) or ()
        supported = _has_any(evidence_text, words) or _has_any(ledger_text, words)
        if action == "date":
            supported = bool(facts.get("together_any"))
        if action == "social":
            supported = _has_any(evidence_text, ACTION_EVIDENCE["social"])
            if "一个朋友" in claim["clause"] or "一个同事" in claim["clause"]:
                supported = True   # 人名兜底替换产物，不算社交事件（另有 root cause）
        if supported:
            continue
        # 「买了」只有消费证据才算；没有账本就归 fabricated（消费编造）
        add("action", action, claim["clause"],
            "当天块活动/地点里没有「%s」的证据" % ACTION_LABEL.get(action, action),
            prompt_has=bool(evidence_text))

    # ---- action.overtime：工作日不加班（日程块 18:30 后没有非居家活动就证明没有加班）
    overtime_claims = claims.get("overtime") or []
    if overtime_claims:
        late_out = []
        for block in facts.get("blocks") or []:
            start = str(block.get("start") or "")
            place = str(block.get("place") or "")
            category = str(block.get("category") or "")
            activity = str(block.get("activity") or "")
            if "加班" in activity:
                late_out.append(block)
                continue
            if start >= "18:30" and place not in ("家", "出租屋", "卧室") \
                    and category not in ("sleep", "life"):
                late_out.append(block)
        if not late_out:
            add("action", "overtime", overtime_claims[0]["clause"],
                "当天日程 18:30 后没有任何非居家活动/加班块（工作日不加班）",
                prompt_has=True)

    # ---- companion
    if not facts.get("together_any"):
        for claim in claims.get("companions", []):
            add("companion", "physical_presence", claim["clause"],
                "当天没有面对面窗口（同城不同住，块里也没有约会）", prompt_has=True)

    # ---- meal
    meals = facts.get("meals") or []
    meal_bands = {item.get("band") for item in meals}
    for claim in claims.get("meals", []):
        if claim["kind"] == "any" and meals:
            add("meal", "says_no_meal", claim["clause"],
                "当天吃饭块：%s" % "；".join("%s %s" % (m["start"], m["text"]) for m in meals),
                prompt_has=True)
        elif claim["kind"] in meal_bands:
            add("meal", "says_no_" + claim["kind"], claim["clause"],
                "当天有%s块：%s" % (claim["kind"],
                                    "；".join(m["text"] for m in meals if m.get("band") == claim["kind"])),
                prompt_has=True)

    # ---- health
    sick_day = bool((facts.get("health") or {}).get("sick"))
    sick_gen = bool((facts.get("health_gen") or {}).get("sick"))
    for claim in claims.get("health", []):
        if claim["kind"] == "sick" and not sick_day:
            add("health", "claims_sick", claim["clause"],
                "当天健康状态=%s（生成时=%s）" % (
                    (facts.get("health") or {}).get("status"),
                    (facts.get("health_gen") or {}).get("status")), prompt_has=sick_gen)
        elif claim["kind"] == "good" and sick_day:
            add("health", "denies_sick", claim["clause"],
                "当天健康状态=%s" % (facts.get("health") or {}).get("status"), prompt_has=sick_gen)

    # ---- weather
    weather = facts.get("weather") or {}
    day_tag = WEATHER_TEXT_TAG.get(str(weather.get("text") or ""), "")
    temp = weather.get("temp", "")
    try:
        temp_val = float(temp)
    except (TypeError, ValueError):
        temp_val = None
    prompt_has_weather = bool(re.search(
        r"^- \[[^\]]+\] \[(?:天气|weather)\]", str(prompt or ""), re.M))
    for claim in claims.get("weather", []):
        tag = claim["tag"]
        conflict = ""
        if tag == "rain" and day_tag not in ("rain", "storm"):
            conflict = "当天天气=%s，日记写下雨" % WEATHER_TAG_LABEL.get(day_tag, day_tag or "?")
        elif tag in ("clear", "cloud") and day_tag in ("rain", "overcast", "snow"):
            conflict = "当天天气=%s，日记写晴/多云" % WEATHER_TAG_LABEL.get(day_tag, day_tag)
        elif tag == "snow" and day_tag != "snow":
            conflict = "当天天气=%s，日记写下雪" % WEATHER_TAG_LABEL.get(day_tag, day_tag or "?")
        elif tag == "cold" and temp_val is not None and temp_val >= 28:
            conflict = "当天 %s℃，日记写天凉/降温" % temp
        elif tag == "hot" and temp_val is not None and temp_val <= 12:
            conflict = "当天 %s℃，日记写很热" % temp
        if conflict:
            add("weather", "conflict:" + tag, claim["clause"], conflict,
                prompt_has=prompt_has_weather)
        elif not prompt_has_weather:
            add("weather", "ungrounded:" + tag, claim["clause"],
                "当天天气=%s %s℃，但素材包/提示词里没有任何天气事实"
                % (str(weather.get("text") or ""), temp), prompt_has=False)

    # ---- fabricated
    block_text = evidence_text + " " + ledger_text
    for claim in claims.get("fabricated", []):
        kind = claim["kind"]
        words = {
            "phone_call": ("电话", "通话", "语音"),
            "visit_me": ("来看", "来找", "出现"),
            "gift": ("礼物", "送", "买", "快递", "外卖"),
            "transfer": ("转账", "红包"),
            "trip": ("旅游", "旅行", "景区"),
            "party": ("聚餐", "聚会", "唱歌", "KTV"),
            "spend": ("消费", "账单", "付款", "花了"),
        }.get(kind, ())
        if _has_any(block_text, words):
            continue
        if kind == "gift" and re.search(r"(?:买了|买点|买些)", claim["clause"]):
            add("fabricated", "ungrounded_purchase", claim["clause"],
                "素材包/账本里没有这笔消费", prompt_has=True)
            continue
        add("fabricated", kind, claim["clause"], "素材包里没有对应事件", prompt_has=True)

    # ---- coverage（关键事件有没有写进去）
    if facts.get("out"):
        out_groups = set()
        for block in facts.get("out_blocks") or []:
            out_groups |= set(block.get("groups") or [])
        if out_groups and not _out_mentioned(facts, claims):
            add("coverage", "out_not_mentioned",
                "当天出门去了：" + "、".join(facts.get("places") or []),
                "日记里没有提到任何出门地点", prompt_has=True)
    if sick_day and not any(claim["kind"] == "sick" for claim in claims.get("health", [])):
        if sick_gen:
            add("coverage", "sick_not_mentioned",
                "当天健康状态=%s" % (facts.get("health") or {}).get("status"),
                "生成时仍生病（提示词里有健康事实），但日记没写",
                prompt_has=True)
        else:
            add("coverage", "sick_not_mentioned",
                "当天健康状态=%s" % (facts.get("health") or {}).get("status"),
                "生成时已康复，素材包里没有昨天生病的事实",
                prompt_has=False)
    if facts.get("together_any") and not claims.get("companions"):
        add("coverage", "together_not_mentioned", "当天有面对面约会窗口",
            "日记没写在一起", prompt_has=True)
    if len(meals) >= 2 and not any(EAT_RE.search(sentence) for sentence in claims.get("sentences", [])):
        add("coverage", "meal_not_mentioned", "当天有 %d 顿饭" % len(meals),
            "日记里没有任何吃饭内容", prompt_has=True)
    if facts.get("wake") and re.search(r"(?:早起|六点就起|七点就起|天没亮就起)", body):
        add("coverage", "wake_conflict", "睡到 %s 才起" % facts.get("wake"),
            "日记写早起", prompt_has=True)

    # ---- order（时间顺序）：只抓「前一句在晚上/下班后，后一句又说早上」这种硬逆序；
    # 中文口语「快六点」在晚上语境是 18:00，单纯比分钟会误报，不做。
    times = [item for item in claims.get("times", []) if isinstance(item, dict)]
    inversions = 0
    worst = ""
    for index in range(1, len(times)):
        current = times[index]
        previous = times[index - 1]
        if current["minute"] + 120 >= previous["minute"]:
            continue
        prev_text = previous["sentence"] + previous["clause"]
        cur_text = current["sentence"] + current["clause"]
        prev_evening = bool(re.search(r"(晚上|傍晚|夜里|下班|天黑)", prev_text))
        cur_morning = bool(re.search(r"(早上|上午|凌晨|天没亮|一早|一大早)", cur_text))
        if prev_evening and cur_morning:
            inversions += 1
            if not worst:
                worst = "晚上之后又写早上：%s → %s" % (
                    previous["clause"][:30], current["clause"][:30])
    if inversions:
        add("order", "time_inversion", worst, "时间词共 %d 个，硬逆序 %d 处" % (
            len(times), inversions))

    # ---- transition（相邻句跨地点却没有过渡词）
    connectors = ("然后", "后来", "接着", "之后", "下午", "晚上", "中午", "早上", "出门",
                  "回家", "到家", "到了", "路上", "从", "等", "再", "才", "就", "过了")
    abrupt = 0
    sentences = claims.get("sentences", [])
    prev_groups = set()
    for sentence in sentences:
        groups = match_places(sentence)
        if groups and prev_groups and not (groups & prev_groups) and not _has_any(sentence, connectors):
            abrupt += 1
        if groups:
            prev_groups = groups
    if abrupt:
        add("transition", "abrupt_scene_switch", "相邻句跨地点且无过渡词 %d 处" % abrupt,
            "只统计显式地点句")

    # 命中统计（用于事实表 vs 日记命中）
    hits = {
        "place_groups": sorted(set().union(*[set(c["groups"]) for c in claims.get("places", [])]) if claims.get("places") else set()),
        "meal_count_mentioned": len([s for s in claims.get("sentences", []) if EAT_RE.search(s)]),
        "sick_mentioned": any(c["kind"] == "sick" for c in claims.get("health", [])),
        "together_mentioned": bool(claims.get("companions")),
        "out_mentioned": bool(facts.get("out")) and _out_mentioned(facts, claims),
        "weather_claims": len(claims.get("weather", [])),
        "time_mentions": len(claims.get("times", [])),
        "sentence_count": len(sentences),
    }
    return issues, claims, hits


# ---------------------------------------------------------------- 模板复读
def template_metrics(diaries):
    """跨天模板化指标：重复整句/开头/结尾/4-gram Jaccard。diaries: {day: text}"""
    import itertools
    sentence_days = {}
    for day, text in diaries.items():
        for sentence in set(_sentences(text)):
            body = re.sub(r"[，,。！？!?；;\s]", "", sentence)
            if len(body) < 8:
                continue
            sentence_days.setdefault(sentence, []).append(day)
    repeated = [{"sentence": sentence, "days": days}
                for sentence, days in sentence_days.items() if len(days) >= 2]
    repeated.sort(key=lambda item: -len(item["days"]))
    openings = {}
    closings = {}
    for day, text in diaries.items():
        body = re.sub(r"\s+", "", text)
        openings.setdefault(body[:10], []).append(day)
        closings.setdefault(body[-10:], []).append(day)
    bigram_sets = {}
    for day, text in diaries.items():
        body = re.sub(r"[，,。！？!?；;\s]", "", text)
        bigram_sets[day] = {body[index:index + 4] for index in range(max(0, len(body) - 3))}
    pair_scores = {}
    for left, right in itertools.combinations(bigram_sets, 2):
        small = min(len(bigram_sets[left]), len(bigram_sets[right])) or 1
        overlap = len(bigram_sets[left] & bigram_sets[right]) / small
        pair_scores[(left, right)] = round(overlap, 3)
    per_day = {}
    for day in diaries:
        scores = [score for (left, right), score in pair_scores.items()
                  if day in (left, right)]
        per_day[day] = round(max(scores) if scores else 0.0, 3)
    return {
        "repeated_sentences": repeated[:20],
        "repeated_openings": [{"head": head, "days": days}
                             for head, days in openings.items() if len(days) >= 2],
        "repeated_closings": [{"tail": tail, "days": days}
                              for tail, days in closings.items() if len(days) >= 2],
        "pair_4gram_jaccard": [{"days": list(pair), "score": score}
                               for pair, score in sorted(pair_scores.items(), key=lambda item: -item[1])[:10]],
        "per_day_max_overlap": per_day,
    }


def template_issues(text, day, metrics):
    """把跨天模板指标落成当天 issue。"""
    issues = []
    body = re.sub(r"\s+", "", str(text or ""))
    for item in metrics.get("repeated_sentences") or []:
        if day not in item.get("days"):
            continue
        issues.append({"category": "template", "subtype": "repeated_sentence",
                       "detail": "整句在 %d 天重复：%s" % (len(item["days"]), item["sentence"][:80]),
                       "evidence": "重复日：" + "、".join(item["days"]), "prompt_has_fact": None})
    for item in metrics.get("repeated_openings") or []:
        if day in item.get("days"):
            issues.append({"category": "template", "subtype": "same_opening",
                           "detail": "开头 10 字与另一天相同：%s" % item["head"],
                           "evidence": "、".join(item["days"]), "prompt_has_fact": None})
    for item in metrics.get("repeated_closings") or []:
        if day in item.get("days"):
            issues.append({"category": "template", "subtype": "same_closing",
                           "detail": "结尾 10 字与另一天相同：%s" % item["tail"],
                           "evidence": "、".join(item["days"]), "prompt_has_fact": None})
    overlap = (metrics.get("per_day_max_overlap") or {}).get(day, 0)
    if overlap >= 0.25:
        issues.append({"category": "template", "subtype": "high_overlap",
                       "detail": "与另一天 4-gram 重叠 %.0f%%（阈值 25%%）" % (overlap * 100),
                       "evidence": "字符级复读偏高", "prompt_has_fact": None})
    return issues


# ---------------------------------------------------------------- 事实表 vs 命中
def fact_matrix(facts, hits):
    """每天的事实表 vs 日记命中（报告里的「事实表 vs 日记命中」）。"""
    out_groups = sorted(set().union(*[set(b.get("groups") or [])
                                      for b in facts.get("out_blocks") or []])
                        if facts.get("out_blocks") else set())
    return {
        "places": {"fact": facts.get("places") or [],
                   "diary_hit_groups": hits.get("place_groups") or [],
                   "hit": bool(set(facts.get("groups") or []) &
                               set(hits.get("place_groups") or []))},
        "out": {"fact": bool(facts.get("out")), "fact_groups": out_groups,
                "diary_hit": bool(hits.get("out_mentioned"))},
        "meals": {"fact_count": facts.get("meal_count") or 0,
                  "fact": [("%s %s" % (m["start"], m["text"])) for m in facts.get("meals") or []],
                  "diary_food_sentences": int(hits.get("meal_count_mentioned") or 0),
                  "hit": bool((facts.get("meal_count") or 0) == 0
                              or (hits.get("meal_count_mentioned") or 0) > 0)},
        "health": {"fact_status": (facts.get("health") or {}).get("status"),
                   "fact_sick": bool((facts.get("health") or {}).get("sick")),
                   "diary_sick_mentioned": bool(hits.get("sick_mentioned")),
                   "hit": (not (facts.get("health") or {}).get("sick"))
                          or bool(hits.get("sick_mentioned"))},
        "companion": {"fact_together": bool(facts.get("together_any")),
                      "diary_together_mentioned": bool(hits.get("together_mentioned")),
                      "hit": (not facts.get("together_any"))
                             or bool(hits.get("together_mentioned"))},
        "weather": {"fact": facts.get("weather") or {},
                    "diary_claims": int(hits.get("weather_claims") or 0),
                    "grounded": False},
    }


# ---------------------------------------------------------------- 脱敏
_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s'\"]{2,}")
_KEY_RE = re.compile(r"sk-[A-Za-z0-9]{6,}")


def desensitize(text, limit=600):
    """报告样例脱敏：去绝对路径/密钥/长数字 UID，截断。"""
    value = str(text or "")
    value = _PATH_RE.sub("<PATH>", value)
    value = _KEY_RE.sub("<KEY>", value)
    value = re.sub(r"\b\d{8,}\b", "<NUM>", value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > limit:
        value = value[:limit] + "…"
    return value