# -*- coding: utf-8 -*-
"""玩水/海边/水上乐园骨架：纯函数，seg/choose 由 engine 注入。"""
from __future__ import annotations

import random


def swim_segments(ctx, seg, choose, swim_details):
    ctx = dict(ctx or {})
    home = ctx.get("home") or "家"
    swim = ctx.get("swim") or {}
    where = str(swim.get("place") or "")
    place_name = str(swim.get("spot") or "泳池")
    return [
        seg("00:00", "07:00", "睡觉", home, "home", "sleep"),
        seg("07:00", "08:00", "起床洗漱、吃早餐", home, "home", "life"),
        seg("08:00", "09:00", "收拾泳衣和毛巾，装进包里", home, "home", "life",
            detail="还带了防晒和换洗衣服"),
        seg("09:00", "10:00", "出门去" + where, "路上", "road", "commute",
            transit="subway", vehicle="地铁"),
        seg("10:00", "12:00", str(swim.get("activity") or ("在%s玩水" % place_name)),
            where, "scenic", "play", spot=place_name,
            detail=str(swim.get("detail") or ""), swim=True),
        seg("12:00", "13:00", "上岸冲一下，在附近吃午饭", where + "·附近小馆子",
            "restaurant", "meal"),
        seg("13:00", "15:00", "下午接着在%s玩" % place_name, where, "scenic", "play",
            spot=place_name, detail=choose(list(swim_details), random.Random(place_name), ""),
            swim=True),
        seg("15:00", "16:00", "冲个澡换好衣服，头发还没干", where, "scenic", "rest",
            spot=place_name, detail="头发湿着，懒得吹"),
        seg("16:00", "17:00", "坐车回家，路上有点犯困", "路上", "road", "commute",
            transit="subway", vehicle="地铁"),
        seg("17:00", "18:00", "到家把泳衣洗了晾上", home, "home", "life",
            detail="晒了一下午，脸有点红"),
        seg("18:00", "19:00", "吃晚饭", home, "home", "meal"),
        seg("19:00", "20:00", "窝在沙发上刷手机，腿有点酸", home, "home", "rest"),
        seg("20:00", "22:00", "洗完澡敷个面膜", home, "home", "rest"),
        seg("22:00", "23:00", "吹头发、护肤", home, "home", "life"),
        seg("23:00", "24:00", "睡觉", home, "home", "sleep"),
    ]
