# -*- coding: utf-8 -*-
"""照片生成与人格参考图：调用我能永远定格你、下载本地化、候选/模板图（从 photo_engine 拆出）。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import random
import tempfile
import urllib.error
import urllib.request

from astrbot.api import logger

from typing import Any, TYPE_CHECKING

try:
    from .photo_data import (
    GENERATE_TIMEOUT_SECONDS,
    _MAX_IMAGE_BYTES,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_data import (
            GENERATE_TIMEOUT_SECONDS,
            _MAX_IMAGE_BYTES,
        )

try:
    from .amo_observability import note as _note_swallowed
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_observability import note as _note_swallowed

try:
    from .amo_image_common import (
    apply_persona_config,
    find_omni,
    image_component,
    persona_refs_of,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_image_common import (
            apply_persona_config,
            find_omni,
            image_component,
            persona_refs_of,
        )

try:
    from .photo_plan import locks_for_plan
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_plan import locks_for_plan

try:
    from .photo_view import HEADWEAR_LOCK, SELFIE_NEGATIVE
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_view import HEADWEAR_LOCK, SELFIE_NEGATIVE

async def _call_omni(omni, args):
    """调我能永远定格你；旧版不认识 scene_lock/pose_lock/allow_local_refs 时逐个去掉重试。"""
    removable = ("pose_ref", "scene_lock", "pose_lock", "allow_local_refs")
    while True:
        try:
            return await asyncio.wait_for(
                omni.generate_images_for_plugin(**args), timeout=GENERATE_TIMEOUT_SECONDS)
        except TypeError as exc:
            victim = next((key for key in removable if key in args), "")
            if not victim:
                raise
            args.pop(victim)
            logger.warning("[amo] 我能永远定格你旧版不支持 %s，去掉后重试: %s" % (victim, exc))


class PhotoGenerateMixin:
    """生成图片：我能永远定格你调用、下载与本地化、人格参考图同步。"""
    if TYPE_CHECKING:  # 宿主成员（插件主类/其它 mixin 提供）：严格口径用声明而不是 ignore
        VIEWS: Any
        VIEW_LABELS: Any
        _cache_dir: Any
        _enabled_map: Any
        _pose_ref: Any
        _prob: Any
        _save_enabled: Any
        _save_template_map: Any
        _template_map: Any
        persona_ref: Any
        persona_refs: Any
        plugin: Any
        template_dir: Any
        user_ref_path: Any
        user_refs: Any

    def _find_omni(self):
        """握手：找不到返回 None；找到就顺手把三视图自愈进生图插件（幂等）。"""
        omni = find_omni(self.plugin, self, "photo_engine.PhotoEngine._find_omni")
        if omni is not None:
            self._ensure_persona_refs(omni)
        return omni

    def _ensure_persona_refs(self, omni, scope="photo_engine.PhotoEngine._ensure_persona_refs"):
        """生图插件的三视图为空、本地有 → 同步一次；同步过的不重复写盘。

        改名/重载后它那份 persona_ref_image 会变成 []（旧路径在它 data_dir 外，
        被它自己的安全校验静默丢掉），表现就是 0 张参考图、耳朵长相漂。这里在
        握手 / 首次生图时自愈，失败不阻塞出图。
        """
        if omni is None:
            return False
        try:
            current = persona_refs_of(omni)
        except Exception as exc:
            _note_swallowed(scope + ".read", exc)
            current = []
        refs = self.persona_refs()
        if not refs:
            return False
        signature = tuple(refs)
        if current and tuple(current) == signature:
            self._persona_refs_signature = signature
            self._persona_synced_ok = True
            return False
        if (getattr(self, "_persona_refs_signature", None) == signature
                and getattr(self, "_persona_synced_ok", False)):
            # 同一份已经同步成功过：不要每 60 秒重写一次它的配置
            return False
        self._persona_synced_ok = False
        ok = self._sync_omni_refs(omni=omni, refs=refs)
        self._persona_refs_signature = signature
        self._persona_synced_ok = bool(ok)
        return bool(ok)

    async def _bootstrap_persona_refs(self):
        """启动自愈：等注册表就绪后补一次三视图；任何失败都只留日志。"""
        await asyncio.sleep(2)
        try:
            omni = self._find_omni()
        except Exception as exc:
            _note_swallowed("photo_engine.PhotoEngine._bootstrap_persona_refs", exc)
            return
        if omni is None:
            logger.debug("[amo] 启动自愈：还没找到生图插件，首次生图时再同步三视图")

    def _selfie_ready(self, omni):
        """她能不能出「自拍」：看我能永远定格你里有没有她的形象。

        不同版本的 OmniDraw 把形象放在不同地方（踩过坑：只认老字段时，
        新版照样有形象，但这里判成「没有」→ 自拍被降级成街景）：
        - 老版：``plugin_config.persona_ref_image`` / ``persona_base_prompt``；
        - 新版：``plugin_config.personas``（每项带 ``ref_images`` / 基底提示词），
          配置里落成 JSON 的 ``persona_config``；
        - 都没有时，看我们自己 ``templates/`` 里有没有三视图。
        """
        try:
            conf = getattr(omni, "plugin_config", None)
            if conf is not None:
                for key in ("persona_ref_image", "persona_base_prompt"):
                    if str(getattr(conf, key, "") or "").strip():
                        return True
                for profile in (getattr(conf, "personas", None) or []):
                    for key in ("ref_images", "persona_ref_image", "ref_image"):
                        value = getattr(profile, key, None)
                        if isinstance(value, (list, tuple, set)):
                            if any(str(item).strip() for item in value):
                                return True
                        elif str(value or "").strip():
                            return True
                    if str(getattr(profile, "persona_base_prompt", "") or "").strip():
                        return True
                raw = str(getattr(conf, "persona_config", "") or "").strip()
                if raw:
                    try:
                        data = json.loads(raw)
                    except Exception:
                        data = {}
                    if isinstance(data, dict):
                        active = str(data.get("active_persona_id") or "")
                        for profile in (data.get("profiles") or []):
                            if not isinstance(profile, dict):
                                continue
                            if active and str(profile.get("id") or "") not in ("", active):
                                continue
                            if (str(profile.get("persona_ref_image") or "").strip()
                                    or str(profile.get("persona_base_prompt") or "").strip()):
                                return True
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._selfie_ready", _amo_exc)
        # 最后看我们自己的模板：OmniDraw 那边没配形象时，三视图仍然是可用的参考
        try:
            return bool(self.persona_refs())
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._selfie_ready", _amo_exc)
            return False

    def _pick_selfie(self, kind):
        # outfit/selfie 是明确要她出镜；不要走概率抽到街景
        if kind in ("outfit", "selfie"):
            return True
        ratio = self._prob("photo_selfie_ratio", 50)
        return random.random() * 100 < ratio

    def _selfie_reference_refs(self, pose, policy, room_ref):
        """备好自拍参考图：姿势→房间（仅在家）→穿搭 scope 平铺图。

        人设三视图不在这里拼：生图插件会把它们插在姿势图和房间之间。
        返回 (refs, pose_ref, outfit_refs, outfit_scope)。
        """
        want_outfit = bool(policy.get("outfit", True))
        want_pose = bool(policy.get("pose", True))
        outfit: dict[Any, Any] = {}
        outfit_scope = ""
        outfit_refs: list[Any] = []
        if want_outfit:
            try:
                wardrobe = getattr(self.plugin, "wardrobe", None)
                if wardrobe is not None:
                    outfit = wardrobe.current() or {}
                try:
                    from .outfit_refs import refs_for_outfit, scope_of_outfit
                except ImportError:  # 直接按文件加载时
                    from outfit_refs import refs_for_outfit, scope_of_outfit
                # 按 wardrobe.current() 的 scope（day/night/hotel/swim）取平铺图：
                # 外套/长裙都在，缺图跳过，最多 5 张（coat→top→skirt→bottom→shoes）
                outfit_scope = scope_of_outfit(outfit)
                outfit_refs = refs_for_outfit(self.plugin, outfit, limit=5)
            except Exception as exc:
                logger.debug("[amo] 服装参考图准备失败: %s" % exc)
                outfit_refs = []
        # 姿势参考图：面板里生成过的才有；由生图插件放到三视图前面（只学动作）
        pose_ref = ""
        if want_pose:
            try:
                pose_ref = self._pose_ref(pose)
            except Exception as exc:
                logger.debug("[amo] 姿势参考图准备失败: %s" % exc)
                pose_ref = ""
        # pose 同时留在 refs 里，是给旧版生图插件兜底（它不认识 pose_ref 时仍能拿姿势图）。
        refs = [pose_ref] if pose_ref else []
        if room_ref and bool(policy.get("room", False)):
            refs.append(room_ref)
        refs.extend(outfit_refs)
        if pose_ref or refs:
            logger.info(
                "[amo] 参考图顺序：姿势=%d 房间=%d 穿搭=%d（scope=%s）共=%d",
                1 if pose_ref else 0,
                1 if room_ref and bool(policy.get("room", False)) else 0,
                len(outfit_refs), outfit_scope or "-", len(refs))
        return refs, pose_ref, outfit_refs, outfit_scope


    async def _generate(self, omni, prompt, selfie, pose=None, plan=None, room_refs=None):
        refs = []
        pose_ref = ""
        # 空镜：调用方没走 _share 时也按配置默认带房间参考图（仅在家；无图自动跳过）
        enabled = getattr(self, "_room_refs_enabled", None)
        attach = getattr(self, "_attach_room_ref", None)
        if (not selfie and plan is not None and not getattr(plan, "room_ref", "")
                and not getattr(plan, "room_ref_checked", False)
                and callable(enabled) and callable(attach) and enabled(room_refs)):
            attach(plan)
        room_ref = str(getattr(plan, "room_ref", "") or "") if plan is not None else ""
        # 参考图策略从计划取：允许哪些参考图由 plan.refs_policy 决定，这里不再
        # 自己判断场景/姿势组合；张数仍由下面的 keys / limit 决定（行为不变）
        policy = dict(getattr(plan, "refs_policy", None) or {}) if plan is not None else {}
        want_outfit = bool(policy.get("outfit", True))
        want_pose = bool(policy.get("pose", True))
        if selfie:
            # 显式走 mixin：测试/模拟器常把 _generate 绑到只带最小属性的假引擎上
            refs, pose_ref, outfit_refs, outfit_scope = PhotoGenerateMixin._selfie_reference_refs(
                self, pose, policy, room_ref)
        else:
            refs = [room_ref] if room_ref else []
            outfit_refs, outfit_scope = [], ""
        if plan is not None:
            plan.debug["refs"] = {
                "outfit": bool(want_outfit),
                "pose": bool(want_pose),
                "room": bool(policy.get("room", False)),
                "pose_ref": bool(pose_ref),
                "outfit_scope": outfit_scope,
                "outfit_count": len(outfit_refs),
                "refs": len(refs),
            }
        args = {"prompt": prompt, "count": 1, "record_usage": True}
        # 姿势图单独传给生图插件：它会把姿势放在三视图前面；
        # refs 里也保留一份，旧版生图插件不认识 pose_ref 时仍能拿到姿势参考。
        if selfie and pose_ref:
            args["pose_ref"] = pose_ref
            args["allow_local_refs"] = True
        if refs:
            args["refs"] = refs
            args["allow_local_refs"] = True
        if refs or pose_ref:
            if selfie:
                hint = "（参考图顺序："
                segments = []
                if pose_ref:
                    segments.append("第一张是姿势参考，只学动作和手的位置，不要照搬那张的脸和衣服")
                segments.append("接下来是她的三视图，长相、发型、体型和头饰以三视图为准；"
                                + HEADWEAR_LOCK)
                if room_ref and bool(policy.get("room", False)):
                    if outfit_refs:
                        segments.append("然后是一张房间参考图，只参考家具、布局、光线和空间关系，"
                                        "不要照搬成空镜，不要复制图里的文字；它后面是服装平铺图")
                    else:
                        segments.append("最后一张是房间参考图，只参考家具、布局、光线和空间关系，"
                                        "不要照搬成空镜，不要复制图里的文字")
                    segments.append("画面以她为主体，房间只作背景参考，不要变成房间空镜")
                if outfit_refs:
                    segments.append("三视图只参考长相、发型和体型，衣服不要照搬三视图；"
                                    "最后几张是今天这套衣服的平铺图，平铺图里有几件就穿几件"
                                    "（含外套/下装/鞋），颜色和款式逐件以平铺图为准，"
                                    "图里没有的不要加")
                hint += "；".join(segments) + "）"
            else:
                hint = ("（参考图是房间参考图，只参考家具、布局、光线和空间关系，"
                        "不要照搬成空镜，不要复制图里的文字。）")
            args["prompt"] = prompt + "\n" + hint
        if selfie:
            args["mode"] = "selfie"
            negative = SELFIE_NEGATIVE
            if outfit_refs:
                negative += "，人设默认服装，三视图里的服装，模板服装，图里没有的衣服"
            if bool(getattr(plan, "bed_scene", False)):
                negative += "，床上的鞋，室内穿鞋，鞋踩床单，被窝里出现鞋"
            args["negative_prompt"] = negative
        # 场景/姿势锁：从计划取（locks_for_plan 会把结果写回 plan.locks），
        # 这里不再自己判断 zone/pose 组合
        scene_lock = pose_lock = ""
        if plan is not None:
            try:
                scene_lock, pose_lock = plan.locks or locks_for_plan(plan)
            except Exception as _amo_exc:
                _note_swallowed("photo_engine.PhotoEngine._generate.locks", _amo_exc)
                scene_lock = pose_lock = ""
        if scene_lock:
            args["scene_lock"] = scene_lock
        if pose_lock:
            args["pose_lock"] = pose_lock
        try:
            result = await _call_omni(omni, args)
        except asyncio.TimeoutError:
            logger.warning("[amo] 照片生成超时（%d 秒）" % GENERATE_TIMEOUT_SECONDS)
            return None
        except Exception as exc:
            logger.warning("[amo] 照片生成失败（%s）: %s" % ("自拍" if selfie else "街景", exc))
            return None
        if isinstance(result, dict) and result.get("success") and result.get("images"):
            return result["images"][0]
        message = ""
        if isinstance(result, dict):
            message = str(result.get("message") or "")
        logger.warning("[amo] 照片没出来：%s" % (message or "未知原因"))
        return None

    @staticmethod
    def _image_component(item):
        return image_component(item)

    def preview_data_url(self, item, size=512):
        """给面板预览用的小图（data URL），避免 <img> 直接连中转站。"""
        if not isinstance(item, dict):
            return ""
        path = str(item.get("file_path") or "")
        if not path or not os.path.exists(path):
            return ""
        try:
            from PIL import Image as PILImage

            with PILImage.open(path) as image:
                image.thumbnail((size, size))
                buffer = io.BytesIO()
                image.convert("RGB").save(buffer, format="JPEG", quality=82)
            return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine.preview_data_url", _amo_exc)
            return ""

    async def _localize(self, item):
        """把远端图片先落到本地，避免发送时 task1.65535.space 偶发连不上。"""
        if not isinstance(item, dict):
            return item
        if item.get("data_url"):
            return item
        path = str(item.get("file_path") or "")
        if path and os.path.exists(path):
            return item
        url = str(item.get("url") or item.get("image_url") or "")
        if not url.startswith("http"):
            return item
        cache_dir = self._cache_dir()
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.PhotoEngine._localize", _amo_exc)
            return item
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        for _ in range(2):
            try:
                payload, content_type = await asyncio.to_thread(self._fetch_image, url)
                if "png" in content_type or payload[:8] == b"\x89PNG\r\n\x1a\n":
                    ext = ".png"
                elif "webp" in content_type or payload[:4] == b"RIFF":
                    ext = ".webp"
                else:
                    ext = ".jpg"
                target = os.path.join(cache_dir, digest + ext)
                with open(target, "wb") as handle:
                    handle.write(payload)
                copied = dict(item)
                copied["file_path"] = target
                return copied
            except urllib.error.HTTPError as _amo_exc:
                # 403/404 重试也没用，直接走原图发送
                _note_swallowed("photo_engine.PhotoEngine._localize", _amo_exc)
                break
            except Exception as _amo_exc:
                _note_swallowed("photo_engine.PhotoEngine._localize", _amo_exc)
                await asyncio.sleep(1)
        return item

    @staticmethod
    def _fetch_image(url):
        """线程里把图片读完再回来；事件循环里 read() 会把整条链路卡住。"""
        # 部分生图 CDN 会对没有 UA 的 urllib 直接 403，带上浏览器 UA 再取。
        request = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*",
        })
        with urllib.request.urlopen(request, timeout=20) as response:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            payload = response.read(_MAX_IMAGE_BYTES + 1)
        if len(payload) > _MAX_IMAGE_BYTES:
            raise ValueError("图片超过 %d MB，丢弃" % (_MAX_IMAGE_BYTES // (1024 * 1024)))
        return payload, content_type

    async def generate_custom(self, prompt, refs=None):
        """按提示词生成一张（可选带角色模板参考图），不发送。"""
        omni = self._find_omni()
        prompt = str(prompt or "").strip()
        if omni is None or not prompt:
            return None
        if refs is None:
            ref = self.persona_ref()
            refs = [ref] if ref else []
        refs = [item for item in refs if item]
        args = {"prompt": prompt, "count": 1, "event": None, "record_usage": False}
        if refs:
            args["refs"] = refs
            args["allow_local_refs"] = True
        try:
            result = await omni.generate_images_for_plugin(**args)
        except Exception as exc:
            logger.warning("[amo] 自定义生图失败: %s" % exc)
            result = None
        if not isinstance(result, dict) or not result.get("success"):
            # 中转偶发 502：等 3 秒重试一次
            await asyncio.sleep(3)
            try:
                result = await omni.generate_images_for_plugin(**args)
            except Exception as exc:
                logger.warning("[amo] 自定义生图重试失败: %s" % exc)
                return None
        if not isinstance(result, dict) or not result.get("success"):
            message = result.get("message") if isinstance(result, dict) else ""
            logger.warning("[amo] 自定义生图没出图: %s" % (message or "未知原因"))
            return None
        images = result.get("images") or []
        return await self._localize(images[0]) if images else None


    def sheet_ref_path(self):
        """三视图参考图（面板上传的那张）；没有就返回空。"""
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            path = os.path.join(self.template_dir(), "amo_sheet_ref" + ext)
            if os.path.isfile(path):
                return path
        return ""

    def sheet_ref_preview(self):
        path = self.sheet_ref_path()
        return self.preview_data_url({"file_path": path}) if path else ""

    def set_sheet_ref(self, raw):
        """保存三视图参考图：支持 data URL / http(s) / 本地文件；返回落盘路径。"""
        raw = str(raw or "").strip()
        if not raw:
            return ""
        payload = b""
        if raw.startswith("data:"):
            try:
                head, _, body = raw.partition(",")
                payload = base64.b64decode(body)
            except Exception as _amo_exc:
                _note_swallowed("photo_engine.set_sheet_ref.data_url", _amo_exc)
                return ""
        elif raw.startswith("http://") or raw.startswith("https://"):
            try:
                request = urllib.request.Request(raw, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"})
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = response.read(_MAX_IMAGE_BYTES + 1)
            except Exception as _amo_exc:
                _note_swallowed("photo_engine.set_sheet_ref.url", _amo_exc)
                return ""
        elif os.path.isfile(raw):
            try:
                with open(raw, "rb") as handle:
                    payload = handle.read(_MAX_IMAGE_BYTES + 1)
            except Exception as _amo_exc:
                _note_swallowed("photo_engine.set_sheet_ref.file", _amo_exc)
                return ""
        if not payload or len(payload) > _MAX_IMAGE_BYTES:
            return ""
        if payload[:8] == b"\x89PNG\r\n\x1a\n":
            ext = ".png"
        elif payload[:4] == b"RIFF":
            ext = ".webp"
        else:
            ext = ".jpg"
        target_dir = self.template_dir()
        os.makedirs(target_dir, exist_ok=True)
        self.clear_sheet_ref()
        target = os.path.join(target_dir, "amo_sheet_ref" + ext)
        temp = target + ".tmp"
        try:
            with open(temp, "wb") as handle:
                handle.write(payload)
            os.replace(temp, target)
            return target
        except Exception as _amo_exc:
            _note_swallowed("photo_engine.set_sheet_ref.write", _amo_exc)
            try:
                if os.path.exists(temp):
                    os.remove(temp)
            except Exception as _amo_exc2:
                _note_swallowed("photo_engine.set_sheet_ref.cleanup", _amo_exc2)
            return ""

    def clear_sheet_ref(self):
        target_dir = self.template_dir()
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            path = os.path.join(target_dir, "amo_sheet_ref" + ext)
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except Exception as _amo_exc:
                _note_swallowed("photo_engine.clear_sheet_ref", _amo_exc)
        return ""

    async def persona_sheet(self, view):
        """角色三视图：正面 / 侧面 / 全身。有上传参考图就先按它生成。"""
        prompts = {
            "front": "角色设定图·正面：同一角色的正面站姿半身，二次元动漫风格，纯白背景，"
                     "五官清晰对称，蓝发蓝眼，鲸鱼鳍耳，日常服装，正对镜头",
            "side": "角色设定图·侧面：同一角色的侧面站姿半身，二次元动漫风格，纯白背景，"
                    "侧脸清晰，蓝发蓝眼，鲸鱼鳍耳，日常服装",
            "full": "角色设定图·全身：同一角色的正面全身站姿，二次元动漫风格，纯白背景，"
                    "从头到脚完整可见，蓝发蓝眼，鲸鱼鳍耳，日常服装",
        }
        sheet_ref = self.sheet_ref_path()
        refs = [sheet_ref] if sheet_ref else self.persona_refs()
        return await self.generate_custom(prompts.get(str(view), prompts["front"]), refs=refs)


    async def user_sheet(self, view):
        """用户三视图：正面 / 侧面 / 全身（合拍用）。有上传参考图就先按它生成。"""
        prompts = {
            "front": "用户设定图·正面：成年男性，用户本人，正面站姿半身，二次元动漫风格，纯白背景，"
                     "五官清晰对称，短黑发，日常男装，正对镜头",
            "side": "用户设定图·侧面：同一男性的侧面站姿半身，二次元动漫风格，纯白背景，"
                    "侧脸清晰，短黑发，日常男装",
            "full": "用户设定图·全身：同一男性的正面全身站姿，二次元动漫风格，纯白背景，"
                    "从头到脚完整可见，短黑发，日常男装",
        }
        ref = self.user_ref_path()
        refs = [ref] if ref else self.user_refs()
        return await self.generate_custom(prompts.get(str(view), prompts["front"]), refs=refs)

    async def import_persona(self, view, raw):
        """B：导入现成的三视图，直接存成对应视角的模板并启用。"""
        raw = str(raw or "").strip()
        if not raw:
            return "缺少图片"
        temp = ""
        if raw.startswith("data:"):
            try:
                _head, _sep, body = raw.partition(",")
                payload = base64.b64decode(body)
            except Exception as _amo_exc:
                _note_swallowed("photo_engine.import_persona.decode", _amo_exc)
                return "图片解码失败"
            if not payload or len(payload) > _MAX_IMAGE_BYTES:
                return "图片为空或太大"
            target_dir = self.template_dir()
            os.makedirs(target_dir, exist_ok=True)
            fd, temp = tempfile.mkstemp(suffix=".png", dir=target_dir)
            os.close(fd)
            try:
                with open(temp, "wb") as handle:
                    handle.write(payload)
            except Exception as _amo_exc:
                _note_swallowed("photo_engine.import_persona.write", _amo_exc)
                return "图片保存失败"
            raw = temp
        try:
            return await self.adopt_persona(raw, view)
        finally:
            if temp:
                try:
                    os.remove(temp)
                except OSError as _amo_exc:  # cleanup: 临时文件删不掉不影响导入结果
                    _note_swallowed("photo_engine.import_persona.cleanup", _amo_exc)

    def _sync_omni_refs(self, omni=None, refs=None):
        """把三视图同步给「我能永远定格你」。

        以前这里直接读/写它的 ``config_path`` 和 AstrBot 的
        ``Astrbot_amoupup_photo_config.json``，而且把 list 写进了它期望字符串的
        ``persona_ref_image`` 键——它一改 schema 就会写坏/写空。现在只走它自己的
        配置入口（``_apply_runtime_config`` + ``_persist_config``），格式和校验都由它负责。
        返回 True 表示它确实认下了这些参考图（空 refs / 找不到插件都返回 False）。
        """
        if refs is None:
            refs = self.persona_refs()
        refs = [str(item) for item in (refs or []) if str(item).strip()]
        if omni is None:
            omni = self._find_omni()
        if omni is None:
            return False
        if not refs:
            logger.debug("[amo] 本地没有三视图，跳过同步生图插件参考图")
            return False
        if not apply_persona_config(omni, refs, "photo_engine.PhotoEngine._sync_omni_refs"):
            logger.debug("[amo] 生图插件没有可用的配置入口，本次只在内存里更新参考图")
            return False
        try:
            accepted = persona_refs_of(omni)
        except Exception as exc:
            _note_swallowed("photo_engine.PhotoEngine._sync_omni_refs.verify", exc)
            accepted = []
        if len(accepted) != len(refs):
            logger.warning("[amo] 三视图同步后生图插件只认了 %d/%d 张（路径可能不在它的 data_dir 下）",
                           len(accepted), len(refs))
        else:
            logger.info("[amo] 已同步 %d 张人设参考图给生图插件", len(refs))
        return bool(accepted)

    async def adopt_persona(self, url, view="front"):
        """把选中的三视图存成对应视角的模板，以后生图都用它。"""
        url = str(url or "").strip()
        if not url:
            return "缺少图片地址"
        omni = self._find_omni()
        if omni is None:
            return "没找到「我能永远定格你」"
        target_dir = self.template_dir()
        if url.startswith("http"):
            try:
                with await asyncio.to_thread(lambda: urllib.request.urlopen(url, timeout=90)) as response:
                    payload = response.read()
                    content_type = str(response.headers.get("Content-Type") or "").lower()
            except Exception as exc:
                return "下载模板失败：%s" % exc
        elif os.path.isfile(url):
            try:
                payload = await asyncio.to_thread(lambda: open(url, "rb").read())
            except Exception as exc:
                return "读取本地模板失败：%s" % exc
            content_type = ""
        else:
            return "只支持 http 或本地图片"
        if "png" in content_type or payload[:8] == b"\x89PNG\r\n\x1a\n":
            ext = ".png"
        elif "webp" in content_type or payload[:4] == b"RIFF":
            ext = ".webp"
        else:
            ext = ".jpg"
        view = str(view or "front").strip().lower()
        if view not in self.VIEWS:
            view = "front"
        target = os.path.join(target_dir, "amo_persona_%s%s" % (view, ext))
        temp = target + ".tmp"
        try:
            with open(temp, "wb") as handle:
                handle.write(payload)
            for name in os.listdir(target_dir):
                if (name.startswith("amo_persona_" + view) and not name.endswith(".tmp")
                        and os.path.join(target_dir, name) != target):
                    try:
                        os.remove(os.path.join(target_dir, name))
                    except Exception as exc:
                        _note_swallowed("photo_engine.py.1813", exc)
            os.replace(temp, target)
        except Exception as exc:
            try:
                if os.path.exists(temp):
                    os.remove(temp)
            except Exception as exc:
                _note_swallowed("photo_engine.py.1820", exc)
            return "保存模板失败：%s" % exc
        mapping = self._template_map()
        mapping[view] = target
        self._save_template_map(mapping)
        enabled = self._enabled_map()
        enabled[view] = True
        self._save_enabled(enabled)
        self._sync_omni_refs()
        label = self.VIEW_LABELS.get(view, view)
        return "✅ 已保存%s模板：%s" % (label, target)
