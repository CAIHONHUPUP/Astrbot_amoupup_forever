# -*- coding: utf-8 -*-
"""出差/生病/痛经/周末加班骨架：纯函数，seg/with_city 由 engine 注入。"""
from __future__ import annotations


def business_trip_segments(ctx, seg, with_city):
    home = ctx["home"]
    event = ctx.get("event") or {}
    city = str(event.get("city") or "成都")
    where = str(event.get("where") or ("外省·" + city))
    office = where + "·客户公司"
    hotel = where + "·酒店"
    effects = event.get("effects") or {}
    return [
        seg("00:00", "06:00", "睡觉", home, "home", "sleep"),
        seg("06:00", "07:00", "起床收拾行李", home, "home", "life"),
        seg("07:00", "08:00", "接到外派通知，打车去" + with_city(ctx, "北站"), "路上", "road", "commute",
            transit="taxi", event="business_trip", effects=effects),
        seg("08:00", "09:00", "进站候车", with_city(ctx, "北站"), "station", "commute", transit="train"),
        seg("09:00", "12:00", "坐高铁去" + city, "路上", "road", "commute",
            transit="train", detail="临时买票，只剩二等座"),
        seg("12:00", "13:00", "到站吃午饭", where + "·车站附近", "restaurant", "meal"),
        seg("13:00", "14:00", "打车去客户公司", "路上", "road", "commute", transit="taxi"),
        seg("14:00", "18:00", "在客户公司处理紧急项目", office, "office", "work"),
        seg("18:00", "19:00", "打车去酒店", "路上", "road", "commute", transit="taxi"),
        seg("19:00", "20:00", "酒店附近吃晚饭", where + "·小吃街", "restaurant", "meal"),
        seg("20:00", "21:00", "回酒店整理资料", hotel, "hotel", "work"),
        seg("21:00", "23:00", "在酒店休息", hotel, "hotel", "rest"),
        seg("23:00", "24:00", "睡觉", hotel, "hotel", "sleep"),
    ]


def sick_rest_segments(ctx, seg):
    home = ctx["home"]
    health = ctx.get("health") or {}
    treatment = str(health.get("treatment") or "")
    if "打吊水" in treatment or "看医生" in treatment:
        hospital = "医院"
        drip = "打吊水" in treatment
        if drip:
            return [
                seg("00:00", "08:00", "睡觉", home, "home", "sleep"),
                seg("08:00", "09:00", "起床吃早餐", home, "home", "meal"),
                seg("09:00", "10:00", "收拾一下去医院", home, "home", "life"),
                seg("10:00", "13:00", "在医院挂号、排队、打吊水",
                    hospital, "hospital", "health", detail="吊水约 3 小时"),
                seg("13:00", "14:00", "回家吃午饭", home, "home", "meal"),
                seg("14:00", "17:00", "卧床休息、捂汗", home, "home", "rest"),
                seg("17:00", "18:00", "吃药、量体温", home, "home", "life"),
                seg("18:00", "19:00", "喝点粥当晚饭", home, "home", "meal"),
                seg("19:00", "22:00", "早早躺着刷手机", home, "home", "rest"),
                seg("22:00", "23:00", "洗漱", home, "home", "life"),
                seg("23:00", "24:00", "睡觉", home, "home", "sleep"),
            ]
        return [
            seg("00:00", "08:00", "睡觉", home, "home", "sleep"),
            seg("08:00", "09:00", "起床吃早餐", home, "home", "meal"),
            seg("09:00", "10:00", "收拾一下去医院", home, "home", "life"),
            seg("10:00", "12:00", "在诊室看医生、拿药", hospital, "hospital", "health", detail="看诊拿药"),
            seg("12:00", "13:00", "回家吃午饭", home, "home", "meal"),
            seg("13:00", "16:00", "卧床休息、捂汗", home, "home", "rest"),
            seg("16:00", "17:00", "吃药、量体温", home, "home", "life"),
            seg("17:00", "18:00", "靠在沙发上发呆", home, "home", "rest"),
            seg("18:00", "19:00", "喝点粥当晚饭", home, "home", "meal"),
            seg("19:00", "22:00", "早早躺着刷手机", home, "home", "rest"),
            seg("22:00", "23:00", "洗漱", home, "home", "life"),
            seg("23:00", "24:00", "睡觉", home, "home", "sleep"),
        ]
    return [
        seg("00:00", "08:00", "睡觉", home, "home", "sleep"),
        seg("08:00", "09:00", "起床吃早餐", home, "home", "meal"),
        seg("09:00", "10:00", "吃药、量体温", home, "home", "life"),
        seg("10:00", "12:00", "窝在床上休息", home, "home", "rest"),
        seg("12:00", "13:00", "简单吃点午饭", home, "home", "meal"),
        seg("13:00", "16:00", "睡午觉、捂汗", home, "home", "rest"),
        seg("16:00", "17:00", "起来喝水、吃药", home, "home", "life"),
        seg("17:00", "18:00", "靠在沙发上发呆", home, "home", "rest"),
        seg("18:00", "19:00", "喝点粥当晚饭", home, "home", "meal"),
        seg("19:00", "22:00", "早早躺着刷手机", home, "home", "rest"),
        seg("22:00", "23:00", "洗漱", home, "home", "life"),
        seg("23:00", "24:00", "睡觉", home, "home", "sleep"),
    ]


