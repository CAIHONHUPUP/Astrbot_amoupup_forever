# -*- coding: utf-8 -*-
"""wardrobe_seed：Wardrobe 的一部分方法（逐字搬运）。"""

from __future__ import annotations
from typing import TYPE_CHECKING, Any

try:  # 兼容层：旧插件名 / 旧数据目录 / 旧配置名的唯一来源
    from . import amo_compat as _compat
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        import amo_compat as _compat

try:  # 包模式（AstrBot 加载插件）
    from .wardrobe_data import (
        COAT_SEED_VERSION, DAY_SEED_VERSION, DAY_TARGET_OUTFITS, DRESS_SEED_VERSION,
        HOME_BOTTOMS, HOME_SEED_VERSION, HOME_SHOES, HOME_TOPS, HOTEL_SEED, HOTEL_SEED_VERSION,
        SEED_COATS, SEED_OUTFITS, SEED_SLEEP, SEED_SOCKS, SEED_SWIM_SETS,
        SLEEP_EXTRA_ITEMS, SOCKS_SEED_VERSION, _ensure_socks_items,
        SLEEP_SEED_VERSION, SLEEP_TARGET_OUTFITS, SWIM_SEED_VERSION, _amo_clock, _day_item_pools,
        _item_ref_path, _new_id, _note_swallowed, atomic_write_json, combo_compatible,
        is_day_outfit, is_dress, json, logger, os, random, threading,
    )
except ImportError:  # 直接按文件加载时（测试/模拟器）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from wardrobe_data import (
            COAT_SEED_VERSION, DAY_SEED_VERSION, DAY_TARGET_OUTFITS, DRESS_SEED_VERSION,
            HOME_BOTTOMS, HOME_SEED_VERSION, HOME_SHOES, HOME_TOPS, HOTEL_SEED, HOTEL_SEED_VERSION,
            SEED_COATS, SEED_OUTFITS, SEED_SLEEP, SEED_SOCKS, SEED_SWIM_SETS,
        SLEEP_EXTRA_ITEMS, SOCKS_SEED_VERSION, _ensure_socks_items,
            SLEEP_SEED_VERSION, SLEEP_TARGET_OUTFITS, SWIM_SEED_VERSION, _amo_clock, _day_item_pools,
            _item_ref_path, _new_id, _note_swallowed, atomic_write_json, combo_compatible,
            is_day_outfit, is_dress, json, logger, os, random, threading,
        )


