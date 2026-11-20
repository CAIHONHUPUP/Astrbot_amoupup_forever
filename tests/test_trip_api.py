# -*- coding: utf-8 -*-
"""旅游一期 10A：配置 schema / 路由 / 面板 API / /amo trip 指令。

每一条都对「改回去就红」：删掉 schema 键、把 stop 放出去、删掉路由、
去掉主人校验、把 cancel 放到出发后，都会在这里失败。
"""
from __future__ import annotations

import ast
import asyncio
import datetime as dt
import json
import os
import re
import sys
import unittest
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_trip as trip  # noqa: E402
from fixtures import FixedClock  # noqa: E402
from amo_api import ApiMixin  # noqa: E402
from amo_api_trip import TripApiMixin  # noqa: E402
from amo_commands import CommandMixin  # noqa: E402
from amo_route_table import (  # noqa: E402
    ROUTE_COUNT_EXPECTED,
    ROUTE_SPECS,
)
from amo_routes import WRITE_ROUTES, WebRouteMixin  # noqa: E402

TODAY = "2026-11-16"
#: 出发用例都按 TODAY 造数据；把时钟钉在同一天的白天，状态机才不会用真实墙钟
#: 把「出发后 5 小时」直接判成已到达（0 点后必红）。
FROZEN = dt.datetime(2026, 11, 16, 10, 0)

TRAVEL_DEFAULTS: dict[str, object] = {
    "travel_enabled": True,
    "travel_cooldown_days": 20,
    "travel_max_per_year": 6,
    "travel_prob_holiday": 0.6,
    "travel_prob_low": 0.35,
    "travel_days_min": 2,
    "travel_days_max": 3,
    "travel_price_multiplier": 1.6,
    "travel_budget_min": 2000,
    "travel_budget_max": 5000,
    "travel_assassin_prob": 0.06,
    "travel_return_cost": 800,
    "travel_hotel_min": 300,
    "travel_food_min": 100,
    "travel_silence_ok": True,
}

_TYPE_NAMES = {bool: "bool", int: "int", float: "float"}


class FakeDB:
    def __init__(self) -> None:
        self.kv: dict[str, Any] = {}

    def kv_get(self, key: str, default: object = None) -> object:
        return self.kv.get(key, default)

    def kv_set(self, key: str, value: object) -> None:
        self.kv[key] = value


class FakeWallet:
    def __init__(self, balance: int = 8000) -> None:
        self._balance = balance
        self.reads = 0

    def balance(self) -> int:
        self.reads += 1
        return self._balance


class FakeState:
    def __init__(self) -> None:
        self.moods: list[int] = []
        self.spots = [{"city": "杭州", "name": "西湖"},
                      {"city": "厦门", "name": "鼓浪屿"},
                      {"city": "大理", "name": "洱海"},
                      {"city": "成都", "name": "宽窄巷子"}]

    def scenic_spots(self) -> list[dict[str, str]]:
        return list(self.spots)

    def add_mood(self, delta: int) -> None:
        self.moods.append(delta)


class FakeEvent:
    def __init__(self, text: str = "") -> None:
        self.message_str = text
        self.unified_msg_origin = "test:private:1001"


