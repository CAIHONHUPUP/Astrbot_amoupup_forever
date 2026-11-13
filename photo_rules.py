# -*- coding: utf-8 -*-
"""生图场景规则表（2026-11-12 照片严格跟随日程）。

这里只放**纯数据 + 纯函数**，不 import 插件引擎，方便被
``photo_scene`` / ``photo_engine`` / ``draw_engine`` / ``schedule_change`` 共用：

- ``zone_for_place()``：地点词 → 区域（日程写 zone 时也用它同步）；
- ``SCENE_RULES``：一个场景一条结构化规则——正向场景、允许/默认姿势、
  允许陈设、禁止项、守卫句、是否允许用姿势库；
- ``resolve_plan()``：日程 activity → ``PhotoPlan``（照片/自拍整条链路只认它）；
- ``pick_pose_for_plan()``：姿势只能从规则里出，公共场合不再随机到床姿。

老代码里的 ``SCENE_POLICY`` / ``SCENE_ALLOWED`` / ``OUTDOOR_POSES`` 仍保留在
``photo_engine`` 作为兼容视图，新链路全部走这张表。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


#: 地点词 → zone：顺序 = 从最具体到最泛，「街」「路」最后兜底
PLACE_ZONE_RULES = (
    (("医院", "诊所", "卫生院", "急诊"), "hospital"),
    (("景区", "景点", "古镇", "海边", "山上"), "scenic"),
    (("游乐园", "乐园", "欢乐谷", "迪士尼", "方特", "环球影城"), "playground"),
    (("家", "客厅", "卧室", "沙发", "床", "厨房", "阳台", "被窝", "宿舍"), "home"),
    (("酒店", "宾馆", "民宿", "旅馆", "客栈"), "hotel"),
    (("写字楼", "公司", "办公室", "工位", "单位"), "office"),
    (("商场", "购物中心"), "mall"),
    (("餐厅", "饭馆", "饭店", "食堂"), "restaurant"),
    (("地铁站", "公交站", "车站", "火车站", "机场", "候车厅"), "station"),
    (("地铁", "公交", "大巴", "车厢", "车上", "出租车", "网约车"), "road"),
    (("街",), "street"),
    (("路", "路边"), "road"),
)

#: 只认“整个地点就是这个词”的强改写（聊天改日程只改 place 不改 zone 时，用它纠偏）
EXPLICIT_PLACE_ZONES = {
    "家": "home",
    "家里": "home",
    "医院": "hospital",
    "路上": "road",
    "外面": "road",
    "公司": "office",
}

#: 日程类别的冲突优先级（同一分钟命中多条时，数字大的优先）
CATEGORY_PRIORITY = {
    "work": 5,
    "study": 5,
    "commute": 4,
    "meal": 3,
    "play": 3,
    "social": 3,
    "life": 2,
    "rest": 1,
    "sleep": 0,
}

TRAVEL_WORDS = ("旅游", "旅行", "景区", "景点", "古镇", "海边", "爬山", "公园", "游乐园")
SWIM_WORDS = ("游泳", "泳池", "泳馆", "海边", "沙滩", "水上乐园", "水世界", "海里", "玩水")
SWIM_STRONG_WORDS = ("游泳", "泳池", "游泳馆", "泳馆", "水上乐园", "水世界",
                     "海里", "玩水", "踩浪", "戏水", "冲浪", "滑道")
SWIM_NEGATIVE_WORDS = ("散步", "拍照", "看海", "路过", "买泳装")
SHOPPING_WORDS = ("逛街", "商场", "专柜", "步行街")

#: 交通工具词（road 之下再分地铁/公交/私家车/打车/走路）
RIDE_WORDS = {
    "subway": ("地铁", "subway", "metro", "车厢"),
    "bus": ("公交", "大巴", "巴士"),
    "drive": ("开车", "驾驶", "自驾", "车里", "车上"),
    "taxi": ("打车", "出租车", "网约", "taxi", "副驾"),
}

#: 禁止项 token → 中文（守卫句负向兜底用）
PROP_WORDS = {
    "bedding": "床铺被褥",
    "instant_noodles": "泡面",
    "thermos": "保温杯",
    "tableware": "餐具",
    "food": "食物",
    "office_desk": "办公桌和电脑",
    "transport": "地铁/公交/车厢",
    "mall_shelf": "商场橱窗",
}
POSTURE_WORDS = {
    "kneel": "跪坐",
    "sit_on_seat": "坐在座位上",
    "sit_on_floor": "坐在地上",
    "cross_leg": "盘腿坐",
    "lie": "躺下",
    "stand": "站起来",
    "drive_seat": "坐在驾驶位",
}

#: 家里再细分（按 category / activity），姿势和场景都跟着变
HOME_SUB_RULES = (
    ("home_bed", ("sleep",), ("睡", "被窝", "赖床", "上床")),
    ("home_bath", (), ("洗", "刷牙", "护肤", "化妆", "洗脸", "吹头")),
    ("home_desk", ("work",), ("加班", "改方案", "文档", "电脑", "写")),
    ("home_meal", ("meal",), ("吃", "早饭", "午饭", "晚饭", "面", "饭")),
    ("home_sofa", ("rest",), ("沙发", "追剧", "刷手机", "瘫", "躺")),
)


def _default_rule(scene, pose_lines, default_pose, allowed_poses=(), props=(), banned=(),
                  guard="", allow_library_pose=False, seat=""):
    poses = tuple(allowed_poses) or (default_pose,)
    return {
        "scene": scene,
        "poses": poses,
        "default_pose": default_pose,
        "pose_lines": dict(pose_lines),
        "props": tuple(props),
        "banned": tuple(banned),
        "guard": guard,
        "allow_library_pose": allow_library_pose,
        "seat": seat,
    }


#: 一个场景一条规则；photo_engine / draw_engine 的 prompt 都从这里取字段
SCENE_RULES = {
    "home_bed": _default_rule(
        "家里的卧室，床铺、被子和床头的小灯",
        {"library": "坐在床边，双腿并拢，一只手撑在床沿，另一只手举手机自拍",
         "lie_on_bed": "侧躺在床上，一只手撑着头，另一只手举手机自拍"},
        "library", ("library", "lie_on_bed"),
        props=("床", "被子", "枕头", "床头灯", "窗帘"),
        banned=("office_desk", "transport", "instant_noodles", "thermos"),
        guard="画面里只有卧室本来就有的东西：床、被子、枕头、床头灯、窗帘；房间里没有办公桌、车厢或街景",
        allow_library_pose=True, seat="soft"),
    "home_sofa": _default_rule(
        "家里的客厅，沙发、抱枕、茶几和窗边的光",
        {"sit_sofa": "坐在沙发上，身体微微侧向镜头，一只手搭在抱枕上",
         "library": "坐在沙发上，身体微微后仰看镜头"},
        "sit_sofa", ("sit_sofa", "library"),
        props=("沙发", "抱枕", "茶几", "水杯", "窗边的光"),
        banned=("office_desk", "transport", "instant_noodles", "thermos", "mall_shelf"),
        guard="画面里只有客厅本来就有的东西：沙发、抱枕、茶几、水杯和窗边的光；没有办公桌、车厢或街景",
        allow_library_pose=True, seat="soft"),
    "home_desk": _default_rule(
        "家里的书桌前，笔记本电脑、台灯和摊开的资料",
        {"sit_desk": "坐在书桌前的椅子上，一只手搭在桌沿，另一只手举手机自拍"},
        "sit_desk", ("sit_desk",),
        props=("书桌", "笔记本电脑", "台灯", "资料", "椅子"),
        banned=("bedding", "transport", "instant_noodles", "thermos"),
        guard="画面里只有书桌周围本来就有的东西：书桌、电脑、台灯、资料和椅子；没有床铺、车厢或商场",
        seat="chair"),
    "home_meal": _default_rule(
        "家里的餐桌旁，桌上的碗筷和热乎的饭菜",
        {"sit_dining": "坐在餐桌旁，双手放在桌上，正对镜头"},
        "sit_dining", ("sit_dining",),
        props=("餐桌", "碗筷", "饭菜", "杯子", "餐椅"),
        banned=("bedding", "transport", "office_desk"),
        guard="画面里只有餐桌周围本来就有的东西：餐桌、碗筷、饭菜、杯子、餐椅；没有床铺、车厢或办公桌",
        seat="chair"),
    "home_bath": _default_rule(
        "家里的卫生间，洗手台、镜子和挂着的毛巾",
        {"stand_wash": "站在洗手台前，身体侧一点，一只手举手机自拍"},
        "stand_wash", ("stand_wash",),
        props=("洗手台", "镜子", "毛巾", "水龙头", "瓷砖"),
        banned=("bedding", "transport", "office_desk"),
        guard="画面里只有卫生间本来就有的东西：洗手台、镜子、毛巾、水龙头和瓷砖；没有床铺、车厢或办公桌"),
    "home_generic": _default_rule(
        "家里，客厅的沙发和窗边",
        {"library": "坐在沙发上，身体微微侧向镜头，一只手举手机自拍"},
        "library", ("library", "sit_sofa"),
        props=("沙发", "抱枕", "茶几", "窗边的光"),
        banned=("office_desk", "transport", "instant_noodles", "thermos"),
        guard="画面里只有家里本来就有的东西：沙发、抱枕、茶几、水杯和窗边的光；没有办公桌、车厢或街景",
        allow_library_pose=True, seat="soft"),
    "office": _default_rule(
        "公司里的办公室，她的工位、电脑屏幕、办公桌和旁边的隔断",
        {"sit_desk": "坐在工位的椅子上，一只手搭在桌沿，另一只手举手机自拍，身体微微侧向镜头",
         "stand_desk": "站在工位旁，一只手搭在椅背上，微微侧身看镜头"},
        "sit_desk", ("sit_desk", "stand_desk"),
        props=("办公桌", "显示器", "键盘", "马克杯", "文件", "办公椅"),
        banned=("bedding", "instant_noodles", "transport", "mall_shelf"),
        guard="画面里只有工位本来就有的东西：办公桌、显示器、键盘、马克杯、文件和办公椅；没有床铺、车厢或商场橱窗",
        seat="chair"),
    "restaurant": _default_rule(
        "餐厅里，餐桌、碗筷餐盘、菜单、杯子、头顶的暖色吊灯",
        {"sit_table": "坐在餐桌旁，双手放在桌上，正对镜头"},
        "sit_table", ("sit_table",),
        props=("餐桌", "碗筷", "餐盘", "菜单", "杯子", "吊灯"),
        banned=("kneel", "stand", "lie", "bedding", "transport"),
        guard="画面里只有餐厅本来就有的东西：餐桌、碗筷、餐盘、菜单、杯子和吊灯；没有床铺、车厢或办公桌",
        seat="chair"),
    "mall": _default_rule(
        "商场里，明亮的中庭、店铺橱窗和来往的人",
        {"stand_shop": "站在橱窗前，一只手自然垂着，微微侧身看镜头",
         "walk": "在商场里慢慢走，回头看向镜头"},
        "stand_shop", ("stand_shop", "walk"),
        props=("店铺橱窗", "扶梯", "地砖", "指示牌", "来往的人"),
        banned=("kneel", "sit_on_floor", "lie", "bedding", "instant_noodles"),
        guard="画面里只有商场本来就有的东西：橱窗、扶梯、地砖、指示牌和来往的人；没有床铺、泡面或车厢",
        seat="bench"),
    "scenic": _default_rule(
        "景区里，远处的山和树木、石板路和零星的游客",
        {"stand_spot": "站在景点前，抬手挡了一下阳光，头发被风吹起",
         "walk": "沿着石板路往前走，回头看向镜头"},
        "stand_spot", ("stand_spot", "walk"),
        props=("景点建筑或山水", "栏杆", "游客", "路牌", "自然光"),
        banned=("kneel", "sit_on_floor", "lie", "bedding", "instant_noodles", "office_desk"),
        guard="画面里只有景区本来就有的东西：山水、景点建筑、栏杆、路牌和游客；没有床铺、泡面、办公桌或车厢",
        seat="bench"),
    "playground": _default_rule(
        "游乐园里，彩色过山车、旋转木马、气球和来往的游客",
        {"stand_gate": "站在游乐园入口，手里拿着气球，正对镜头",
         "walk": "在游乐园里边走边回头看镜头"},
        "stand_gate", ("stand_gate", "walk"),
        props=("过山车", "旋转木马", "气球", "游客", "彩色招牌"),
        banned=("kneel", "lie", "bedding", "office_desk", "instant_noodles"),
        guard="画面里只有游乐园本来就有的东西：过山车、旋转木马、气球、游客和彩色招牌；没有床铺、办公桌或泡面",
        seat="bench"),
    "swim": _default_rule(
        "泳池边，蓝色水面、泳道和池边的躺椅",
        {"stand_water": "站在浅水区，水到腰际，一只手举手机自拍，另一只手拨了一下湿头发",
         "sit_edge": "坐在泳池边，双脚泡在水里，身体微微侧向镜头"},
        "stand_water", ("stand_water", "sit_edge"),
        props=("水面", "泳道", "池边躺椅", "毛巾", "防晒"),
        banned=("kneel", "lie", "bedding", "office_desk", "transport"),
        guard="画面里只有泳池/海边本来就有的东西：水面、泳道、池边躺椅、毛巾和防晒；没有床铺、办公桌或车厢"),
    "hotel": _default_rule(
        "酒店房间里，大床、落地窗和暖色的灯",
        {"sit_edge": "坐在床边，一只手撑在身侧，身体微微后仰看镜头",
         "stand_window": "站在落地窗边，身体侧一点看镜头"},
        "sit_edge", ("sit_edge", "stand_window"),
        props=("床和枕头", "落地窗", "床头灯", "行李", "地毯"),
        banned=("kneel", "lie", "office_desk", "transport"),
        guard="画面里只有酒店房间本来就有的东西：床、枕头、落地窗、床头灯、行李和地毯；没有办公桌、车厢或街景",
        seat="soft"),
    "station": _default_rule(
        "车站候车厅，指示牌、行李和来往的旅客",
        {"stand_luggage": "站在候车厅里，一只手扶着行李箱拉杆，另一只手举手机自拍",
         "stand": "站在候车厅里，身体侧一点看镜头"},
        "stand_luggage", ("stand_luggage", "stand"),
        props=("候车座椅", "指示牌", "行李箱", "时刻表", "行人"),
        banned=("kneel", "sit_on_floor", "lie", "bedding", "instant_noodles"),
        guard="画面里只有候车厅本来就有的东西：座椅、指示牌、行李箱、时刻表和行人；没有床铺、泡面或车厢",
        seat="bench"),
    "hospital": _default_rule(
        "医院走廊/候诊区，候诊椅、叫号屏、扶手和消毒水味的环境",
        {"sit_wait": "坐在候诊椅上，双手放在腿上，正对镜头"},
        "sit_wait", ("sit_wait",),
        props=("候诊椅", "叫号屏", "走廊", "扶手", "白墙"),
        banned=("lie", "kneel", "bedding", "transport"),
        guard="画面里只有医院本来就有的东西：候诊椅、叫号屏、走廊、扶手和白墙；没有床铺、车厢或商场",
        seat="chair"),
    "street": _default_rule(
        "街边，路面、行道树、店面、路牌和走过的行人",
        {"stand_road": "站在路边，身体侧一点，回头看向镜头",
         "walk": "沿着人行道往前走，回头看向镜头"},
        "stand_road", ("stand_road", "walk"),
        props=("路面", "行道树", "店面", "路牌", "走过的行人"),
        banned=("kneel", "sit_on_floor", "lie", "bedding", "instant_noodles", "thermos"),
        guard="画面里只有街边本来就有的东西：路面、行道树、店面、路牌和行人；地面干净；没有床铺、泡面、保温杯或车厢",
        seat="bench"),
    "road_subway": _default_rule(
        "真实地铁车厢：两侧是硬质长条座椅，头顶横向扶手杆和一排吊环，车门上方有线路图，窗外是隧道或站台",
        {"stand_hold_strap": "站在车厢里，一只手扶着头顶的吊环，另一只手举手机自拍，身体微微侧向镜头",
         "stand": "站在车厢里，身体侧一点看镜头"},
        "stand_hold_strap", ("stand_hold_strap", "stand"),
        props=("硬质长条座椅", "扶手杆", "吊环", "车窗", "线路图", "乘客"),
        banned=("kneel", "sit_on_seat", "cross_leg", "lie", "bedding",
                "instant_noodles", "thermos", "office_desk"),
        guard="画面里只有地铁车厢本来就有的东西：硬质长条座椅、扶手杆、吊环、车窗、线路图和乘客；地面和座椅干净；没有床铺、泡面、保温杯或办公桌",
        seat="hard_bench"),
    "road_bus": _default_rule(
        "真实公交车厢：一排排硬质塑料座椅，竖杆扶手和头顶横杆，下车铃、刷卡机，窗外是街道和站牌",
        {"stand_hold_pole": "站在车厢里，一只手扶着竖杆或头顶横杆，另一只手举手机自拍，身体微微侧向镜头",
         "stand": "站在车厢里，身体侧一点看镜头"},
        "stand_hold_pole", ("stand_hold_pole", "stand"),
        props=("硬质塑料座椅", "竖杆扶手", "头顶横杆", "下车铃", "刷卡机", "窗外街道"),
        banned=("kneel", "sit_on_seat", "cross_leg", "lie", "bedding",
                "instant_noodles", "thermos", "office_desk"),
        guard="画面里只有公交车厢本来就有的东西：硬质塑料座椅、竖杆扶手、横杆、下车铃、刷卡机和窗外街道；地面干净；没有床铺、泡面、保温杯或办公桌",
        seat="hard_bench"),
    "road_car_drive": _default_rule(
        "私家车驾驶位：方向盘、仪表盘、中控台，前挡风玻璃外是路面和车流",
        {"drive_seat": "坐在驾驶座上，系好安全带，一只手举手机自拍，另一只手放在方向盘下沿"},
        "drive_seat", ("drive_seat",),
        props=("方向盘", "仪表盘", "中控台", "安全带", "挡风玻璃", "车外路面"),
        banned=("stand", "kneel", "lie", "bedding", "instant_noodles", "thermos"),
        guard="画面里只有驾驶位本来就有的东西：方向盘、仪表盘、中控台、安全带、挡风玻璃和车外路面；人坐在驾驶座；没有站立、跪坐或床铺",
        seat="car_seat"),
    "road_car_taxi": _default_rule(
        "出租车/网约车后排，座椅、车窗，窗外是街道和车流",
        {"sit_back": "坐在后排座椅上，身体微微侧向车窗，一只手举手机自拍"},
        "sit_back", ("sit_back",),
        props=("后排座椅", "车窗", "安全带", "车外街景"),
        banned=("drive_seat", "stand", "kneel", "lie", "bedding", "instant_noodles"),
        guard="画面里只有网约车后排本来就有的东西：座椅、车窗、安全带和车外街景；人坐在后排；没有方向盘、站立、跪坐或床铺",
        seat="car_seat"),
    "road_walk": _default_rule(
        "街边，路面、行道树、店面和走过的人",
        {"stand_road": "站在路边，身体侧一点，回头看向镜头",
         "walk": "沿着人行道往前走，回头看向镜头"},
        "stand_road", ("stand_road", "walk"),
        props=("路面", "行道树", "店面", "走过的行人"),
        banned=("kneel", "sit_on_floor", "lie", "bedding", "instant_noodles", "thermos"),
        guard="画面里只有路面本来就有的东西：路面、行道树、店面和走过的行人；没有床铺、泡面、保温杯或车厢",
        seat="bench"),
    "unknown": _default_rule(
        "简洁的室内日常环境：浅色墙面、窗边自然光、少量简单家具",
        {"stand": "站在窗边，身体微微侧向镜头，自然放松"},
        "stand", ("stand",),
        props=("浅色墙面", "窗", "简单家具", "自然光"),
        banned=("bedding", "instant_noodles", "thermos", "office_desk", "transport"),
        guard="画面里只有墙面、窗、简单家具和自然光；不出现任何具体城市、店铺、站牌或交通工具标志",
        seat=""),
}

#: 英文场景锁：传给万象画卷副脑，解析完 JSON 后直接覆盖 environment_and_scene
SCENE_EN = {
    "home_bed": "a real bedroom at home: a bed with duvet and pillows, a bedside lamp, curtains, warm indoor light",
    "home_sofa": "a real living room at home: a sofa with cushions, a coffee table, a glass of water, daylight by the window",
    "home_desk": "a real home desk corner: a laptop, a desk lamp, papers spread out, a chair",
    "home_meal": "a real dining table at home: bowls and chopsticks, warm home-cooked food, a chair",
    "home_bath": "a real home bathroom: a sink, a mirror, hanging towels, tiled wall",
    "home_generic": "a real home interior: sofa, cushions, coffee table, warm daylight by the window",
    "office": "a real office workspace: a desk with monitor and keyboard, a mug, papers, an office chair, partition walls",
    "restaurant": "a real restaurant interior: a dining table, plates and bowls, a menu, warm pendant lights",
    "mall": "a real shopping mall: bright atrium, shop windows, escalators, visitors walking by",
    "scenic": "a real outdoor scenic spot: mountains and trees in the distance, stone path, a few tourists",
    "swim": "a real swimming pool edge or beach: blue water, lane lines, sun loungers and a towel",
    "hotel": "a real hotel room: a large bed with pillows, floor-to-ceiling window, warm bedside lamp, luggage, carpet",
    "station": "a real station waiting hall: rows of waiting seats, signs, luggage, timetable, passengers",
    "hospital": "a real hospital corridor: waiting chairs, a queue display screen, handrail, white walls",
    "street": "a real street sidewalk: pavement, street trees, shop fronts, road signs, pedestrians walking by",
    "road_subway": "inside a real subway car: hard bench seats along the walls, a horizontal grab bar overhead with hanging straps, a route map above the doors, tunnel or platform outside the windows",
    "road_bus": "inside a real city bus: rows of hard plastic seats, vertical grab poles and an overhead bar, a stop bell, a card reader, streets and bus stops outside the windows",
    "road_car_drive": "the driver's seat of a real private car: steering wheel, dashboard, center console, seat belt, road and traffic seen through the windshield",
    "road_car_taxi": "the back seat of a real taxi or ride-hailing car: rear seat, side window, seat belt, street and traffic outside",
    "road_walk": "a real street sidewalk: pavement, street trees, shop fronts, pedestrians walking by",
    "unknown": "a simple neutral indoor setting: light-colored wall, window with natural light, a few simple furniture pieces, no identifiable landmark",
}

#: 英文姿势锁：(规则 key, pose_rule) → 覆盖 pose_and_action；没有条目的（居家姿势库）不锁
POSE_EN = {
    ("home_sofa", "sit_sofa"): "sitting on the sofa, body slightly turned toward the camera, one hand resting on a cushion, the other hand holding the phone for a selfie",
    ("home_desk", "sit_desk"): "sitting on the chair at the home desk, one hand resting on the desk edge, the other hand holding the phone for a selfie",
    ("home_meal", "sit_dining"): "sitting at the dining table, both hands resting on the table, facing the camera",
    ("home_bath", "stand_wash"): "standing in front of the bathroom sink, body slightly turned, one hand holding the phone for a selfie",
    ("office", "sit_desk"): "sitting on the office chair at her workstation, one hand resting on the desk edge, the other hand holding the phone for a selfie, body slightly turned toward the camera",
    ("office", "stand_desk"): "standing beside the workstation, one hand resting on the back of the chair, body slightly turned toward the camera",
    ("restaurant", "sit_table"): "sitting at the restaurant table, both hands resting on the table, facing the camera",
    ("mall", "stand_shop"): "standing in front of a shop window, one arm relaxed at her side, body slightly turned toward the camera",
    ("mall", "walk"): "walking slowly through the mall, turning her head back toward the camera",
    ("scenic", "stand_spot"): "standing in front of the scenic spot, one hand shading her eyes from the sun, hair blown by the wind",
    ("scenic", "walk"): "walking forward along the stone path, turning her head back toward the camera",
    ("swim", "stand_water"): "standing in shallow water up to her waist, one hand holding the phone for a selfie, the other hand pushing back wet hair",
    ("swim", "sit_edge"): "sitting on the pool edge with both feet in the water, body slightly turned toward the camera",
    ("hotel", "sit_edge"): "sitting on the edge of the hotel bed, one hand propped behind her, body leaning slightly back toward the camera",
    ("hotel", "stand_window"): "standing by the floor-to-ceiling window, body slightly turned toward the camera",
    ("station", "stand_luggage"): "standing in the waiting hall, one hand on the luggage handle, the other hand holding the phone for a selfie",
    ("station", "stand"): "standing in the waiting hall, body slightly turned toward the camera",
    ("hospital", "sit_wait"): "sitting on a waiting chair, both hands on her lap, facing the camera",
    ("street", "stand_road"): "standing on the sidewalk, body slightly turned, looking back at the camera",
    ("street", "walk"): "walking along the sidewalk, turning her head back toward the camera",
    ("road_walk", "stand_road"): "standing on the sidewalk, body slightly turned, looking back at the camera",
    ("road_walk", "walk"): "walking along the sidewalk, turning her head back toward the camera",
    ("road_subway", "stand_hold_strap"): "standing in the subway car, one hand holding the overhead strap, the other hand holding the phone for a selfie, body slightly turned toward the camera",
    ("road_subway", "stand"): "standing in the subway car, body slightly turned toward the camera",
    ("road_bus", "stand_hold_pole"): "standing in the bus, one hand holding a vertical grab pole or the overhead bar, the other hand holding the phone for a selfie, body slightly turned toward the camera",
    ("road_bus", "stand"): "standing in the bus, body slightly turned toward the camera",
    ("road_car_drive", "drive_seat"): "sitting in the driver's seat with the seat belt fastened, one hand holding the phone for a selfie, the other hand resting at the lower edge of the steering wheel",
    ("road_car_taxi", "sit_back"): "sitting in the back seat, body slightly turned toward the side window, one hand holding the phone for a selfie",
    ("unknown", "stand"): "standing by the window, body slightly turned toward the camera, relaxed and natural",
}


def locks_for_plan(plan: PhotoPlan) -> tuple[str, str]:
    """返回 (英文场景锁, 英文姿势锁)；居家姿势库没有英文条目时只锁场景。"""
    if plan is None:
        return "", ""
    scene_en = SCENE_EN.get(str(plan.zone or ""), "")
    pose_en = POSE_EN.get((str(plan.zone or ""), str(plan.pose_rule or "")), "")
    return scene_en, pose_en


#: 兼容 photo_engine 旧视图：zone → 规则 key
LEGACY_ZONE_ALIAS = {    "road": "road_walk",
    "default": "unknown",
    "home": "home_generic",
    "office": "office",
    "restaurant": "restaurant",
    "mall": "mall",
    "scenic": "scenic",
    "hotel": "hotel",
    "station": "station",
    "street": "street",
    "hospital": "hospital",
}


def clean_place(place: Any, limit: int = 16) -> str:
    """日程里的 place 有时是「他桌面上」这种动作描述，不适合当拍照地点。"""
    text = str(place or "").strip()
    if not text:
        return ""
    if "桌" in text or text.startswith("他") or text.startswith("她"):
        return "家里"
    return text[:limit]


def zone_for_place(place: Any) -> str:
    """地点词 → zone；顺序从最具体到最泛，街/路兜底。"""
    text = clean_place(place)
    if not text:
        return ""
    for words, zone in PLACE_ZONE_RULES:
        if any(word in text for word in words):
            return zone
    return ""


def blob_of(activity: Any) -> str:
    data = activity if isinstance(activity, dict) else {}
    return "%s%s%s" % (
        str(data.get("activity") or ""),
        str(data.get("place") or ""),
        str(data.get("spot") or ""),
    )


def is_swim_activity(activity: Any) -> bool:
    data = activity if isinstance(activity, dict) else {}
    if data.get("swim") is True:
        return True
    text = blob_of(activity)
    if any(word in text for word in SWIM_STRONG_WORDS):
        return True
    if any(word in text for word in SWIM_NEGATIVE_WORDS):
        return False
    return False


def road_ride(activity: Any) -> str:
    """这一小时在路上是坐什么：subway / bus / drive / taxi / ''。"""
    data = activity if isinstance(activity, dict) else {}
    text = "%s%s%s" % (
        str(data.get("activity") or ""),
        str(data.get("transit") or ""),
        str(data.get("vehicle") or ""),
    )
    for kind, words in RIDE_WORDS.items():
        if any(word in text for word in words):
            return kind
    return ""


def normalize_activity(activity: Any) -> tuple[dict, list]:
    """读侧一致性：place 与 zone 打架时，以“整个地点词”为准纠偏。

    只修强改写（家/医院/路上/外面/公司），不碰「公司附近」「酒店附近」这种
    带后缀的地点——边上的餐厅不该被改成公司/酒店。
    """
    data = dict(activity) if isinstance(activity, dict) else {}
    place = clean_place(data.get("place"))
    zone = str(data.get("zone") or "").strip()
    fixes = []
    want = EXPLICIT_PLACE_ZONES.get(place, "")
    if want and zone and want != zone:
        fixes.append((zone, want))
        data["zone"] = want
        if want != "road":
            data["transit"] = ""
            data["vehicle"] = ""
    elif not zone and want:
        data["zone"] = want
        fixes.append(("", want))
    if not data.get("zone"):
        data["zone"] = zone_for_place(place)
    return data, fixes


@dataclass
class PhotoPlan:
    """一次生图的完整场景计划：地点、场景、姿势、守卫都在这里定型。"""
    zone: str = "unknown"
    base_zone: str = ""
    scene: str = ""
    pose_rule: str = "stand"
    pose_line: str = ""
    guard: str = ""
    props: tuple = ()
    banned: tuple = ()
    allow_library_pose: bool = False
    source: str = "unknown"
    reason: str = ""
    activity: dict = field(default_factory=dict)
    explicit_place: str = ""
    explicit_conflict: bool = False
    repairs: list = field(default_factory=list)


def _home_key(activity: dict) -> str:
    category = str(activity.get("category") or "")
    text = "%s%s" % (activity.get("activity") or "", activity.get("detail") or "")
    for key, categories, words in HOME_SUB_RULES:
        if categories and category in categories:
            return key
        if any(word in text for word in words):
            return key
    return "home_generic"


def _scene_key(zone: str, activity: dict) -> str:
    if is_swim_activity(activity) and zone in ("scenic", "", "street"):
        return "swim"
    if zone == "home":
        return _home_key(activity)
    if zone == "road":
        ride = road_ride(activity)
        if ride == "subway":
            return "road_subway"
        if ride == "bus":
            return "road_bus"
        if ride == "drive":
            return "road_car_drive"
        if ride == "taxi":
            return "road_car_taxi"
        return "road_walk"
    if zone in SCENE_RULES:
        return zone
    return "unknown"


def _guard_line(rule: dict) -> str:
    positive = str(rule.get("guard") or "").strip()
    negative = []
    for token in rule.get("banned") or ():
        label = POSTURE_WORDS.get(token) or PROP_WORDS.get(token, token)
        # 正向句里已经写了「没有泡面/没有床铺」的，不再重复一条「不要泡面」
        if label and (label in positive or label[:2] in positive):
            continue
        negative.append("不要" + (label or token))
    if not negative:
        return positive
    return positive + "；" + "；".join(negative)


def resolve_plan(activity: Any = None, kind: str = "", explicit_place: str = "",
                 mode: str = "photo", scene_hint: str = "") -> PhotoPlan:
    """日程 → 生图计划。照片语义下日程是唯一真值，显式地点只在“日程不明”时兜底。"""
    data, repairs = normalize_activity(activity)
    zone = str(data.get("zone") or "").strip()
    source = str(data.get("_source") or "") or "schedule"
    reason = str(data.get("_reason") or "")
    explicit_zone = zone_for_place(explicit_place) if explicit_place else ""

    if zone:
        key = _scene_key(zone, data)
    elif mode == "photo" and explicit_place and explicit_zone:
        key = _scene_key(explicit_zone, data)
        source = "explicit_place"
        reason = reason or "日程里没有可用地点，用他说的地点兜底"
    else:
        key = "unknown"
        if source == "schedule":
            source = "unknown_zone" if data.get("place") else "no_schedule"
        reason = reason or "没有可用的日程地点"

    rule = SCENE_RULES.get(key) or SCENE_RULES["unknown"]
    conflict = bool(
        explicit_place and zone and explicit_zone and explicit_zone != zone
        and mode == "photo"
    )
    plan = PhotoPlan(
        zone=key,
        base_zone=zone or explicit_zone,
        scene=scene_hint or str(rule.get("scene") or ""),
        pose_rule=str(rule.get("default_pose") or "stand"),
        pose_line=str((rule.get("pose_lines") or {}).get(rule.get("default_pose"), "")),
        guard=_guard_line(rule),
        props=tuple(rule.get("props") or ()),
        banned=tuple(rule.get("banned") or ()),
        allow_library_pose=bool(rule.get("allow_library_pose")),
        source=source,
        reason=reason,
        activity=data,
        explicit_place=str(explicit_place or ""),
        explicit_conflict=conflict,
        repairs=repairs,
    )
    return plan


def pose_text(pose: Any) -> str:
    """姿势 dict → 提示词片段（和 pose_library.pose_line 同格式，避免互相 import）。"""
    pose = pose if isinstance(pose, dict) else {}
    name = str(pose.get("name") or "").strip()
    note = str(pose.get("note") or "").strip()
    if not name:
        return ""
    return "姿势：" + name + (("（" + note + "）") if note else "")


def pick_pose_for_plan(plan: PhotoPlan, library: Any = None) -> tuple[Any, str]:
    """姿势只能由场景规则决定：库姿势只在规则允许的居家场景用。"""
    rule = SCENE_RULES.get(plan.zone) or SCENE_RULES["unknown"]
    if plan.allow_library_pose and library is not None:
        getter = getattr(library, "random_pose", None)
        if callable(getter):
            pose = None
            try:
                pose = getter(plan.base_zone or plan.zone)
            except TypeError:
                try:
                    pose = getter()
                except Exception:
                    pose = None
            except Exception:
                pose = None
            text = pose_text(pose)
            if text:
                # 用了姿势库的姿势：姿势锁交给库里的描述，不再套规则默认英文姿势
                plan.pose_rule = "library"
                return pose, text
    pose_rule = rule.get("default_pose") or "stand"
    line = str((rule.get("pose_lines") or {}).get(pose_rule) or "")
    return None, line
