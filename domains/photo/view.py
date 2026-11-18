# -*- coding: utf-8 -*-
"""照片/画画的「视角」约束（2026-11-11 加入）。

她要发一张照片，视角只有两种，绝不能混：

- **她本人出镜** → 自拍视角：手机前置摄像头、手臂伸出把镜头举在面前、人物占主体；
- **风景/环境** → 她的拍摄视角：手机后置摄像头、第一人称、镜头在她眼睛的高度、
  画面里不出现她本人（顶多露一小截手），环境占主体。

混着说会出问题：模型会画成「第三人在看风景」，或者「自拍里只有半张脸」。
所以提示词里永远只带一条视角后缀（``view_suffix()``）。

``looks_like_her()`` 给自定义生图（``/amo draw``、面板「画图测试」）判视角用：
「拍张你在工位的照片」→ 她出镜（自拍）；「画一张你看到的海」→ 她的视角（空镜）。
"""

from __future__ import annotations

import re

# 视角后缀（photo_engine / draw_engine 共用；出图日志里肉眼可查）
VIEW_SELFIE = (
    "自拍视角：画面主体是bot自己（虚拟角色大肥鱼），不是用户；用户不在画面里；"
    "手机前置摄像头，她自然地举着手机自拍；"
    "不要出现手机背面、后置摄像头或镜头对着画面（自拍是前置摄像头），手机本体不要入镜，"
    "人物占画面主要位置，背景轻微虚化"
)
VIEW_POV = (
    "她的拍摄视角：第一人称手机实拍视角（first-person smartphone perspective），这是bot的第一人称视角，"
    "不是用户拍的；她举着手机随手拍，镜头在她眼睛的高度，"
    "画面里不出现她本人、最多只露出一小截手或手臂；"
    "手机不要入镜，不要出现手机背面、摄像头或镜头对着画面，环境占画面主要位置"
)
SCENERY_ONLY = "环境空镜，画面以环境为主，可有路人/游客但不要抢镜，不出现她本人，也不出现用户"
ROOM_NO_BODY = "画面里不出现任何人，也不出现手、脚、头发、人影等任何人体局部"

# 自拍（2026-11-17 内容政策）：只说日常自拍构图，不写身体部位/皮肤/潮红/取景范围
SELFIE_FRAMING = (
    "第一人称日常自拍视角：她本人自然出现在镜头前，人物占画面主体；"
    "画面必须包含她的脸，人物上半身在画面里；不要裁成只有腿/脚/拖鞋的局部特写；"
    "采用日常自拍构图，背景只是陪衬，不要变成房间空镜"
)
SELFIE_STYLE = "手机前置摄像头质感，轻微美颜，真实生活照，像刚发给老公的微信自拍，不是网图也不是插画"
#: 自拍头饰只按三视图的蓝蝴蝶结；女仆头饰/发箍/白边头饰都算额外发饰（P3-1）。
HEADWEAR_LOCK = "头饰只按三视图：蓝蝴蝶结；不要女仆头饰、发箍、白边头饰、额外发饰"

SELFIE_NEGATIVE = (
    "多只手，畸形的手指，畸形的手，畸形的脸，多余的人，空房间，只有一只手，"
    "男人，合影，第二个人，另一个人，多余的手，旁观者视角，第三人称视角，"
    "第三人称空镜，文字，水印，插画，二次元，"
    "男人的肩膀，别人的肩膀，男人的手，情侣合影，双人，两人，陌生人，"
    "man, male, second person, another person, group photo, couple photo, "
    "third-person view, extra arms, human shoulder, two people, "
    "床上的鞋，室内穿鞋，穿着鞋躺床上，鞋踩床单，鞋出现在被窝里，"
    "女仆头饰，女仆发箍，白边头饰，白色发箍，发箍，额外发饰，女仆装发带，白色发带，"
    "脚部特写，畸形的脚，脚趾粘连，多脚趾，少脚趾，脚踝扭曲，腿脚融合，"
    "脚和拖鞋融在一起，拖鞋融化变形，腿脚比例失调，"
    "deformed feet, fused toes, extra toes, missing toes, distorted ankles, "
    "foot merged into leg, melted slippers, bad foot anatomy, close-up of feet"
)
SELFIE_HINTS = (
    "自拍", "拍张你", "拍一张你", "拍下你", "拍拍你", "拍一下你", "拍你",
    "你的照片", "你的样子", "让我看看你", "看看你",
    "你在干嘛", "你在干什么", "你在做什么", "你在哪",
)
# 这些是「她要拍环境」，反过来说明人不出镜；先于 SELFIE_HINTS 判断
POV_HINTS = ("你看到", "你拍的", "你眼里", "从你那边", "第一人称")