class FakePlugin(TripApiMixin, CommandMixin):
    """只绑这一个 mixin 的假插件：够 engine / api / 指令三条路走。"""

    def __init__(self, config: dict[str, object] | None = None,
                 body: dict[str, object] | None = None,
                 balance: int = 8000, admin: bool = True) -> None:
        self.db = FakeDB()
        self.wallet = FakeWallet(balance)
        self.state = FakeState()
        self.body = dict(body or {})
        self.admin = admin
        self.conf: dict[str, Any] = {
            "travel_enabled": True, "travel_cooldown_days": 20,
            "travel_max_per_year": 6, "travel_days_max": 3,
            "travel_budget_max": 5000, "travel_hotel_min": 300,
            "travel_food_min": 100, "travel_return_cost": 800,
            "travel_price_multiplier": 1.6, "travel_prob_holiday": 0.6,
            "travel_prob_low": 0.35,
        }
        if config:
            self.conf.update(config)

    async def _ensure_ready(self) -> None:
        return None

    async def _body(self) -> dict[str, object]:
        return dict(self.body)

    def _q(self, key: str, default: object = None) -> object:
        return default

    def today(self) -> str:
        return TODAY

    def _is_admin(self, event: FakeEvent) -> bool:
        return self.admin

    def _canonical_uid(self, event: FakeEvent) -> str:
        return "1001"

    def cfg_int(self, name: str, default: int = 0) -> int:
        return int(self.conf.get(name, default))

    def cfg_float(self, name: str, default: float = 0.0) -> float:
        return float(self.conf.get(name, default))

    def cfg_bool(self, name: str, default: bool = False) -> bool:
        return bool(self.conf.get(name, default))

    def cfg_str(self, name: str, default: str = "") -> str:
        return str(self.conf.get(name, default))


#: Phase 8：旅游拆成门面 + 4 个模块，schema 口径要扫整族源码。
TRIP_MODULE_FILES = ("amo_trip.py", "amo_trip_state.py", "amo_trip_cost.py",
                     "amo_trip_schedule.py", "amo_trip_report.py")


def load_schema() -> dict[str, dict[str, object]]:
    with open(os.path.join(ROOT, "_conf_schema.json"), encoding="utf-8") as fh:
        return json.load(fh)


class TravelSchemaTests(unittest.TestCase):
    def test_travel_defaults_present_and_typed(self) -> None:
        schema = load_schema()
        for key, expected in TRAVEL_DEFAULTS.items():
            self.assertIn(key, schema, "schema 少了 " + key)
            self.assertEqual(schema[key]["default"], expected, key)
            self.assertEqual(schema[key]["type"], _TYPE_NAMES[type(expected)], key)

    def test_schema_defaults_match_engine_reads(self) -> None:
        """旅游各模块里每个 cfg(plugin, "travel_*", 默认值) 都必须和 schema 一致。

        Phase 8 把 amo_trip.py 拆成门面 + state/cost/schedule/report，
        所以这里扫描整个旅游模块族（口径不变：引擎读的默认值必须与 schema 一致）。
        """
        schema = load_schema()
        source = ""
        for name in TRIP_MODULE_FILES:
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                source += fh.read() + "\n"
        reads = dict(re.findall(r'cfg\(plugin, "(travel_[a-z_]+)", ([^)]+?)\)', source))
        self.assertGreaterEqual(len(reads), 10)
        for key, literal in reads.items():
            self.assertIn(key, schema, "引擎读了但 schema 没有：" + key)
            self.assertEqual(schema[key]["default"], ast.literal_eval(literal), key)


class TripRouteTests(unittest.TestCase):
    def test_route_count_and_trip_methods(self) -> None:
        self.assertEqual(len(ROUTE_SPECS), ROUTE_COUNT_EXPECTED)
        by_path = {path: (handler, tuple(methods))
                   for path, handler, methods in ROUTE_SPECS}
        self.assertEqual(by_path["/trip/status"], ("api_trip_status", ("GET",)))
        self.assertEqual(by_path["/trip/start"], ("api_trip_start", ("POST",)))
        self.assertEqual(by_path["/trip/cancel"], ("api_trip_cancel", ("POST",)))
        missing = [name for name in ("api_trip_status", "api_trip_start", "api_trip_cancel")
                   if not hasattr(ApiMixin, name)]
        self.assertEqual(missing, [])

    def test_start_cancel_are_write_routes(self) -> None:
        self.assertIn("/trip/start", WRITE_ROUTES)
        self.assertIn("/trip/cancel", WRITE_ROUTES)
        self.assertNotIn("/trip/status", WRITE_ROUTES)

    def test_write_route_forces_post(self) -> None:
        class Ctx:
            def __init__(self) -> None:
                self.calls: list[tuple[str, list[str], str]] = []

            def register_web_api(self, route: str, handler: object,
                                 methods: list[str], desc: str) -> bool:
                self.calls.append((route, list(methods), desc))
                return True

        class Fake(WebRouteMixin):
            def __init__(self) -> None:
                self.context = Ctx()

            def _web_guard(self, handler: object, is_write: bool = False) -> object:
                return handler

        fake = Fake()
        fake._safe_register("/Astrbot_amoupup_forever/trip/start", lambda: None,
                            ["GET", "POST"], "/trip/start")
        route, methods, desc = fake.context.calls[-1]
        self.assertEqual(desc, "/trip/start")
        self.assertEqual(methods, ["POST"])