class WardrobeSeedMixin:
    # 方法体逐字搬运自拆分前的 wardrobe.py。

    if TYPE_CHECKING:  # 宿主成员（插件主类/其它 mixin 提供）：严格口径用声明而不是 ignore
        VERSION: Any
    def __init__(self, plugin):
        self.plugin = plugin
        self._lock = threading.RLock()
        self._data = None
        self._today_cache = None
        self._synced = ""
        base = ""
        try:
            base = os.path.dirname(str(getattr(plugin.db, "path", "") or ""))
        except Exception as _amo_exc:
            _note_swallowed("wardrobe.Wardrobe.__init__", _amo_exc)
            base = ""
        if not base:
            base = str(getattr(plugin, "plugin_dir", "") or ".")
        self.path = os.path.join(base, "wardrobe.json")
        self._sidecar_base = base
        logger.info("[amo] 穿搭衣柜已加载（%s）" % self.path)

    #: 今日穿搭 sidecar 的插件数据目录：新名优先，旧名只读回退（legacy fallback）
    SIDECAR_PLUGIN_DIRS = _compat.IMAGE_DATA_DIR_NAMES

    @property
    def sidecar_path(self):
        """今日穿搭 sidecar：写入时才解析，新目录优先、旧目录只读回退。"""
        return self._resolve_sidecar_path(self._sidecar_base)

    @classmethod
    def _resolve_sidecar_path(cls, base):
        """今日穿搭 sidecar：新目录存在就写新目录，否则回退旧插件目录（不迁移、不删除）。"""
        parent = os.path.dirname(base)
        for name in cls.SIDECAR_PLUGIN_DIRS:
            candidate = os.path.join(parent, name)
            if os.path.isdir(candidate):
                return os.path.join(candidate, "amo_outfit.json")
        return os.path.join(parent, cls.SIDECAR_PLUGIN_DIRS[0], "amo_outfit.json")

    # ---------- 存储 ----------
    def _empty(self):
        return {"version": self.VERSION, "items": [], "outfits": [], "daily": {}}

    def _load(self):
        with self._lock:
            if self._data is not None:
                return self._data
            data = None
            try:
                with open(self.path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except FileNotFoundError:
                data = None
            except Exception as _amo_exc:
                _note_swallowed("wardrobe.Wardrobe._load", _amo_exc)
                data = None
            if not isinstance(data, dict) or not data.get("items") or not data.get("outfits"):
                data = self._seed()
                self._data = data
                self._save(data)
                return data
            data.setdefault("version", self.VERSION)
            data.setdefault("daily", {})
            data.setdefault("outfits", [])
            data.setdefault("items", [])
            if data.get("day_seed") != DAY_SEED_VERSION:
                self._ensure_day(data)
                data["day_seed"] = DAY_SEED_VERSION
                self._save(data)
            if data.get("sleep_seed") != SLEEP_SEED_VERSION:
                self._ensure_sleep(data)
                data["sleep_seed"] = SLEEP_SEED_VERSION
                self._save(data)
            if data.get("hotel_seed") != HOTEL_SEED_VERSION:
                self._ensure_hotel(data)
                data["hotel_seed"] = HOTEL_SEED_VERSION
                self._save(data)
            if data.get("swim_seed") != SWIM_SEED_VERSION:
                self._ensure_swim(data)
                data["swim_seed"] = SWIM_SEED_VERSION
                self._save(data)
            if data.get("home_seed") != HOME_SEED_VERSION:
                self._ensure_home(data)
                data["home_seed"] = HOME_SEED_VERSION
                self._save(data)
            if data.get("dress_seed") != DRESS_SEED_VERSION:
                self._migrate_dresses(data)
                data["dress_seed"] = DRESS_SEED_VERSION
                self._save(data)
            if data.get("coat_seed") != COAT_SEED_VERSION:
                self._ensure_coats(data)
                data["coat_seed"] = COAT_SEED_VERSION
                self._save(data)
            if data.get("socks_seed") != SOCKS_SEED_VERSION:
                _ensure_socks_items(data, _new_id)
                data["socks_seed"] = SOCKS_SEED_VERSION
                self._save(data)
            self._data = data
            return data

    def _save(self, data=None):
        data = data if data is not None else self._data
        if data is None:
            return
        try:
            atomic_write_json(self.path, data)
        except Exception as _amo_exc:
            _note_swallowed("wardrobe.Wardrobe._save", _amo_exc)

    def _seed(self):
        data = self._empty()
        items = {}
        for top, bottom, shoes in SEED_OUTFITS:
            ids = {}
            for category, name in (("top", top), ("bottom", bottom), ("shoes", shoes)):
                key = (category, name)
                if key not in items:
                    items[key] = {
                        "id": _new_id("i-"),
                        "category": category,
                        "name": name,
                        "source": "seed",
                        "gift_id": "",
                        "created_at": _amo_clock.now_ts(),
                    }
                ids[category] = items[key]["id"]
            data["outfits"].append({
                "id": _new_id("o-"),
                "top": ids["top"],
                "bottom": ids["bottom"],
                "shoes": ids["shoes"],
                "source": "seed",
            })
        data["items"] = list(items.values())
        self._ensure_coats(data)
        data["coat_seed"] = COAT_SEED_VERSION
        _ensure_socks_items(data, _new_id)
        data["socks_seed"] = SOCKS_SEED_VERSION
        self._ensure_day(data)
        data["day_seed"] = DAY_SEED_VERSION
        self._ensure_sleep(data)
        data["sleep_seed"] = SLEEP_SEED_VERSION
        self._ensure_hotel(data)
        data["hotel_seed"] = HOTEL_SEED_VERSION
        self._ensure_swim(data)
        data["swim_seed"] = SWIM_SEED_VERSION
        self._ensure_home(data)
        data["home_seed"] = HOME_SEED_VERSION
        self._migrate_dresses(data)
        data["dress_seed"] = DRESS_SEED_VERSION
        return data

    def _ensure_home(self, data):
        """补上居家便服：只在“在家且非睡点”时穿，不进白天搭配/睡衣/泳装。"""
        items = data.setdefault("items", [])
        keep = set(HOME_TOPS) | set(HOME_BOTTOMS) | set(HOME_SHOES)
        # 清掉早期占位/多余的居家单品，只保留原始两套（避免面板里堆一堆同义家居裤）
        drop = [item for item in items
                if item.get("homewear") and str(item.get("name") or "") not in keep]
        if drop:
            drop_ids = {item.get("id") for item in drop}
            items[:] = [item for item in items if item.get("id") not in drop_ids]
        have = {(item.get("category"), str(item.get("name") or ""))
                for item in items if item.get("homewear")}
        for category, names in (("top", HOME_TOPS), ("bottom", HOME_BOTTOMS),
                                ("shoes", HOME_SHOES)):
            for name in names:
                if (category, name) in have:
                    continue
                items.append({
                    "id": _new_id("i-"),
                    "category": category,
                    "name": name,
                    "source": "seed",
                    "gift_id": "",
                    "created_at": _amo_clock.now_ts(),
                    "homewear": True,
                })

    def _ensure_day(self, data):
        """白天搭配：手工 + 同色系/同材质的自动组合（外套最多一件，长裙单穿）。"""
        items = data.setdefault("items", [])
        outfits = data.setdefault("outfits", [])
        by_id = {item["id"]: item for item in items}
        seed_names = {frozenset(name for name in (top, bottom, shoes) if name)
                      for top, bottom, shoes in SEED_OUTFITS}
        keys = ("coat", "top", "bottom", "skirt", "shoes")
        changed = self._prune_day_outfits(outfits, by_id, seed_names, keys)
        existing = {tuple(outfit.get(key) or "" for key in keys)
                    for outfit in outfits if is_day_outfit(outfit)}
        day_count = len([outfit for outfit in outfits if is_day_outfit(outfit)])
        if day_count >= DAY_TARGET_OUTFITS:
            return changed
        return self._fill_day_outfits(items, outfits, existing, day_count, changed)

    def _prune_day_outfits(self, outfits, by_id, seed_names, keys):
        # 手工种子里若有「不同色系/材质」的组合，先剔掉（睡眠/泳装不归这里管）
        kept = []
        changed = False
        for outfit in outfits:
            if outfit.get("sleep") or outfit.get("swim"):
                kept.append(outfit)
                continue
            names = frozenset(str((by_id.get(outfit.get(key)) or {}).get("name") or "")
                              for key in keys) - {""}
            if outfit.get("source") == "seed" and names not in seed_names:
                parts = [str((by_id.get(outfit.get(key)) or {}).get("name") or "")
                         for key in keys]
                if not combo_compatible(*parts):
                    changed = True
                    continue
            kept.append(outfit)
        if changed:
            outfits[:] = kept
        return changed

    def _fill_day_outfits(self, items, outfits, existing, day_count, changed):
        # 先保证每件上衣各进一套，再按 裤子/短裙/长裙/外套 组合到 DAY_TARGET_OUTFITS
        pools = _day_item_pools(items)
        coats = pools["coat"]
        tops = pools["top"]
        bottoms = pools["bottom"]
        short_skirts = pools["short_skirt"]
        dress_skirts = pools["dress_skirt"]
        shoes_pool = pools["shoes"]
        added = changed
        def add_combo(coat, top, bottom, skirt, shoe):
            nonlocal day_count, added
            if day_count >= DAY_TARGET_OUTFITS:
                return
            key = (coat["id"] if coat else "", top["id"] if top else "",
                   bottom["id"] if bottom else "", skirt["id"] if skirt else "",
                   shoe["id"])
            if key in existing:
                return
            names = [str(part.get("name") or "") if part else ""
                     for part in (coat, top, bottom, skirt, shoe)]
            if not combo_compatible(*names):
                return
            existing.add(key)
            outfits.append({
                "id": _new_id("o-"),
                "coat": key[0],
                "top": key[1],
                "bottom": key[2],
                "skirt": key[3],
                "shoes": key[4],
                "source": "seed",
                "combo": True,
            })
            day_count += 1
            added = True

        # 先保证每件上衣至少进一套，避免随机只铺满几种上衣
        for top in tops:
            before = day_count
            for bottom in bottoms:
                for shoe in shoes_pool:
                    add_combo(None, top, bottom, "", shoe)
                    if day_count > before:
                        break
                if day_count > before:
                    break
        for bottom in bottoms:
            for top in tops:
                for shoe in shoes_pool:
                    if day_count >= DAY_TARGET_OUTFITS:
                        break
                    add_combo(None, top, bottom, "", shoe)
                if day_count >= DAY_TARGET_OUTFITS:
                    break
            if day_count >= DAY_TARGET_OUTFITS:
                break
        for top in tops:
            for skirt in short_skirts:
                for shoe in shoes_pool:
                    if day_count >= DAY_TARGET_OUTFITS:
                        break
                    add_combo(None, top, "", skirt, shoe)
                if day_count >= DAY_TARGET_OUTFITS:
                    break
            if day_count >= DAY_TARGET_OUTFITS:
                break
        for top in tops:
            for skirt in short_skirts:
                for bottom in bottoms:
                    for shoe in shoes_pool:
                        if day_count >= DAY_TARGET_OUTFITS:
                            break
                        add_combo(None, top, bottom, skirt, shoe)
                    if day_count >= DAY_TARGET_OUTFITS:
                        break
                if day_count >= DAY_TARGET_OUTFITS:
                    break
            if day_count >= DAY_TARGET_OUTFITS:
                break
        for dress in dress_skirts:
            for shoe in shoes_pool:
                if day_count >= DAY_TARGET_OUTFITS:
                    break
                add_combo(None, None, None, dress, shoe)
        for coat in coats:
            for bottom in bottoms:
                for top in tops:
                    for shoe in shoes_pool:
                        if day_count >= DAY_TARGET_OUTFITS:
                            break
                        add_combo(coat, top, bottom, "", shoe)
                    if day_count >= DAY_TARGET_OUTFITS:
                        break
                if day_count >= DAY_TARGET_OUTFITS:
                    break
            if day_count >= DAY_TARGET_OUTFITS:
                break
        return added


    def _ensure_sleep(self, data):
        """补上睡衣：20 套手工搭配 + 自动组合到 SLEEP_TARGET_OUTFITS 套，每晚换着穿。"""
        items = data.setdefault("items", [])
        outfits = data.setdefault("outfits", [])
        by_key = {(item.get("category"), item.get("name")): item
                  for item in items if not item.get("hotel")}
        by_id = {item["id"]: item for item in items}
        seed_triples = set()
        for top, bottom, shoes in SEED_SLEEP:
            triple_items = [by_key.get(("top", top)), by_key.get(("bottom", bottom)),
                            by_key.get(("shoes", shoes))]
            if all(triple_items):
                seed_triples.add(tuple(entry["id"] for entry in triple_items if entry is not None))
        kept = []
        repaired = False
        for outfit in outfits:
            if not outfit.get("sleep") or outfit.get("hotel"):
                kept.append(outfit)
                continue
            triple = (outfit.get("top"), outfit.get("bottom"), outfit.get("shoes"))
            if outfit.get("source") == "seed" and triple not in seed_triples:
                names = [str((by_id.get(outfit.get(key)) or {}).get("name") or "")
                         for key in ("coat", "top", "bottom", "skirt", "shoes")]
                if not combo_compatible(*names):
                    repaired = True
                    continue
            kept.append(outfit)
        if repaired:
            outfits[:] = kept
        existing = {
            (outfit.get("top"), outfit.get("bottom"), outfit.get("shoes"))
            for outfit in outfits if outfit.get("sleep") and not outfit.get("hotel")
        }
        added = repaired

        def ensure_item(category, name):
            nonlocal added
            key = (category, name)
            item = by_key.get(key)
            if item is None:
                item = {
                    "id": _new_id("i-"),
                    "category": category,
                    "name": name,
                    "source": "seed",
                    "gift_id": "",
                    "sleep": True,
                    "created_at": _amo_clock.now_ts(),
                }
                items.append(item)
                by_key[key] = item
                added = True
            return item

        for top, bottom, shoes in SEED_SLEEP:
            top_item = ensure_item("top", top)
            bottom_item = ensure_item("bottom", bottom)
            shoes_item = ensure_item("shoes", shoes)
            triple = (top_item["id"], bottom_item["id"], shoes_item["id"])
            if triple in existing:
                continue
            existing.add(triple)
            outfits.append({
                "id": _new_id("o-"),
                "top": triple[0],
                "bottom": triple[1],
                "shoes": triple[2],
                "source": "seed",
                "sleep": True,
            })
            added = True

        for category, name in SLEEP_EXTRA_ITEMS:
            ensure_item(category, name)

        added = self._refill_sleep_combos(data) or added
        return added

    def _refill_sleep_combos(self, data):
        """只用现有睡衣单品补组合（不重新种子，删掉的衣服不会被复活）。"""
        items = data.setdefault("items", [])
        outfits = data.setdefault("outfits", [])
        keys = ("coat", "top", "bottom", "skirt", "shoes")
        existing = {tuple(outfit.get(key) or "" for key in keys)
                    for outfit in outfits if outfit.get("sleep") and not outfit.get("hotel")}
        sleep_count = len([outfit for outfit in outfits
                           if outfit.get("sleep") and not outfit.get("hotel")])
        if sleep_count >= SLEEP_TARGET_OUTFITS:
            return False
        tops = [item for item in items if item.get("sleep") and not item.get("hotel")
                and item.get("category") == "top"]
        bottoms = [item for item in items if item.get("sleep") and not item.get("hotel")
                   and item.get("category") == "bottom"]
        skirts = [item for item in items if item.get("sleep") and not item.get("hotel")
                  and item.get("category") == "skirt"]
        shoes_pool = [item for item in items if item.get("sleep") and not item.get("hotel")
                      and item.get("category") == "shoes"]
        dress_skirts = [item for item in skirts if is_dress(item.get("name"))]
        combos: list[tuple] = []
        for top in tops:
            for bottom in bottoms:
                for shoe in shoes_pool:
                    combos.append(("", top, bottom, "", shoe))
        for dress in dress_skirts:
            for shoe in shoes_pool:
                combos.append(("", "", "", dress, shoe))

        def combo_names(entry):
            return tuple(str(part.get("name") or "") if part else "" for part in entry)

        combos = [entry for entry in combos if combo_compatible(*combo_names(entry))]
        random.Random("amo-wardrobe-sleep-extra").shuffle(combos)
        added = False
        for coat, top, bottom, skirt, shoe in combos:
            if sleep_count >= SLEEP_TARGET_OUTFITS:
                break
            key = (coat, top["id"] if top else "", bottom["id"] if bottom else "",
                   skirt["id"] if skirt else "", shoe["id"])
            if key in existing:
                continue
            existing.add(key)
            outfits.append({
                "id": _new_id("o-"),
                "coat": key[0],
                "top": key[1],
                "bottom": key[2],
                "skirt": key[3],
                "shoes": key[4],
                "source": "seed",
                "sleep": True,
                "combo": True,
            })
            sleep_count += 1
            added = True
        return added

    def _ensure_coats(self, data):
        """补几件冬天外套（羽绒服 / 厚呢 / 针织格子 / 羊羔毛）。"""
        items = data.setdefault("items", [])
        existing = {item.get("name") for item in items if item.get("category") == "coat"}
        added = False
        for name in SEED_COATS:
            if name in existing:
                continue
            items.append({
                "id": _new_id("i-"),
                "category": "coat",
                "name": name,
                "source": "seed",
                "gift_id": "",
                "created_at": _amo_clock.now_ts(),
            })
            added = True
        return added

    def _migrate_dresses(self, data):
        """把裙子挪到「裙子」栏：长裙单穿，短裙配上衣（可再加裤子/睡裤）。"""
        items = data.setdefault("items", [])
        by_id = {item["id"]: item for item in items}
        for item in items:
            if "裙" in str(item.get("name") or "") and item.get("category") in ("top", "bottom"):
                item["category"] = "skirt"
        kept = []
        for outfit in data.setdefault("outfits", []):
            if outfit.get("swim"):
                # 泳装本来就没有鞋，别让下面的「必须有鞋」把它筛掉
                kept.append(outfit)
                continue
            top = by_id.get(outfit.get("top")) or {}
            bottom = by_id.get(outfit.get("bottom")) or {}
            skirt = by_id.get(outfit.get("skirt")) or {}
            if top.get("category") == "skirt":
                outfit["skirt"] = outfit.get("top")
                outfit["top"] = ""
                skirt = top
            if bottom.get("category") == "skirt":
                outfit["skirt"] = outfit.get("bottom")
                outfit["bottom"] = ""
                skirt = bottom
            if is_dress(skirt.get("name")):
                outfit["top"] = ""
                outfit["bottom"] = ""
            kept.append(outfit)
        data["outfits"] = [outfit for outfit in kept
                           if (outfit.get("swim") or outfit.get("shoes"))
                           and (outfit.get("top") or outfit.get("bottom") or outfit.get("skirt"))]

    def _ensure_hotel(self, data):
        """补上酒店睡衣：只有出门旅游、晚上住酒店才会穿，和家里睡衣分开。"""
        items = data.setdefault("items", [])
        outfits = data.setdefault("outfits", [])
        by_key = {(item.get("category"), item.get("name")): item
                  for item in items if item.get("hotel")}
        by_id = {item.get("id"): item for item in items}
        kept = []
        for outfit in outfits:
            if outfit.get("hotel") and any(
                    not by_id.get(outfit.get(key), {}).get("hotel")
                    for key in ("top", "bottom", "shoes")):
                continue
            kept.append(outfit)
        if len(kept) != len(outfits):
            outfits[:] = kept
            added_repaired = True
        else:
            added_repaired = False
        existing = {
            (outfit.get("top"), outfit.get("bottom"), outfit.get("shoes"))
            for outfit in outfits if outfit.get("hotel")
        }
        added = added_repaired
        for top, bottom, shoes in HOTEL_SEED:
            ids = {}
            for category, name in (("top", top), ("bottom", bottom), ("shoes", shoes)):
                key = (category, name)
                item = by_key.get(key)
                if item is None:
                    item = {
                        "id": _new_id("i-"),
                        "category": category,
                        "name": name,
                        "source": "seed",
                        "gift_id": "",
                        "sleep": True,
                        "hotel": True,
                        "created_at": _amo_clock.now_ts(),
                    }
                    items.append(item)
                    by_key[key] = item
                    added = True
                ids[category] = item["id"]
            triple = (ids["top"], ids["bottom"], ids["shoes"])
            if triple in existing:
                continue
            existing.add(triple)
            outfits.append({
                "id": _new_id("o-"),
                "top": triple[0],
                "bottom": triple[1],
                "shoes": triple[2],
                "source": "seed",
                "sleep": True,
                "hotel": True,
            })
            added = True
        return added

    def _ensure_swim(self, data):
        """重建泳装：**整套**（上衣 + 同套泳裤），没有鞋；旧泳装连同参考图一起作废。"""
        items = data.setdefault("items", [])
        outfits = data.setdefault("outfits", [])
        old_items = [item for item in items if item.get("swim")]
        if old_items:
            self._purge_swim_refs(old_items)
            items[:] = [item for item in items if not item.get("swim")]
            outfits[:] = [outfit for outfit in outfits if not outfit.get("swim")]
        added = bool(old_items)
        for set_name, top, bottom in SEED_SWIM_SETS:
            ids = {}
            for category, name in (("top", top), ("bottom", bottom)):
                item = {
                    "id": _new_id("i-"),
                    "category": category,
                    "name": name,
                    "source": "seed",
                    "gift_id": "",
                    "swim": True,
                    "set": set_name,
                    "created_at": _amo_clock.now_ts(),
                }
                items.append(item)
                ids[category] = item["id"]
            outfits.append({
                "id": _new_id("o-"),
                "top": ids["top"],
                "bottom": ids["bottom"],
                "shoes": "",
                "source": "seed",
                "swim": True,
                "set": set_name,
            })
            added = True
        return added

    def _purge_swim_refs(self, items):
        """删掉旧泳装的参考图（衣服按名字一张，整套换新后不再沿用）。"""
        if _item_ref_path is None:
            return
        for item in items:
            try:
                path = _item_ref_path(self.plugin, item)
                if path and os.path.isfile(path):
                    os.remove(path)
                    logger.info("[amo] 旧泳装参考图已删除：%s" % path)
            except Exception as _amo_exc:
                _note_swallowed("wardrobe.Wardrobe._purge_swim_refs", _amo_exc)
    def _trim_daily(self, data):
        days = sorted(data.get("daily", {}))
        for day in days[:-30]:
            data["daily"].pop(day, None)
