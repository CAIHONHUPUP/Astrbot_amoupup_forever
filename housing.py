# -*- coding: utf-8 -*-
"""房屋系统（2026-11-14）：她住哪儿、家是什么样、家里有哪些家具。

数据全部落在 amo 的 kv（键名 housing），结构：

{
  "version": 1,
  "residence_id": "home-rent",
  "homes": [
    {"id": "home-rent", "name": "温馨出租屋", "kind": "rent" | "own",
     "city": "", "style": "出租屋简易风", "colors": "米白色",
     "furniture": ["单人床", ...], "rent_cents": 150000,
     "status": "living" | "idle" | "rented_out",
     "images": {"living": "", "bedroom": ""}, "images_needed": true}
  ]
}

职责：
- current() 永远指向 residence_id 那套；多套房用 move_in() 切换；
- prompt_line() 把当前房间的装修/主色/家具注入聊天，家具清单是事实边界；
- /我的家、/房屋 房产|租房、/搬家 <名字> 由 amo_commands 调用；
- 破产时 on_bankruptcy() 强制搬回最便宜的出租屋；
- 买大件家具/装修会置 images_needed，客厅/卧室参考图由上层按需生成。

纯状态 + 文案，不直接生图，方便单测。
"""
from __future__ import annotations

import os

try:
    from .amo_shared import city_or_empty, logger
except ImportError:  # 直接按文件加载时
    try:
        from amo_shared import city_or_empty, logger
    except Exception:  # pragma: no cover
        import logging

        logger = logging.getLogger(__name__)

        def city_or_empty(plugin):
            return ""

STYLE_DEFAULT = "出租屋简易风"
COLORS_DEFAULT = "米白色"
RENT_LIVING = ["二手布艺沙发", "简易木质茶几", "塑料收纳箱(当电视柜)", "落地晾衣架", "垃圾桶"]
RENT_BEDROOM = ["1.2米单人床", "简易布衣柜", "床头小台灯", "塑料盆"]
OWN_LIVING = ["三人位软包沙发", "实木电视柜", "智能电视", "毛绒地毯", "绿植盆栽", "落地灯"]
OWN_BEDROOM = ["1.8米双人床", "到顶大衣柜", "梳妆台", "床头柜", "暖光台灯"]
DEFAULT_ROOMS = {
    "rent": {"living": list(RENT_LIVING), "bedroom": list(RENT_BEDROOM)},
    "own": {"living": list(OWN_LIVING), "bedroom": list(OWN_BEDROOM)},
}
DEFAULT_FURNITURE = tuple(RENT_LIVING + RENT_BEDROOM)
SEASON_SUMMER = "夏季薄毯、电风扇"
SEASON_WINTER = "冬季厚地毯、暖气片"
IMAGE_COOLDOWN_SECONDS = 24 * 3600
BEDROOM_WORDS = ("床", "衣柜", "梳妆台", "床头柜", "台灯", "睡", "卧室")

def room_of_item(name):
    body = str(name or "")
    return "bedroom" if any(word in body for word in BEDROOM_WORDS) else "living"


def season_line(now=None):
    import datetime as _dt
    month = (now or _dt.datetime.now()).month
    if 6 <= month <= 9:
        return SEASON_SUMMER
    if month in (12, 1, 2):
        return SEASON_WINTER
    return ""


