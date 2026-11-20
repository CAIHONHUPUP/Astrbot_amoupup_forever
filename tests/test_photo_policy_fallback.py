# -*- coding: utf-8 -*-
"""生图内容政策 + 失败不装图验收（2026-11-17）。

覆盖：
1. 自拍提示词去身体化/去潮红/去取景范围，保留人设/场景/姿势；
2. content_policy_violation 自动用安全 prompt 重试一次；
3. 全失败时明确失败、不发图、回复守卫改写「图糊/凑合看」；
4. 失败语气池温柔、6-8 条、不连续重复、不泄漏系统词；
5. 正常出图和成功回复不受影响（反向验证）。
"""
import asyncio
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.dirname(os.path.abspath(__file__))):
    if path not in sys.path:
        sys.path.insert(0, path)

import astrbot_stub  # noqa: E402
astrbot_stub.install()

import photo_share  # noqa: E402
from amo_reply_pipeline import _send_photo_claim  # noqa: E402
from photo_prompt import PhotoPromptMixin  # noqa: E402
from photo_share import (  # noqa: E402
    PHOTO_FAILURE_FALLBACK,
    PHOTO_FAILURE_LINES,
    PhotoShareMixin,
    is_policy_failure,
    pick_failure_line,
    photo_failure_pool,
)
from photo_view import (  # noqa: E402
    POLICY_SENSITIVE_WORDS,
    looks_like_photo_claim,
    policy_sensitive_hits,
    sanitize_selfie_prompt,
    strip_photo_claim,
)

BANNED_REPLY_WORDS = ("图", "照片", "拍糊", "凑合看", "内容政策", "审核", "API",
                      "policy", "violation")
POLICY_400 = "HTTP 400: content_policy_violation"


class FakeDB:
    def __init__(self):
        self.store = {}

    def kv_get(self, key, default=None):
        return self.store.get(key, default)

    def kv_set(self, key, value):
        self.store[key] = value


class FakeState:
    weather: dict[str, object] = {}

    def weather_brief(self):
        return ""

    def mood_score(self):
        return 60


class FakePose:
    def random_pose(self, zone=None):
        return {"name": "托腮", "note": "单手托腮看镜头"}

    def ref_for(self, pose):
        return ""


class FakeWardrobe:
    def current(self):
        return {"top": "米色针织衫", "skirt": "牛仔裤"}


class FakeContext:
    def __init__(self):
        self.sent = []

    async def send_message(self, umo, chain):
        self.sent.append((umo, chain))
        return True


class FakePlugin:
    def __init__(self):
        self.state = FakeState()
        self.pose = FakePose()
        self.wardrobe = FakeWardrobe()
        self.housing = None
        self.db = FakeDB()
        self.context = FakeContext()


class FakeOmni:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def generate_images_for_plugin(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0) if self.outcomes else {
            "success": False, "message": "no outcome"}
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def success_outcome():
    return {"success": True, "images": [{"file_path": "fake-selfie.jpg"}]}


class Engine(PhotoPromptMixin, PhotoShareMixin):
    def __init__(self, outcomes):
        self.plugin = FakePlugin()
        self.plugin.photo = self
        self.plugin.draw = None
        self.omni = FakeOmni(outcomes)
        self._stats = {"sent": 0, "failed": 0, "rolled": 0}
        self.prompts = []
        self.sent = []

    def _cfg_bool(self, key, default=False):
        return False

    def _cfg_int(self, key, default=0):
        return default

    def _cfg_str(self, key, default=""):
        return default

    def _time_light(self, outdoor=False):
        return "上午，明亮自然光"

    def outfit_today(self):
        return "米色针织衫配牛仔裤"

    def _activity(self):
        return {"place": "浴室", "activity": "洗澡", "zone": "home", "category": "rest"}

    def _find_omni(self):
        return self.omni

    def _pick_selfie(self, kind):
        return True

    def _selfie_ready(self, omni):
        return True

    def _room_refs_enabled(self, explicit=None):
        return False

    def _attach_room_ref(self, plan):
        return ""

    def _bump_count(self):
        return None

    def _target_umo(self, profile=None):
        return "umo-test"

    async def _localize(self, item):
        return item

    async def _send(self, umo, item, caption=""):
        self.sent.append((umo, item, caption))
        return True

    async def _generate(self, omni, prompt, selfie, pose=None, plan=None, room_refs=None):
        self.prompts.append(prompt)
        try:
            result = await omni.generate_images_for_plugin(
                prompt=prompt, mode="selfie" if selfie else "photo")
        except Exception:
            return None
        if isinstance(result, dict) and result.get("success") and result.get("images"):
            return result["images"][0]
        return None