class TripApiTests(unittest.TestCase):
    def test_start_outbound_and_status_matches_kv(self) -> None:
        frozen = FixedClock(FROZEN)
        frozen.start()
        self.addCleanup(frozen.stop)
        plugin = FakePlugin(body={"city": "大理", "days": 2})
        result = asyncio.run(plugin.api_trip_start())
        self.assertEqual(result["code"], 0, result)
        raw = plugin.db.kv["trip"]
        self.assertEqual(raw["phase"], "outbound")
        self.assertEqual(raw["city"], "大理")
        self.assertEqual(raw["days"], 2)
        status = asyncio.run(plugin.api_trip_status())
        self.assertEqual(status["code"], 0)
        self.assertEqual(status["data"], {
            "phase": raw["phase"], "city": raw["city"],
            "day_no": raw["day_no"], "days": raw["days"], "spent": raw["spent"],
        })

    def test_cancel_only_before_departure(self) -> None:
        plugin = FakePlugin()
        trip.save(plugin, trip.new_trip("杭州", TODAY, 2))
        result = asyncio.run(plugin.api_trip_cancel())
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(plugin.db.kv["trip"]["phase"], "done")
        self.assertEqual(plugin.db.kv["trip"]["flags"]["cancelled"], True)

        plugin2 = FakePlugin()
        state = trip.new_trip("杭州", TODAY, 2)
        state["phase"] = "outbound"
        trip.save(plugin2, state)
        blocked = asyncio.run(plugin2.api_trip_cancel())
        self.assertEqual(blocked["code"], 1)
        self.assertIn("回来吧", blocked["msg"])
        self.assertEqual(plugin2.db.kv["trip"]["phase"], "outbound")

    def test_cancel_without_trip_is_human(self) -> None:
        plugin = FakePlugin()
        result = asyncio.run(plugin.api_trip_cancel())
        self.assertEqual(result["code"], 1)
        self.assertIn("没有在计划的旅行", result["msg"])

    def test_start_bad_city_lists_candidates(self) -> None:
        plugin = FakePlugin(body={"city": "贵阳"})
        result = asyncio.run(plugin.api_trip_start())
        self.assertEqual(result["code"], 1)
        self.assertIn("杭州", result["msg"])
        self.assertNotIn("trip", plugin.db.kv)

    def test_start_force_bypasses_cooldown(self) -> None:
        frozen = FixedClock(FROZEN)
        frozen.start()
        self.addCleanup(frozen.stop)
        plugin = FakePlugin(body={"city": "大理", "force": True})
        plugin.db.kv[trip.COOLDOWN_KEY] = TODAY
        result = asyncio.run(plugin.api_trip_start())
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(plugin.db.kv["trip"]["phase"], "outbound")

    def test_disabled_start_refused_and_silent(self) -> None:
        plugin = FakePlugin(config={"travel_enabled": False})
        result = asyncio.run(plugin.api_trip_start())
        self.assertEqual(result["code"], 1)
        self.assertIn("travel_enabled", result["msg"])
        self.assertNotIn("trip", plugin.db.kv)
        self.assertEqual(plugin.wallet.reads, 0, "关了就不该读钱包")
        status = asyncio.run(plugin.api_trip_status())
        self.assertEqual(status["data"]["phase"], "")
        self.assertEqual(status["data"]["spent"], 0)


