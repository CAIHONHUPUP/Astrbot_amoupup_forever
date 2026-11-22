# -*- coding: utf-8 -*-
"""日志脱敏（task-26）：密钥/Authorization/token/open_id 不出日志。

现场（work/audit/cross_plugin.md §3.1/§5.3）：生产 astrbot.log 有 23 行明文
Bearer sk-…；provider 异常 traceback 把 headers 当局部变量打了出来。
这里钉住统一 logging.Filter 的行为：
1. record.msg / record.args / 异常栈都脱敏，且正常日志一字不改；
2. 覆盖 forever / watch / data / photo 的 logger（进程级 handler 级 filter）；
3. 安装幂等，重复调用不重复加 filter。
"""
from __future__ import annotations

import io
import logging
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.dirname(os.path.abspath(__file__))):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import observability  # noqa: E402

#: 测试用假密钥：不进真实日志，也不在断言失败信息里打印。
SECRET = "sk-test-DEADBEEF-1234567890"
BEARER = "Bearer " + SECRET
OPEN_ID = "ou_abcdef1234567890abcdef"
TOKEN = "token-abcdef1234567890"
NORMAL = "今天天气不错，晚上想吃火锅。"


def _capture_logger(name):
    """造一个独立 logger + 内存 handler，返回 (logger, stream)。"""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(name)
    logger.handlers = []
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    return logger, stream


class RedactTextTests(unittest.TestCase):
    def test_bearer_and_sk_redacted(self):
        out = observability.redact_text("headers={'Authorization': '%s'}" % BEARER)
        self.assertNotIn(SECRET, out)
        self.assertNotIn("Bearer " + SECRET, out)
        self.assertIn("<redacted>", out)

    def test_api_key_token_authorization_redacted(self):
        for raw in (
            "api_key = 'apikey-ABCDEF123456'",
            "token=abcdEFGH12345678",
            "Authorization: Basic dXNlcjpwYXNz5",
            '{"X-Api-Key": "key-1234567890"}',
        ):
            out = observability.redact_text(raw)
            for piece in ("apikey-ABCDEF123456", "abcdEFGH12345678",
                          "dXNlcjpwYXNz5", "key-1234567890"):
                self.assertNotIn(piece, out, raw)

    def test_open_id_masked_by_default_and_optional(self):
        masked = observability.redact_text("open_id=" + OPEN_ID)
        self.assertNotIn(OPEN_ID, masked)
        self.assertNotIn("ou_abcdef", masked)
        kept = observability.redact_text("open_id=" + OPEN_ID, open_id=False)
        self.assertIn(OPEN_ID, kept)

    def test_normal_text_untouched(self):
        self.assertEqual(observability.redact_text(NORMAL), NORMAL)
        self.assertEqual(observability.redact_text(""), "")
        self.assertEqual(observability.redact_text(None), "")

    def test_redaction_is_idempotent(self):
        once = observability.redact_text(BEARER)
        self.assertEqual(observability.redact_text(once), once)

    def test_stats_count_without_values(self):
        stats = {}
        observability.redact_text(BEARER + " " + OPEN_ID, stats=stats)
        self.assertTrue(stats)
        for value in stats.values():
            self.assertIsInstance(value, int)
            self.assertGreater(value, 0)


class RedactionFilterTests(unittest.TestCase):
    def setUp(self):
        observability.install_log_redaction()

    def test_secret_never_reaches_handler(self):
        logger, stream = _capture_logger("Astrbot_amoupup_forever.test.secret")
        logger.warning("headers=%s", {"Authorization": BEARER})
        text = stream.getvalue()
        self.assertNotIn(SECRET, text)
        self.assertNotIn(OPEN_ID, text)

    def test_args_dict_is_redacted(self):
        logger, stream = _capture_logger("Astrbot_amoupup_data.test.args")
        logger.error("provider failed: %s", {"api_key": "apikey-ABCDEF123456",
                                             "note": NORMAL})
        text = stream.getvalue()
        self.assertNotIn("apikey-ABCDEF123456", text)
        self.assertIn(NORMAL, text)

    def test_exception_traceback_redacted_but_stack_kept(self):
        logger, stream = _capture_logger("Astrbot_amoupup_photo.test.exc")
        try:
            raise RuntimeError("Authorization: Bearer " + SECRET)
        except RuntimeError:
            logger.exception("provider boom")
        text = stream.getvalue()
        self.assertNotIn(SECRET, text)
        self.assertIn("RuntimeError", text, "异常栈信息不能整条丢")
        self.assertIn("Traceback", text)

    def test_normal_log_unchanged(self):
        logger, stream = _capture_logger("Astrbot_amoupup_watch.test.normal")
        logger.info(NORMAL)
        self.assertEqual(stream.getvalue().strip(), NORMAL)

    def test_covers_four_plugin_loggers(self):
        # 四个插件的 logger 名字都覆盖；handler 是 install 之后新建的，
        # 说明 addHandler 钩子生效（不是只给现有 handler 贴一次）。
        for name in ("Astrbot_amoupup_forever", "Astrbot_amoupup_watch",
                     "Astrbot_amoupup_data", "Astrbot_amoupup_photo",
                     "astrbot.plugin.Astrbot_amoupup_watch"):
            logger, stream = _capture_logger(name + ".probe")
            logger.warning("Bearer " + SECRET)
            self.assertNotIn(SECRET, stream.getvalue(), name)

    def test_install_is_idempotent(self):
        first = observability.install_log_redaction()
        second = observability.install_log_redaction()
        self.assertFalse(first)
        self.assertFalse(second)
        self.assertTrue(observability.redaction_installed())

    def test_filter_can_disable_open_id(self):
        # 直接调 filter，避开全局过滤器，验证「open_id 可选脱敏」开关。
        record = logging.LogRecord("x", logging.WARNING, __file__, 1,
                                   "open_id=" + OPEN_ID, (), None)
        observability.RedactionFilter(redact_open_id=False).filter(record)
        self.assertIn(OPEN_ID, record.getMessage())


if __name__ == "__main__":
    unittest.main()
