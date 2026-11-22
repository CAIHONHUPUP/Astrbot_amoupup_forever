# -*- coding: utf-8 -*-
"""可复现的「模拟 provider」：按世界状态生成候选回复，并按脚本注入已知偏差。

它不是模型：没有语义理解，只按模板拼句子；作用是给真实守卫链路提供
「可控输入」。因此本层只能证明守卫/上下文一致性，不能证明模型语义质量。

输出三类：
1. clean   —— 与快照一致、且回答了探针；
2. covered —— 常见偏差（守卫设计覆盖的写法），用来量捕获率；
3. edge    —— 边缘写法（口语/换词），用来找守卫漏网的缝。
"""
from __future__ import annotations

PLACE_WORD = {"home": "家", "office": "公司", "mall": "商场", "restaurant": "餐厅",
              "road": "路上", "street": "街上", "scenic": "景区", "hotel": "酒店",
              "hospital": "医院", "gym": "健身房", "school": "学校", "station": "车站",
              "playground": "游乐园"}

WRONG_PLACE = {
    "home": ["商场", "公司", "医院", "地铁"],
    "office": ["商场", "家", "景区", "咖啡店"],
    "mall": ["公司", "家", "医院"],
    "restaurant": ["公司", "家", "健身房"],
    "road": ["家", "公司", "商场"],
    "street": ["公司", "家", "酒店"],
    "scenic": ["公司", "家", "商场"],
    "hotel": ["公司", "家", "商场"],
    "hospital": ["公司", "商场", "家"],
    "gym": ["公司", "家", "商场"],
    "school": ["公司", "家", "商场"],
}

TOPIC_REPLIES = {
    "work_complain": ["今天辛苦你了，抱抱。", "别往心里去，你已经很好了。", "要不先放一放，别想工作的事。"],
    "food": ["我也馋了，想吃点热的。", "你想吃啥就吃啥，别亏待自己。", "听起来不错，多吃点。"],
    "miss": ["我也想你。", "抱紧你。", "我也一直在想你。"],
    "drama": ["那部我听说过，好像挺好看的。", "你别熬夜看，伤眼睛。", "男主是不是很会撩？"],
    "clothes": ["你穿什么都好看。", "发我看看呗，我帮你参谋。", "喜欢就买，别犹豫。"],
    "sleep": ["困了就早点歇着。", "我陪你聊两句就睡。", "睡不着就数羊，哈哈。"],
    "family": ["阿姨也是关心你。", "别跟家里人置气。", "等有空我们一起回去看看。"],
    "travel": ["你想去哪我都陪你。", "海边挺好的，吹吹风。", "等放假我们好好计划一下。"],
    "gym": ["你最近很自律呀。", "练完记得拉伸。", "别逞强，慢慢来。"],
    "pet": ["这也太可爱了吧。", "猫猫确实治愈。", "想养就养一只呀。"],
    "game": ["你也太菜了吧，哈哈。", "别打太晚。", "下次带我一起。"],
    "movie": ["那部评价确实高。", "你想看我就陪你去。", "看完记得跟我讲。"],
    "friend": ["那你好好陪她们。", "闺蜜这时候最需要你。", "替我跟你朋友问好。"],
    "weather": ["那你多穿点。", "下雨就别乱跑了。", "注意别着凉。"],
    "commute": ["通勤真的累人。", "路上注意安全。", "到家跟我说一声。"],
    "gossip": ["这么刺激？", "然后呢然后呢？", "你们公司故事真多。"],
    "memory": ["当然记得呀。", "那时候我还有点紧张。", "时间过得真快。"],
    "coquetry": ["抱抱，乖。", "亲一下。", "想你想你。"],
    "daily": ["在的呀。", "嗯嗯，我在。", "怎么啦？"],
}

IRRELEVANT = ["你说呢？", "哈哈哈，你猜。", "今天天气还行。", "不知道呀。"]


def _pick(rng, items):
    return items[rng.randrange(len(items))]


def short_activity(snap):
    return str(snap.get("verb") or snap.get("activity") or "忙").strip()


def where_word(snap):
    zone = str(snap.get("zone") or "")
    word = PLACE_WORD.get(zone)
    if word:
        return "地铁上" if zone == "road" and "地铁" in str(snap.get("place") or "") else word
    place = str(snap.get("place") or "").strip()
    return place or "家"


