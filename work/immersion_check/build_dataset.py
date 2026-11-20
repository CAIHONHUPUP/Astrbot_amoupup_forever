# -*- coding: utf-8 -*-
"""IMM 数据集：60 条真实采样（messages 为主，日记/记忆补足）+ 40 条合成对抗。"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import synthetic_40  # noqa: E402

SEED = os.path.join(HERE, "real_seed.json")
OUT = os.path.join(HERE, "imm_dataset.json")

QUOTAS = {"吃": 9, "地点": 8, "上班": 8, "睡觉": 6, "家人朋友聚餐": 6,
          "约会": 4, "旅游": 3, "生病": 4, "宵夜": 2}
PROACTIVE_N = 10

TEMPLATES = {
    "吃": ["中午吃了碗面，汤都喝了大半", "刚啃了个包子，有点干", "今天想吃点热乎的",
           "在楼下小馆吃了碗粉，辣得直冒汗"],
    "上班": ["在工位上改周报呢，头都大了", "刚开完会，脑子嗡嗡的", "单子还没改完，快下班了"],
    "睡觉": ["还在床上赖着呢", "昨晚没睡好，眼睛都睁不开", "刚醒，头发都翘着"],
    "生病": ["还烧着，刚吃了药", "嗓子疼，说话都哑", "躺在被窝里发汗，人软软的"],
    "约会": ["在你对面坐着呢，别看了会脸红", "刚吃完饭，出来走走", "今天穿了你喜欢的那条裙子"],
    "旅游": ["在酒店呢，床有点硬", "刚到景点，人好多", "今天走了两万步，腿快断了"],
    "家人朋友聚餐": ["我妈做的红烧肉，吃撑了", "跟他们一起吃的饭，热闹", "一桌子菜，我又吃多了"],
    "宵夜": ["有点饿，煮个面凑合", "忍忍吧，明天还要早起", "楼下烧烤还开着，馋了"],
    "地点": ["在家呢，窝着刷手机", "路上，地铁快到了", "刚到家，鞋都没换"],
    "元问题": ["你上次说的是暗号那事吧，我记得", "这个我记不清了，你再提醒我一下"],
}

KEYWORDS = ("吃", "饭", "早饭", "午饭", "晚饭", "宵夜", "烧烤", "面", "粥", "外卖", "饿", "渴",
            "睡", "困", "起", "晚安", "加班", "上班", "下班", "公司", "工作", "周报", "开会", "工位",
            "家", "到家", "出门", "路上", "地铁", "商场", "厦门", "酒店", "海", "景点",
            "病", "发烧", "烧", "药", "医院", "难受", "疼", "嗓子", "感冒",
            "约会", "见面", "抱", "亲", "想你", "喜欢", "爱", "礼物", "围巾", "挂件",
            "妈妈", "家人", "朋友", "聚餐", "火锅", "沈幼微", "哥哥",
            "天气", "下雨", "冷", "热", "照片", "语音", "图", "记忆", "记得", "名字", "暗号",
            "钱", "花", "买", "bug", "电脑", "手机")

CAT_PATTERNS = [
    ("宵夜", r"宵夜|夜宵|烧烤|晚上饿|饿不饿|点个外卖|晚上吃"),
    ("吃", r"吃|饭|饿|早餐|中饭|午饭|晚饭|外卖|粥|面条|泡面|煮面|包子|零食|水果|蛋"),
    ("睡觉", r"睡|晚安|困|起床|熬夜|醒|赖床"),
    ("生病", r"发烧|烧着|生病|药|难受|疼|感冒|嗓子|医院"),
    ("上班", r"上班|加班|公司|工作|周报|下班|bug|修|开会|工位|老板"),
    ("约会", r"约会|见面|一起吃饭|抱|亲|想你|喜欢你"),
    ("旅游", r"旅游|厦门|酒店|机票|景点|出去玩|海边"),
    ("家人朋友聚餐", r"妈妈|爸爸|家人|朋友|沈幼微|聚餐|哥哥|姐姐"),
    ("元问题", r"记得|记忆|暗号|飞书|deepseek|崩了|杀毒|语音|图|token|截图|测试|表情包|头像"),
    ("地点", r"在哪|到家|路上|地铁|出门|回去|过来|楼下"),
]


def classify(text):
    value = str(text or "")
    for name, pattern in CAT_PATTERNS:
        if re.search(pattern, value):
            return name
    return ""


def ts_to_dt(ts):
    return dt.datetime.fromtimestamp(float(ts))


def day_of(ts):
    return ts_to_dt(ts).strftime("%Y-%m-%d")


def turns_question(user):
    return bool(re.search(r"[?？]$|吗|呢|啥|什么|哪|几|怎么|为什么|要不要|能不能", str(user or "")))


def topic_words(text):
    return {word for word in KEYWORDS if word in str(text or "")}


def day_world(seed, day, together=False):
    blocks = seed["schedule"].get(day) or []
    diary = seed["diaries"].get(day) or ""
    sick = bool(re.search(r"发烧|烧着|低烧|吃了药|生病", diary))
    kv = {}
    if together:
        kv["date_session"] = {"schema_version": 1, "phase": "active", "together": True,
                              "line": "今天和老公在一起", "place": "商场",
                              "start_ts": 0.0, "end_ts": 0.0}
    if any("酒店" in str(b.get("place")) or "厦门" in str(b.get("place")) for b in blocks):
        kv["trip"] = {"schema_version": 1, "phase": "traveling", "city": "厦门"}
    return {"day": day, "blocks": blocks,
            "health": {"status": "低烧" if sick else "健康", "sick": sick},
            "mood": {"开心": 55.0, "平静": 55.0, "依恋": 65.0},
            "relation": "恋人", "kv": kv,
            "ledger": [row for row in seed["ledger"] if day_of(row.get("ts") or 0) == day],
            "home_outfit": True}


def pair_windows(seed):
    by_umo = {}
    for msg in seed["messages"]:
        by_umo.setdefault(msg["umo"], []).append(msg)
    pairs = []
    for umo, rows in by_umo.items():
        rows.sort(key=lambda m: m["ts"])
        for index, msg in enumerate(rows):
            if msg["role"] != "user":
                continue
            reply = None
            for later in rows[index + 1:]:
                if later["role"] != "bot":
                    continue
                if float(later["ts"]) - float(msg["ts"]) > 1800:
                    break
                reply = later
                break
            pairs.append({"umo": umo, "user": msg, "bot": reply, "index": index, "rows": rows})
    pairs.sort(key=lambda p: p["user"]["ts"])
    return pairs


def _bot_pool(seed):
    return [str(m["content"]) for m in seed["messages"]
            if m["role"] == "bot" and len(str(m["content"]).strip()) >= 6]


def _pick_candidate(user, thread_reply, cat, pool, used, index):
    if thread_reply and str(thread_reply).strip() and str(thread_reply).strip() not in used:
        return str(thread_reply).strip(), "history_thread"
    words = topic_words(user)
    best, best_hits = "", 0
    for line in pool:
        if line in used:
            continue
        hits = len(words & topic_words(line))
        if hits > best_hits:
            best, best_hits = line, hits
    if best_hits >= 1:
        return best, "history_topic"
    options = TEMPLATES.get(cat, TEMPLATES["地点"])
    for offset in range(len(options)):
        option = options[(index + offset) % len(options)]
        if option not in used:
            return option, "template"
    return options[0], "template"


def build_message_item(seed, pair, cat, iid):
    rows = pair["rows"]
    start = max(0, pair["index"] - 2)
    window = [row for row in rows[start:pair["index"] + 8]
              if float(row["ts"]) >= float(pair["user"]["ts"]) - 7200]
    pairs = []
    for index, row in enumerate(window):
        if row["role"] != "user":
            continue
        reply = ""
        for later in window[index + 1:]:
            if later["role"] == "bot":
                if float(later["ts"]) - float(row["ts"]) <= 1800:
                    reply = later["content"]
                break
        pairs.append((str(row["content"]), reply, float(row["ts"])))
    pairs = pairs[:6]
    if len(pairs) < 4:
        extra = []
        for offset, row in enumerate(rows[pair["index"] + 8:pair["index"] + 16]):
            if row["role"] != "user":
                continue
            idx = pair["index"] + 8 + offset
            reply = ""
            for later in rows[idx + 1:idx + 3]:
                if later["role"] == "bot" and float(later["ts"]) - float(row["ts"]) <= 1800:
                    reply = later["content"]
                    break
            extra.append((str(row["content"]), reply, float(row["ts"])))
        pairs.extend(extra)
    day = day_of(pair["user"]["ts"])
    world = day_world(seed, day, together=(day == "2026-11-15"))
    pool = _bot_pool(seed)
    used, turns = set(), []
    questions = TEMPLATES.get(cat, TEMPLATES["地点"])
    fill = 0
    for index, (user, reply, ts) in enumerate(pairs[:6]):
        if not str(user).strip():
            continue
        candidate, source = _pick_candidate(user, reply, cat, pool, used, index)
        used.add(candidate)
        turns.append({"user": user, "candidate": candidate, "candidate_source": source,
                      "question": turns_question(user),
                      "now": ts_to_dt(ts).isoformat()})
    while len(turns) < 4:
        user = questions[fill % len(questions)]
        candidate, source = _pick_candidate(user, "", cat, pool, used, len(turns))
        used.add(candidate)
        turns.append({"user": user, "candidate": candidate, "candidate_source": source,
                      "question": turns_question(user),
                      "now": ts_to_dt(float(pair["user"]["ts"]) + 600 * (fill + 1)).isoformat()})
        fill += 1
    return {"id": iid, "category": cat, "source": "real", "seed_source": "messages",
            "seed_text": str(pair["user"]["content"])[:120],
            "day": day, "world": world, "turns": turns[:6]}


#: 日记/记忆派生的真实场景：显式 (他说, 她候选) 对，candidate_source=authored
DERIVED_SPEC = [
    ("约会", "2026-11-15 和老公在商场吃火锅、逛店", [
        ("今天想吃什么呀", "火锅！上次说好的那家"),
        ("我们上次那家火锅还开着吗", "开着呢，我路过看了一眼"),
        ("我今天穿这条裙子好看吗", "好看，你穿什么都好看"),
        ("吃完去哪里逛", "去楼上看看裤子，正好想买")]),
    ("约会", "2026-11-15 晚上和他一起吃饭", [
        ("你到哪了", "到门口了，你在哪"),
        ("我在门口等你", "我看见你了，这边这边"),
        ("今天好冷，你外套借我", "给你披着，我不冷"),
        ("我们吃点热的吧", "好呀，就吃火锅")]),
    ("旅游", "厦门中山路酒店，出站风很大", [
        ("到厦门了吗", "到了，刚出站，风好大"),
        ("酒店订的哪", "中山路附近那家，房间不大但干净"),
        ("晚上去海边走走吗", "想呀，你陪我聊着我就去"),
        ("记得早点回酒店", "知道啦，我早点回")]),
    ("旅游", "厦门沙茶面、海蛎煎", [
        ("今天吃了什么呀", "沙茶面，还有海蛎煎"),
        ("好玩吗", "好玩，就是一个人有点想你"),
        ("一个人玩会不会没意思", "有点，下回你陪我来"),
        ("什么时候回家", "后天，机票订好了")]),
    ("家人朋友聚餐", "妈妈做了红烧肉，一桌菜", [
        ("今天家里吃饭吗", "吃呀，我妈做了一桌子"),
        ("你妈做的红烧肉好吃吗", "好吃，我吃了两碗饭"),
        ("跟家里人一起吃的", "嗯，热闹得很"),
        ("你又吃多了吧", "撑得走不动了")]),
    ("家人朋友聚餐", "和朋友楼下小馆吃饭", [
        ("晚上跟谁吃饭", "跟朋友，楼下小馆"),
        ("吃得开心吗", "开心，聊到好晚"),
        ("吃到几点", "八点多就散了"),
        ("要不要我去接你", "不用啦，我走回去就几分钟")]),
    ("家人朋友聚餐", "和沈幼微楼下小馆吃饭", [
        ("沈幼微最近怎么样", "她挺好的，还是老样子"),
        ("你们吃的啥", "小馆的家常菜，也不贵"),
        ("聊了些什么", "聊她的工作，还有她对象"),
        ("她还在原来那家公司吗", "在呀，说最近挺忙")]),
    ("家人朋友聚餐", "过年家里人一起吃饭", [
        ("过年家里人多吗", "多呀，一屋子人"),
        ("年夜饭吃什么", "鸡鸭鱼肉都齐了"),
        ("你妈会问你对象吗", "问了，我就笑笑不说话"),
        ("那你怎么说", "我说在处着呢，别催")]),
    ("家人朋友聚餐", "同事聚餐，人多热闹", [
        ("今天聚餐啊", "嗯，部门一起去了"),
        ("吃的什么", "火锅，一大桌"),
        ("喝了酒没", "就一小杯，没多喝"),
        ("别喝太多", "知道，我清醒着呢")]),
    ("家人朋友聚餐", "哥哥嫂子来家里", [
        ("你哥来过吗", "来了，还带了嫂子"),
        ("家里热闹吧", "热闹，说话声都没停"),
        ("晚上吃什么", "我妈煮了一大锅"),
        ("聊到几点", "到十点多才走")]),
    ("生病", "2026-11-16 发烧，吃了药，嗓子干", [
        ("烧退了吗", "退了一点，还有三十七度多"),
        ("吃药了吗", "吃了，中午那次"),
        ("嗓子还疼吗", "疼，说话都哑"),
        ("要不要我去看你", "别来了，怕传染你")]),
    ("生病", "2026-11-16 裹着被子发汗", [
        ("好点没", "在出汗，人还是软"),
        ("人还难受吗", "难受，头也晕"),
        ("多喝水", "嗯，杯子就在床头"),
        ("睡一会儿吧", "好，我眯一会儿")]),
    ("生病", "2026-11-16 中午喝粥，没胃口", [
        ("中午吃的什么", "喝了小半碗粥"),
        ("有胃口吗", "没有，闻着都腻"),
        ("要不要吃点别的", "先不折腾了"),
        ("别硬撑", "知道，我躺着呢")]),
    ("生病", "2026-11-16 量体温还是低烧", [
        ("多少度", "三十七度八"),
        ("还烧着吗", "还烧着，刚量完"),
        ("药还剩多少", "还有两顿"),
        ("明天别去上班了", "嗯，我请假")]),
    ("宵夜", "半夜起来吃了小蛋糕", [
        ("饿了吗", "有点，翻来覆去睡不着"),
        ("要不要吃点东西", "冰箱里有个小蛋糕"),
        ("半夜吃东西会胖吧", "就吃一小块，应该没事"),
        ("就吃一点点", "知道啦，就一小口")]),
    ("宵夜", "楼下烧烤还开着", [
        ("晚上吃了吗", "还没呢，就喝了口水"),
        ("楼下那家还开着", "开着，人还不少"),
        ("点个烧烤吧", "那我点几串素的"),
        ("那我不客气了", "你吃吧，我看着你吃")]),
]


def build_derived_item(seed, cat, iid, seed_text, pairs):
    day = {"生病": "2026-11-16", "约会": "2026-11-15", "家人朋友聚餐": "2026-11-15",
           "旅游": "2026-11-14", "宵夜": "2026-11-17"}.get(cat, "2026-11-17")
    world = day_world(seed, day, together=(cat == "约会"))
    if cat == "旅游":
        world["blocks"] = [{"start": "09:00", "end": "24:00", "place": "厦门·酒店",
                            "zone": "hotel", "activity": "在酒店歇脚", "category": "rest",
                            "subject": "bot"}]
        world["kv"]["trip"] = {"schema_version": 1, "phase": "traveling", "city": "厦门"}
    if cat == "家人朋友聚餐":
        world["blocks"] = [{"start": "17:00", "end": "21:00", "place": "家", "zone": "home",
                            "activity": "和家人吃饭", "category": "meal", "subject": "bot",
                            "people": ["妈妈"], "with": ["妈妈"]}]
    if cat == "宵夜":
        world["blocks"] = [{"start": "22:00", "end": "24:00", "place": "家", "zone": "home",
                            "activity": "窝着刷手机", "category": "rest", "subject": "bot"}]
    if cat == "生病":
        world["health"] = {"status": "低烧", "sick": True}
        world["blocks"] = [{"start": "09:00", "end": "21:00", "place": "家", "zone": "home",
                            "activity": "裹着被子发汗", "category": "rest", "subject": "bot"}]
    turns = []
    for index, (user, candidate) in enumerate(pairs):
        turns.append({"user": user, "candidate": candidate, "candidate_source": "authored",
                      "question": turns_question(user),
                      "now": "%sT%02d:%02d:00" % (day, 9 + index, 5 + index * 13)})
    return {"id": iid, "category": cat, "source": "real-derived",
            "seed_source": "diary/memory", "seed_text": str(seed_text)[:140],
            "day": day, "world": world, "turns": turns}


def build_proactive_items(seed, iid_start):
    lines = [p for p in seed["proactive"] if p.get("text")]
    lines.sort(key=lambda p: p["ts"])
    chosen = lines[-PROACTIVE_N:] if len(lines) >= PROACTIVE_N else lines
    items = []
    for index, line in enumerate(chosen):
        day = day_of(line["ts"])
        world = day_world(seed, day)
        near = [m for m in seed["messages"]
                if abs(float(m["ts"]) - float(line["ts"])) < 3600 and m["role"] == "user"]
        near.sort(key=lambda m: abs(float(m["ts"]) - float(line["ts"])))
        users = [str(m["content"]) for m in near[:4]]
        while len(users) < 4:
            users.append(["好呀", "你吃了吗", "今天忙不忙", "早点休息"][len(users) % 4])
        turns = [{"user": "", "candidate": str(line["text"]), "candidate_source": "history_thread",
                  "proactive": True, "question": False, "now": ts_to_dt(line["ts"]).isoformat()}]
        for j, text in enumerate(users):
            turns.append({"user": text, "candidate": TEMPLATES["吃"][j % 4],
                          "candidate_source": "template", "question": turns_question(text),
                          "now": ts_to_dt(float(line["ts"]) + 300 * (j + 1)).isoformat()})
        items.append({"id": "R%02d" % (iid_start + index), "category": "主动消息",
                      "source": "real", "seed_source": "proactive_log",
                      "seed_text": str(line["text"])[:120], "day": day, "world": world,
                      "turns": turns})
    return items


def main():
    seed = json.load(open(SEED, encoding="utf-8"))
    pairs = pair_windows(seed)
    per_cat = {name: [] for name in QUOTAS}
    used = set()
    for pair in pairs:
        cat = classify(pair["user"]["content"])
        if cat in per_cat and len(per_cat[cat]) < QUOTAS[cat]:
            key = (pair["umo"], pair["user"]["id"])
            if key in used:
                continue
            per_cat[cat].append(pair)
            used.add(key)

    real_items, counter = [], 1
    for cat, chosen in per_cat.items():
        for pair in chosen:
            item = build_message_item(seed, pair, cat, "R%02d" % counter)
            real_items.append(item)
            counter += 1
    for cat, quota in QUOTAS.items():
        have = sum(1 for item in real_items if item["category"] == cat)
        need = quota - have
        for spec_cat, seed_text, spec_pairs in DERIVED_SPEC:
            if need <= 0:
                break
            if spec_cat != cat:
                continue
            real_items.append(build_derived_item(seed, cat, "R%02d" % counter, seed_text, spec_pairs))
            counter += 1
            need -= 1
        while need > 0:
            pairs_tpl = [(user, TEMPLATES[cat][idx % len(TEMPLATES[cat])])
                         for idx, user in enumerate(["在干嘛", "吃了吗", "忙不忙", "早点睡"])]
            real_items.append(build_derived_item(seed, cat, "R%02d" % counter, "模板补足", pairs_tpl))
            counter += 1
            need -= 1
    real_items.extend(build_proactive_items(seed, counter))

    synth = synthetic_40.ITEMS
    hist, turn_hist = {}, {}
    for item in real_items + synth:
        hist[item["category"]] = hist.get(item["category"], 0) + 1
        turn_hist[len(item["turns"])] = turn_hist.get(len(item["turns"]), 0) + 1
    dataset = {"schema": "imm_dataset_v1",
               "built_at": dt.datetime.now().isoformat(timespec="seconds"),
               "category_hist": hist, "turn_hist": turn_hist,
               "real": real_items, "synthetic": synth, "items": real_items + synth}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(dataset, fh, ensure_ascii=False, indent=1)

    src = {}
    for item in real_items:
        for turn in item["turns"]:
            src[turn["candidate_source"]] = src.get(turn["candidate_source"], 0) + 1
    print("TOTAL", len(dataset["items"]), "real", len(real_items), "synth", len(synth))
    print("CATS", json.dumps(hist, ensure_ascii=False))
    print("TURNS", turn_hist)
    print("REAL_CATS", json.dumps({c: sum(1 for i in real_items if i["category"] == c)
                                   for c in QUOTAS}, ensure_ascii=False))
    print("SHORT", [i["id"] for i in dataset["items"] if len(i["turns"]) < 4])
    print("CAND_SRC", json.dumps(src, ensure_ascii=False))
    print("DERIVED", sum(1 for i in real_items if i["source"] == "real-derived"),
          "PROACTIVE", sum(1 for i in real_items if i["category"] == "主动消息"))


if __name__ == "__main__":
    main()
