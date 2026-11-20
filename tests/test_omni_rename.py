# -*- coding: utf-8 -*-
"""生图插件改名后的握手：新名优先、旧名回退（legacy fallback）、停用不串用、别名不重复。"""
from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_image_common as common  # noqa: E402


class _StarMeta:
    def __init__(self, name, instance=None, activated=True, version="3.5.0", display="我能永远定格你"):
        self.name = name
        self.star_cls = instance
        self.activated = activated
        self.version = version
        self.display_name = display


class _OmniInstance:
    def generate_images_for_plugin(self, *args, **kwargs):
        return []


class _Context:
    def __init__(self, metas):
        self.metas = dict(metas)
        self.queried = []

    def get_registered_star(self, name):
        self.queried.append(name)
        return self.metas.get(name)


class _Plugin:
    def __init__(self, context):
        self.context = context


class _Holder:
    def __init__(self):
        self._omni = None
        self._omni_checked = 0.0


def _find(metas, force=True):
    context = _Context(metas)
    holder = _Holder()
    return common.find_omni(_Plugin(context), holder, force=force), holder, context


class RenameHandshakeTests(unittest.TestCase):
    def test_new_plugin_name_is_first_choice(self):
        instance = _OmniInstance()
        found, _, context = _find({common.OMNI_PLUGIN_NAME: _StarMeta(common.OMNI_PLUGIN_NAME, instance)})
        self.assertIs(found, instance)
        self.assertEqual(context.queried, [common.OMNI_PLUGIN_NAME])

    def test_legacy_plugin_name_still_handshakes(self):
        instance = _OmniInstance()
        found, _, context = _find({common.OMNI_PLUGIN_NAME_LEGACY: _StarMeta(
            common.OMNI_PLUGIN_NAME_LEGACY, instance, version="3.4.3", display="万象画卷·能陪自用版")})
        self.assertIs(found, instance)
        self.assertEqual(context.queried, [common.OMNI_PLUGIN_NAME, common.OMNI_PLUGIN_NAME_LEGACY])

    def test_both_names_loaded_picks_one_without_duplicate(self):
        new_instance, old_instance = _OmniInstance(), _OmniInstance()
        found, _, context = _find({
            common.OMNI_PLUGIN_NAME: _StarMeta(common.OMNI_PLUGIN_NAME, new_instance),
            common.OMNI_PLUGIN_NAME_LEGACY: _StarMeta(common.OMNI_PLUGIN_NAME_LEGACY, old_instance),
        })
        self.assertIs(found, new_instance)
        self.assertEqual(context.queried, [common.OMNI_PLUGIN_NAME])

    def test_disabled_new_name_falls_back_to_legacy(self):
        old_instance = _OmniInstance()
        found, _, _ = _find({
            common.OMNI_PLUGIN_NAME: _StarMeta(common.OMNI_PLUGIN_NAME, _OmniInstance(), activated=False),
            common.OMNI_PLUGIN_NAME_LEGACY: _StarMeta(common.OMNI_PLUGIN_NAME_LEGACY, old_instance),
        })
        self.assertIs(found, old_instance)

    def test_disabled_plugins_do_not_reuse_cached_instance(self):
        cached = _OmniInstance()
        context = _Context({common.OMNI_PLUGIN_NAME: _StarMeta(common.OMNI_PLUGIN_NAME, cached, activated=False)})
        holder = _Holder()
        holder._omni = cached
        holder._omni_checked = time.monotonic()
        self.assertIsNone(common.find_omni(_Plugin(context), holder))
        self.assertIsNone(holder._omni)


class _WarnLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, *args, **kwargs):
        self.warnings.append(" ".join(str(a) for a in args))


class SelfUseHintTests(unittest.TestCase):
    def _hint(self, metas):
        original = common.logger
        fake = _WarnLogger()
        common.logger = fake
        try:
            common.self_use_hint(_Plugin(_Context(metas)))
        finally:
            common.logger = original
        return fake.warnings

    def test_new_plugin_is_self_use(self):
        self.assertEqual(self._hint({common.OMNI_PLUGIN_NAME: _StarMeta(common.OMNI_PLUGIN_NAME)}), [])

    def test_legacy_self_use_version_is_accepted(self):
        self.assertEqual(self._hint({common.OMNI_PLUGIN_NAME_LEGACY: _StarMeta(
            common.OMNI_PLUGIN_NAME_LEGACY, version="3.4.4", display="万象画卷")}), [])

    def test_upstream_legacy_warns_with_new_install_target(self):
        warnings = self._hint({common.OMNI_PLUGIN_NAME_LEGACY: _StarMeta(
            common.OMNI_PLUGIN_NAME_LEGACY, version="3.2.0", display="万象画卷")})
        self.assertEqual(len(warnings), 1)
        self.assertIn("Astrbot_amoupup_photo", warnings[0])


if __name__ == "__main__":
    unittest.main()