class TripCommandTests(unittest.TestCase):
    def call_cmd(self, plugin: FakePlugin, args: list[str],
                 message: str = "") -> str:
        event = FakeEvent(message or ("/amo " + " ".join(args)))
        return asyncio.run(CommandMixin._cmd_trip(plugin, event, args, "1001"))

    def test_start_parses_city_and_days(self) -> None:
        frozen = FixedClock(FROZEN)
        frozen.start()
        self.addCleanup(frozen.stop)
        plugin = FakePlugin()
        text = self.call_cmd(plugin, ["trip", "start", "大理", "2"])
        self.assertIn("出发去大理", text)
        self.assertEqual(plugin.db.kv["trip"]["phase"], "outbound")
        self.assertEqual(plugin.db.kv["trip"]["city"], "大理")
        self.assertEqual(plugin.db.kv["trip"]["days"], 2)

    def test_status_matches_trip(self) -> None:
        frozen = FixedClock(FROZEN)
        frozen.start()
        self.addCleanup(frozen.stop)
        plugin = FakePlugin()
        self.call_cmd(plugin, ["trip", "start", "大理", "2"])
        text = self.call_cmd(plugin, ["trip", "status"])
        info = trip.status(plugin)
        self.assertIn(str(info["city"]), text)
        self.assertIn("第 1 天", text)
        self.assertIn("共 %d 天" % info["days"], text)

    def test_cancel_only_before_departure(self) -> None:
        plugin = FakePlugin()
        trip.save(plugin, trip.new_trip("杭州", TODAY, 2))
        text = self.call_cmd(plugin, ["trip", "cancel"])
        self.assertIn("取消", text)
        self.assertEqual(plugin.db.kv["trip"]["phase"], "done")

        plugin2 = FakePlugin()
        state = trip.new_trip("杭州", TODAY, 2)
        state["phase"] = "outbound"
        trip.save(plugin2, state)
        blocked = self.call_cmd(plugin2, ["trip", "cancel"])
        self.assertIn("回来吧", blocked)
        self.assertEqual(plugin2.db.kv["trip"]["phase"], "outbound")

    def test_owner_only(self) -> None:
        plugin = FakePlugin(admin=False)
        text = self.call_cmd(plugin, ["trip", "start", "大理"])
        self.assertIn("只给主人", text)
        self.assertNotIn("trip", plugin.db.kv)

    def test_disabled_start_is_refused(self) -> None:
        plugin = FakePlugin(config={"travel_enabled": False})
        text = self.call_cmd(plugin, ["trip", "start", "大理"])
        self.assertIn("没开", text)
        self.assertNotIn("trip", plugin.db.kv)

    def test_bad_days_token_is_refused(self) -> None:
        plugin = FakePlugin()
        text = self.call_cmd(plugin, ["trip", "start", "大理", "两天"])
        self.assertIn("天数要写数字", text)
        self.assertNotIn("trip", plugin.db.kv)

    def test_no_subcommand_shows_idle_status(self) -> None:
        plugin = FakePlugin()
        self.assertEqual(self.call_cmd(plugin, ["trip"]), "现在没在旅游。")

    def test_unknown_subcommand_shows_usage(self) -> None:
        plugin = FakePlugin()
        self.assertIn("用法：/amo trip", self.call_cmd(plugin, ["trip", "飞走"]))

    def test_dispatch_routes_trip_subcommand(self) -> None:
        plugin = FakePlugin()
        event = FakeEvent("/amo trip status")
        self.assertEqual(asyncio.run(CommandMixin._handle_command(plugin, event)),
                         "现在没在旅游。")


if __name__ == "__main__":
    unittest.main()
