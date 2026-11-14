# -*- coding: utf-8 -*-
"""日程主干生成引擎。

设计目标（对应 5.1.0 重构）：

1. 每天只生成一次“主干”：24 个整点槽位，每个槽位都有
   start/end/activity/place/zone/category/transit/vehicle/mood。
2. 主干先由确定性规则保证物理连贯（不能瞬移、不能情绪突变），
   再让大模型只改写“这一小时在做什么”，不允许改时间、地点和类别。
3. 三级 10 分钟细分彻底废弃；点击二级卡片时，由 main.py 调用
   generate_diary() 实时生成这一小时的第一人称小日记。
4. 休息日天气好时优先读取 scenic_spots.json 里的贵阳/贵州景区；
   周末开车出行固定绑定角色资产（黑色奔驰 C 级），并带堵车/找车位细节。
5. 突发事件：
   - 休息日：老板临时叫去公司改 Bug / 朋友临时叫出去聚餐；
   - 工作日：被公司外派到插件内置的随机省份。
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

import asyncio
import hashlib
import json
import random
import re
from datetime import date, datetime, timedelta

try:
    from .schedule_holiday import (
        is_long_holiday,
        pick_amusement_spot,
        pick_holiday_plan,
    )
    from .schedule_segments import (
        amusement_segments,
        homebody_segments,
        shopping_segments,
    )
    from .schedule_scenic import scenic_segments
    from .schedule_day_segments import (
        indoor_rest_segments,
        outing_segments,
        workday_segments,
    )
    from .schedule_rest_segments import (
        business_trip_segments,
        overtime_segments,
        period_rest_segments,
        sick_rest_segments,
    )
    from .schedule_social_segments import friend_dinner_segments
    from .schedule_swim import swim_segments
except ImportError:  # 直接按文件加载时
    from schedule_holiday import (
        is_long_holiday,
        pick_amusement_spot,
        pick_holiday_plan,
    )
    from schedule_segments import (
        amusement_segments,
        homebody_segments,
        shopping_segments,
    )
    from schedule_scenic import scenic_segments
    from schedule_day_segments import (
        indoor_rest_segments,
        outing_segments,
        workday_segments,
    )
    from schedule_rest_segments import (
        business_trip_segments,
        overtime_segments,
        period_rest_segments,
        sick_rest_segments,
    )
    from schedule_social_segments import friend_dinner_segments
    from schedule_swim import swim_segments

# 统一时区：日程的 generated_at / 跨天日期按配置时区算，不吃服务器本地时区。
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .clock import now as _clock_now, today as _clock_today
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .clock import now as _clock_now, today as _clock_today
    except Exception:
        try:
            from clock import now as _clock_now, today as _clock_today
        except Exception:
            def _clock_now(*args, **kwargs):
                return datetime.now()

            def _clock_today(*args, **kwargs):
                return date.today()

MOOD_KEYS = ("开心", "平静", "郁闷", "生气", "暴躁", "委屈", "疲惫", "焦虑", "兴奋", "依恋")
MOOD_BASE = {
    "开心": 55, "平静": 60, "郁闷": 18, "生气": 8, "暴躁": 6, "委屈": 20,
    "疲惫": 28, "焦虑": 18, "兴奋": 30, "依恋": 45,
}

# 外派省份：全部写在插件里，按日期种子随机，不依赖任何外部接口。
PROVINCE_CITIES = {
    "四川省": "成都",
    "云南省": "昆明",
    "湖南省": "长沙",
    "广西壮族自治区": "南宁",
    "广东省": "广州",
    "湖北省": "武汉",
    "陕西省": "西安",
    "浙江省": "杭州",
    "江苏省": "南京",
    "福建省": "福州",
    "江西省": "南昌",
    "安徽省": "合肥",
    "河南省": "郑州",
    "山东省": "济南",
    "河北省": "石家庄",
    "山西省": "太原",
    "辽宁省": "沈阳",
    "吉林省": "长春",
    "黑龙江省": "哈尔滨",
    "甘肃省": "兰州",
    "青海省": "西宁",
    "海南省": "海口",
    "新疆维吾尔自治区": "乌鲁木齐",
    "内蒙古自治区": "呼和浩特",
    "宁夏回族自治区": "银川",
    "西藏自治区": "拉萨",
}

POSITIVE_LABELS = ("开心", "兴奋", "黏人")
def _city_of(ctx):
    """当前城市；没配城市就是空串（不再兜底写死某个城市）。"""
    return str((ctx or {}).get("city") or "").strip()


def _with_city(ctx, text):
    """把词尾拼在城市后面；没有城市就只留这个词（北站/车站这类）。"""
    city = _city_of(ctx)
    text = str(text or "")
    return (city + text) if city else text


def _place_of(ctx, name):
    """「城市·地点」；没有城市就只写地点，避免出现「·饭店」这种半截字符串。"""
    city = _city_of(ctx)
    name = str(name or "").strip()
    if not name:
        return city
    return (city + "·" + name) if city else name


CITY_POOL = [
    "贵阳", "上海", "杭州", "成都", "重庆", "长沙", "昆明", "广州",
    "深圳", "南京", "武汉", "西安", "北京", "苏州", "天津", "青岛",
]
DISTRICT_MAP = {
    "贵阳": ["观山湖区", "云岩区", "南明区", "花溪区", "乌当区"],
    "上海": ["徐汇区", "静安区", "浦东新区", "长宁区", "杨浦区"],
    "杭州": ["西湖区", "滨江区", "拱墅区", "余杭区"],
    "成都": ["武侯区", "锦江区", "高新区", "成华区"],
    "重庆": ["渝中区", "江北区", "南岸区", "渝北区"],
    "长沙": ["岳麓区", "芙蓉区", "天心区", "开福区"],
    "昆明": ["五华区", "盘龙区", "西山区", "官渡区"],
    "广州": ["天河区", "越秀区", "海珠区", "番禺区"],
    "深圳": ["南山区", "福田区", "罗湖区", "宝安区"],
    "南京": ["鼓楼区", "玄武区", "建邺区", "江宁区"],
    "武汉": ["武昌区", "洪山区", "江汉区", "汉阳区"],
    "西安": ["雁塔区", "碑林区", "莲湖区", "高新区"],
}
HOME_STYLES = ["小窝", "公寓", "出租屋", "家"]
COMPANY_POOL = ["互联网公司", "设计工作室", "广告公司", "贸易公司", "医院", "学校", "书店", "咖啡店"]
CAR_POOL = [
    "黑色奔驰C级", "白色大众高尔夫", "银色特斯拉Model 3",
    "红色Mini Cooper", "深灰色奥迪A4L", "蓝色比亚迪海豹",
    "白色丰田凯美瑞", "黑色沃尔沃S60",
]
CAR_BRANDS = ["奔驰", "宝马", "奥迪", "特斯拉", "比亚迪", "大众", "本田", "丰田", "Mini", "保时捷", "路虎", "沃尔沃", "吉利", "蔚来"]

NEGATIVE_LABELS = ("低落", "烦躁", "委屈", "焦虑", "疲惫")

CATEGORY_MOOD = {
    "sleep": {"平静": 2.0, "焦虑": -2.0, "疲惫": -1.0, "生气": -1.0},
    "work": {"疲惫": 2.2, "焦虑": 1.4, "开心": -0.4},
    "study": {"疲惫": 1.4, "焦虑": 1.0},
    "commute": {"疲惫": 1.4, "暴躁": 0.8, "开心": -0.5},
    "meal": {"开心": 1.8, "疲惫": -1.2, "平静": 0.8},
    "rest": {"平静": 1.4, "疲惫": -1.4, "焦虑": -0.8},
    "play": {"开心": 2.4, "兴奋": 1.2, "疲惫": -0.8},
    "social": {"开心": 3.0, "依恋": 1.6, "疲惫": -1.0},
    "life": {"平静": 0.8, "疲惫": 0.4},
}

TECH_WORK_BANK = [
    "开电脑看邮件", "写代码改需求", "开会对齐方案", "改 Bug",
    "回消息处理需求", "摸鱼喝咖啡", "整理文档", "和同事讨论接口",
]
OFFICE_WORK_BANK = [
    "开电脑看邮件", "整理文档", "开会过方案", "回消息处理事情",
    "做表格对数据", "和同事讨论事情", "泡杯咖啡续命", "写周报",
]
STUDENT_BANK = [
    "上课记笔记", "在图书馆自习", "赶作业", "做实验", "准备考试",
    "在教室听讲座", "小组讨论", "背单词",
]
TEACHER_BANK = [
    "备课", "上课讲题", "批作业", "开教研会", "找学生谈话",
    "整理课件", "监考",
]
MED_BANK = [
    "查房", "写病历", "上门诊", "交接班", "看检查结果", "处理医嘱",
]
SALES_BANK = [
    "给客户打电话", "整理报价", "跑客户", "开销售会", "回客户消息",
    "准备方案", "跟进合同",
]
CREATIVE_BANK = [
    "改稿", "画图", "写文案", "对需求", "找参考", "调细节",
    "和甲方沟通", "导出终稿",
]
SERVICE_BANK = [
    "备餐", "接待客人", "收拾台面", "盘点库存", "交接班",
    "处理订单", "擦桌子",
]
ADMIN_BANK = [
    "整理文件", "做报表", "开会", "写材料", "对接部门",
    "核对数据", "回复邮件",
]

# 职业关键词 -> 工作活动库；匹配不到的走通用办公（不再默认程序员）
OCCUPATION_BANKS = (
    (("程序员", "开发", "架构", "运维", "测试", "技术", "科技", "算法", "前端", "后端", "软件"), TECH_WORK_BANK),
    (("学生", "研究生", "大学生", "高中生", "初中生", "考研"), STUDENT_BANK),
    (("老师", "教师", "教授", "讲师", "教练", "园长"), TEACHER_BANK),
    (("医生", "护士", "医师", "药师", "医护", "牙医"), MED_BANK),
    (("销售", "商务", "市场", "采购", "客户经理", "业务员", "公关"), SALES_BANK),
    (("设计", "文案", "编辑", "作者", "画师", "摄影师", "剪辑", "自媒体", "编剧"), CREATIVE_BANK),
    (("厨师", "餐饮", "店长", "服务员", "咖啡师", "店员", "收银"), SERVICE_BANK),
    (("会计", "财务", "行政", "人事", "公务员", "文员", "法务", "律师", "hr"), ADMIN_BANK),
)

# 会出差/跑外勤的职业
TRAVEL_KEYWORDS = ("销售", "商务", "市场", "采购", "项目经理", "顾问", "客户", "出差", "外勤")


def work_bank_for(world):
    """按职业挑工作活动库；认不出来就用通用办公，不再默认「写代码/改 Bug」。"""
    world = world or {}
    text = (str(world.get("occupation") or "") + " " + str(world.get("workplace") or "")).lower()
    for keywords, bank in OCCUPATION_BANKS:
        for keyword in keywords:
            if keyword in text:
                return bank
    return OFFICE_WORK_BANK


def travels_for_work(world):
    text = (str((world or {}).get("occupation") or "") + " " + str((world or {}).get("workplace") or "")).lower()
    return any(keyword in text for keyword in TRAVEL_KEYWORDS)


def occupation_kind(world):
    """粗分类，用来决定突发事件文案。"""
    world = world or {}
    text = (str(world.get("occupation") or "") + " " + str(world.get("workplace") or "")).lower()
    for keywords, kind in (
        (("学生", "研究生", "大学生", "高中生", "初中生", "考研"), "student"),
        (("老师", "教师", "教授", "讲师", "教练", "园长"), "teacher"),
        (("医生", "护士", "医师", "药师", "医护", "牙医"), "med"),
        (("程序员", "开发", "架构", "运维", "测试", "技术", "科技", "算法", "前端", "后端", "软件"), "tech"),
    ):
        for keyword in keywords:
            if keyword in text:
                return kind
    return "office"


#: 活动里有这些词，就说明这一小时在吃/在买吃的。
_FOOD_ACTIVITY_WORDS = (
    "便利店", "买饭", "买啥", "买东西", "买杯", "买点", "吃", "饭", "外卖",
    "扒两口", "垫两口", "点单", "点餐", "餐",
)
#: 细节里有这些词，就说明这一小时在工作/开会。
_MEETING_DETAIL_WORDS = ("会议", "开会", "过方案", "对齐方案")
#: 活动是睡觉/刚醒。
_SLEEP_ACTIVITY_WORDS = ("睡觉", "睡着了", "睡沉", "赖床", "被窝", "眯", "躺")
#: 工作类细节（和「在睡觉」对不上）。
_WORK_DETAIL_WORDS = ("周报", "需求", "改稿", "数据", "邮件", "咖啡续", "会议", "开会", "第三版")


def detail_conflicts(activity, detail):
    """这一小时的「细节」和「活动」明显不是一回事时返回 True。

    真机案例（2026-11-10 11:00）：activity 被模型润色成「在便利店纠结买啥」，
    但 detail 还是按 zone=office/category=work 随机挑的「被拉进临时会议」；
    小时日记和面板于是同时出现「在便利店」和「被拉进临时会议」两套剧情。
    """
    activity = str(activity or "")
    detail = str(detail or "")
    if not activity or not detail:
        return False
    if any(word in activity for word in _FOOD_ACTIVITY_WORDS) and any(
        word in detail for word in _MEETING_DETAIL_WORDS
    ):
        return True
    if any(word in activity for word in _SLEEP_ACTIVITY_WORDS) and any(
        word in detail for word in _WORK_DETAIL_WORDS
    ):
        return True
    return False

COMMUTE_BANK = [
    "挤地铁去公司", "在车上听歌", "等红灯", "换乘", "堵在路上",
    "刷手机看路况", "快步往公司走",
]
LUNCH_BANK = [
    "下楼吃牛肉粉", "和同事拼饭", "点外卖", "吃碗牛肉面",
    "去便利店随便对付一口", "边吃边刷手机",
]
BREAKFAST_BANK = [
    "煎个蛋配牛奶", "豆浆配油条", "楼下吃碗粉", "煮点速冻饺子",
    "面包配咖啡", "随便对付一口",
]
REST_BANK = [
    "瘫在沙发上", "刷手机", "追剧", "发呆", "看短视频",
    "窝着不想动", "听歌",
]
DINNER_BANK = [
    "煮面", "点外卖", "炒两个菜", "下楼吃小馆子", "喝碗热汤",
]
HOME_LIFE_BANK = [
    "刷牙洗脸", "煎蛋热牛奶", "收拾包", "换衣服", "洗衣服",
    "扫地", "洗澡", "吹头发", "护肤", "收拾明天要带的东西",
]
#: 晚上/睡前那一小时，不应该出现「煎蛋热牛奶 / 收拾包」。
NIGHT_LIFE_BANK = [
    "洗澡", "吹头发", "护肤", "敷面膜", "换睡衣", "刷牙",
]
SCENIC_BANK = [
    "排队买票", "拍照", "慢慢逛", "找地方坐一会儿", "买奶茶",
    "看路边的小摊", "跟着人群往前走",
]
#: 休息日偶尔去玩水（不是每周都去）：游泳馆 / 海边 / 水上乐园 随机挑一个
SWIM_PLACES = (
    ("游泳馆", "在泳池里游了几圈", "水有点凉，游一会儿就上岸歇"),
    ("海边", "在海边踩浪、玩水", "浪打过来，裤子湿了一截"),
    ("水上乐园", "在水上乐园排队玩滑道", "排队排了好久，滑下来那一下特别爽"),
)
SWIM_DETAILS = (
    "水有点凉，下去那一下打了个激灵", "游了几圈就喘，趴在池边歇",
    "被晒得有点晕，赶紧补防晒", "人比想象中多，等了好久才轮到",
)
ROAD_BANK = [
    "开车去景区", "堵在路上", "找停车位", "导航绕路", "等红绿灯",
]
TAXI_BANK = [
    "打车", "在网约车后排刷手机", "看着窗外的街景", "催司机快一点",
]
STATION_BANK = [
    "进站候车", "检票上车", "在候车厅买水", "看大屏找检票口",
]
HOTEL_BANK = [
    "在酒店整理资料", "躺床上刷手机", "洗澡", "给手机充电", "看明天的安排",
]
INDOOR_BANK = [
    "逛展", "看电影", "逛街", "在咖啡店坐着", "看橱窗",
]


def clamp(value, low=0.0, high=100.0):
    try:
        value = float(value)
    except Exception as _amo_exc:
        _note_swallowed("schedule_engine.clamp", _amo_exc)
        return float(low)
    return max(float(low), min(float(high), value))


def md5_int(text):
    return int(hashlib.md5(str(text).encode("utf-8")).hexdigest()[:8], 16)


def parse_hhmm(value, default=0):
    try:
        parts = str(value).strip().split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception as _amo_exc:
        _note_swallowed("schedule_engine.parse_hhmm", _amo_exc)
        return default


def fmt_hhmm(minutes):
    """把分钟格式化成 HH:MM；1440 输出 24:00（用于 23:00-24:00 闭环）。"""
    try:
        minutes = int(minutes)
    except Exception as _amo_exc:
        _note_swallowed("schedule_engine.fmt_hhmm", _amo_exc)
        minutes = 0
    if minutes >= 24 * 60:
        return "24:00"
    if minutes < 0:
        minutes = 0
    return "%02d:%02d" % (minutes // 60, minutes % 60)


def json_extract(raw):
    """从模型输出里尽量抠出 JSON，容忍 ```json 包裹和前后废话。"""
    text = str(raw or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start >= 0 and end > start:
            chunk = text[start:end + 1]
            try:
                return json.loads(chunk)
            except Exception as _amo_exc:
                _note_swallowed("schedule_engine.json_extract", _amo_exc)
                continue
    try:
        return json.loads(text)
    except Exception as _amo_exc:
        _note_swallowed("schedule_engine.json_extract", _amo_exc)
        return None


class ScheduleEngine:
    """把“状态 -> 主干 -> 可选润色 -> 动态日记”拆开。"""

    def __init__(self, state):
        self.state = state
        self.p = state.p

    # ------------------------------------------------------------------
    # 0. 角色位置/资产：配置优先，其次人格提示词/世界观，最后自动随机
    # ------------------------------------------------------------------
    def resolve_profile(self, world, persona="", seed=""):
        """把“城市/住址/公司/车”解析出来，并在缺失时自动生成一套稳定值。"""
        world = world or {}
        text = "\n".join([
            str(persona or ""),
            str(world.get("city") or ""),
            str(world.get("workplace") or ""),
            str(world.get("daily_life") or ""),
            str(world.get("world_rules") or ""),
            str(world.get("summary") or ""),
        ])
        cached: dict[Any, Any] = {}
        try:
            cached = self.state.db.kv_get("schedule_profile", {}) or {}
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine.resolve_profile", _amo_exc)
            cached = {}
        if not isinstance(cached, dict):
            cached = {}
        city = str(self.p.cfg_str("schedule_city", "") or "").strip()
        if not city:
            city = self._extract_city(text)
        if not city:
            city = str(world.get("city") or "").strip()
        if not city or city in ("城市", "未知", "某市"):
            # 配置/人格/世界观都没有城市就留空：日程骨架走「不写具体城市」的分支，
            # 不再从 CITY_POOL 随机编一个；旧档案里的城市也不沿用（否则「清空城市」清不掉）
            city = ""
        if cached.get("city") and cached.get("city") != city:
            cached = {}

        home = str(self.p.cfg_str("schedule_home", "") or "").strip()
        if not home:
            home = self._extract_home(text)
        if not home:
            home = str(cached.get("home") or "").strip()
        if not home:
            home = self._random_home(city, seed)

        workplace = str(self.p.cfg_str("schedule_workplace", "") or "").strip()
        if not workplace:
            workplace = self._extract_work(text)
        if not workplace:
            world_work = str(world.get("workplace") or "").strip()
            if world_work and world_work not in ("公司", "单位", "工作地点", "上班"):
                workplace = world_work
        if not workplace:
            workplace = str(cached.get("workplace") or "").strip()
        if not workplace:
            workplace = self._random_workplace(city, seed)

        # 角色资产：一个字段同时认车/房/奢侈品。车用来开，房用来住，其余当"他送的东西"。
        assets_cfg = str(self.p.cfg_str("schedule_car", "") or "").strip()
        cfg_car, cfg_home, cfg_assets = self._split_assets(assets_cfg)
        car = cfg_car
        if not car:
            car = self._extract_car(text)
        if not car:
            car = str(cached.get("car") or "").strip()
        if not car:
            car = CAR_POOL[md5_int(str(world.get("name") or "") + "|car") % len(CAR_POOL)]
        if cfg_home:
            home = cfg_home

        base = {
            "city": city,
            "home": home,
            "workplace": workplace,
            "car": car,
        }
        try:
            if cached != base:
                self.state.db.kv_set("schedule_profile", base)
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine.resolve_profile", _amo_exc)
        profile = dict(base)
        profile["has_gift_car"] = False
        profile["has_gift_home"] = False
        try:
            gift = getattr(self.p, "gift", None)
            if gift is not None:
                car_gift = gift.asset_override("vehicle")
                if car_gift:
                    profile["car"] = car_gift
                    profile["has_gift_car"] = True
                home_gift = gift.asset_override("home")
                if home_gift:
                    profile["has_gift_home"] = True
                    profile["home"] = (home_gift if str(home_gift) not in ("房", "房子", "住宅", "楼", "小区")
                                       else "新家")
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine.resolve_profile", _amo_exc)
        return profile

    @staticmethod
    def _split_assets(value):
        """把「角色资产」拆成 车 / 房 / 其他；支持顿号、逗号、分号分隔。"""
        text = str(value or "").strip()
        if not text:
            return "", "", ""
        parts = [p.strip() for p in re.split(r"[、,，;；/|]+", text) if p.strip()]
        car_words = ("奔驰", "宝马", "奥迪", "大众", "丰田", "本田", "保时捷", "特斯拉",
                     "比亚迪", "凯迪拉克", "沃尔沃", "路虎", "捷豹", "雷克萨斯", "蔚来",
                     "理想", "小鹏", "吉利", "五菱", "车")
        home_words = ("房", "别墅", "公寓", "住宅", "大平层", "洋房", "小区", "楼")
        cars, homes, others = [], [], []
        for part in parts:
            if any(word in part for word in home_words):
                homes.append(part)
            elif any(word in part for word in car_words):
                cars.append(part)
            else:
                others.append(part)
        return "、".join(cars), "、".join(homes), "、".join(others)

    @staticmethod
    def _extract_city(text):
        text = str(text or "")
        for city in CITY_POOL:
            if city in text:
                return city
        return ""

    @staticmethod
    def _extract_home(text):
        text = str(text or "")
        patterns = (
            r"(?:住在|家住在|家在|住址在|家在)([^，。；\n]{2,20})",
            r"(?:我家在|我住在)([^，。；\n]{2,20})",
        )
        for pattern in patterns:
            matched = re.search(pattern, text)
            if not matched:
                continue
            value = str(matched.group(1) or "").strip(" 的了是在")
            if value and "公司" not in value and "上班" not in value:
                return value[:20]
        return ""

    @staticmethod
    def _extract_work(text):
        text = str(text or "")
        patterns = (
            r"(?:在|就职于|任职于|工作在)([^，。；：（）「」“”\n]{2,24})(?:上班|工作|任职|实习)",
            r"([^，。；：（）「」“”\n]{2,24})(?:上班|工作)",
        )
        for pattern in patterns:
            matched = re.search(pattern, text)
            if not matched:
                continue
            value = str(matched.group(1) or "").strip(" 的了是在")
            if value and "家" not in value and "住" not in value:
                return value[:24]
        return ""

    @staticmethod
    def _extract_car(text):
        text = str(text or "")
        for brand in CAR_BRANDS:
            if brand in text:
                return brand
        return ""

    @staticmethod
    def _random_home(city, seed):
        rng = random.Random(md5_int(str(city or "") + "|home|" + str(seed or "")))
        if not str(city or "").strip():
            return rng.choice(HOME_STYLES)
        districts = DISTRICT_MAP.get(str(city or "")) or ["市区"]
        return rng.choice(districts) + "·" + rng.choice(HOME_STYLES)

    @staticmethod
    def _random_workplace(city, seed):
        rng = random.Random(md5_int(str(city or "") + "|work|" + str(seed or "")))
        if not str(city or "").strip():
            return rng.choice(COMPANY_POOL)
        return rng.choice(COMPANY_POOL) + "（" + str(city or "市区") + "）"

    # ------------------------------------------------------------------
    # 1. 当天上下文
    # ------------------------------------------------------------------
    def build_context(self, day, meta, weather, world, mood, period, health, persona="", salt=""):
        seed_text = "%s|%s" % (day, salt)
        seed = md5_int(seed_text)
        profile = self.resolve_profile(world, persona, seed=str(seed))
        city = profile["city"]
        home = profile["home"]
        workplace = profile["workplace"]
        car = profile["car"]
        _, _, cfg_assets = self._split_assets(self.p.cfg_str("schedule_car", "") or "")
        yesterday = self.yesterday_summary(day)
        good_weather = self._good_weather(weather)
        event = self._pick_event(day, meta, weather, health, period, seed, world)
        holiday_plan = self._pick_holiday_plan(
            day, meta, weather, health, period, event, good_weather, seed, city,
        )
        if holiday_plan is not None:
            scenic = holiday_plan.get("scenic")
            swim = holiday_plan.get("swim")
        else:
            scenic = self._pick_scenic(day, meta, weather, health, period, event,
                                       good_weather, seed, city)
            swim = None
            if not scenic:
                # 一天只安排一件事：去景区就不去玩水，反之亦然
                swim = self._pick_swim(day, meta, weather, health, period, event,
                                       good_weather, seed, city)

        mood_now = {key: clamp(mood.get(key, MOOD_BASE[key])) for key in MOOD_KEYS}
        return {
            "version": "backbone-v5",
            "day": day,
            "meta": dict(meta or {}),
            "weather": dict(weather or {}),
            "world": dict(world or {}),
            "city": city,
            "home": home,
            "workplace": workplace,
            "car": car,
            "has_gift_car": bool(profile.get("has_gift_car")),
            "has_gift_home": bool(profile.get("has_gift_home")),
            "assets": self._gift_assets(cfg_assets),
            "worn": self._gift_worn(),
            "gift_log": self._gift_log(),
            "mood": mood_now,
            "period": dict(period or {}),
            "health": dict(health or {}),
            "yesterday": yesterday,
            "good_weather": good_weather,
            "event": event,
            "holiday_plan": holiday_plan,
            "scenic": scenic,
            "swim": swim,
            "profile": profile,
            "location": (event.get("where") if event.get("kind") == "business_trip" else city),
            "seed": seed,
            "generated_at": _clock_now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _gift_assets(self, extra=""):
        """他送的大件（车/房/贵重）+ 配置里的角色资产，写进日程上下文。"""
        items = []
        extra = str(extra or "").strip()
        if extra:
            items.append(extra)
        try:
            gift = getattr(self.p, "gift", None)
            if gift is not None:
                text = gift.asset_text()
                if text:
                    items.append(text)
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine._gift_assets", _amo_exc)
        return "、".join([item for item in items if item])

    def _gift_worn(self):
        """他送的小件里能穿戴的（手表/围巾/包/口红），写进日程上下文。"""
        try:
            gift = getattr(self.p, "gift", None)
            if gift is not None:
                return gift.worn_text()
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine._gift_worn", _amo_exc)
        return ""

    @staticmethod
    def _gift_mention_hours(ctx):
        """每天最多两个整点允许在日记里提他送的礼物。

        按日期固定，避免每小时都在写"捏着挂件/戴着围巾"，把礼物写成了强迫症。
        """
        day = str((ctx or {}).get("day") or "")
        seed = str((ctx or {}).get("seed") or "")
        rnd = random.Random("%s|%s" % (day, seed))
        pool = list(range(9, 22))
        first = rnd.choice(pool)
        second = rnd.choice([h for h in pool if abs(h - first) >= 2] or pool)
        return {first, second}

    @staticmethod
    def _use_gift_today(ctx, label):
        """同一天同一件东西固定选择：六成用他送的，四成用自己的。"""
        day = str(ctx.get("day") or "")
        if not day:
            return True
        return md5_int(day + "|wear|" + label) % 10 < 6

    @staticmethod
    def _wear_phrase(label, gifted):
        if label == "包":
            return "背着包（他送的）" if gifted else "背着包"
        return ("戴了%s（他送的）" if gifted else "戴了%s") % label

    def _apply_gift_effects(self, items, ctx):
        """出门时写穿戴：他送的标「他送的」；自己的只写通用名。"""
        temp = (ctx.get("weather") or {}).get("temp")
        labels = []
        gift = getattr(self.p, "gift", None)
        if gift is not None:
            try:
                labels = gift.wear_labels(temp)
            except Exception as _amo_exc:
                _note_swallowed("schedule_engine.ScheduleEngine._apply_gift_effects", _amo_exc)
                labels = []
        try:
            temp_value = float(temp) if temp is not None else None
        except (TypeError, ValueError):
            # 和风缺字段/旧 kv 里的 "N/A" 会让 float() 抛错，整个主干生成失败、
            # 退回兜底日程（当天活动/地点/穿戴全废）
            temp_value = None
        own = ["围巾"] if temp_value is not None and temp_value <= 20 else []
        if not labels and not own:
            return items
        extras = []
        for label in labels:
            extras.append(self._wear_phrase(label, self._use_gift_today(ctx, label)))
        for label in own:
            if label not in labels:
                extras.append(self._wear_phrase(label, False))
        suffix = "；".join(extras)
        for item in items:
            if item.get("category") not in ("commute", "play"):
                continue
            place = str(item.get("place") or "")
            if not place or place == "家" or place.startswith("家"):
                continue
            detail = str(item.get("detail") or "").strip()
            if suffix and suffix not in detail:
                item["detail"] = (detail + "；" + suffix).strip("；")
        return items

    def _gift_log(self):
        """带日期的礼物记录，让日程日记有迹可查。"""
        try:
            gift = getattr(self.p, "gift", None)
            if gift is not None:
                return gift.gift_log_text()
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine._gift_log", _amo_exc)
        return ""

    def yesterday_summary(self, day):
        """读取昨日遗留：熬夜、睡眠不足、生病，今天必须疲惫。"""
        try:
            target = date.fromisoformat(str(day))
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine.yesterday_summary", _amo_exc)
            target = _clock_today()
        prev_day = (target - timedelta(days=1)).isoformat()
        items: list[Any] = []
        try:
            items = self.state.schedule.get(prev_day) or self.state.db.get_schedule(prev_day) or []
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine.yesterday_summary", _amo_exc)
            items = []
        if not isinstance(items, list):
            items = []
        sleep_hours = 0
        late_night = False
        for item in items:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category") or "")
            activity = str(item.get("activity") or "")
            if category == "sleep":
                sleep_hours += 1
            start = parse_hhmm(item.get("start"), 0)
            if 0 <= start < 4 * 60 and category not in ("sleep", ""):
                late_night = True
            if any(word in activity for word in ("熬夜", "通宵", "失眠", "加班到")):
                late_night = True
        if items and sleep_hours and sleep_hours < 6:
            late_night = True
        note = ""
        if late_night:
            note = "昨晚熬到很晚，今天一整天都容易累、注意力不集中"
        elif sleep_hours and sleep_hours <= 6:
            note = "昨晚睡得不太够，今天起床有点费劲"
        return {
            "day": prev_day,
            "late_night": late_night,
            "sleep_hours": sleep_hours,
            "note": note,
        }

    def _good_weather(self, weather):
        weather = weather or {}
        tag = str(weather.get("tag") or "").lower()
        text = str(weather.get("text") or "")
        if tag in ("rain", "storm", "snow", "thunder", "fog"):
            return False
        if any(word in text for word in ("雨", "雪", "雷", "雾", "沙尘")):
            return False
        try:
            temp = float(weather.get("temp", 20))
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine._good_weather", _amo_exc)
            temp = 20.0
        return 5.0 <= temp <= 34.0

    # 各职业的「临时加班」文案
    OVERTIME_NOTES = {
        "tech": ("临时加班", "线上出问题了，被叫去公司处理"),
        "student": ("临时加课", "老师临时加了两节课，晚上才回"),
        "teacher": ("临时代课", "学校临时安排代课/监考，走不开"),
        "med": ("临时排班", "科室临时排了班，得顶上去"),
        "office": ("临时加班", "公司临时有事，被叫过去帮忙"),
    }

    def _pick_event(self, day, meta, weather, health, period, seed, world=None):
        """按日期种子决定突发事件，保证同一天多次读取结果一致。"""
        prob = clamp(self.p.cfg_float("schedule_event_prob", 0.28), 0.0, 0.9)
        rng = random.Random(seed ^ 0x5F3759DF)
        if rng.random() > prob:
            return {"kind": "none"}
        rest = bool((meta or {}).get("rest"))
        if self._is_long_holiday(meta):
            # 长假由 holiday_plan 安排，不再随机塞加班/出差。
            return {"kind": "none"}
        kind = occupation_kind(world)
        if not rest:
            # 只有会跑外勤的职业才出差；其他人是加班/加课/排班
            if travels_for_work(world) and rng.random() < 0.55:
                provinces = sorted(PROVINCE_CITIES.keys())
                province = provinces[rng.randrange(len(provinces))]
                city = PROVINCE_CITIES[province]
                return {
                    "kind": "business_trip",
                    "title": "临时外派",
                    "province": province,
                    "city": city,
                    "where": "%s·%s" % (province, city),
                    "note": "早上接到通知，被派去%s出差，当天回不来" % city,
                    "task": "处理外派的事",
                    "effects": {"疲惫": 10, "焦虑": 10, "开心": -6, "生气": 3},
                    "soothe": False,
                }
            title, note = self.OVERTIME_NOTES.get(kind, self.OVERTIME_NOTES["office"])
            return {
                "kind": "overtime",
                "title": title,
                "note": "早上" + note + "，今天回不去了",
                "task": "处理临时的事",
                "effects": {"疲惫": 12, "焦虑": 8, "生气": 10, "开心": -8},
                "soothe": False,
            }
        # 休息日：临时加班 / 朋友聚餐。
        if rng.random() < 0.55:
            title, note = self.OVERTIME_NOTES.get(kind, self.OVERTIME_NOTES["office"])
            return {
                "kind": "overtime",
                "title": title,
                "note": "上午接到电话，" + note + "，休息日也泡汤了",
                "task": "处理临时的事",
                "effects": {"生气": 16, "暴躁": 8, "焦虑": 12, "开心": -12, "疲惫": 6},
                "soothe": False,
            }
        return {
            "kind": "friend_dinner",
            "title": "朋友聚餐",
            "note": "下午朋友突然发消息，说好久没见了，晚上一起吃饭",
            "effects": {"开心": 14, "依恋": 8, "兴奋": 6, "疲惫": -4},
            "soothe": True,
        }

    def _is_long_holiday(self, meta):
        return is_long_holiday(self.p, meta)

    def _pick_amusement_spot(self, city, seed):
        return pick_amusement_spot(self.p, city, seed)

    def _pick_holiday_plan(self, day, meta, weather, health, period, event,
                           good_weather, seed, city):
        return pick_holiday_plan(
            self, day, meta, weather, health, period, event,
            good_weather, seed, city,
        )

    def _pick_scenic(self, day, meta, weather, health, period, event, good_weather, seed, city):
        if not (meta or {}).get("rest"):
            return None
        if event.get("kind") == "overtime":
            return None
        if (health or {}).get("sick"):
            return None
        if int((period or {}).get("pain") or 0) >= 8:
            return None
        try:
            spots = self.p.scenic_spots() or []
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine._pick_scenic", _amo_exc)
            spots = []
        spots = [item for item in spots if isinstance(item, dict) and item.get("name")]
        _city = str(city or "").strip()
        if not _city:
            # 没配城市：不挑任何城市的景点，给一个不写城市的通用公园
            return {
                "name": "人民公园", "city": "", "province": "",
                "area": "", "projects": ["慢慢逛", "找个地方坐会儿", "拍几张照"],
                "duration": "3-4小时", "indoor": False,
            }
        _local = [item for item in spots if _city and _city in str(item.get("city") or "")]
        if _city and not _local and not ScheduleEngine._is_long_holiday(self, meta):
            return None
        if _local:
            spots = _local
        if not spots:
            return {
                "name": "人民公园", "city": _city, "province": "",
                "area": "", "projects": ["慢慢逛", "找个地方坐会儿", "拍几张照"],
                "duration": "3-4小时", "indoor": False,
            }
        # 以前这里写死「贵阳/贵州」：非贵阳城市 guiyang/guizhou 恒为空 →
        # 下雨时 pool=[] 又回退成全部景点（含户外），文案还说「找个室内的地方」。
        # 现在按 spots 自己的 indoor 标记选，和城市无关。
        if not good_weather:
            indoor = [item for item in spots if item.get("indoor")]
            pool = indoor or list(spots)
        elif ScheduleEngine._is_long_holiday(self, meta):
            # 长假去外地：先挑不在本市的
            pool = [item for item in spots if str(city or "") not in str(item.get("city") or "")] or list(spots)
        else:
            pool = list(spots)
        if good_weather:
            outdoor = [item for item in pool if not item.get("indoor")]
            if outdoor:
                pool = outdoor
        longer = [item for item in pool if not str(item.get("duration") or "").startswith("1")]
        if longer:
            pool = longer
        pool = sorted(pool, key=lambda item: str(item.get("name") or ""))
        if not pool:
            pool = sorted(spots, key=lambda item: str(item.get("name") or ""))
        chosen = dict(pool[seed % len(pool)])
        chosen["why"] = "天气不错，想出去走走" if good_weather else "天气一般，找个室内的地方待着"
        chosen["from_city"] = city
        return chosen

    # ------------------------------------------------------------------
    # 2. 主干骨架
    # ------------------------------------------------------------------
    def _pick_swim(self, day, meta, weather, health, period, event, good_weather, seed, city):
        """休息日偶尔去玩水：游泳馆 / 海边 / 水上乐园，随机一个。

        规则（用户 2026-11-12）：
        - 不是每周都去，也不是每个休息日都去；一周最多一次；
        - 冬天（温度低于 ``schedule_swim_min_temp``）或天气不好不去；
        - 这一周已经去过一次，就整周不再安排（旅游时例外）；
        - 三个地方随机挑，去了哪个就记在 kv 里，同一周不重复。
        """
        if not (meta or {}).get("rest"):
            return None
        if (event or {}).get("kind") in ("overtime", "business_trip"):
            return None
        if (health or {}).get("sick") or int((period or {}).get("pain") or 0) >= 8:
            return None
        try:
            temp = float((weather or {}).get("temp") or 0)
        except (TypeError, ValueError):
            temp = 0.0
        min_temp = 22.0
        try:
            min_temp = self.p.cfg_float("schedule_swim_min_temp", 22.0)
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine._pick_swim.temp", _amo_exc)
        if not good_weather or temp < float(min_temp):
            return None
        try:
            iso = date.fromisoformat(str(day)).isocalendar()
            weekday = date.fromisoformat(str(day)).weekday()
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine._pick_swim.day", _amo_exc)
            return None
        week_key = "swim_week:%04d-%02d" % (iso[0], iso[1])
        used = ""
        try:
            used = str(self.p.db.kv_get(week_key, "") or "")
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine._pick_swim.kv", _amo_exc)
            used = ""
        used_kind, _, used_day = used.partition("|")
        traveling = ScheduleEngine._is_long_holiday(self, meta)
        if used_kind and traveling and used_day != str(day):
            # 旅游期间不受「这周已经去过」的限制
            used_kind, used_day = "", ""
        if used_day and used_day != str(day) and not traveling:
            return None
        rng = random.Random("amo-swim:" + week_key)
        preferred = rng.choice([5, 6])
        if used_day != str(day) and not traveling and weekday != preferred:
            return None
        if not used_kind:
            chance = 0.32
            try:
                chance = clamp(self.p.cfg_float("schedule_swim_prob", 0.32), 0.0, 1.0)
            except Exception as _amo_exc:
                _note_swallowed("schedule_engine.ScheduleEngine._pick_swim.prob", _amo_exc)
            if rng.random() > chance:
                return None
            place, activity, detail = SWIM_PLACES[rng.randrange(len(SWIM_PLACES))]
        else:
            match = [item for item in SWIM_PLACES if item[0] == used_kind]
            place, activity, detail = match[0] if match else SWIM_PLACES[0]
        where = ("%s·%s" % (str(city or "").strip(), place)).strip("·")
        try:
            if not used_kind:
                self.p.db.kv_set(week_key, place + "|" + str(day))
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine._pick_swim.save", _amo_exc)
        return {
            "place": where,
            "spot": place,
            "activity": activity,
            "detail": detail,
            "zone": "scenic",
            "why": "天气挺热，想去玩水",
        }

    def _swim_segments(self, ctx):
        return swim_segments(ctx, self._seg, self._choose, SWIM_DETAILS)

    def _outing_segments(self, ctx):
        return outing_segments(ctx, self._seg)

    def build_skeleton(self, ctx):
        event_kind = (ctx.get("event") or {}).get("kind")
        meta = ctx.get("meta") or {}
        day_id = str(((ctx or {}).get("day_type") or {}).get("id") or "")
        sick = bool((ctx.get("health") or {}).get("sick"))
        good_weather = bool(ctx.get("good_weather", True))
        holiday = ctx.get("holiday_plan") or {}
        if event_kind == "business_trip" and not meta.get("rest"):
            segments = self._business_trip_segments(ctx)
        elif holiday:
            kind = str(holiday.get("kind") or "")
            if kind == "beach":
                segments = self._swim_segments(ctx)
            elif kind == "shopping":
                segments = self._shopping_segments(ctx)
            elif kind == "amusement":
                segments = self._amusement_segments(ctx)
            else:
                segments = self._scenic_segments(ctx)
        elif ctx.get("swim") and not sick and good_weather:
            # 去游泳/海边/水上乐园：整天按玩水安排，衣柜会自动换泳装
            segments = self._swim_segments(ctx)
        elif day_id == "outing":
            # 生病、天气不好就别硬出门，按宅家过
            segments = self._outing_segments(ctx) if (good_weather and not sick) else self._rest_segments(ctx)
        elif day_id == "homebody":
            # 宅家日真的待在家，不再复用“去商场”的室内休息骨架。
            segments = self._homebody_segments(ctx)
        elif meta.get("rest"):
            # 法定节假日/周末优先于加班日，避免节假日小概率还排上班。
            segments = self._rest_segments(ctx)
        elif day_id == "overtime":
            segments = self._workday_segments(ctx, overtime=True)
        else:
            # 工作日的「临时加班」以前只改文案、不改骨架：突发事件写着「今天回不去了」，
            # 日程却还是 18:00 下班回家。这里让骨架真的晚走。
            segments = self._workday_segments(ctx, overtime=(event_kind == "overtime"))
        items = self._expand(segments, ctx)
        items = self._fill_activities(items, ctx)
        items = self._smooth_moods(items, ctx)
        items = self._apply_gift_effects(items, ctx)
        items = self._validate(items, ctx)
        return items

    def _seg(self, start, end, activity, place, zone, category,
             transit="", vehicle="", detail="", effects=None,
             event="", spot="", soothe=False, swim=False):
        return {
            "start": start, "end": end, "activity": activity, "place": place,
            "zone": zone, "category": category, "transit": transit,
            "vehicle": vehicle, "detail": detail, "effects": effects or {},
            "event": event, "spot": spot, "soothe": soothe,
            "swim": True if swim else False,
        }

    def _workday_segments(self, ctx, overtime=False):
        return workday_segments(ctx, self._seg, overtime=overtime)

    def _business_trip_segments(self, ctx):
        return business_trip_segments(ctx, self._seg, _with_city)

    def _rest_segments(self, ctx):
        event_kind = (ctx.get("event") or {}).get("kind")
        health = ctx.get("health") or {}
        period = ctx.get("period") or {}
        if health.get("sick"):
            return self._sick_rest_segments(ctx)
        # 痛经是痛经，不是发烧：以前 pain>=8 也走「吃药、量体温、捂汗」，每个月都被写成病号。
        if int(period.get("pain") or 0) >= 8:
            return self._period_rest_segments(ctx)
        if event_kind == "overtime" and not (ctx.get("meta") or {}).get("rest"):
            return self._overtime_segments(ctx)
        if event_kind == "friend_dinner":
            return self._friend_dinner_segments(ctx)
        if ctx.get("scenic") and ctx.get("good_weather"):
            return self._scenic_segments(ctx)
        return self._indoor_rest_segments(ctx)

    def _scenic_segments(self, ctx):
        return scenic_segments(
            ctx, self._seg, _place_of, _city_of,
            long_holiday=self._is_long_holiday((ctx or {}).get("meta")),
        )

    def _shopping_segments(self, ctx):
        return shopping_segments(ctx, self._seg)

    def _amusement_segments(self, ctx):
        return amusement_segments(ctx, self._seg)

    def _homebody_segments(self, ctx):
        return homebody_segments(ctx, self._seg)

    def _indoor_rest_segments(self, ctx):
        return indoor_rest_segments(ctx, self._seg, _place_of)

    def _sick_rest_segments(self, ctx):
        return sick_rest_segments(ctx, self._seg)

    def _period_rest_segments(self, ctx):
        return period_rest_segments(ctx, self._seg)

    def _overtime_segments(self, ctx):
        return overtime_segments(ctx, self._seg)

    def _friend_dinner_segments(self, ctx):
        return friend_dinner_segments(ctx, self._seg, _place_of)

    # ------------------------------------------------------------------
    # 3. 展开成 24 个整点
    # ------------------------------------------------------------------
    def _expand(self, segments, ctx):
        items = []
        for seg in segments:
            start = parse_hhmm(seg.get("start"), 0)
            end = parse_hhmm(seg.get("end"), start + 60)
            if end <= start:
                end = start + 60
            start = max(0, min(24 * 60, start))
            end = max(start, min(24 * 60, end))
            first_hour = start // 60
            last_hour = (end + 59) // 60
            for hour in range(first_hour, last_hour):
                if hour >= 24:
                    break
                slot_start = hour * 60
                slot_end = min(end, (hour + 1) * 60)
                item = {
                    "start": fmt_hhmm(slot_start),
                    "end": fmt_hhmm(slot_end),
                    "activity": str(seg.get("activity") or "休息"),
                    "place": str(seg.get("place") or ctx["home"]),
                    "zone": str(seg.get("zone") or "home"),
                    "category": str(seg.get("category") or "rest"),
                    "transit": str(seg.get("transit") or ""),
                    "vehicle": str(seg.get("vehicle") or ""),
                    "detail": str(seg.get("detail") or "") if hour == first_hour else "",
                    "spot": str(seg.get("spot") or ""),
                    "swim": bool(seg.get("swim")),
                    "soothe": bool(seg.get("soothe")),
                    "event": str(seg.get("event") or "") if hour == first_hour else "",
                    "effects": dict(seg.get("effects") or {}) if hour == first_hour else {},
                }
                items.append(item)
        # 补洞：极端情况下骨架没有覆盖完整 24 小时。
        by_start = {}
        for item in items:
            by_start[parse_hhmm(item.get("start"))] = item
        result = []
        for hour in range(24):
            key = hour * 60
            item: Any = by_start.get(key)
            if item is None:
                item = {
                    "start": fmt_hhmm(key), "end": fmt_hhmm(key + 60),
                    "activity": "睡觉" if hour < 7 or hour >= 23 else "休息",
                    "place": ctx["home"], "zone": "home",
                    "category": "sleep" if hour < 7 or hour >= 23 else "rest",
                    "transit": "", "vehicle": "", "detail": "", "spot": "",
                    "swim": False, "soothe": False, "event": "", "effects": {},
                }
            item["end"] = fmt_hhmm(key + 60)
            result.append(item)
        return result

    # ------------------------------------------------------------------
    # 4. 活动文案（LLM 不可用时的真人化兜底）
    # ------------------------------------------------------------------
    def _fill_activities(self, items, ctx):
        rng = random.Random(int(ctx.get("seed") or 0) ^ 0x9E3779B9)
        prev = ""
        for item in items:
            hour = parse_hhmm(item.get("start")) // 60
            activity = self._activity_for(item, ctx, hour, rng, prev)
            if activity and activity != prev:
                item["activity"] = activity
            if not item.get("detail"):
                item["detail"] = self._detail_for(item, ctx, hour, rng)
            prev = item.get("activity", "")
        return items

    def _activity_for(self, item, ctx, hour, rng, prev):
        zone = str(item.get("zone") or "")
        category = str(item.get("category") or "")
        transit = str(item.get("transit") or "")
        spot = str(item.get("spot") or "")
        base = str(item.get("activity") or "")
        generic = base in ("上班", "休息", "吃午饭", "吃晚饭", "睡觉", "起床吃早餐", "洗澡护肤")
        if not generic:
            return base
        if category == "sleep":
            if hour == 7 and (ctx.get("yesterday") or {}).get("late_night"):
                return "赖床、起不来"
            return "睡觉"
        if zone == "road":
            if transit == "drive":
                bank = ROAD_BANK
            elif transit == "taxi":
                bank = TAXI_BANK
            elif transit == "train":
                return "坐高铁去" + str((ctx.get("event") or {}).get("city") or "外地")
            elif transit == "subway":
                bank = COMMUTE_BANK
            else:
                bank = ["在路上"]
            return self._choose(bank, rng, prev)
        if zone == "station":
            return self._choose(STATION_BANK, rng, prev)
        if zone == "hotel":
            return self._choose(HOTEL_BANK, rng, prev)
        if zone == "office":
            # 按类别挑，而不是白名单小时：加班到 18-21 点时也要是「上班」，
            # 不能再落到 REST_BANK 的「瘫在沙发上」。
            if category == "work":
                return self._choose(work_bank_for(ctx.get("world")), rng, prev)
            return "午休" if hour == 13 else self._choose(REST_BANK, rng, prev)
        if zone == "scenic":
            if spot:
                projects = [str(x) for x in ((ctx.get("scenic") or {}).get("projects") or []) if str(x)]
                if projects:
                    project = projects[hour % len(projects)]
                    return "逛" + spot + "：" + project
            return self._choose(SCENIC_BANK, rng, prev)
        if zone == "restaurant":
            if category == "meal":
                bank = self._meal_bank(hour)
                return self._choose(bank, rng, prev)
            return self._choose(["喝杯东西", "坐着歇脚", "刷手机"], rng, prev)
        if zone == "mall":
            return self._choose(INDOOR_BANK, rng, prev)
        if zone == "home":
            if category == "meal":
                bank = self._meal_bank(hour)
                return self._choose(bank, rng, prev)
            if category == "work":
                return self._choose(work_bank_for(ctx.get("world")), rng, prev)
            if category == "rest":
                return self._choose(REST_BANK, rng, prev)
            if category == "life":
                bank = NIGHT_LIFE_BANK if hour >= 20 else HOME_LIFE_BANK
                return self._choose(bank, rng, prev)
        return base or self._choose(REST_BANK, rng, prev)

    @staticmethod
    def _meal_bank(hour):
        """早饭/午饭/晚饭分开：08:00 不该「下楼吃小馆子」，22:00 也不该吃早餐库。"""
        if hour < 10:
            return BREAKFAST_BANK
        if 10 <= hour < 15:
            return LUNCH_BANK
        return DINNER_BANK

    def _detail_for(self, item, ctx, hour, rng):
        zone = str(item.get("zone") or "")
        category = str(item.get("category") or "")
        activity = str(item.get("activity") or "")

        def pick(bank):
            # 细节只是点缀，不能和这一小时的活动对着干（便利店却说被拉进会议）。
            bank = [word for word in bank if word and not detail_conflicts(activity, word)]
            return self._choose(bank, rng, "")

        if zone == "scenic":
            text = str(item.get("place") or "") + " " + activity
            if any(word in text for word in ("游泳", "泳池", "海边", "沙滩", "水上乐园",
                                             "水世界", "玩水")):
                return pick(list(SWIM_DETAILS))
            return pick([
                "看到猴子抢游客的水", "排队买奶茶排了二十分钟", "停车位找了半小时",
                "拍照拍到手机快没电", "被路边小吃香得走不动",
            ])
        if zone == "road":
            if item.get("vehicle"):
                return pick([
                    "导航一路飘红", "停车场绕了三圈", "被加塞了", "堵在市区路上",
                ])
            return pick(["车上人很多", "差点坐过站", "一路小跑"])
        if zone == "playground":
            return pick(["排队排到腿酸", "过山车下来嗓子都喊哑了", "气球差点飞走",
                         "巡游队伍太热闹了", "买了个发箍戴上"])
        if zone == "office" and category == "work":
            return pick(["改到第三版", "需求又变了", "咖啡续了一杯", "被拉进临时会议"])
        if zone == "restaurant":
            return pick(["多放了点辣", "辣得直吸气", "吃到一半才想起拍照"])
        if zone == "home" and category == "rest":
            return pick(["沙发太舒服了", "剧追到停不下来", "刷到一个好笑的视频"])
        return ""

    def _repair_details(self, items, ctx):
        """模型润色完 activity 之后，把和它对不上的 detail 重新挑一个。

        `build_skeleton()` 里 detail 是按 zone/category 选的，随后 `enrich()`
        会用模型重写 activity；重写后两者可能打架（见 detail_conflicts）。
        这里在润色之后统一兜一次底，保证存下来的骨架自洽。
        """
        rng = random.Random(int(ctx.get("seed") or 0) ^ 0x5EED5)
        for item in items:
            detail = str(item.get("detail") or "").strip()
            if not detail or not detail_conflicts(item.get("activity"), detail):
                continue
            hour = parse_hhmm(item.get("start")) // 60
            item["detail"] = self._detail_for(item, ctx, hour, rng)
        return items


    @staticmethod
    def _choose(bank, rng, prev):
        bank = [item for item in bank if item and item != prev]
        if not bank:
            return ""
        return bank[rng.randrange(len(bank))]

    # ------------------------------------------------------------------
    # 5. 情绪连贯
    # ------------------------------------------------------------------
    def _smooth_moods(self, items, ctx):
        vector = {key: clamp((ctx.get("mood") or {}).get(key, MOOD_BASE[key])) for key in MOOD_KEYS}
        yesterday = ctx.get("yesterday") or {}
        health = ctx.get("health") or {}
        period = ctx.get("period") or {}
        if yesterday.get("late_night"):
            vector["疲惫"] = max(clamp(vector["疲惫"] + 16), 68.0)
            vector["开心"] = clamp(vector["开心"] - 6)
            vector["暴躁"] = clamp(vector["暴躁"] + 3)
        if health.get("sick"):
            vector["疲惫"] = clamp(vector["疲惫"] + 10)
            vector["开心"] = clamp(vector["开心"] - 5)
        if int(period.get("pain") or 0) >= 7:
            vector["疲惫"] = clamp(vector["疲惫"] + 8)
            vector["委屈"] = clamp(vector["委屈"] + 5)
        prev_label = ""
        for item in items:
            hour = parse_hhmm(item.get("start")) // 60
            before = dict(vector)
            # 缓慢回落到基础值，避免情绪一条直线。
            for key in MOOD_KEYS:
                vector[key] = vector[key] + (MOOD_BASE[key] - vector[key]) * 0.05
            if 0 <= hour < 6:
                vector["疲惫"] = vector["疲惫"] + 2
                vector["平静"] = vector["平静"] + 1
            elif 6 <= hour < 9:
                vector["疲惫"] = vector["疲惫"] - 2
                vector["开心"] = vector["开心"] + 1
            elif 12 <= hour < 14:
                vector["开心"] = vector["开心"] + 1
            elif 22 <= hour < 24:
                vector["疲惫"] = vector["疲惫"] + 2
                vector["依恋"] = vector["依恋"] + 1
            for key, delta in (CATEGORY_MOOD.get(item.get("category"), {}) or {}).items():
                if key in vector:
                    vector[key] = vector[key] + delta
            effects = item.get("effects") or {}
            if effects:
                for key, delta in effects.items():
                    if key in vector:
                        vector[key] = vector[key] + float(delta)
            # 单小时最多变化 8 点；突发事件允许 22 点。
            cap = 22.0 if effects else 8.0
            for key in MOOD_KEYS:
                vector[key] = clamp(vector[key], before[key] - cap, before[key] + cap)
                vector[key] = clamp(vector[key])
            if yesterday.get("late_night") and hour < 18:
                vector["疲惫"] = max(vector["疲惫"], 64.0)
            label = self._mood_label(vector)
            if prev_label and not item.get("soothe") and not effects:
                if prev_label in NEGATIVE_LABELS and label in POSITIVE_LABELS:
                    label = prev_label
                elif prev_label in POSITIVE_LABELS and label in NEGATIVE_LABELS:
                    label = prev_label
            item["mood"] = label
            item["mood_valence"] = round(self._mood_valence(vector), 1)
            prev_label = label
        return items

    @staticmethod
    def _mood_label(vector):
        if max(vector["生气"], vector["暴躁"]) >= 58:
            return "烦躁"
        if vector["委屈"] >= 58:
            return "委屈"
        if vector["郁闷"] >= 58:
            return "低落"
        if vector["疲惫"] >= 64:
            return "疲惫"
        if vector["焦虑"] >= 58:
            return "焦虑"
        if max(vector["开心"], vector["兴奋"]) >= 64:
            return "开心"
        if vector["依恋"] >= 64:
            return "黏人"
        return "平静"

    @staticmethod
    def _mood_valence(vector):
        positive = (vector["开心"] + vector["兴奋"] + vector["平静"] + vector["依恋"]) / 4.0
        negative = (vector["郁闷"] + vector["疲惫"] + vector["生气"] + vector["暴躁"] + vector["委屈"] + vector["焦虑"]) / 6.0
        return clamp(50 + (positive - negative) * 0.8)

    # ------------------------------------------------------------------
    # 6. 物理校验：时间连续 + 不瞬移
    # ------------------------------------------------------------------
    def _validate(self, items, ctx):
        for item in items:
            item.pop("segments", None)
            item["start"] = fmt_hhmm(parse_hhmm(item.get("start"), 0))
            item["end"] = fmt_hhmm(parse_hhmm(item.get("end"), parse_hhmm(item.get("start"), 0) + 60))
            if parse_hhmm(item["end"]) <= parse_hhmm(item["start"]):
                item["end"] = fmt_hhmm(parse_hhmm(item["start"]) + 60)
        for index, item in enumerate(items):
            expected = fmt_hhmm(index * 60)
            if item.get("start") != expected:
                item["start"] = expected
            item["end"] = fmt_hhmm((index + 1) * 60)
        # 最后一条必须是 24:00 闭环，不能留 23:59。
        if items:
            items[-1]["end"] = "24:00"

        direct_ok = {
            ("office", "restaurant"), ("restaurant", "office"),
            ("office", "mall"), ("mall", "office"),
            ("scenic", "restaurant"), ("restaurant", "scenic"),
            ("restaurant", "hotel"), ("hotel", "restaurant"),
            ("restaurant", "home"), ("home", "restaurant"),
            ("home", "hospital"), ("hospital", "home"), ("hospital", "hospital"),
            ("mall", "restaurant"), ("restaurant", "mall"),
            ("station", "restaurant"), ("restaurant", "station"),
            ("home", "home"), ("office", "office"), ("scenic", "scenic"),
            ("restaurant", "restaurant"), ("mall", "mall"), ("playground", "playground"),
            ("playground", "restaurant"), ("restaurant", "playground"),
        }
        for index in range(1, len(items)):
            prev = items[index - 1]
            cur = items[index]
            prev_zone = str(prev.get("zone") or "")
            zone = str(cur.get("zone") or "")
            if not zone or not prev_zone or zone == prev_zone:
                continue
            if prev_zone == "road" or zone == "road":
                continue
            if (prev_zone, zone) in direct_ok:
                continue
            # 兜底修复：把当前小时改成“在路上”，下一小时再到达。
            original_place = str(cur.get("place") or "")
            cur["activity"] = "从%s赶去%s" % (prev.get("place") or "上一个地方", original_place)
            cur["place"] = "路上"
            cur["zone"] = "road"
            cur["category"] = "commute"
            cur["transit"] = cur.get("transit") or ("drive" if ctx.get("car") else "taxi")
            cur["vehicle"] = cur.get("vehicle") or (ctx.get("car") if cur["transit"] == "drive" else "")
            cur["detail"] = "上一小时还在%s，这一小时在路上" % (prev.get("place") or "别处")
            cur["effects"] = {}
        # 周末景区必须体现资产绑定：开车、堵车、找停车位。
        if (ctx.get("meta") or {}).get("rest") and ctx.get("scenic") and ctx.get("good_weather"):
            scenic_items = [item for item in items if item.get("zone") == "scenic"]
            if scenic_items:
                first = scenic_items[0]
                if not first.get("vehicle"):
                    first["vehicle"] = ctx.get("car") or ""
                if "停车" not in str(first.get("detail") or ""):
                    first["detail"] = (str(first.get("detail") or "") + "；自己开车来的，停车位找了很久").strip("；")
        return items

    #: 文案里出现这些词 = 人在路上，区域必须是 road
    TRAVEL_WORDS = (
        "地铁", "公交", "车厢", "车上", "车站", "站台", "打车", "出租车", "开车",
        "骑车", "骑行", "路上", "通勤", "换乘", "高铁", "火车", "飞机", "赶去", "赶往",
    )
    #: 「到家 / 在家」这类不是“在路上”，别误判
    AT_HOME_WORDS = ("到家", "在家", "家里", "进门", "躺沙发", "被窝")

    @staticmethod
    def _guess_transit(text):
        body = str(text or "")
        if "地铁" in body:
            return "subway"
        if "公交" in body:
            return "bus"
        if "打车" in body or "出租车" in body:
            return "taxi"
        if "开车" in body:
            return "drive"
        if "高铁" in body or "火车" in body:
            return "train"
        if "飞机" in body:
            return "plane"
        return "walk"

    @classmethod
    def _align_zone_with_activity(cls, items):
        """文案和区域对齐：她说“在地铁上”，生图取景就必须是车厢/路面，不能是办公室。

        LLM 只被允许改 activity（骨架的 zone/place 不让它动），所以它写出
        「地铁上听着歌晃回家」时 zone 还停在 office —— 照片就成了办公室。
        """
        for item in items or []:
            zone = str(item.get("zone") or "")
            place = str(item.get("place") or "")
            act = str(item.get("activity") or "")
            if zone == "road":
                # 区域是“路上”，地点也统一成“路上”，别出现“地铁上 / 写字楼”并存
                if place and place != "路上":
                    item["place"] = "路上"
                if not item.get("transit"):
                    item["transit"] = cls._guess_transit(act)
                continue
            if zone in ("home", "hospital"):
                continue
            if not any(word in act for word in cls.TRAVEL_WORDS):
                continue
            if any(word in act for word in cls.AT_HOME_WORDS):
                continue
            item["place"] = "路上"
            item["zone"] = "road"
            item["transit"] = item.get("transit") or cls._guess_transit(act)
            item["vehicle"] = item.get("vehicle") or ("地铁" if item["transit"] == "subway" else "")
            item["effects"] = {}
        return items

    # ------------------------------------------------------------------
    # 7. 让模型只改写 activity，不改物理骨架
    # ------------------------------------------------------------------
    async def enrich(self, umo, ctx, items, llm):
        if not items or llm is None or not self.p.cfg_bool("schedule_llm_backbone", True):
            return items
        lines = []
        for item in items:
            lines.append("%s-%s｜%s｜%s｜%s｜%s" % (
                item.get("start"), item.get("end"), item.get("place"),
                item.get("category"), item.get("zone"), item.get("activity"),
            ))
        event = ctx.get("event") or {}
        scenic = ctx.get("scenic") or {}
        day_type = ctx.get("day_type") or {}
        day_type_line = ""
        if str(day_type.get("guide") or "").strip():
            day_type_line = "今天是「%s」：%s\n" % (
                str(day_type.get("name") or "普通一天"), str(day_type["guide"]).strip(),
            )
        system = "你是角色的日程文案助手。只输出 JSON 数组，不要输出解释。"
        prompt = (
            "角色设定：\n" + _brief_world(ctx.get("world") or {}) + "\n"
            "今天：%s，%s；天气：%s。\n"
            "%s"
            "情绪：%s；生理期：%s；身体：%s；昨日：%s\n"
            "突发事件：%s\n"
            "景区：%s\n"
            "下面是已经固定好的 24 个整点骨架（时间｜地点｜类别｜区域｜当前文案）：\n%s\n"
            "请为每个小时写一句更真实、更有生活感的 activity（8-22字），"
            "必须遵守：\n"
            "1. 顺序对应，正好 24 个字符串；\n"
            "2. 不能改时间、地点、类别；睡觉就写睡觉，在路上就写在路上；\n"
            "3. 在景区要写具体项目；开车要体现堵车/找停车位；\n"
            "4. 不要连续两个小时用同一句；不要写“日程”“AI”“模型”；\n"
            "5. 只输出 JSON 数组，例如 [\"...\",\"...\"]。"
        ) % (
            ctx.get("day"), (ctx.get("meta") or {}).get("label") or "普通一天",
            _weather_text(ctx.get("weather") or {}),
            day_type_line,
            _mood_text(ctx.get("mood") or {}),
            (ctx.get("period") or {}).get("phase", ""),
            (ctx.get("health") or {}).get("status", "健康"),
            (ctx.get("yesterday") or {}).get("note") or "正常",
            event.get("note") or "无",
            _scenic_text(scenic),
            "\n".join(lines),
        )
        try:
            raw = await asyncio.wait_for(llm.chat_raw(umo, system, prompt), timeout=15)
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine.enrich", _amo_exc)
            return items
        data = json_extract(raw)
        if isinstance(data, dict):
            data = data.get("activities") or data.get("items") or data.get("schedule")
        if not isinstance(data, list) or len(data) != len(items):
            return items
        for item, value in zip(items, data):
            text = ""
            detail = ""
            if isinstance(value, dict):
                text = str(value.get("activity") or "").strip()
                detail = str(value.get("detail") or "").strip()
            else:
                text = str(value or "").strip()
            text = re.sub(r"\s+", " ", text)
            if 2 <= len(text) <= 30:
                item["activity"] = text
            if detail and len(detail) <= 40:
                item["detail"] = detail
        # activity 被改写过，detail 可能已经和它对不上，统一按新活动重新挑。
        self._align_zone_with_activity(items)
        return self._repair_details(items, ctx)

    # ------------------------------------------------------------------
    # 8. 动态日记
    # ------------------------------------------------------------------
    @staticmethod
    def _diary_contradicts(prev_diary, diary):
        """粗校验：上一小时已经写他进门/在家，这一小时不能又写他还没回来。"""
        prev = str(prev_diary or "")
        cur = str(diary or "")
        if not prev or not cur:
            return False
        at_home = (
            ("阿墨" in prev or "他" in prev)
            and any(word in prev for word in ("进门", "在家", "回来", "到家", "身边", "陪你", "坐在"))
        )
        alone = any(word in cur for word in ("还没回来", "不在家", "没回来", "一个人", "等他回来"))
        return bool(at_home and alone)

    async def generate_diary(self, umo, ctx, item, prev_item, next_item, llm, prev_diary=""):
        if llm is None:
            return {
                "diary": self.fallback_diary(ctx, item, prev_item),
                "mood": item.get("mood") or "平静",
                "place": item.get("place") or "",
                "activity": item.get("activity") or "",
            }

        def _parse(raw):
            text = ""
            data = json_extract(raw)
            if isinstance(data, dict):
                text = str(data.get("diary") or data.get("text") or "").strip()
            elif isinstance(data, str):
                text = data.strip()
            if not text:
                text = str(raw or "").strip()
            return self._clean_diary(text)

        system = "你就是这个角色本人。你在写只给自己看的第一人称小日记，只输出 JSON。"
        prompt = self._diary_prompt(ctx, item, prev_item, next_item, prev_diary)
        raw = ""
        try:
            raw = await asyncio.wait_for(llm.chat_raw(umo, system, prompt), timeout=60)
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine.generate_diary", _amo_exc)
            raw = ""
        diary = _parse(raw)
        if len(diary) < 20:
            diary = self.fallback_diary(ctx, item, prev_item)
        if prev_diary and self._diary_contradicts(prev_diary, diary):
            strict = (
                prompt
                + "\n【硬性续写】上一小时日记里他已经进门/在家/在陪你。"
                + "这一小时必须承接这个事实，不能写他还没回来、不在家、你一个人。"
            )
            try:
                raw = await asyncio.wait_for(llm.chat_raw(umo, system, strict), timeout=60)
            except Exception as _amo_exc:
                _note_swallowed("schedule_engine.ScheduleEngine.generate_diary", _amo_exc)
                raw = ""
            retried = _parse(raw)
            if retried and len(retried) >= 20 and not self._diary_contradicts(prev_diary, retried):
                diary = retried
            else:
                diary = self.fallback_diary(ctx, item, prev_item)
        return {
            "diary": diary,
            "mood": item.get("mood") or "平静",
            "place": item.get("place") or "",
            "activity": item.get("activity") or "",
        }

    def _diary_prompt(self, ctx, item, prev_item, next_item, prev_diary=""):
        event = ctx.get("event") or {}
        scenic = ctx.get("scenic") or {}
        prev_text = "无"
        if prev_item:
            prev_text = "%s-%s 在%s，%s，心情%s" % (
                prev_item.get("start"), prev_item.get("end"), prev_item.get("place"),
                prev_item.get("activity"), prev_item.get("mood") or "平静",
            )
        next_text = "无"
        if next_item:
            next_text = "%s-%s 去%s，%s" % (
                next_item.get("start"), next_item.get("end"), next_item.get("place"),
                next_item.get("activity"),
            )
        prev_diary_text = str(prev_diary or "").strip()[:500] or "（还没有生成上一小时日记）"
        car_line = ""
        if item.get("vehicle"):
            car_line = "这一小时是开%s；必须写清堵车、找停车位或路上的细节。" % item.get("vehicle")
        if item.get("detail"):
            car_line = (car_line + " 可以用的细节：%s。" % item.get("detail")).strip()
        asset_line = ""
        assets = str(ctx.get("assets") or "").strip()
        if assets:
            asset_line = ("他送你的东西：%s。写这一小时时可以自然戴/用/开，"
                          "但不要每段都提，也不要写你还想要更大的。" % assets)
        allow_gift = False
        try:
            hour = int(str(item.get("start") or "")[:2])
            allow_gift = hour in self._gift_mention_hours(ctx)
        except (TypeError, ValueError):
            allow_gift = False
        worn_line = ""
        worn = str(ctx.get("worn") or "").strip()
        if worn:
            if allow_gift:
                worn_line = "你今天身上/包里有：%s（他送的）。这一小时可以自然带一句，只提一句，别写成交代行踪。" % worn
            else:
                worn_line = "这一小时不要主动提他送的东西（今天已经提过了）；除非上一小时写了、这一小时要承接。"
        outfit_line = ""
        outfit = str(ctx.get("outfit") or "").strip()
        if outfit:
            outfit_line = ("你今天穿的是：%s（今天锁定的这一套，日记里写到衣服只能写它，不能另编）。" % outfit)
        gift_line = ""
        gift_log = str(ctx.get("gift_log") or "").strip()
        if gift_log and allow_gift:
            gift_line = ("他送过你的东西（日期）：%s。这一小时可以自然带一句；"
                         "日期不是今天的，不要写成今天送的。" % gift_log)
        return (
            "角色设定：\n" + _brief_world(ctx.get("world") or {}) + "\n"
            "今天：%s，%s；天气：%s；所在地：%s。\n"
            "身体：%s；生理期：%s；昨日：%s。\n"
            "此刻十维情绪：%s。\n"
            "上一小时：%s\n"
            "上一小时已经写下的小日记（必须承接，不能矛盾）：\n%s\n"
            "这一小时：%s-%s，%s，在%s（%s），心情%s。\n"
            "下一小时：%s\n"
            "突发事件：%s\n"
            "景区信息：%s\n"
            "%s\n"
            "%s\n"
            "%s\n"
            "%s\n"
            "%s\n"
            "请写一段 120-260 字的第一人称小日记，要求：\n"
            "1. 只写这一小时，不要提前写下一小时；\n"
            "2. 写清交通方式（开车/打车/地铁/走路）、具体干了什么、吃了什么、遇到什么趣事；\n"
            "3. 情绪必须从上一小时自然延续，不能无缘无故从郁闷/生气变成开心/兴奋；\n"
            "4. 如果是周末开车出去玩，要提到车、堵车、停车位；\n"
            "5. 口语化、生动，像真人发朋友圈，不要旁白，不要提日程/AI/模型；\n"
            "6. 必须承接上一小时日记：上一小时已经出现的人、地点、动作不能矛盾；\n"
            "7. 如果上一小时写了他已经进门/在家/在陪你，这一小时绝对不能写他还没回来、不在家、你一个人；\n"
            "8. 如果上一小时没写他出现，这一小时也不要凭空让他出现；要出现必须写清开门/回来/打电话；\n"
            "9. 不要重复上一小时已经说过的整段，只写这一小时的新细节；\n"
            "10. 只输出 JSON：{\"diary\":\"...\"}。"
        ) % (
            ctx.get("day"), (ctx.get("meta") or {}).get("label") or "普通一天",
            _weather_text(ctx.get("weather") or {}), ctx.get("location") or _city_of(ctx) or "未设置",
            (ctx.get("health") or {}).get("status", "健康"),
            (ctx.get("period") or {}).get("phase", ""),
            (ctx.get("yesterday") or {}).get("note") or "正常",
            _mood_text(ctx.get("mood") or {}),
            prev_text,
            prev_diary_text,
            item.get("start"), item.get("end"), item.get("activity"),
            item.get("place"), item.get("zone"), item.get("mood") or "平静",
            next_text,
            event.get("note") or "无",
            _scenic_text(scenic),
            car_line,
            asset_line,
            worn_line,
            gift_line,
            outfit_line,
        )

    @staticmethod
    def _clean_diary(text):
        text = str(text or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip().strip('"').strip("“”").strip()
        for prefix in ("小日记：", "日记：", "今天的日记：", "今天："):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
        text = re.sub(r"[ \t]+", " ", text)
        text = text.replace("\r", "").strip()
        return text[:600]

    def fallback_diary(self, ctx, item, prev_item=None):
        """模型不可用时的兜底日记，仍然保证交通、地点、心情和趣事。"""
        place = str(item.get("place") or "家")
        activity = str(item.get("activity") or "待着")
        mood = str(item.get("mood") or "平静")
        weather = _weather_text(ctx.get("weather") or {})
        detail = str(item.get("detail") or "").strip()
        zone = str(item.get("zone") or "")
        car = str(item.get("vehicle") or "")
        event = ctx.get("event") or {}
        parts = []
        if zone == "road":
            if car:
                parts.append("这小时我开着%s在路上，%s。" % (car, detail or "堵得我有点烦"))
            elif item.get("transit") == "train":
                parts.append("这小时我在高铁上，%s。" % (detail or "看着窗外的田发呆"))
            else:
                parts.append("这小时我在路上，%s。" % (detail or "挤得脚都没地方放"))
        elif zone == "scenic":
            parts.append("这小时我在%s，%s。" % (place, activity))
            if detail:
                parts.append(detail + "。")
        elif zone == "office":
            parts.append("这小时我在%s，%s。" % (place, activity))
        elif zone == "home" and item.get("category") == "sleep":
            parts.append("这小时我睡得迷迷糊糊的，连梦都做得断断续续。")
        else:
            parts.append("这小时我在%s，%s。" % (place, activity))
        if event.get("kind") != "none" and (item.get("event") or item.get("effects")):
            parts.append("今天这事来得太突然了，%s。" % (event.get("note") or ""))
        parts.append("外面%s，我心情%s，%s。" % (
            weather, mood, detail or "只想安安静静待一会儿",
        ))
        return "".join(parts)[:600]


def _brief_world(world):
    world = world or {}
    hobbies = world.get("hobbies") or []
    if isinstance(hobbies, list):
        hobbies = "、".join([str(item) for item in hobbies])
    return "\n".join([
        "名字：%s；年龄：%s；城市：%s；职业：%s；任职：%s" % (
            world.get("name", ""), world.get("age", ""), world.get("city", ""),
            world.get("occupation", ""), world.get("workplace", ""),
        ),
        "性格：%s；爱好：%s；日常：%s" % (
            world.get("personality", ""), hobbies, world.get("daily_life", ""),
        ),
    ])


def _weather_text(weather):
    weather = weather or {}
    city = str(weather.get("city") or "").strip()
    prefix = (city + " ") if city else ""
    return "%s%s %s℃" % (prefix, weather.get("text", ""), weather.get("temp", ""))


def _mood_text(mood):
    mood = mood or {}
    return " ".join("%s%d" % (key, int(round(float(mood.get(key, 0) or 0)))) for key in MOOD_KEYS)


def _scenic_text(scenic):
    if not scenic:
        return "无"
    projects = scenic.get("projects") or []
    if isinstance(projects, list):
        projects = "、".join([str(item) for item in projects])
    return "%s（%s·%s，%s）：%s" % (
        scenic.get("name", ""), scenic.get("city", ""), scenic.get("area", ""),
        scenic.get("duration", ""), projects,
    )


JIT_HOURS = (8, 12, 15, 19, 23)


def jit_plan(hour, hunger, cash_cents, sick=False, mood_word="", stock_pnl=0):
    """到点即时决策：只给一个当前动作，不排未来。"""
    hour = int(hour)
    hunger = float(hunger)
    cash_cents = int(cash_cents)
    if sick:
        if hour in (8, 12, 19):
            if cash_cents >= 1000:
                return {"action": "喝白粥、吃药休息", "place": "家", "zone": "home",
                        "category": "life", "cost_cents": 1000,
                        "diary": "生病了只能喝粥，好想吃火锅。"}
            return {"action": "裹着被子躺着，没胃口", "place": "家", "zone": "home",
                    "category": "rest", "cost_cents": 0, "diary": "生病又没钱，只能躺着。"}
    if hour in (8, 12, 19) and hunger < 80:
        if cash_cents >= 1500:
            if hour == 12:
                food, cost = "楼下吃碗牛肉面", 1500
            elif hour == 8:
                food, cost = "买个包子和豆浆", 800
            else:
                food, cost = "随便吃点家常菜", 2000
            return {"action": "正在" + food, "place": "楼下小馆", "zone": "restaurant",
                    "category": "meal", "cost_cents": cost,
                    "diary": "有点饿，花了 %d 块%s。" % (cost // 100, food)}
        if cash_cents >= 500:
            return {"action": "在吃泡面", "place": "家", "zone": "home", "category": "meal",
                    "cost_cents": 500, "diary": "没什么钱，泡面凑合一下。"}
        return {"action": "饿着肚子喝水", "place": "家", "zone": "home", "category": "rest",
                "cost_cents": 0, "diary": "肚子好饿，可是连泡面都买不起了。"}
    if hour == 15:
        if "郁闷" in str(mood_word) or "低落" in str(mood_word):
            return {"action": "窝着发呆，不太想动", "place": "家", "zone": "home",
                    "category": "rest", "cost_cents": 0, "diary": "下午有点丧，什么都不想做。"}
        return {"action": "在忙手头的事，偶尔刷手机", "place": "", "zone": "home",
                "category": "life", "cost_cents": 0, "diary": "下午慢慢过。"}
    if hour == 23:
        return {"action": "洗漱完准备睡了", "place": "家", "zone": "home",
                "category": "sleep", "cost_cents": 0, "diary": "今天就这样吧，睡觉。"}
    return {"action": "在忙自己的事", "place": "", "zone": "home",
            "category": "life", "cost_cents": 0, "diary": ""}