#: 内容政策敏感词：正向自拍提示词里一律不写；兜底清洗也按这些词检查。
#: 2026-11-17 三节点 content_policy_violation 事故后加入。
POLICY_SENSITIVE_WORDS = (
    "潮红", "脸颊", "脸红", "皮肤", "肌肤", "锁骨", "胸前", "胸口", "乳沟",
    "大腿", "小腿", "双腿", "光着脚", "赤脚", "脚踝", "身体", "身躯", "臀部",
    "湿身", "贴身", "紧贴", "水珠", "出水芙蓉", "裸露", "半裸", "性感", "妩媚",
    "head to thighs", "from head to", "damp hair clinging", "cheeks flushed",
    "cleavage", "suggestive", "provocative", "nsfw",
)

_POLICY_REPLACEMENTS = (
    (re.compile(r"刚(?:洗完澡|洗完|沐浴完|沐浴)"), "刚洗漱完，浴室里有水汽"),
    (re.compile(r"出水芙蓉"), "刚洗漱完"),
    (re.compile(r"(?:frame at most from head to thighs|framed? from head to thighs|from head to thighs|head to thighs)", re.I),
     "日常自拍构图"),
    (re.compile(r"damp hair clinging[^，,。;；]*", re.I), "头发微湿"),
    (re.compile(r"cheeks? flushed[^，,。;；]*", re.I), ""),
    (re.compile(r"(?:脸颊|脸蛋|脸上|皮肤|肌肤|锁骨|胸前|胸口|乳沟|大腿|小腿|双腿|光着脚|赤脚|脚踝|身体|身躯|臀部)"), ""),
    (re.compile(r"(?:潮红|泛红|脸红|红晕|微红)"), ""),
    (re.compile(r"(?:湿漉漉|湿哒哒|湿透|滴水|水珠)"), "水汽"),
    (re.compile(r"(?:贴身|紧贴|贴着|勾勒|曲线|身材|性感|妩媚|诱惑)"), ""),
    (re.compile(r"(?:裸露|半裸|赤身|裸体|nsfw|suggestive|provocative)", re.I), ""),
)


def sanitize_selfie_prompt(text, safe=False):
    """把自拍提示词洗成中性日常描述；safe=True 时连浴室/水汽/湿发也去掉。

    safe 版只保留人设、穿搭、场景和日常姿势：命中 content_policy_violation
    后用它重试，避免再撞同一批词。
    """
    value = str(text or "")
    for pattern, repl in _POLICY_REPLACEMENTS:
        value = pattern.sub(repl, value)
    if safe:
        for word in ("浴室", "洗澡", "沐浴", "水汽", "微湿", "湿漉漉", "湿"):
            value = value.replace(word, "")
    value = re.sub(r"\s*([，,。；;])\s*", r"\1", value)
    value = re.sub(r"(?:[，,]\s*){2,}", "，", value)
    value = value.strip("，,。；; ")
    return value


def policy_sensitive_hits(text):
    """返回提示词里命中的内容政策敏感词（测试/日志用）。"""
    body = str(text or "").lower()
    return [word for word in POLICY_SENSITIVE_WORDS if word.lower() in body]


#: 只在「她声称已经有图/图糊了」时命中；普通聊天提到照片不算。
PHOTO_CLAIM_PATTERNS = (
    re.compile(r"(?:图|图片|照片|相片|自拍)[^，,。！？!?；;\n]{0,8}(?:糊|模糊|花|不清|好看|不错|可以|在下面|发你|给你)"),
    re.compile(r"(?:这张|那张|刚才那张|刚拍的|刚照的)[^，,。！？!?；;\n]{0,8}(?:图|图片|照片|相片)"),
    re.compile(r"(?:拍好了|拍完了|拍到了|按下快门|咔嚓|拍给你了|发你了|发给你了|你看这张)"),
    re.compile(r"(?:凑合看|将就看|勉强看|糊了|拍糊|拍虚|没对焦)"),
    re.compile(r"(?:图|图片|照片)(?:在下面|来了|到了)"),
)


def looks_like_photo_claim(text):
    """这句话是不是在声称「已经有图了 / 图糊了 / 凑合看」。"""
    body = str(text or "")
    return any(pattern.search(body) for pattern in PHOTO_CLAIM_PATTERNS)


