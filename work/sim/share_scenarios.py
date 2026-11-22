# -*- coding: utf-8 -*-
"""分享事件对话模拟：场景 / 话题 / 用户回应 / 确定性 mock provider。

约束（只读测试）：
- 不联网、不读生产配置、不写任何数据库；随机数固定种子，结果可复现。
- mock provider 只模拟「模型大概会写出什么句子」，**不模拟模型是否理解提示词**；
  因此本文件产出的结论只覆盖确定性守卫层，不覆盖模型语义质量。

用法：由 share_sim.py 导入，不单独运行。
"""
from __future__ import annotations

import random
from typing import Any

#: 对话组数、每组轮数（任务要求 >= 10 轮）
GROUP_COUNT = 50
TURNS_PER_GROUP = 10
#: 固定种子：同一份代码每次跑出同一批对话
SEED_BASE = 20261121

#: 场景对齐生产的 current_activity()：place/zone/activity。
#: zone 取值参考 amo_state_consistency._scene：home* / road / outside。
SCENES: list[dict[str, Any]] = [
    {"key": "home", "place": "家", "zone": "home", "activity": "窝在沙发上刷手机",
     "scene_action": "（窝在沙发上举着手机）", "reaction": "在沙发上笑到打滚",
     "source": "在抖音刷到的", "home": True},
    {"key": "road", "place": "路上", "zone": "road", "activity": "挤地铁回家",
     "scene_action": "（在地铁上扶着扶手）", "reaction": "在地铁上差点笑出声",
     "source": "通勤路上刷到的", "home": False},
    {"key": "mall", "place": "商场", "zone": "outside", "activity": "在商场逛街",
     "scene_action": "（拎着袋子站在扶梯上）", "reaction": "在商场扶梯上差点笑出声",
     "source": "在商场歇脚的时候刷到的", "home": False},
    {"key": "office", "place": "公司", "zone": "outside", "activity": "在公司上班",
     "scene_action": "（趴在工位上偷偷看）", "reaction": "在工位上憋笑憋到抖",
     "source": "午休的时候刷到的", "home": False},
    {"key": "noodle", "place": "面馆", "zone": "outside", "activity": "在面馆吃面",
     "scene_action": "（在面馆等面）", "reaction": "吃面差点喷出来",
     "source": "在面馆等面的时候刷到的", "home": False},
]

#: 分享话题：视频 / 图片 / 事情（任务要求的三种分享类型都在里面）。
#: animal 为 None 表示非动物对象；label 是回复里会出现的对象词。
TOPICS: list[dict[str, Any]] = [
    {"key": "cat_box", "kind": "video", "animal": "猫", "label": "猫",
     "desc": "一只橘猫把自己塞进纸箱，只露出个屁股",
     "follow": "它把自己卡住了，越挣扎越出不来", "other_desc": "它卡在纸箱里出不来"},
    {"key": "corgi_tail", "kind": "video", "animal": "狗", "label": "狗",
     "desc": "一只柯基追自己的尾巴，追到原地转圈",
     "follow": "它转晕了，一屁股坐地上", "other_desc": "它追着尾巴原地转圈"},
    {"key": "bunny_pancake", "kind": "image", "animal": "兔", "label": "兔",
     "desc": "一只兔子瘫成一张饼贴在凉席上",
     "follow": "它听见零食袋的声音，一下弹起来", "other_desc": "它瘫成一张饼"},
    {"key": "parrot", "kind": "video", "animal": "鸟", "label": "鸟",
     "desc": "一只鹦鹉学人说话，学得特别像",
     "follow": "它开始学我笑，笑得更夸张", "other_desc": "它学人说话"},
    {"key": "hamster", "kind": "image", "animal": "仓鼠", "label": "仓鼠",
     "desc": "一只仓鼠把腮帮子塞得鼓鼓的，还在往里塞",
     "follow": "它塞到最后吐出来一颗", "other_desc": "它腮帮子塞得鼓鼓的"},
    {"key": "meme", "kind": "image", "animal": None, "label": "表情包",
     "desc": "一张表情包，写着“我很好，别管我”",
     "follow": "配图是一张翻白眼的脸", "other_desc": "配图是张翻白眼的脸"},
    {"key": "milktea", "kind": "thing", "animal": None, "label": "奶茶店",
     "desc": "楼下奶茶店排队排到马路牙子上",
     "follow": "排到一半说珍珠卖完了", "other_desc": "排队排到马路上"},
    {"key": "canteen", "kind": "thing", "animal": None, "label": "食堂",
     "desc": "公司食堂今天的番茄炒蛋是甜的",
     "follow": "同事吃了一口脸都绿了", "other_desc": "番茄炒蛋是甜的"},
]

