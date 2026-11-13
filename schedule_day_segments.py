# -*- coding: utf-8 -*-
"""工作日/外出/室内休息骨架：纯函数，seg/place_of 由 engine 注入。"""
from __future__ import annotations


def workday_segments(ctx, seg, overtime=False):
    home = ctx["home"]
    office = ctx["workplace"]
    late = bool((ctx.get("yesterday") or {}).get("late_night"))
    sick = bool((ctx.get("health") or {}).get("sick"))
    wake = "赖床、起不来" if late else "起床洗漱、吃早餐"
    has_car = bool(ctx.get("has_gift_car"))
    if has_car:
        commute = "开车去公司"
    elif sick:
        commute = "带病出门，慢慢走去地铁站"
    else:
        commute = "挤地铁去公司"
    if overtime:
        effects = (ctx.get("event") or {}).get("effects") or {}
        evening = [
            seg("18:00", "19:00", "临时的事还没完，还在公司", office, "office", "work",
                effects=effects),
            seg("19:00", "20:00", "点个外卖，边吃边改", office, "office", "work"),
            seg("20:00", "21:00", "总算收尾，收拾东西准备走", office, "office", "work"),
            seg("21:00", "22:00", "下班回家", "路上", "road", "commute",
                transit="drive" if has_car else "subway",
                vehicle=ctx.get("car") if has_car else "",
                detail="晚高峰，慢慢挪" if has_car else ""),
            seg("22:00", "23:00", "到家洗漱", home, "home", "life"),
            seg("23:00", "24:00", "睡觉", home, "home", "sleep"),
        ]
    else:
        evening = [
            seg("18:00", "19:00", "下班开车回家" if has_car else "下班回家", "路上", "road", "commute",
                transit="drive" if has_car else "subway",
                vehicle=ctx.get("car") if has_car else "",
                detail="晚高峰，慢慢挪" if has_car else ""),
            seg("19:00", "20:00", "吃晚饭", home, "home", "meal"),
            seg("20:00", "22:00", "休息", home, "home", "rest"),
            seg("22:00", "23:00", "洗澡护肤", home, "home", "life"),
            seg("23:00", "24:00", "睡觉", home, "home", "sleep"),
        ]
    return [
        seg("00:00", "07:00", "睡觉", home, "home", "sleep"),
        seg("07:00", "08:00", wake, home, "home", "life", detail="昨晚没睡好" if late else ""),
        seg("08:00", "09:00", commute, "路上", "road", "commute",
            transit="drive" if has_car else "subway",
            vehicle=ctx.get("car") if has_car else "",
            detail="早高峰有点堵，到公司还得找车位" if has_car else ""),
        seg("09:00", "12:00", "上班", office, "office", "work"),
        seg("12:00", "13:00", "吃午饭", office + "附近", "restaurant", "meal"),
        seg("13:00", "14:00", "午休", office, "office", "rest"),
        seg("14:00", "18:00", "上班", office, "office", "work"),
    ] + evening


def outing_segments(ctx, seg):
    """外出日骨架：早上出门，白天在外面玩，傍晚回家。"""
    ctx = dict(ctx or {})
    home = ctx.get("home") or "家"
    city = ctx.get("city") or "城里"
    spot = ctx.get("scenic") or {}
    where = str(spot.get("name") or "").strip() or ("%s的街上" % city)
    zone = "scenic" if str(spot.get("name") or "").strip() else "mall"
    return [
        seg("00:00", "08:00", "睡觉", home, "home", "sleep"),
        seg("08:00", "09:00", "起床收拾，翻衣柜挑今天要穿的衣服", home, "home", "life"),
        seg("09:00", "10:00", "出门，坐车去%s" % where, "路上", "road", "commute",
            transit="在路上", vehicle="地铁"),
        seg("10:00", "12:00", "在%s慢慢逛" % where, where, zone, "play"),
        seg("12:00", "13:00", "找了家小店吃饭", "%s附近" % where, "restaurant", "meal"),
        seg("13:00", "16:00", "接着逛，走走停停", where, zone, "play"),
        seg("16:00", "17:00", "找个地方坐下歇一会儿，喝点东西", where, zone, "rest"),
        seg("17:00", "18:00", "往回走，路上有点累", "路上", "road", "commute",
            transit="在路上", vehicle="地铁"),
        seg("18:00", "19:00", "到家先洗把脸缓一缓", home, "home", "life"),
        seg("19:00", "20:00", "到家附近吃晚饭", home, "home", "meal"),
        seg("20:00", "22:00", "洗完澡窝在沙发上，翻今天拍的照片", home, "home", "rest"),
        seg("22:00", "24:00", "睡觉", home, "home", "sleep"),
    ]


def indoor_rest_segments(ctx, seg, place_of):
    home = ctx["home"]
    spot = ctx.get("scenic") or {}
    name = str(spot.get("name") or "商场")
    place = place_of(ctx, name)
    car = ctx["car"]
    return [
        seg("00:00", "08:00", "睡觉", home, "home", "sleep"),
        seg("08:00", "09:00", "起床吃早餐", home, "home", "meal"),
        seg("09:00", "10:00", "收拾屋子、洗衣服", home, "home", "life"),
        seg("10:00", "11:00", "开车去" + name, "路上", "road", "commute",
            transit="drive", vehicle=car, detail="下雨天路滑，开得慢"),
        seg("11:00", "13:00", "逛" + name, place,
            "mall" if not spot.get("indoor") else "scenic", "play", vehicle=car, spot=name),
        seg("13:00", "14:00", "吃午饭", place + "·餐厅", "restaurant", "meal"),
        seg("14:00", "16:00", "继续逛、找地方坐着", place, "mall", "play"),
        seg("16:00", "17:00", "喝杯热饮", place + "·咖啡店", "restaurant", "rest"),
        seg("17:00", "18:00", "开车回家", "路上", "road", "commute",
            transit="drive", vehicle=car),
        seg("18:00", "19:00", "到家缓一会儿", home, "home", "rest"),
        seg("19:00", "20:00", "吃晚饭", home, "home", "meal"),
        seg("20:00", "22:00", "追剧、刷手机", home, "home", "rest"),
        seg("22:00", "23:00", "洗澡护肤", home, "home", "life"),
        seg("23:00", "24:00", "睡觉", home, "home", "sleep"),
    ]
