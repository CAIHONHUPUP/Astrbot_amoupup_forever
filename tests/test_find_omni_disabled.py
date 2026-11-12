# -*- coding: utf-8 -*-
"""停用「万象画卷」后不能还能出图。

AstrBot 停用插件只把 metadata.activated 置 False，实例还挂在内存里；
`find_omni` 以前只看实例在不在，于是停用之后照样能生图——用户以为关了。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_image_common import find_omni  # noqa: E402


class Star:
    def generate_images_for_plugin(self, **kwargs):
        return {"success": True}


class Meta:
    def __init__(self, activated=True, star=True):
        self.activated = activated
        self.star_cls = Star() if star else None


class Context:
    def __init__(self, meta):
        self.meta = meta

    def get_registered_star(self, name):
        return self.meta


class Plugin:
    def __init__(self, meta):
        self.context = Context(meta)


class Holder:
    def __init__(self):
        self._omni = None
        self._omni_checked = 0.0


class FindOmniTests(unittest.TestCase):
    def test_enabled_plugin_is_returned(self):
        holder = Holder()
        self.assertIsNotNone(find_omni(Plugin(Meta(True)), holder, force=True))

    def test_disabled_plugin_is_ignored(self):
        holder = Holder()
        self.assertIsNone(find_omni(Plugin(Meta(False)), holder, force=True))

    def test_disabling_invalidates_cached_instance(self):
        holder = Holder()
        plugin = Plugin(Meta(True))
        self.assertIsNotNone(find_omni(plugin, holder, force=True))
        # 用户刚在后台点了停用
        plugin.context.meta = Meta(False)
        self.assertIsNone(find_omni(plugin, holder))

    def test_meta_without_activated_flag_still_works(self):
        meta = Meta(True)
        del meta.activated
        self.assertIsNotNone(find_omni(Plugin(meta), Holder(), force=True))


if __name__ == "__main__":
    unittest.main()
