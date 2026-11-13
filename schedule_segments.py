# -*- coding: utf-8 -*-
"""长假/宅家骨架：纯函数，seg 由 ScheduleEngine 传入。

拆出 schedule_engine.py 的 holiday-specific segments；
scenic/swim 仍留在 engine，后续轮次继续。
"""
from __future__ import annotations


def homebody_segments(ctx, seg):
    """宅家日：真正待在家，不去商场/景区/公司。"""
    home = ctx["home"]
    return [
        seg("00:00", "08:00", "睡觉", home, "home", "sleep"),
        seg("08:00", "09:00", "起床洗漱、吃早餐", home, "home", "meal"),
        seg("09:00", "10:00", "赖在床上刷手机", home, "home", "rest",
            detail="不急着起，窗帘都没拉开"),
        seg("10:00", "11:00", "收拾屋子、洗衣服", home, "home", "life"),
        seg("11:00", "12:00", "慢慢做午饭", home, "home", "meal"),
        seg("12:00", "13:00", "吃午饭", home, "home", "meal"),
        seg("13:00", "15:00", "窝在沙发上睡午觉", home, "home", "rest"),
        seg("15:00", "16:00", "泡杯茶，翻两页书", home, "home", "rest"),
        seg("16:00", "18:00", "追剧、看综艺", home, "home", "rest"),
        seg("18:00", "19:00", "做晚饭", home, "home", "meal"),
        seg("19:00", "21:00", "窝在沙发上刷手机", home, "home", "rest"),
        seg("21:00", "22:00", "洗澡护肤", home, "home", "life"),
        seg("22:00", "23:00", "躺床上看书", home, "home", "rest"),
        seg("23:00", "24:00", "睡觉", home, "home", "sleep"),
    ]


def shopping_segments(ctx, seg):
    """长假逛街：整天以商场为主，不去景区。"""
    home = ctx["home"]
    plan = ctx.get("holiday_plan") or {}
    name = str(plan.get("place") or "商场")
    car = ctx["car"]
    return [
        seg("00:00", "08:00", "睡觉", home, "home", "sleep"),
        seg("08:00", "09:00", "起床洗漱、吃早餐", home, "home", "meal"),
        seg("09:00", "10:00", "收拾一下，准备出门", home, "home", "life"),
        seg("10:00", "11:00", "开车去" + name, "路上", "road", "commute",
            transit="drive", vehicle=car),
        seg("11:00", "13:00", "在" + name + "逛店、看橱窗", name, "mall", "play",
            spot=name),
        seg("13:00", "14:00", "在商场里吃午饭", name + "·餐厅", "restaurant", "meal"),
        seg("14:00", "17:00", "继续逛，试衣服、买点小东西", name, "mall", "play",
            spot=name),
        seg("17:00", "18:00", "开车回家", "路上", "road", "commute",
            transit="drive", vehicle=car),
        seg("18:00", "19:00", "到家瘫一会儿", home, "home", "rest"),
        seg("19:00", "20:00", "吃晚饭", home, "home", "meal"),
        seg("20:00", "22:00", "整理买回来的东西、追剧", home, "home", "rest"),
        seg("22:00", "23:00", "洗澡护肤", home, "home", "life"),
        seg("23:00", "24:00", "睡觉", home, "home", "sleep"),
    ]


def amusement_segments(ctx, seg):
    """长假游乐园：入口、排队、过山车/巡游，zone=playground。"""
    home = ctx["home"]
    plan = ctx.get("holiday_plan") or {}
    name = str(plan.get("place") or "游乐园")
    car = ctx["car"]
    return [
        seg("00:00", "08:00", "睡觉", home, "home", "sleep"),
        seg("08:00", "09:00", "起床洗漱、吃早餐", home, "home", "meal"),
        seg("09:00", "10:00", "背小包、防晒，准备出门", home, "home", "life"),
        seg("10:00", "11:00", "开车去" + name, "路上", "road", "commute",
            transit="drive", vehicle=car, detail="停车场排队"),
        seg("11:00", "12:00", "进园、排队玩第一个项目", name, "playground", "play",
            spot=name),
        seg("12:00", "13:00", "在园里吃午饭", name + "·餐厅", "restaurant", "meal"),
        seg("13:00", "16:00", "玩过山车、旋转木马，看巡游", name, "playground", "play",
            spot=name),
        seg("16:00", "17:00", "买杯饮料、坐着歇脚", name, "playground", "rest",
            spot=name),
        seg("17:00", "18:00", "开车回家", "路上", "road", "commute",
            transit="drive", vehicle=car),
        seg("18:00", "19:00", "到家瘫一会儿", home, "home", "rest"),
        seg("19:00", "20:00", "吃晚饭", home, "home", "meal"),
        seg("20:00", "22:00", "翻今天拍的照片、追剧", home, "home", "rest"),
        seg("22:00", "23:00", "洗澡护肤", home, "home", "life"),
        seg("23:00", "24:00", "睡觉", home, "home", "sleep"),
    ]