def period_rest_segments(ctx, seg):
    """痛经卧床：热水袋、红糖水，不是「吃药量体温」。"""
    home = ctx["home"]
    return [
        seg("00:00", "08:00", "睡得不太安稳", home, "home", "sleep"),
        seg("08:00", "09:00", "起床吃早餐", home, "home", "meal"),
        seg("09:00", "11:00", "抱着热水袋窝在床上", home, "home", "rest"),
        seg("11:00", "12:00", "给自己冲杯红糖水", home, "home", "life"),
        seg("12:00", "13:00", "简单吃点午饭", home, "home", "meal"),
        seg("13:00", "16:00", "疼得没力气，继续躺着", home, "home", "rest"),
        seg("16:00", "17:00", "煮点热乎的，暖暖肚子", home, "home", "life"),
        seg("17:00", "18:00", "靠在沙发上发会儿呆", home, "home", "rest"),
        seg("18:00", "19:00", "喝点粥当晚饭", home, "home", "meal"),
        seg("19:00", "22:00", "早早躺下刷手机", home, "home", "rest"),
        seg("22:00", "23:00", "洗漱", home, "home", "life"),
        seg("23:00", "24:00", "睡觉", home, "home", "sleep"),
    ]


def overtime_segments(ctx, seg):
    home = ctx["home"]
    office = ctx["workplace"]
    car = ctx["car"]
    event = ctx.get("event") or {}
    return [
        seg("00:00", "08:00", "睡觉", home, "home", "sleep"),
        seg("08:00", "09:00", "起床吃早餐", home, "home", "meal"),
        seg("09:00", "10:00", "本来打算在家躺一天", home, "home", "rest"),
        seg("10:00", "11:00", "接到电话，" + str(event.get("task") or "处理点急事"), home, "home", "work",
            event="overtime", effects=event.get("effects") or {}),
        seg("11:00", "12:00", "开车赶去公司", "路上", "road", "commute",
            transit="drive", vehicle=car, detail="周末也堵，心里更烦了"),
        seg("12:00", "13:00", "在公司楼下随便吃一口", office + "附近", "restaurant", "meal"),
        seg("13:00", "18:00", "在公司" + str(event.get("task") or "处理事情") + "、被拉去开会", office, "office", "work"),
        seg("18:00", "19:00", "下班回家", "路上", "road", "commute",
            transit="drive", vehicle=car),
        seg("19:00", "20:00", "到家吃晚饭", home, "home", "meal"),
        seg("20:00", "22:00", "瘫着不想动", home, "home", "rest"),
        seg("22:00", "23:00", "洗澡", home, "home", "life"),
        seg("23:00", "24:00", "睡觉", home, "home", "sleep"),
    ]