def _guard_ctx(engine):
    return SimpleNamespace(plugin=engine.plugin, umo="umo-test")


def _share(engine, *outcomes):
    engine.omni.outcomes = list(outcomes)
    return asyncio.run(engine._share("daily", "", {}))


# ---------- 1. 提示词安全化 ----------

def test_normal_selfie_prompt_has_no_policy_sensitive_words():
    engine = Engine([])
    prompt = engine._selfie_prompt_for("daily", "", engine._activity())
    assert policy_sensitive_hits(prompt) == [], prompt
    for word in ("head to thighs", "damp hair clinging", "cheeks flushed", "出水芙蓉"):
        assert word not in prompt.lower(), (word, prompt)


def test_normal_selfie_prompt_keeps_neutral_bath_steam_and_outfit():
    engine = Engine([])
    prompt = engine._selfie_prompt_for("daily", "", engine._activity())
    assert "头发微湿" in prompt, prompt
    assert "水汽" in prompt, prompt
    assert "米色针织衫" in prompt, prompt
    assert "自拍" in prompt, prompt


def test_safe_selfie_prompt_is_more_neutral_but_keeps_outfit_and_place():
    engine = Engine([])
    safe_prompt = engine._selfie_prompt_for("daily", "", engine._activity(), safe=True)
    assert "日常自拍" in safe_prompt, safe_prompt
    assert "米色针织衫" in safe_prompt, safe_prompt
    assert "浴室" not in safe_prompt, safe_prompt
    assert "湿" not in safe_prompt, safe_prompt
    assert "水汽" not in safe_prompt, safe_prompt
    assert policy_sensitive_hits(safe_prompt) == [], safe_prompt


def test_sanitizer_rewrites_incident_phrases():
    dirty = ("第一人称自拍，出水芙蓉，脸颊潮红，头发湿漉漉贴着，"
             "framed from head to thighs，damp hair clinging，cheeks flushed")
    cleaned = sanitize_selfie_prompt(dirty)
    assert "出水芙蓉" not in cleaned
    assert "潮红" not in cleaned
    assert "脸颊" not in cleaned
    assert "head to thighs" not in cleaned.lower()
    assert "damp hair clinging" not in cleaned.lower()
    assert "cheeks flushed" not in cleaned.lower()
    assert policy_sensitive_hits(cleaned) == []


# ---------- 2. 政策失败重试 ----------

def test_policy_violation_retries_safe_prompt_once_then_succeeds():
    engine = Engine([])
    ok = _share(engine, RuntimeError(POLICY_400), success_outcome())
    assert ok is True
    assert len(engine.prompts) == 2, engine.prompts
    assert "content_policy" not in engine.prompts[0]
    assert "浴室" not in engine.prompts[1]
    assert "湿" not in engine.prompts[1]
    assert engine._stats["policy_fail"] == 1
    assert engine._stats["policy_fallback"] == 1
    assert engine._stats["policy_fallback_ok"] == 1
    assert engine._stats["policy_fallback_fail"] == 0
    assert len(engine.sent) == 1


def test_policy_violation_twice_reports_failure_and_sends_nothing():
    engine = Engine([])
    ok = _share(engine, RuntimeError(POLICY_400),
                {"success": False, "message": POLICY_400})
    assert ok is False
    assert len(engine.prompts) == 2
    assert engine.sent == []
    assert engine._stats["policy_fail"] == 1
    assert engine._stats["policy_fallback_fail"] == 1
    assert engine.photo_guard_state("umo-test") == "failed"


def test_non_policy_failure_also_retries_once_with_safe_prompt():
    engine = Engine([])
    ok = _share(engine, {"success": False, "message": "network timeout"},
                success_outcome())
    assert ok is True
    assert len(engine.prompts) == 2
    assert engine._stats.get("policy_fail", 0) == 0
    assert engine._stats["policy_fallback"] == 1


def test_normal_success_does_not_retry_and_marks_ok():
    engine = Engine([])
    ok = _share(engine, success_outcome())
    assert ok is True
    assert len(engine.prompts) == 1
    assert engine._stats.get("policy_fallback", 0) == 0
    assert engine.photo_guard_state("umo-test") == "ok"


def test_policy_detector_only_matches_policy_errors():
    assert is_policy_failure(POLICY_400) is True
    assert is_policy_failure("HTTP 400 invalid request") is False
    assert is_policy_failure("network timeout") is False
    assert is_policy_failure("违规内容，请求被拒绝") is True


# ---------- 3. 回复守卫 ----------