def build_room_image_prompt(room_type, style, colors, furniture_list, now=None):
    """按房间动态拼生图 Prompt；返回 (正向词, 负向词)。"""
    room_type = str(room_type or "客厅")
    style = str(style or "简约风")
    colors = str(colors or "米白色")
    base = ("一张空无一人的%s室内图，第一人称视角，广角镜头，照片级写实，室内摄影，"
            "8k分辨率，温馨的生活气息，阳光透过窗户洒进来。" % room_type)
    furniture_str = "、".join(str(x) for x in (furniture_list or []) if str(x).strip()) or "家具寥寥无几"
    if "卧" in room_type:
        must_not = "沙发、电视柜、茶几、餐桌、马桶、厨房灶台、人物、宠物"
    else:
        must_not = "床、衣柜、梳妆台、床头柜、马桶、厨房灶台、人物、宠物"
    attr = ("装修风格为%s，主色调为%s。房间内真实摆放着：%s。"
            "房间里只能出现清单里的家具，不要添加清单外的家具、电器或杂物；"
            "家具要落地、有真实阴影、比例正确，空间不要拥挤。"
            "请将这些物品合理搭配进整体的装修风格中，保持画面的和谐与生活感。"
            % (style, colors, furniture_str))
    attr += "画面里不要出现：%s。" % must_not
    season = season_line(now)
    if season:
        attr += "季节感：%s。" % season
    negative = ("人物, 人类, 文字, 水印, 二次元, 插画, 拥挤, 杂乱无章, 多出来的腿, "
                + must_not)
    return base + attr, negative

BIG_WORDS = ("沙发", "床", "衣柜", "餐桌", "书桌", "书柜", "电视柜", "茶几", "冰箱",
             "洗衣机", "空调", "梳妆台", "地毯", "窗帘", "吊灯", "置物架", "鞋柜",
             "电脑桌", "床头柜")
SMALL_WORDS = ("台灯", "杯子", "抱枕", "床单", "被套", "花瓶", "挂画", "小摆件",
               "香薰", "收纳盒", "垃圾桶", "碗", "盘子", "拖鞋", "毛巾")

KIND_LABEL = {"rent": "租房", "own": "已购房"}
STATUS_LABEL = {"living": "自住中", "idle": "闲置", "rented_out": "已出租",
                "expired": "已退租"}


def classify_furniture(name):
    """大件才触发重新生图；杯子、抱枕这类小件不折腾。"""
    body = str(name or "").strip()
    if not body:
        return "small"
    if any(word in body for word in BIG_WORDS):
        return "big"
    if any(word in body for word in SMALL_WORDS):
        return "small"
    return "small"


