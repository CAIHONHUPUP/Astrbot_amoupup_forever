# -*- coding: utf-8 -*-
"""Phase 10：面板拆分（index.html / assets/*）与日记质检入口的静态验收。"""
from __future__ import annotations

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from panel_source import panel_source  # noqa: E402

DASH = os.path.join(ROOT, "views", "dashboard")
ASSETS = ("panel.css", "api-client.js", "panel.js", "panel-views.js")


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class PanelSplitFilesTests(unittest.TestCase):
    def setUp(self):
        self.index = read_text(os.path.join(DASH, "index.html"))
        self.full = panel_source(ROOT)

    def asset(self, name):
        return read_text(os.path.join(DASH, "assets", name))

    def test_index_has_no_inline_style(self):
        self.assertNotIn("<style", self.index)
        self.assertNotIn("</style>", self.index)

    def test_index_has_no_inline_script_body(self):
        self.assertNotIn("<script>", self.index)
        self.assertEqual(self.index.count("<script"), self.index.count("<script src="))

    def test_index_links_only_relative_assets(self):
        self.assertIn('<link rel="stylesheet" href="assets/panel.css" />', self.index)
        refs = re.findall(r'(?:href|src)="([^"]+)"', self.index)
        self.assertEqual(refs, [
            "assets/panel.css", "assets/api-client.js",
            "assets/panel.js", "assets/panel-views.js", "assets/panel-user.js",
        ])

    def test_no_root_or_parent_resource_paths(self):
        refs = re.findall(r'(?:href|src)="([^"]+)"', self.index)
        self.assertTrue(refs)
        for ref in refs:
            self.assertFalse(ref.startswith(("/", "\\")), ref)
            self.assertNotIn("..", ref)
            self.assertTrue(ref.startswith("assets/"), ref)

    def test_assets_exist_and_are_nonempty(self):
        for name in ASSETS:
            path = os.path.join(DASH, "assets", name)
            self.assertTrue(os.path.isfile(path), name)
            self.assertGreater(os.path.getsize(path), 1024, name)

    def test_index_line_budget(self):
        self.assertLess(len(self.index.splitlines()), 200)

    def test_asset_line_budgets(self):
        for name in ASSETS:
            lines = len(self.asset(name).splitlines())
            self.assertLess(lines, 2500, "%s %d 行" % (name, lines))

    def test_assets_live_under_dashboard(self):
        dash = os.path.normpath(DASH)
        for name in ASSETS:
            path = os.path.normpath(os.path.join(DASH, "assets", name))
            self.assertEqual(os.path.commonpath([path, dash]), dash)

    def test_panel_source_helper_restores_full_panel(self):
        self.assertIn("--bg1: #eaf8ff", self.full)
        self.assertIn("function render(data)", self.full)
        self.assertIn("function load() {", self.full)
        self.assertNotIn("<script src=", self.full)
        self.assertNotIn('<link rel="stylesheet"', self.full)


class PanelApiTests(unittest.TestCase):
    def setUp(self):
        self.api = read_text(os.path.join(DASH, "assets", "api-client.js"))
        self.panel = read_text(os.path.join(DASH, "assets", "panel.js"))

    def test_api_client_declares_panelapi(self):
        self.assertIn("window.PanelApi = {", self.api)
        for name in ("call", "httpCall", "bridgeCall", "routeCandidates", "withTimeout",
                     "actionTimeout", "safeDetail", "friendlyError", "toast", "normalize"):
            self.assertIn("%s: %s," % (name, name), self.api, name)

    def test_api_client_declares_each_exported_function(self):
        for name in ("officialBridge", "bridge", "actionTimeout", "withTimeout",
                     "routeCandidates", "bridgeCall", "httpCall", "call",
                     "safeDetail", "friendlyError", "toast", "normalize"):
            self.assertRegex(self.api, r"(?:async )?function %s\(" % name, name)

    def test_api_client_keeps_legacy_globals(self):
        for name in ("call", "toast", "normalize", "safeDetail", "friendlyError"):
            self.assertIn("window.%s = %s;" % (name, name), self.api, name)

    def test_api_client_holds_bridge_and_http_only(self):
        for marker in ("function officialBridge(", "function createInlineBridge(",
                       "function routeCandidates(", "function bridgeCall(", "function httpCall("):
            self.assertIn(marker, self.api, marker)
        self.assertNotIn("function render(data)", self.api)

    def test_panel_js_uses_panelapi(self):
        self.assertIn("window.PanelApi", self.panel)
        self.assertIn("function render(data)", self.panel)
        self.assertNotIn("window.PanelApi = {", self.panel)


class ScheduleRowRenderTests(unittest.TestCase):
    """日程行必须把时间/活动/地点都挂上去（2026-11-17 删按钮时误删过 appendChild）。"""

    def setUp(self):
        self.panel = read_text(os.path.join(DASH, "assets", "panel.js"))

    def test_row_appends_time_activity_meta(self):
        for name in ("hourTime", "activity", "metaLine"):
            self.assertIn("line.appendChild(%s)" % name, self.panel, name)

    def test_no_manual_edit_delete_buttons_on_rows(self):
        self.assertNotIn('editButton.textContent = "改"', self.panel)
        self.assertNotIn('deleteButton.textContent = "删"', self.panel)

class DiaryAuditEntryTests(unittest.TestCase):
    def setUp(self):
        self.index = read_text(os.path.join(DASH, "index.html"))
        self.panel = read_text(os.path.join(DASH, "assets", "panel.js"))

    def test_audit_button_and_result_area(self):
        self.assertEqual(self.index.count('id="diaryAudit"'), 1)
        self.assertEqual(self.index.count('id="diaryAuditText"'), 1)
        self.assertIn("日记质检", self.index)

    def test_audit_calls_existing_get_route(self):
        self.assertIn('call("diary/audit"', self.panel)
        self.assertIn("{ get: true }", self.panel)
        views = read_text(os.path.join(DASH, "assets", "panel-views.js"))
        self.assertNotIn('call("diary/audit"', views)

    def test_audit_failure_uses_friendly_error(self):
        self.assertIn("window.PanelApi.friendlyError", self.panel)

    def test_audit_route_is_read_only_and_count_unchanged(self):
        from amo_route_table import ROUTE_COUNT_EXPECTED, ROUTE_SPECS
        paths = {path: methods for path, _handler, methods in ROUTE_SPECS}
        self.assertEqual(paths["/diary/audit"], ("GET",))
        self.assertEqual(len(ROUTE_SPECS), ROUTE_COUNT_EXPECTED)
        self.assertEqual(len(ROUTE_SPECS), 113)


if __name__ == "__main__":
    unittest.main()