#: 任务指定的用户回应（媒体类用原话，事情类换成同功能的口语）。
MEDIA_UTTERANCES = {
    "laugh": ["哈哈哈", "哈哈笑死", "笑死我了"],
    "pretty": ["好看", "好看好看", "好可爱"],
    "what": ["什么视频", "啥视频", "什么呀"],
    "where": ["在哪", "在哪看到的", "哪儿刷的"],
    "next": ["然后呢", "然后呢然后呢", "后来呢"],
}
#: 分享的是图片时，用户不会问「什么视频」，换成同功能的图类问法。
IMAGE_UTTERANCES = {
    "laugh": ["哈哈哈", "哈哈笑死", "笑死我了"],
    "pretty": ["好看", "好看好看", "好可爱"],
    "what": ["什么图", "发的啥", "什么呀"],
    "where": ["在哪", "在哪看到的", "哪儿刷的"],
    "next": ["然后呢", "然后呢然后呢", "后来呢"],
}
THING_UTTERANCES = {
    "laugh": ["哈哈哈", "哈哈哈哈", "笑死我了"],
    "pretty": ["真的假的", "不是吧", "这么离谱"],
    "what": ["什么事", "什么事啊", "咋了"],
    "where": ["在哪", "哪里", "哪儿"],
    "next": ["然后呢", "后来呢", "接着呢"],
}
INTENTS = ("laugh", "pretty", "what", "where", "next")

#: 动物词表（比生产 amo_state_consistency 的更宽，便于检查判定）
ANIMAL_WORDS = {
    "猫": ("猫", "猫猫", "小猫", "橘猫"),
    "狗": ("狗", "狗子", "狗狗", "小狗", "柯基"),
    "兔": ("兔", "兔子", "小兔"),
    "鸟": ("鸟", "小鸟", "鹦鹉"),
    "仓鼠": ("仓鼠",),
}


def utterances_for(topic: dict[str, Any]) -> dict[str, list[str]]:
    if topic["kind"] == "thing":
        return THING_UTTERANCES
    if topic["kind"] == "image":
        return IMAGE_UTTERANCES
    return MEDIA_UTTERANCES