def test_reply_guard_rewrites_claim_after_failed_photo():
    engine = Engine([])
    _share(engine, RuntimeError(POLICY_400), {"success": False, "message": POLICY_400})
    fixed = _send_photo_claim("图有点糊，凑合看吧", _guard_ctx(engine))
    assert fixed, "失败后不能原样放行有图的话"
    assert fixed != "图有点糊，凑合看吧"
    for word in BANNED_REPLY_WORDS:
        assert word not in fixed, (word, fixed)
    assert fixed in [line["text"] for line in PHOTO_FAILURE_LINES]


def test_reply_guard_allows_claim_after_successful_photo():
    engine = Engine([])
    _share(engine, success_outcome())
    text = "拍好啦，你看这张"
    assert _send_photo_claim(text, _guard_ctx(engine)) is None


def test_reply_guard_ignores_plain_chat():
    engine = Engine([])
    text = "今天有点累，想抱抱你"
    assert _send_photo_claim(text, _guard_ctx(engine)) is None


def test_reply_guard_rewrites_claim_when_no_photo_attempt_exists():
    engine = Engine([])
    fixed = _send_photo_claim("图有点糊，凑合看吧", _guard_ctx(engine))
    assert fixed
    for word in BANNED_REPLY_WORDS:
        assert word not in fixed, (word, fixed)


def test_reply_guard_blocks_claim_while_photo_pending():
    engine = Engine([])
    engine._mark_photo_pending("umo-test")
    fixed = _send_photo_claim("刚拍的这张照片还行吧", _guard_ctx(engine))
    assert fixed
    for word in BANNED_REPLY_WORDS:
        assert word not in fixed, (word, fixed)


def test_claim_detector_hits_wording_but_not_plain_photo_talk():
    assert looks_like_photo_claim("图有点糊，凑合看吧") is True
    assert looks_like_photo_claim("拍好啦，发你了") is True
    assert looks_like_photo_claim("这张照片不太清楚") is True
    assert looks_like_photo_claim("今天想和你一起拍照") is False
    assert looks_like_photo_claim("照片里那只猫好可爱") is False
    assert strip_photo_claim("好呀，图有点糊，凑合看吧") == "好呀"


# ---------- 4. 失败语气池 ----------

def test_failure_pool_is_tender_and_leaks_nothing():
    assert 6 <= len(PHOTO_FAILURE_LINES) <= 8
    for line in PHOTO_FAILURE_LINES:
        text = line["text"]
        for word in BANNED_REPLY_WORDS:
            assert word not in text, (word, text)
        assert any(word in text for word in ("没拍成", "没存", "没成功", "没拍好", "没了", "没留", "不好看"))


def test_failure_pool_respects_mood_and_relation():
    low = photo_failure_pool(mood_score=10)
    high = photo_failure_pool(mood_score=90)
    assert low and high and set(low) != set(high)
    sweet = photo_failure_pool(mood_score=60, relation="相恋")
    assert any("好不好" in line or "嘛" in line for line in sweet)


def test_failure_line_never_repeats_consecutively():
    last = ""
    seen = []
    for _ in range(12):
        line = pick_failure_line(mood_score=60, relation="相恋", last=last)
        assert line != last
        last = line
        seen.append(line)
    assert len(set(seen)) >= 2


def test_notify_failure_uses_pool_not_template_and_not_system_words():
    engine = Engine([])
    asyncio.run(engine._notify_failure("umo-test", "share_failed", {"relation": "相恋"}))
    assert len(engine.plugin.context.sent) == 1
    _umo, chain = engine.plugin.context.sent[0]
    component = chain.chain[0] if getattr(chain, "chain", None) else chain
    text = component.text if hasattr(component, "text") else str(component)
    assert text in [line["text"] for line in PHOTO_FAILURE_LINES], text
    for word in BANNED_REPLY_WORDS:
        assert word not in text, (word, text)


def test_policy_failure_emits_policy_and_fallback_logs():
    records = []

    class CaptureLogger:
        def warning(self, message, *args, **kwargs):
            records.append(message % args if args else message)

        def info(self, message, *args, **kwargs):
            records.append(message % args if args else message)

    old_logger = photo_share.logger
    photo_share.logger = CaptureLogger()
    try:
        engine = Engine([])
        _share(engine, RuntimeError(POLICY_400), {"success": False, "message": POLICY_400})
    finally:
        photo_share.logger = old_logger
    joined = "\n".join(records)
    assert "[PHOTO_POLICY] policy_fail=1" in joined, joined
    assert "fallback=1" in joined, joined
    assert "fallback_fail=1" in joined, joined


def test_failure_fallback_line_is_in_pool():
    assert PHOTO_FAILURE_FALLBACK in [line["text"] for line in PHOTO_FAILURE_LINES]
