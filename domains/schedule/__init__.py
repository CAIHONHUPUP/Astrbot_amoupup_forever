# -*- coding: utf-8 -*-
"""日程域（pilot）：只定义「当天唯一事实视图」的契约，不 import 插件模块。

背景：聊天、日记、照片、面板都读同一份当天事实（amo_fact_timeline.build_day_view）。
以前每处各读日程/账本/健康，口径会分叉；把「视图里必须有什么」写成常量后，
消费方可以按契约写，视图改结构时契约测试先红。

契约测试见 tests/test_domain_boundaries.py。
"""
from __future__ import annotations

#: build_day_view 顶层必须有的键
DAY_VIEW_KEYS = ("day", "now", "events", "sources", "count")
#: events 里消费方可以依赖的字段（其余字段允许按来源增减）
EVENT_REQUIRED_FIELDS = ("time", "start", "end", "activity", "place", "source")
#: 视图里「此刻正在进行」那条事件的取法（amo_fact_timeline.current_view_event）
CURRENT_EVENT_FUNC = "current_view_event"