class Housing:
    def __init__(self, plugin):
        self.p = plugin
        self.data = self._load()

    # ---------- 存取 ----------
    def _kv_get(self, key, default):
        try:
            return self.p.db.kv_get(key, default)
        except Exception:
            return default

    def _kv_set(self, key, value):
        try:
            self.p.db.kv_set(key, value)
        except Exception as exc:
            logger.debug("[amo] 房屋状态写不进去: %s" % exc)

    def _city(self):
        try:
            return str(city_or_empty(self.p) or "").strip()
        except Exception:
            return ""

    def _seed(self):
        city = self._city()
        home = {
            "id": "home-rent",
            "name": "温馨出租屋",
            "kind": "rent",
            "city": city,
            "style": STYLE_DEFAULT,
            "colors": COLORS_DEFAULT,
            "furniture": list(RENT_LIVING + RENT_BEDROOM),
            "room_furniture": {"living": list(RENT_LIVING), "bedroom": list(RENT_BEDROOM)},
            "rent_cents": 150000,
            "status": "living",
            "images": {"living": "", "bedroom": ""},
            "images_needed": True,
            "image_status": "pending",
            "last_image_ts": 0,
        }
        return {"version": 1, "residence_id": home["id"], "homes": [home]}

    def _load(self):
        raw = self._kv_get("housing", None)
        if not isinstance(raw, dict) or not isinstance(raw.get("homes"), list) or not raw["homes"]:
            data = self._seed()
            self._kv_set("housing", data)
            return data
        homes = []
        for index, row in enumerate(raw["homes"]):
            if not isinstance(row, dict):
                continue
            home = dict(row)
            home.setdefault("id", "home-%d" % index)
            home.setdefault("name", "家")
            home.setdefault("kind", "rent")
            home.setdefault("city", "")
            home.setdefault("style", STYLE_DEFAULT)
            home.setdefault("colors", COLORS_DEFAULT)
            furniture = home.get("furniture")
            home["furniture"] = [str(x) for x in furniture] if isinstance(furniture, list) else list(DEFAULT_FURNITURE)
            rooms = home.get("room_furniture")
            if not isinstance(rooms, dict) or not (rooms.get("living") or rooms.get("bedroom")):
                legacy = {"单人床", "木质书桌", "布艺小沙发", "粉色台灯"}
                if set(home["furniture"]) == legacy:
                    preset = DEFAULT_ROOMS.get("own" if home.get("kind") == "own" else "rent", DEFAULT_ROOMS["rent"])
                    rooms = {"living": list(preset["living"]), "bedroom": list(preset["bedroom"])}
                    home["furniture"] = list(preset["living"] + preset["bedroom"])
                else:
                    rooms = {"living": [], "bedroom": []}
                    for item in home["furniture"]:
                        rooms[room_of_item(item)].append(item)
            home["room_furniture"] = {
                "living": [str(x) for x in (rooms.get("living") or [])],
                "bedroom": [str(x) for x in (rooms.get("bedroom") or [])],
            }
            home.setdefault("rent_cents", 0)
            home.setdefault("status", "living" if home.get("id") == raw.get("residence_id") else "idle")
            images = home.get("images")
            home["images"] = dict(images) if isinstance(images, dict) else {"living": "", "bedroom": ""}
            home.setdefault("images_needed", not (home["images"].get("living") and home["images"].get("bedroom")))
            home.setdefault("image_status", "ready" if (home["images"].get("living") and home["images"].get("bedroom")) else "pending")
            home.setdefault("last_image_ts", 0)
            homes.append(home)
        data = {"version": 1, "residence_id": str(raw.get("residence_id") or homes[0]["id"]), "homes": homes}
        self._kv_set("housing", data)
        return data

    def _save(self):
        self._kv_set("housing", self.data)

    # ---------- 查询 ----------
    def homes(self):
        return list(self.data.get("homes") or [])

    def current(self):
        rid = str(self.data.get("residence_id") or "")
        for home in self.homes():
            if home.get("id") == rid:
                return home
        homes = self.homes()
        return homes[0] if homes else self._seed()["homes"][0]

    def by_ref(self, ref):
        key = str(ref or "").strip()
        if not key:
            return None
        for home in self.homes():
            if home.get("id") == key or str(home.get("name") or "").strip() == key:
                return home
        return None

    def image_dir(self):
        """参考图目录：plugin_data/Astrbot_amoupup_forever/outfit_refs/家。"""
        try:
            root = os.path.dirname(str(self.p.db.path or ""))
        except Exception:
            root = ""
        return os.path.join(root, "outfit_refs", "家")

    # ---------- 变更 ----------
    def add_home(self, name, kind="own", city="", style="简约风", colors="米白色",
                 furniture=None, rent_cents=0):
        body = str(name or "").strip() or ("新家" if kind == "own" else "出租屋")
        used = {str(home.get("id") or "") for home in self.homes()}
        index = len(used) + 1
        while ("home-%d" % index) in used:
            index += 1
        home = {
            "id": "home-%d" % index,
            "name": body,
            "kind": "own" if kind == "own" else "rent",
            "city": str(city or self._city()),
            "style": str(style or "简约风"),
            "colors": str(colors or "米白色"),
            "furniture": [],
            "room_furniture": {"living": [], "bedroom": []},
            "rent_cents": int(rent_cents or 0),
            "status": "idle",
            "images": {"living": "", "bedroom": ""},
            "images_needed": True,
            "image_status": "pending",
            "last_image_ts": 0,
        }
        if furniture:
            for item in furniture:
                home["furniture"].append(str(item))
                home["room_furniture"][room_of_item(item)].append(str(item))
        else:
            preset = DEFAULT_ROOMS.get("own" if home["kind"] == "own" else "rent", DEFAULT_ROOMS["rent"])
            home["room_furniture"] = {"living": list(preset["living"]),
                                      "bedroom": list(preset["bedroom"])}
            home["furniture"] = list(preset["living"] + preset["bedroom"])
        self.data.setdefault("homes", []).append(home)
        self._save()
        return home

    def _cleanup_home_images(self, home):
        """删掉旧家客厅/卧室的物理图片，并把路径置空，防止搬完家还引用旧图。"""
        deleted = []
        images = dict((home or {}).get("images") or {})
        for room in ("living", "bedroom"):
            path = str(images.get(room) or "").strip()
            if not path:
                continue
            try:
                if os.path.isfile(path):
                    os.remove(path)
                    deleted.append(path)
            except Exception as exc:
                logger.debug("[amo] 旧家场景图删除失败（%s）: %s" % (path, exc))
        if isinstance(home, dict):
            home["images"] = {"living": "", "bedroom": ""}
            home["images_needed"] = True
        return deleted

    def move_in(self, ref):
        """/搬家 <房产名|id>：清理旧图、切换 current、退租标记。"""
        home = self.by_ref(ref)
        if home is None:
            names = "、".join(str(item.get("name")) for item in self.homes()) or "（无）"
            return False, "找不到这个家。现有：%s" % names, None
        old = self.current()
        deleted = []
        if old.get("id") != home.get("id"):
            deleted = self._cleanup_home_images(old)
            if old.get("kind") == "rent" and home.get("kind") == "own":
                # 退租搬进自己买的房：合同标记过期，不再产生房租
                old["status"] = "expired"
                old["contract_ended"] = True
        self.data["residence_id"] = home["id"]
        for item in self.homes():
            if item.get("id") == home["id"]:
                item["status"] = "living"
            elif item.get("status") == "living":
                item["status"] = "idle"
        home["images_needed"] = not (home["images"].get("living") and home["images"].get("bedroom"))
        home["image_status"] = "ready" if not home["images_needed"] else "pending"
        self.last_move = {"old": old, "deleted": deleted}
        self._save()
        if home.get("kind") == "own":
            text = "已搬入「%s」，终于不用交房租啦！" % home.get("name")
        else:
            text = "已搬进「%s」，先把家里收拾一下。" % home.get("name")
        return True, text, home

    def set_style(self, style, colors=""):
        """装修：换风格/主色，客厅卧室要重新生成。"""
        home = self.current()
        if style:
            home["style"] = str(style)
        if colors:
            home["colors"] = str(colors)
        home["images_needed"] = True
        home["image_status"] = "pending"
        self._save()
        return home

    def rent_out(self, ref, rent_cents=0):
        """把闲置的自有房产出租，赚被动收入。"""
        home = self.by_ref(ref)
        if home is None:
            return False, "找不到这套房产"
        if home.get("kind") != "own":
            return False, "只能出租自己名下的房产"
        if home.get("id") == self.current().get("id"):
            return False, "自己正住着，先搬家再出租"
        home["status"] = "rented_out"
        home["rent_cents"] = int(rent_cents or home.get("rent_cents") or 0)
        self._save()
        return True, "已把「%s」出租，每月收租 %.0f 元" % (
            home.get("name"), int(home.get("rent_cents") or 0) / 100.0)

    def sell_home(self, ref):
        """卖掉一套房：清图、移除；如果是当前住处就搬回出租屋。"""
        home = self.by_ref(ref)
        if home is None:
            return False, "找不到这套房产"
        was_current = home.get("id") == self.current().get("id")
        self._cleanup_home_images(home)
        self.data["homes"] = [item for item in self.homes() if item.get("id") != home.get("id")]
        name = home.get("name") or "房子"
        if not self.data["homes"]:
            self.data["homes"] = self._seed()["homes"]
            self.data["residence_id"] = self.data["homes"][0]["id"]
        elif was_current:
            rents = [item for item in self.homes() if item.get("kind") == "rent"]
            if not rents:
                rents = [self.add_home("临时出租屋", kind="rent", rent_cents=100000)]
            cheapest = min(rents, key=lambda item: int(item.get("rent_cents") or 0))
            self.move_in(cheapest["id"])
        self._save()
        return True, "已卖掉「%s」，钱到账了。" % name

    def add_furniture(self, name):
        """添一件家具；大件触发一次场景图更新。"""
        body = str(name or "").strip()
        home = self.current()
        if not body:
            return {"ok": False, "reason": "empty", "home": home}
        if body in home.get("furniture", []):
            return {"ok": False, "reason": "exists", "home": home, "big": False, "redraw": False}
        home["furniture"].append(body)
        rooms = home.setdefault("room_furniture", {"living": [], "bedroom": []})
        rooms.setdefault(room_of_item(body), []).append(body)
        big = classify_furniture(body) == "big"
        redraw = False
        if big:
            home["images_needed"] = True
            home["image_status"] = "pending"
            redraw = self.can_redraw()
        self._save()
        return {"ok": True, "home": home, "big": big, "redraw": redraw}

    def on_bankruptcy(self):
        """破产/法拍：强制搬回最便宜的出租屋。"""
        rents = [home for home in self.homes() if home.get("kind") == "rent"]
        if not rents:
            home = self.add_home("临时出租屋", kind="rent", rent_cents=100000,
                                 style=STYLE_DEFAULT, furniture=list(DEFAULT_FURNITURE))
            rents = [home]
        cheapest = min(rents, key=lambda home: int(home.get("rent_cents") or 0))
        ok, _, _ = self.move_in(cheapest["id"])
        if ok:
            spawn = getattr(self.p, "_spawn", None)
            ensure = getattr(self.p, "_ensure_home_images", None)
            if callable(spawn) and callable(ensure):
                try:
                    spawn(ensure(force=True))
                except Exception:
                    pass
        return cheapest if ok else None

    # ---------- 文案 ----------
    def kind_label(self, home):
        return KIND_LABEL.get(str((home or {}).get("kind") or ""), "租房")

    def _furniture_text(self, home, limit=None):
        items = [str(x) for x in (home or {}).get("furniture") or [] if str(x).strip()]
        if not items:
            return "（空）"
        if limit and len(items) > 8:
            return "、".join(items[:int(limit)]) + "…等 %d 件物品" % len(items)
        return "、".join(items)

    def _city_prefix(self, home):
        city = str((home or {}).get("city") or "") or self._city()
        return (city + "·") if city else ""

    def prompt_line(self):
        home = self.current()
        return ("【家】你住在%s%s（%s），装修是%s，主色%s；家里的陈设以这份清单为准：%s。"
                "说到房间、家具、在家做什么时，就按这份清单写。"
                % (self._city_prefix(home), home.get("name") or "家",
                   self.kind_label(home), home.get("style") or "简约风",
                   home.get("colors") or "米白色", self._furniture_text(home)))

    def card_text(self, page=""):
        target = str(page or "").strip()
        if target in ("房产", "房子"):
            return self._own_page()
        if target in ("租房", "房租"):
            return self._rent_page()
        home = self.current()
        status = str(home.get("image_status") or "")
        if (home.get("images") or {}).get("living"):
            if status == "pending":
                images = "旧图 · 新家具正在拆快递/摆放中，晚点给你看新图哦"
            else:
                images = "已生成"
        elif home.get("images_needed") or status == "pending":
            images = "正在给新家打扫卫生、布置家具，图片生成中..."
        else:
            images = "待生成"
        return ("【当前居住】%s · %s%s（%s）\n装修：%s；主色：%s\n家具：%s\n场景图：%s\n"
                "切换查看：发送 /房屋 房产 · /房屋 租房；搬家：/搬家 <名字>"
                % (self.kind_label(home), self._city_prefix(home), home.get("name") or "家",
                   STATUS_LABEL.get(str(home.get("status") or ""), "自住中"),
                   home.get("style") or "简约风", home.get("colors") or "米白色",
                   self._furniture_text(home, 6), images))

    def _own_page(self):
        rows = [home for home in self.homes() if home.get("kind") == "own"]
        if not rows:
            return "名下暂无房产，还在努力攒首付中~"
        current = self.current()
        lines = ["【房产】"]
        for home in rows:
            tag = STATUS_LABEL.get(str(home.get("status") or ""), "闲置")
            if home.get("id") == current.get("id"):
                tag = "自住中"
            extra = ""
            if home.get("status") == "rented_out" and int(home.get("rent_cents") or 0) > 0:
                extra = "，每月收租 %.0f 元" % (int(home["rent_cents"]) / 100.0)
            lines.append("· [%s] %s%s（%s）%s" % (
                tag, self._city_prefix(home), home.get("name") or "家",
                home.get("style") or "简约风", extra))
        return "\n".join(lines)

    def _rent_page(self):
        rows = [home for home in self.homes()
                if home.get("kind") == "rent" and home.get("status") != "expired"]
        current = self.current()
        if not rows:
            return "目前未产生租房支出。"
        if current.get("kind") == "own":
            return "目前未产生租房支出。"
        lines = ["【租房】"]
        for home in rows:
            tag = "当前租住" if home.get("id") == current.get("id") else "已退租"
            rent = int(home.get("rent_cents") or 0) / 100.0
            lines.append("· [%s] %s%s（月租 %.0f 元，下次交租：每月 1 号）" % (
                tag, self._city_prefix(home), home.get("name") or "出租屋", rent))
        return "\n".join(lines)

    # ---------- 参考图 ----------
    def set_room_image(self, room, path):
        home = self.current()
        images = home.setdefault("images", {})
        images[str(room or "living")] = str(path or "")
        home["images_needed"] = not (images.get("living") and images.get("bedroom"))
        self._save()
        return home

    def can_redraw(self, now=None):
        """大件/搬家/换风格才重绘；两次生图至少间隔 24 小时。"""
        import time as _time
        now = float(now if now is not None else _time.time())
        try:
            last = float(self.current().get("last_image_ts") or 0)
        except Exception:
            last = 0
        return last <= 0 or (now - last) >= IMAGE_COOLDOWN_SECONDS

    def mark_image_ready(self, now=None):
        import time as _time
        home = self.current()
        home["last_image_ts"] = float(now if now is not None else _time.time())
        home["image_status"] = "ready"
        home["images_needed"] = False
        self._save()
        return home

    def mark_image_failed(self):
        home = self.current()
        home["image_status"] = "pending"
        home["images_needed"] = True
        self._save()
        return home

    def room_prompt(self, room_type="客厅", now=None):
        home = self.current()
        key = "bedroom" if "卧" in str(room_type) else "living"
        items = (home.get("room_furniture") or {}).get(key) or []
        return build_room_image_prompt(room_type, home.get("style"), home.get("colors"), items, now)

    def room_scene_line(self, room="living"):
        """自拍时给模型的一句话：这个房间该有什么、不该有什么。"""
        home = self.current()
        key = "bedroom" if str(room or "") in ("bedroom", "卧室") else "living"
        label = "卧室" if key == "bedroom" else "客厅"
        items = (home.get("room_furniture") or {}).get(key) or []
        if not items:
            return ""
        if key == "bedroom":
            bad = "沙发、电视柜、茶几、餐桌、马桶、厨房灶台"
        else:
            bad = "床、衣柜、梳妆台、床头柜、马桶、厨房灶台"
        return ("背景是她的%s，房间里只能出现这些家具：%s；不要出现%s；"
                "不要出现人物、宠物、文字、水印，家具要落地有真实阴影。"
                % (label, "、".join(items), bad))

    def room_image(self, room="living"):
        return str((self.current().get("images") or {}).get(room) or "")
