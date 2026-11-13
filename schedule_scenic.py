# -*- coding: utf-8 -*-
"""景区一天骨架：纯函数，地点/城市/seg 由 ScheduleEngine 注入。"""
from __future__ import annotations


def scenic_segments(ctx, seg, place_of, city_of, long_holiday=False):
    home = ctx["home"]
    spot = ctx.get("scenic") or {}
    name = str(spot.get("name") or "附近公园")
    place = place_of(ctx, name)
    car = ctx["car"]
    projects = [str(item) for item in (spot.get("projects") or []) if str(item)]
    project_a = projects[0] if projects else "景区主景点"
    project_b = projects[1] if len(projects) > 1 else "拍照散步"
    long_trip = bool(long_holiday) and city_of(ctx) not in str(spot.get("city") or "")
    if long_trip:
        return [
            seg("00:00", "08:00", "睡觉", home, "home", "sleep"),
            seg("08:00", "09:00", "起床吃早餐", home, "home", "meal"),
            seg("09:00", "10:00", "收拾东西准备出发", home, "home", "life"),
            seg("10:00", "12:00", "开车去" + name, "路上", "road", "commute",
                transit="drive", vehicle=car, detail="出城有点堵，导航显示还要两个多小时"),
            seg("12:00", "13:00", "到景区附近吃午饭", place + "·餐馆", "restaurant", "meal"),
            seg("13:00", "15:00", "逛" + name + "：" + project_a, place, "scenic", "play",
                transit="drive", vehicle=car, spot=name, soothe=True),
            seg("15:00", "16:00", "找地方歇脚、喝点东西", place + "·茶饮店", "restaurant", "rest"),
            seg("16:00", "17:00", "继续逛" + name + "：" + project_b, place, "scenic", "play",
                transit="drive", vehicle=car, spot=name, soothe=True),
            seg("17:00", "19:00", "开车回" + (city_of(ctx) or "城里"), "路上", "road", "commute",
                transit="drive", vehicle=car, detail="返程堵在高速上"),
            seg("19:00", "20:00", "到家附近吃晚饭", home + "附近", "restaurant", "meal"),
            seg("20:00", "22:00", "回家整理照片、追剧", home, "home", "rest"),
            seg("22:00", "23:00", "洗澡护肤", home, "home", "life"),
            seg("23:00", "24:00", "睡觉", home, "home", "sleep"),
        ]
    return [
        seg("00:00", "08:00", "睡觉", home, "home", "sleep"),
        seg("08:00", "09:00", "起床吃早餐", home, "home", "meal"),
        seg("09:00", "10:00", "收拾包、换衣服准备出门", home, "home", "life"),
        seg("10:00", "11:00", "开车去" + name, "路上", "road", "commute",
            transit="drive", vehicle=car, detail="周末出城有点堵，停车场还不好找"),
        seg("11:00", "12:00", "到" + name + "，排队停车买票", place, "scenic", "play",
            transit="drive", vehicle=car, spot=name, detail="停车位找了半天", soothe=True),
        seg("12:00", "13:00", "在景区附近吃午饭", place + "·餐馆", "restaurant", "meal"),
        seg("13:00", "15:00", "逛" + name + "：" + project_a, place, "scenic", "play",
            spot=name, soothe=True),
        seg("15:00", "16:00", "找家店喝下午茶", place + "·茶饮店", "restaurant", "rest"),
        seg("16:00", "17:00", "继续逛" + name + "：" + project_b, place, "scenic", "play",
            spot=name, soothe=True),
        seg("17:00", "18:00", "开车回家", "路上", "road", "commute",
            transit="drive", vehicle=car, detail="晚高峰，堵在回家的路上"),
        seg("18:00", "19:00", "到家瘫一会儿", home, "home", "rest"),
        seg("19:00", "20:00", "吃晚饭", home, "home", "meal"),
        seg("20:00", "22:00", "整理照片、追剧", home, "home", "rest"),
        seg("22:00", "23:00", "洗澡护肤", home, "home", "life"),
        seg("23:00", "24:00", "睡觉", home, "home", "sleep"),
    ]