def clean_answer(probe, snap, meal_ok, together, rng):
    word = where_word(snap)
    verb = short_activity(snap)
    people = [str(item) for item in (snap.get("people") or []) if str(item).strip()]
    if probe == "where":
        return _pick(rng, ["我在%s呢。" % word, "在%s呀，怎么啦？" % word,
                           "还能在哪，%s呗。" % word, "%s这边呢。" % word,
                           "就在%s呀。" % word])
    if probe == "doing":
        return _pick(rng, ["在%s呀。" % verb, "%s呢，你呢？" % verb,
                           "正%s，怎么啦？" % verb, "没干嘛，%s。" % verb])
    if probe == "who":
        if together:
            return _pick(rng, ["跟你呀。", "还能跟谁，跟你呗。", "就我们俩呀。"])
        if people:
            return "跟%s呢。" % "、".join(people)
        return _pick(rng, ["自己一个人呀。", "就我自己呢。", "没人，一个人待着。"])
    if probe == "eaten":
        if meal_ok:
            return _pick(rng, ["吃了，还挺饱。", "刚吃完，你呢？", "吃过了，别担心。"])
        return _pick(rng, ["还没呢，等会儿吃。", "没呢，你吃了吗？", "还没，一会儿再弄。"])
    if probe == "when_return":
        end = str(snap.get("end") or "")
        if str(snap.get("zone") or "") == "home":
            return _pick(rng, ["我就在家呀。", "我已经在家了呀。"])
        return _pick(rng, ["%s就回。" % (end or "晚点"), "%s差不多到家。" % (end or "晚点"),
                           "快了，%s吧。" % (end or "晚点")])
    return _pick(rng, TOPIC_REPLIES["daily"])


def clean_topic(topic, rng):
    replies = TOPIC_REPLIES.get(topic) or TOPIC_REPLIES["daily"]
    return _pick(rng, replies)


def drift_text(drift, snap, prev_snap, probe, rng):
    kind = str((drift or {}).get("kind") or "")
    tier = str((drift or {}).get("tier") or "covered")
    zone = str(snap.get("zone") or "")
    if kind == "place":
        wrong = _pick(rng, [item for item in WRONG_PLACE.get(zone, ["商场"]) if item])
        if tier == "edge":
            return _pick(rng, ["我在%s门口等你呢。" % wrong, "我在%s这边躲雨。" % wrong,
                               "刚在%s那边转了一圈。" % wrong])
        return "我在%s呢，%s。" % (wrong, short_activity(snap))
    if kind == "action":
        if tier == "edge":
            return _pick(rng, ["刚开完会，脑子还是懵的。", "还在改方案，烦死了。",
                               "刚爬起来，还没洗脸。"])
        category = str(snap.get("category") or "")
        if category in ("work", "study") and zone == "office":
            return _pick(rng, ["在做饭呢。", "在睡觉呢。"])
        return _pick(rng, ["我在上班呢。", "在加班。", "在开会呢。", "在做饭呢。"])
    if kind == "companion":
        if tier == "edge":
            return _pick(rng, ["他靠在我肩上呢。", "他就坐我边上。", "他手还搭在我腰上呢。"])
        return _pick(rng, ["他在我旁边呢，刚还抱了我一下。", "他搂着我呢。", "他刚亲了我一下。"])
    if kind == "meal":
        if tier == "edge":
            return _pick(rng, ["刚吃了两口面包。", "刚扒了两口饭。"])
        return _pick(rng, ["刚吃完饭，好撑。", "在吃面呢。", "刚吃完，香。"])
    if kind == "health":
        if tier == "edge":
            return _pick(rng, ["嗓子有点疼，可能是感冒了。", "头有点晕，不太舒服。"])
        return _pick(rng, ["有点发烧，头好晕。", "还在低烧，难受。"])
    if kind == "stale":
        prev_place = str((prev_snap or {}).get("place") or "")
        prev_zone = str((prev_snap or {}).get("zone") or "")
        word = PLACE_WORD.get(prev_zone) or prev_place or "外面"
        verb = short_activity(prev_snap or {})
        return _pick(rng, ["还在%s呢，%s。" % (word, verb), "%s这边还没弄完呢。" % word,
                           "我刚还在%s，现在有点累。" % word])
    if kind == "irrelevant":
        return _pick(rng, IRRELEVANT)
    return ""


def respond(turn, snap, prev_snap, meal_ok, together, rng):
    """返回 (candidate_text, drift_meta)。drift_meta 为 None 表示 clean。"""
    drift = turn.get("drift")
    probe = turn.get("probe")
    if drift:
        body = drift_text(drift, snap, prev_snap, probe, rng)
        if body:
            if probe and drift.get("kind") != "irrelevant" and rng.random() < 0.35:
                body = body + "你呢？"
            return body, dict(drift)
    if probe:
        return clean_answer(probe, snap, meal_ok, together, rng), None
    return clean_topic(turn.get("topic") or "daily", rng), None