#: 没有照片链路证据时也明确是「翻车图」的说法（事故原话：图有点糊，凑合看吧）。
PHOTO_BROKEN_CLAIM_PATTERNS = (
    re.compile(r"(?:图|图片|照片|相片)[^，,。！？!?；;\n]{0,8}(?:糊|模糊|不清|花)"),
    re.compile(r"(?:凑合看|将就看|勉强看|糊了|拍糊|拍虚|没对焦)"),
)


def looks_like_broken_photo_claim(text):
    """这句是不是在说「图糊了/凑合看」这种明确翻车、必须有图的话。"""
    body = str(text or "")
    return any(pattern.search(body) for pattern in PHOTO_BROKEN_CLAIM_PATTERNS)


def strip_photo_claim(text):
    """删掉声称有图的句子；整段都是这种句子时返回空串（调用方决定发不发）。"""
    body = str(text or "")
    if not body or not looks_like_photo_claim(body):
        return body
    segments = re.split(r"([，,。！？!?；;\n])", body)
    kept = []
    for index in range(0, len(segments), 2):
        content = segments[index]
        delim = segments[index + 1] if index + 1 < len(segments) else ""
        if content and looks_like_photo_claim(content):
            continue
        if content or delim:
            kept.append(content + delim)
    result = "".join(kept)
    result = re.sub(r"^[，,。！？!?；;\s]+", "", result)
    result = re.sub(r"[，,；;\s]+$", "", result)
    return result.strip()


def view_suffix(selfie: bool) -> str:
    """返回该用哪一条视角后缀。"""
    return VIEW_SELFIE if selfie else VIEW_POV


def looks_like_her(text: str) -> bool:
    """这句话是不是要「她本人出镜」（自拍），而不是「她拍的环境」。"""
    text = str(text or "")
    if not text:
        return False
    if any(hint in text for hint in POV_HINTS):
        return False
    return any(hint in text for hint in SELFIE_HINTS)


#: 他点名要看脚/脚趾：单独的近景构图（普通自拍仍然不裁脚）
FOOT_CLOSEUP_HINTS = ("脚趾", "脚背", "看脚", "拍脚", "脚部", "小脚")
FOOT_CLOSEUP_PROMPT = (
    "自然生活照：她坐着，把脚自然伸向镜头，脚趾清晰可见；赤脚，画面里没有任何鞋子或拖鞋：不穿在脚上，也不放在脚边、不垫在脚下（bare feet, no shoes or slippers anywhere in frame）；正常手机拍摄距离，不是微距/极近特写，不要只拍脚部局部；可以带出家里的沙发、地毯、家具和温暖光线；脚自然放松，五个脚趾清晰完整、脚趾不粘连、"
    "脚踝自然、皮肤干净；画面只拍脚和小腿下段，不拍脸；背景是家里的沙发/毛毯，"
    "柔和室内光，手机随手拍质感"
)


def wants_foot_closeup(text):
    body = str(text or "")
    return any(word in body for word in FOOT_CLOSEUP_HINTS)


def foot_closeup_prompt(scene="", shoes=""):
    where = str(scene or "").strip() or "她此刻待的地方"
    shoe = str(shoes or "").strip() or "鞋"
    return ("自然生活照：她坐在" + where + "，把脚自然伸向镜头，脚趾清晰；"
            "她刚刚把" + shoe + "脱下来放在脚边/旁边（也可以是脱下来的袜子），露出赤脚和脚趾；"
            "鞋子不要还穿在脚上，脚趾要完整露出来；"
            "正常手机拍摄距离，不是微距/极近特写；背景自然带出" + where + "的桌面、地面、家具和光线；"
            "五个脚趾清晰完整、脚趾不粘连、脚踝自然")


SELFIE_FRAMING_FOOT = (
    "自然距离的手机随手拍：脚和腿自然在画面里，脚趾清晰；脱下来的鞋子/袜子可以放在脚边，但脚上不穿鞋袜；背景按当前场景自然带出（桌面/地面/家具/光线）；不是微距/极近特写，不要求露脸；五个脚趾清晰完整、脚趾不粘连、"
    "脚踝自然、皮肤干净；背景是家里的沙发/毛毯，柔和室内光，手机随手拍质感"
)


FOOT_CLOSEUP_NEGATIVE = (
    "脚还穿在鞋里，脚趾穿进鞋里，鞋子还穿在脚上，袜子在脚上，鞋和脚融合，鞋子变形，"
    "脚只从鞋里露出一半，脚趾被鞋挡住，shoes worn on feet, foot inside shoe, socks on feet, fused shoe" 
)
