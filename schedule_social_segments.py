# -*- coding: utf-8 -*-
"""朋友聚餐骨架：纯函数，seg/place_of 由 engine 注入。"""
from __future__ import annotations


def friend_dinner_segments(ctx, seg, place_of):
    home = ctx["home"]
    event = ctx.get("event") or {}
    if ctx.get("scenic") and ctx.get("good_weather"):
        spot = ctx.get("scenic") or {}
        name = str(spot.get("name") or "附近公园")
        place = place_of(ctx, name)
        car = ctx["car"]
        projects = [str(item) for item in (spot.get("projects") or []) if str(item)]
        project_a = projects[0] if projects else "景区主景点"
        return [
            seg("00:00", "08:00", "睡觉", home, "home", "sleep"),
            seg("08:00", "09:00", "起床吃早餐", home, "home", "meal"),
            seg("09:00", "10:00", "收拾出门", home, "home", "life"),
            seg("10:00", "11:00", "开车去" + name, "路上", "road", "commute",
                transit="drive", vehicle=car),
            seg("11:00", "12:00", "到景区，停车买票", place, "scenic", "play",
                transit="drive", vehicle=car, spot=name, soothe=True),
            seg("12:00", "13:00", "景区附近吃午饭", place + "·餐馆", "restaurant", "meal"),
            seg("13:00", "15:00", "逛" + name + "：" + project_a, place, "scenic", "play",
                spot=name, soothe=True),
            seg("15:00", "16:00", "喝下午茶", place + "·茶饮店", "restaurant", "rest"),
            seg("16:00", "17:00", "再逛一会儿、拍照", place, "scenic", "play", spot=name, soothe=True),
            seg("17:00", "18:00", "开车回家", "路上", "road", "commute",
                transit="drive", vehicle=car),
            seg("18:00", "19:00", "回家换衣服，出门赴约", "路上", "road", "commute", transit="taxi"),
            seg("19:00", "21:00", "和朋友吃饭、聊天", place_of(ctx, "饭店"), "restaurant", "social",
                event="friend_dinner", effects=event.get("effects") or {}, soothe=True),
            seg("21:00", "22:00", "饭后散步、吹吹风", "路上", "road", "other"),
            seg("22:00", "23:00", "回家洗澡", home, "home", "life"),
            seg("23:00", "24:00", "睡觉", home, "home", "sleep"),
        ]
    return [
        seg("00:00", "08:00", "睡觉", home, "home", "sleep"),
        seg("08:00", "09:00", "起床吃早餐", home, "home", "meal"),
        seg("09:00", "10:00", "收拾屋子", home, "home", "life"),
        seg("10:00", "12:00", "在家追剧、刷手机", home, "home", "rest"),
        seg("12:00", "13:00", "吃午饭", home, "home", "meal"),
        seg("13:00", "16:00", "睡午觉、发呆", home, "home", "rest"),
        seg("16:00", "17:00", "收拾一下准备出门", home, "home", "life"),
        seg("17:00", "18:00", "出门赴约", "路上", "road", "commute", transit="taxi"),
        seg("18:00", "20:00", "和朋友吃饭、聊天", place_of(ctx, "饭店"), "restaurant", "social",
            event="friend_dinner", effects=event.get("effects") or {}, soothe=True),
        seg("20:00", "21:00", "饭后散步", "路上", "road", "other"),
        seg("21:00", "22:00", "回家", home, "home", "rest"),
        seg("22:00", "23:00", "洗澡护肤", home, "home", "life"),
        seg("23:00", "24:00", "睡觉", home, "home", "sleep"),
    ]