class MockProvider:
    """确定性 mock provider：按场景/话题/意图拼出「模型可能写的句子」。

    它不读提示词，只按模板 + 固定种子注入常见的线上失败模式：
    换对象、重复开头、角色反转、地点动作错位、不接最后一句。
    """

    def __init__(self, seed: int, inject: bool = True):
        self.rng = random.Random(seed)
        self.inject = bool(inject)

    # ---------- 对齐（正常）样本 ----------
    def aligned_share(self, scene: dict[str, Any], topic: dict[str, Any], fresh: bool) -> str:
        label = topic["label"]
        if not fresh:
            return "刚才那个%s还有后续，%s。%s" % (label, topic["follow"], scene["scene_action"])
        if topic["kind"] == "video":
            # 三种真实说法混着来：其中「我刚刷到…」不会命中生产入账条件（见报告探针）
            form = self.rng.choice(("我刚刷到一个%s的视频", "我刷到一个%s的视频", "给你看个%s的视频"))
            return (form + "，%s。%s") % (label, topic["desc"], scene["scene_action"])
        if topic["kind"] == "image":
            form = self.rng.choice(("给你看个图，%s。%s", "给你看个图片，%s。%s"))
            if form.count("%s") == 2:
                return form % (topic["desc"], scene["scene_action"])
            return form % (topic["desc"],)
        return "老公，%s，笑死我了。%s" % (topic["desc"], scene["scene_action"])

    def aligned_reply(self, scene: dict[str, Any], topic: dict[str, Any], intent: str) -> str:
        label = topic["label"]
        desc = topic["desc"]
        react = scene["reaction"]
        if intent == "laugh":
            return "哈哈哈真的，%s，我%s。" % (desc, react)
        if intent == "pretty":
            if topic["kind"] == "thing":
                return "真的，%s，我%s。" % (desc, react)
            return "%s那个样子太可爱了，%s，我又看了一遍。" % (label, desc)
        if intent == "what":
            if topic["kind"] == "thing":
                return "就是%s的事，%s。" % (label, desc)
            if topic["kind"] == "image":
                return "就是%s的图，%s。" % (label, desc)
            return "就是%s的视频，%s。" % (label, desc)
        if intent == "where":
            return "%s，%s。" % (scene["source"], desc)
        return "然后%s，我%s。" % (topic["follow"], react)

    # ---------- 注入失败模式 ----------
    def _swap_reply(self, topic: dict[str, Any], scene: dict[str, Any]) -> str:
        others = [name for name in ANIMAL_WORDS if name != topic["animal"]]
        other = self.rng.choice(others)
        desc = topic.get("other_desc") or topic["desc"]
        return "哈哈哈这%s也太逗了，%s，我%s。" % (other, desc, scene["reaction"])

    def _repeat_opener_reply(self, topic: dict[str, Any]) -> str:
        if topic["kind"] == "thing":
            return "老公我刚刷到一件事，%s。" % topic["desc"]
        return "老公我刚刷到一个%s的视频，%s。" % (topic["label"], topic["desc"])

    def _role_reversal_reply(self, topic: dict[str, Any]) -> str:
        kind_word = "图" if topic["kind"] == "image" else "视频"
        return "你发的那个%s%s我还没点开，你再发我一遍？" % (topic["label"], kind_word)

    def _place_mismatch_reply(self, scene: dict[str, Any]) -> str:
        key = scene["key"]
        if key == "home":
            return "我还在路上呢，快到了。"
        if key == "road":
            return "（窝在沙发上，把手机举高一点）刚到家，外套都脱了。"
        if key == "mall":
            return "（窝在沙发上）我刚洗完澡，趴着呢。"
        return "我在厨房煮面，差点把锅烧了。"

    def _miss_reply(self) -> str:
        return self.rng.choice(["嗯嗯，好呀。", "你在干嘛呀。", "我等会儿再说。"])

    def _inject_share_place(self, scene: dict[str, Any], topic: dict[str, Any]) -> str:
        """分享句里塞一个和场景矛盾的括号动作（内联，模型常这么写）。"""
        wrong = "（窝在沙发上举着手机）" if scene["key"] != "home" else "（在地铁上扶着扶手）"
        if topic["kind"] == "thing":
            return "老公，%s%s，笑死我了。" % (wrong, topic["desc"])
        return "老公我刚刷到一个%s的视频%s，%s。" % (topic["label"], wrong, topic["desc"])

    # ---------- 单轮生成 ----------
    def turn(self, scene: dict[str, Any], topic: dict[str, Any], intent: str,
             fresh: bool) -> dict[str, Any]:
        share = self.aligned_share(scene, topic, fresh)
        share_injected = ""
        if self.inject and self.rng.random() < 0.15:
            share = self._inject_share_place(scene, topic)
            share_injected = "place_mismatch"

        reply = self.aligned_reply(scene, topic, intent)
        reply_injected = ""
        if self.inject and self.rng.random() < 0.35:
            modes: list[tuple[str, float]] = [("place_mismatch", 0.30), ("miss_last_line", 0.24)]
            if topic["animal"]:
                modes.append(("swap_object", 0.20))
            if not fresh:
                modes.append(("repeat_opener", 0.18))
            if topic["kind"] in ("video", "image"):
                modes.append(("role_reversal", 0.12))
            total = sum(weight for _, weight in modes)
            roll = self.rng.random() * total
            mode = modes[-1][0]
            for name, weight in modes:
                roll -= weight
                if roll <= 0:
                    mode = name
                    break
            if mode == "swap_object":
                reply = self._swap_reply(topic, scene)
            elif mode == "repeat_opener":
                reply = self._repeat_opener_reply(topic)
            elif mode == "role_reversal":
                reply = self._role_reversal_reply(topic)
            elif mode == "place_mismatch":
                reply = self._place_mismatch_reply(scene)
            else:
                reply = self._miss_reply()
            reply_injected = mode

        return {"share": share, "reply": reply,
                "share_injected": share_injected, "reply_injected": reply_injected}


def build_dialogue(gidx: int) -> list[dict[str, Any]]:
    """为第 gidx 组生成 10 轮的 (场景, 话题, 意图, 用户原话)。

    话题带重复（用于检查「同一个对象/同一件事 2 小时内不重复刚刷到」）：
    每组 8 个话题里抽 6 个，按 [0,1,2,0,3,1,4,2,5,1] 排，保证有 3 次回访。
    """
    rng = random.Random(SEED_BASE + gidx * 97)
    order = list(range(len(TOPICS)))
    rng.shuffle(order)
    pattern = [0, 1, 2, 0, 3, 1, 4, 2, 5, 1]
    intents = list(INTENTS)
    rng.shuffle(intents)
    intents = intents + intents
    scene = SCENES[rng.randrange(len(SCENES))]
    rows = []
    for turn in range(TURNS_PER_GROUP):
        if turn and rng.random() < 0.35:
            scene = SCENES[rng.randrange(len(SCENES))]
        topic = TOPICS[order[pattern[turn] % len(order)]]
        intent = intents[turn % len(intents)]
        user_text = rng.choice(utterances_for(topic)[intent])
        rows.append({"scene": scene, "topic": topic, "intent": intent,
                     "user": user_text})
    return rows