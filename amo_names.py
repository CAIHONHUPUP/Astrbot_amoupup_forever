# -*- coding: utf-8 -*-
"""聊天回复的白名单人名守卫（2026-11-15 用户要求）。

她的世界只有这 7 个人：柳如烟、白凝冰、季博达、方圆、妈妈、爸爸、沈幼微（+ 老公/她本人）。
任何回复里出现名单外的「人名」（比如临时编的同事、朋友），一律打回重写；
重写仍不干净就换成中性词，绝不让编造的名字发出去、写进记忆。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("amo.names")

ROSTER = ("柳如烟", "白凝冰", "季博达", "方圆", "妈妈", "爸爸", "沈幼微")
NAME_SET = frozenset(ROSTER)
NICKNAMES = frozenset({"如烟", "凝冰", "博达", "幼微", "妈妈", "爸爸"})
SELF_ALIASES = frozenset({"老公", "亲爱的", "宝贝", "对象", "男朋友", "他", "你", "我", "她",
                          "自己", "人家"})

ROSTER_LINE = ("你认识的人只有：[%s]。严禁凭空捏造任何其他名字，"
               "不要提任何名单外的朋友、同事、亲戚、路人。" % "、".join(ROSTER))

#: 名单人物的固定关系/性别：别再让模型随手把「同事」换成任意名字，
#: 也别把男性写成暧昧对象（2026-11-20）。
ROLES = {
    "柳如烟": "闺蜜",
    "白凝冰": "同事",
    "季博达": "男同事（只限工作往来，不单独约会、不暧昧）",
    "方圆": "同事",
    "沈幼微": "朋友",
    "妈妈": "家人",
    "爸爸": "家人",
}
ROLE_LINE = "人物关系：" + "；".join("%s=%s" % (name, role) for name, role in ROLES.items()) + "。"


def strip_unbacked_names(text, context, fallback="同事"):
    """名单里的名字如果在素材里根本没出现，就换成中性称呼。

    2026-11-20：微事件原文是「同事讲了个冷笑话」，主动消息却写成
    「柳如烟刚讲了个冷笑话」；下一轮又变成「方圆」。素材里没出现过的名字
    一律不算数，避免编人名、也避免把某个人写成“神秘的他”。
    """
    body = str(text or "")
    ctx = str(context or "")
    for name in ROSTER:
        if name in body and name not in ctx:
            body = body.replace(name, fallback)
    return body

STOPWORDS = frozenset(""".split()
家里 家人 公司 单位 学校 商场 超市 菜场 店里 饭店 餐厅 食堂 楼下 楼上 隔壁 小区 医院 银行 地铁 公交
朋友 同事 领导 老板 经理 主管 总监 客户 甲方 乙方 保安 前台 保洁 服务员 厨子 同学 老师 医生 护士
房东 室友 邻居 物业 中介 快递 外卖 客服 司机 店员 客人 阿姨 叔叔 师傅 哥哥 姐姐 弟弟 妹妹 爷爷 奶奶
外婆 外公 亲戚 表姐 表哥 表妹 堂姐 闺蜜 死党 姐妹 兄弟 大家 他们 她们 我们 你们 别人 有人 对方
妈妈 爸爸 老公 老婆 对象 心里 时候 今天 昨天 明天 前天 后天 周末 周一 周二 周三 周四 周五 周六
周日 周年 周围 周报 上周 本周 下周 月底 月初 年头 年底 上午 下午 晚上 半夜 凌晨 早上 中午 傍晚
一会儿 一阵 一点 一起 刚才 最近 现在 以后 之前 后来 其实 反正 可能 大概 应该 真的 特别 有点 还有
就是 但是 所以 因为 如果 虽然 然后 而且 不过 觉得 知道 看见 听到 想着 说着 天气 心情 肚子 胃口
眼睛 身体 头发 衣服 鞋子 工资 房租 房价 股票 基金 余额 手机 电脑 电视 游戏 水电 花呗 信用卡
酒店 车站 机场 景区 公园 火锅店 奶茶店 咖啡店 理发店 药店 洗衣店 这家 那家 一家 两家 三家 几家 东西
""".split())

_C = r"[一-龥]"
_FUNCTION_CHARS = frozenset(
    "的了在是正还又就也都很大太要会能把被给和跟与我和你他她它这那哪今昨明早晚午天日月年号点分时"
    "上下前后里外中间边么吧呢啊呀哦嘛着自己俩位个些点次趟回")
_VERBS = "说|聊|吃|去|逛|来|问|约|请|走|接|送|借|还|骂|批评|结婚|生日|出差|相亲|逛街|玩"
_SAYS = "说|约我|问我|问起|告诉我|发微信|打电话|请我|带我|陪我|批评我|骂我|跟我|找我"
_TIMES = "今天|昨天|最近|刚刚|又|下周|明天|周末"
_PREFIX = "和|跟|与|给|陪|请|被|让|叫|找|对|向"

_REL_PATTERN = re.compile(
    r"(?:%s)(%s{3})(?=(?:%s))|(?:%s)(%s{2})(?=(?:%s))"
    % (_PREFIX, _C, _VERBS, _PREFIX, _C, _VERBS))
_SAY_PATTERN = re.compile(
    r"(%s{3})(?=(?:%s))|(%s{2})(?=(?:%s))" % (_C, _SAYS, _C, _SAYS))
_KNOWN = re.compile(r"(?:认识|名叫|叫|是)(%s{2,3})(?:是我的|是我|，|。|$)" % _C)

_SURNAMES = frozenset(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛"
    "奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余"
    "元卜顾孟平黄穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强"
    "贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯管卢莫房裘缪解应宗丁宣邓郁单杭洪包"
    "诸左石崔吉钮龚程嵇邢滑裴陆荣翁荀羊惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓"
    "蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖"
    "卓蔺屠蒙池乔阴胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍却璩桑桂濮牛寿通边扈燕冀郏浦尚农温别"
    "庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧殳沃利蔚越夔"
    "隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公"
)
_SUR_CLASS = "[" + "".join(sorted(_SURNAMES)) + "]"
_NAMEISH = _SUR_CLASS + _C + "{1,2}"
_TIME_PATTERN = re.compile(r"(%s)(?=(?:%s))" % (_NAMEISH, _TIMES))
_LIST_PATTERN = re.compile(r"(%s)(?=(?:和|跟|与|、))" % _NAMEISH)

#: 常见词/动补结构：长得像「姓+名」但不是人名。
#: 宁可少拦一个编造的名字，也不许把「我明白」「还是老样子」这类日常句改坏。
_NOT_NAMES = frozenset("""
明白 知道 方便 高兴 开心 安静 安全 常常 常见 平常 实在 时间 成本 家里 家人
后来 后面 白天 马上 一直 一起 一样 一定 一般 一边 文章 金额 花香 石头 方向
方法 方案 方面 关系 关于 对于 由于 因为 所以 但是 如果 虽然 而且 不过 然后
已经 曾经 将来 将近 刚刚 刚才 现在 目前 以后 以前 之后 之前 时候 什么 怎么
为什么 多少 几个 这些 那些 这个 那个 这样 那样 大家 人家 别人 自己 我们
你们 他们 她们 它们 咱们 身边 差不多 起来 下来 出来 回来 过来 进去 上去
下去 出去 回去 过去 看到 看见 听说 说起 想到 记得 觉得 满意 麻烦 照顾
""".split())

#: 句尾否定/语气词、动补结尾：带这些字的一律不当人名（「笑笑不」「吃完」）
_NAME_STOP_CHARS = frozenset("不没别吗呢吧啊呀嘛哦哈嗯的了着过完掉好到")


def _suspect(token):
    token = str(token or "").strip()
    # 人名至少 2 字、最多 4 字
    if not (2 <= len(token) <= 4):
        return False
    if token in NAME_SET:
        return False
    if token in SELF_ALIASES or token in STOPWORDS or token in NICKNAMES:
        return False
    if token in _NOT_NAMES:
        return False
    # 名单外的人名按「姓 + 名」判：姓氏表里没有这个首字就不算名字。
    # 这一条挡掉了「笑笑不」「吃完」「老样子」这类整句误报。
    if token[:1] not in _SURNAMES:
        return False
    if token[:1] in "她他你我它咱您":
        return False
    if any(char in _FUNCTION_CHARS for char in token):
        return False
    if any(char in _NAME_STOP_CHARS for char in token):
        return False
    for cut in (token[1:], token[2:]):
        if cut and cut in STOPWORDS:
            return False
    return True


def foreign_names(text):
    """找出疑似名单外人名（去重保序）。"""
    raw = str(text or "")
    if not raw.strip():
        return []
    hits = []
    for match in _REL_PATTERN.finditer(raw):
        hits.append(match.group(1) or match.group(2) or "")
    for match in _SAY_PATTERN.finditer(raw):
        hits.append(match.group(1) or match.group(2) or "")
    for match in _TIME_PATTERN.finditer(raw):
        hits.append(match.group(1) or "")
    for match in _LIST_PATTERN.finditer(raw):
        hits.append(match.group(1) or "")
    for match in _KNOWN.finditer(raw):
        hits.append(match.group(1) or "")
    out = []
    for item in hits:
        if item and _suspect(item) and item not in out:
            out.append(item)
    return out


def sanitize(text, npc=""):
    """兜底：把名单外的名字换成「有人」，句子还能读，且绝不带名字。"""
    value = str(text or "")
    hits = foreign_names(value)
    if not hits:
        return value
    npc = str(npc or "")
    logger.warning("[amo] 拦截名单外人名 %s | %s", hits, value[:80])
    if npc and len(hits) == 1 and npc not in hits:
        fixed = value.replace(hits[0], npc)
        if not foreign_names(fixed):
            return fixed
    for hit in hits:
        value = value.replace(hit, "有人")
    return value
