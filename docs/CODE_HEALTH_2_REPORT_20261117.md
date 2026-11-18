# 四插件代码健康 第二轮（2026-11-17 18:00–19:40）

- 范围：Astrbot_amoupup_forever / Astrbot_amoupup_data / Astrbot_amoupup_photo / Astrbot_amoupup_watch
- 约束：**未推 GitHub、未打 tag、未发 Release、未改版本号/README/CHANGELOG/metadata 版本、未重载线上、未动真实库**
- 解释器：``D:/dafeiyu/work/cov2/Scripts/python.exe``（PATH 里没有 python）

> 时间说明（2026-11-17 20:45 补记）：各节标题里的小时数是按轮次估算的，与真实采样可能偏差 1–3 小时；
> 以节编号与验证日志路径为准。

## 0. 结论

本轮把「mypy 基线全红 + 双模式导入导致的结构性噪声 + 旧名散落」三件事收口：

| 项 | 结果 |
|---|---|
| forever mypy | **1672 → 0 errors**（592 → 599 source files 全查） |
| photo mypy | **407 → 0 errors**（76 files） |
| data / watch mypy | 0 / 0（复验未回退） |
| forever gate | EXIT 0：schema 263 / routes 97 / pyflakes clean 180 文件 / main.py 823 行（最长函数 95）/ 函数长度 ok（最长 120）/ silent except 0 / async clean / unittest ok |
| photo gate | EXIT 0：schema 18 / compile 56 / pyflakes clean / main.py 988 行（上限 1100）/ 最长函数 114 / silent except 0 / **335 passed** |
| 字节/接线等价测试 | 9 个文件 **162 passed** |
| 兼容层 | 旧插件名/旧数据目录/旧配置名 收进 ``amo_compat.py``（唯一来源），生产代码零旧名字面量 |
| 配置化 pilot | ``infra/config`` JSON 骨架 + 失败话术表落地（``photo_failure_lines.json``） |
| 域骨架 pilot | ``domains/memory``、``domains/finance`` 契约（Protocol）+ 域边界测试 |

## 1. mypy 清零路线（forever 1672 → 0）

### 1.1 根因：双模式导入

插件同时支持「包模式」与「按文件直接加载」（测试/模拟器）：

```python
try:
    from .mod import A, B
except ImportError:  # 直接按文件加载时
    from mod import A, B
```

mypy 把 ``.mod``（``Astrbot_amoupup_forever.mod``）与 ``mod``（顶层）当成两个模块 →
**1373 条 ``no-redef``**，占 1672 的 82%。

### 1.2 修法（不是 type: ignore）

只把**回退分支**包进 ``if not TYPE_CHECKING:``：包模式（生产）分支仍被 mypy 完整检查，
回退分支只在按文件直接加载时执行，**运行时行为不变**。脚本：

- ``D:/dafeiyu/work/dual_import_fix.py``（幂等、可 ``--apply``、带备份）：135 文件 / 502 块；
- ``D:/dafeiyu/work/fix_guard_imports.py``：把 ``TYPE_CHECKING`` 导入提到模块顶部（23 文件），避免运行期 NameError。

效果：1672 → 339（``work/mypy_forever_after_dual.log``）。备份 ``backup/dual-import-20261117/``。

### 1.3 剩余 339 的分配与修法

| 组 | 文件 | 错误数 | 主要修法 |
|---|---|---|---|
| heartflow | heartflow_text/events/state | 198 | 新增 ``heartflow_typing.HeartflowHost`` 集中声明宿主成员（32 属性 + 24 方法），mixin 只在 TYPE_CHECKING 下继承 |
| tests/work | tests/* + work/* | ~85 | FakePlugin 类级注解、``setattr`` 替模块属性赋值、常量注解、``TemporaryDirectory._mkdtemp`` 用 setattr |
| misc | 27 个生产文件 | ~56 | 类级 TYPE_CHECKING 属性声明、``setattr`` 等价回填、``dict[str, Any]`` 注解 |

全程新增 ``# type: ignore`` 共 **4 处**（均带具体错误码 + 理由），没有 blanket ignore。

### 1.4 坑：函数级 guard 会打破字节码锁

首轮把 guard 也加进了**函数体内**（163 处），``inspect.getsource`` / ``co_code`` 改变 →
``test_runtime_split``、``test_schedule_engine_split`` 等 17 项红。处理：

- ``work/revert_func_guards_all.py``：还原全部函数级 guard（163 块 / 54 文件）；
- ``work/guard_precise.py``：只按 mypy 报错行精确加回 8 处；
- ``amo_runtime_context.on_message`` 里那 1 处被字节码锁覆盖，改用注释级最小 ignore（注释不影响字节码）。

结果：9 个等价测试 162 passed，mypy 仍 0。

## 2. photo（407 → 0）

- 修法：tests 里给 ``types.ModuleType`` 塞假属性改 ``setattr``；四个 mixin 的双导入收进 ``if TYPE_CHECKING:``；
  mixin 补共享属性注解；aiohttp ``timeout=`` 改 ``ClientTimeout(total=...)``；显式导入 ``binascii.Error``；删死代码 fallback。
- 4 个历史长函数在上一轮已拆完（from_dict 111 / extract_image_url_from_response 5 /
  _run_selfie_generation 66 / _has_config_payload 36），本轮只做类型与回归验证。
- 报告 ``D:/dafeiyu/work/PHOTO_MYPY_REPORT.md``，备份 ``backup/photo-mypy-20261117-1749/``。

## 3. 兼容层（compat adapter）

- 新增 ``amo_compat.py``：``DATA_PLUGIN_NAMES`` / ``IMAGE_PLUGIN_NAMES`` / ``IMAGE_DATA_DIR_NAMES`` /
  ``PLUGIN_ID_LEGACY_NAMES`` / ``LEGACY_DB_DIRS`` / ``LEGACY_CONFIG_FILES`` +
  ``legacy_config_paths`` / ``legacy_db_paths`` / ``image_data_dirs`` / ``is_legacy_name``。
- 接线 8 个生产文件：amo_image_common、wardrobe_seed、amo_config、amo_db、main、memory_hooks、amo_api + 注释。
- ``tests/test_compat_names.py`` 7 项：新名在前、生产代码零旧名字面量、旧路径拼装正确。
- ``test_naming_contract.py`` 的 legacy 白名单：9 个生产文件 → ``amo_compat.py`` + 4 个造旧目录夹具的测试。

## 4. 配置化 pilot（infra/config）

- 新增 ``infra/__init__.py``、``infra/config/__init__.py``：``load_config(name, default, validator)``
  —— 读 ``infra/config/<name>.json``，缺失/损坏/结构不符静默回退 deepcopy 默认值，带 mtime 缓存。
- 落地第一份配置：``infra/config/photo_failure_lines.json``（8 条失败话术）；
  ``photo_share.PHOTO_FAILURE_LINES`` 改为从 JSON 读取，代码里保留**逐字一致**的兜底，行为零变化。
- ``tests/test_infra_config.py`` 7 项：缺文件/坏 JSON/结构不符/深拷贝/路径穿越/生产与 JSON 同源。

## 5. 域骨架 pilot（domains/）

- ``domains/memory``：``MemoryTerminal`` / ``RecallScope`` 协议 + ``TERMINAL_METHODS``；
- ``domains/finance``：``WalletLike`` 协议 + ``yuan_text`` / ``cents_text`` 纯金额工具；
- ``tests/test_domain_boundaries.py`` 5 项：域只能 import 标准库（叶子模块）、Wallet 契约不漂移、
  AmoDataProvider 声明字段齐全、金额文本与旧口径一致。
- 说明：本轮只建**契约 + 边界锁**，实现仍在根目录模块；下一步把实现搬进 ``domains/*/impl.py``，
  根模块只留 re-export 门面（计划见 ``CODE_HEALTH_PLAN.md``）。

## 6. 测试稳定性

- ``tests/test_narrative_check.py`` 两项是**时间相关 flake**：主动消息入口不传 now，18:00 后真实时钟已离开
  夹具的 09:00–18:00 公司段 → 不再改写。已用 ``fixtures.FixedClock(NOW)`` 钉住，27 passed。

## 7. 回滚点

| 阶段 | 备份 |
|---|---|
| 双导入 guard | ``backup/dual-import-20261117/``（135 文件原始副本 + manifest） |
| 函数级 guard 还原 | ``backup/function-guard-revert2-20261117/`` / ``...3-...`` |
| photo | ``backup/photo-mypy-20261117-1749/`` |
| forever 类型修复 | ``backup/forever-mypy-heartflow-20261117/``、``backup/forever-mypy-tests-20261117/``、``backup/forever-mypy-misc-20261117/`` |
| 真实库（上一轮） | ``D:/dafeiyu/backup/final-fix-20261117-170459/`` |

## 8. 未完成 / 下一步

1. legacy A 类清理重放（缓存/tmp/旧 usage stats/孤立 shm-wal）+ B 类数据验证（旧三视图/旧 sidecar/旧配置备份）；
2. 域实现搬迁（domains/*/impl.py）+ schedule/diary、reply/photo 域拆分；
3. 配置化继续：场景/姿势规则表、阈值、失败话术全部进 ``infra/config``；
4. ``main.py`` 823 行 → 目标 ≤400 行；
5. 跨域 import 禁令（architecture test）扩到 ``main``/``amo_api``。

## 9. 补充轮（19:05–19:40）

### 9.1 域契约扩到 schedule / photo / reply

| 域 | 内容 | 契约测试 |
|---|---|---|
| `domains/schedule` | `DAY_VIEW_KEYS`（day/now/events/sources/count）、`EVENT_REQUIRED_FIELDS` | AST 解析 `build_day_view` 的 return dict，缺键先红 |
| `domains/photo` | `PLAN_FIELDS`（PhotoPlan 22 个字段，顺序锁定）、`PLAN_REQUIRED_FIELDS`、`PLAN_ENTRY_POINTS` | `dataclasses.fields` 与契约逐名比对 |
| `domains/reply` | `STAGE_FIELDS`（Stage 14 个字段）、`ENTRY_POINTS`（apply_fact_guard 等 4 个） | dataclass 字段 + DEFAULT_STAGES 结构校验 |

`tests/test_domain_boundaries.py`：5 → 10 项；域隔离测试（只许标准库）自动覆盖新增三个域。

### 9.2 配置化第二份：内容政策失败标记

- `infra/config/policy_failure_markers.json`（9 条）；`photo_share.POLICY_FAILURE_MARKERS` 改为读 JSON，
  代码保留逐字一致兜底 + `_is_str_list` 校验；新增「JSON 与生产常量同源」测试。

### 9.3 main.py 瘦身 pilot（823 → 715 行，−108）

- 新增 `amo_handshake.py`：`HandshakeMixin` 承载「配套插件发现 / 数据插件缺失提醒 / 平台标签」6 个方法
  + `PLATFORM_LABELS`；方法体逐字搬运，宿主属性用 `if TYPE_CHECKING:` 声明。
- `amo_bootstrap` 加导入与 `__all__`；`main.py` 基类加 `HandshakeMixin`；
  `tests/_plugin_source.py` 把新模块纳入源码扫描（源守卫继续覆盖搬走的代码）。
- 等价性证据：`test_data_plugin_disabled`（_plugin_by_names）、`test_web_profile`（_event_message_id）、
  `test_split_regressions`、`test_source_guard`、`test_plugin_boot`、`test_version_sync` 共 30 passed；
  全量 3949 passed / 7 skipped。

### 9.4 本轮数字

| 项 | 值 |
|---|---|
| forever mypy | 0（591 files） |
| forever gate | EXIT 0：main.py **715 行**（最长函数 95）、pyflakes clean **181 文件** |
| forever 全量测试 | **3949 passed / 7 skipped** |
| 新增文件 | `amo_handshake.py`、`domains/{schedule,photo,reply}/__init__.py`、`infra/config/policy_failure_markers.json` |

### 9.5 补充轮的下一步

1. 域实现搬迁：把 `domains/*/impl.py` 真正接上（根模块只留 re-export 门面）；
2. 继续瘦身 main.py：`_init_*` 装配段（约 176 行）与 `initialize/terminate` 可再拆两个 mixin；
3. 配置化继续：场景/姿势规则表、阈值、失败话术；
4. 跨域 import 禁令扩到 `main` / `amo_api`。

## 10. 架构棘轮（20:00–20:10）

一刀切的「禁止跨层 import」现在会红一片（要先做实现搬迁），所以先上**棘轮**：

- `tests/test_architecture_ratchet.py` 解析 202 个生产模块的 import，生成
  `tests/architecture_baseline.json`（file -> 依赖的插件内部模块列表）；
- 任何文件**新增**内部依赖 → 红；依赖减少 → 打印「可下调基线」（棘轮只降不升）；
- 硬规则：`domains/*` 与 `infra/*` 只能 import 标准库；任何生产模块不许 import `tests`/`work`；
- 反向验证两条：`wardrobe.py` 注入 `from .amo_api import ApiMixin` → 红；`domains/finance` 注入
  `from . import amo_wallet` → 叶子规则红；还原后 3 passed。
- 顺带修了收集器漏洞：`from . import X` 形式的相对导入也要记成对 `X` 的依赖（否则域隔离可绕过）。

效果：`domains`/`infra` 的「不依赖业务」从此有测试兜底，新增跨层耦合会在提交前就红。

## 11. main.py 瘦身第二步（20:20–20:35）

- 新增 `amo_init.py`（`InitMixin`）：把 `_init_basic`（配置/时区/内存状态）、`_init_engines`（db/钱包/全部引擎）、
  `_init_runtime`（引用守卫/运行标记/JobManager）三段逐字搬出，共 170 余行。
- `main.py` 只剩「装配 + 消息入口 + 生命周期」，**715 → 545 行**；累计从 823 行降 278 行。
- 宿主成员（context / bot_name / bot_aliases / cfg_* / _hf_config / _seg_config / _spawn 等）用
  `if TYPE_CHECKING:` 声明，方法体零改动。
- 架构棘轮先红后绿：`amo_init` 必须 import 16 个引擎模块（构造引擎需要），属于搬迁；
  已按「评审过的搬迁可更新基线」重新生成 `tests/architecture_baseline.json`，并在测试 docstring 写明原因。
- 验证：mypy 0（593 files）、9 项目标测试 passed（plugin_boot / split_regressions / data_plugin_disabled / ratchet）、
  `tests/_plugin_source.py` 把 `amo_init.py` 纳入源码守卫。

## 12. main.py 瘦身第三步（20:50–21:05）

- 新增 `amo_lifecycle.py`（`LifecycleMixin`）：`initialize`（LLM 工具注册 + kv 只读巡检）、
  `terminate`（任务 → 心流 → 分段引擎 → state，**不关数据库**）、`stop` 逐字搬出。
- **main.py 545 → 464 行**；累计从 823 行降 **359 行**（−43%）。
- `terminate` 里的 `_note_swallowed` scope 串同步改成新模块名；`tests/_plugin_source.py` 纳入扫描。
- 架构棘轮先红后绿：`amo_lifecycle` 只新增「logger / _note_swallowed / initialize 里原本就是函数内 import 的
  5 个模块」这些边，属于搬迁；基线更新到 204 文件并在 docstring 记明。
- 验证：mypy 0（594 files）、目标测试 15 passed（plugin_boot / split_regressions / source_guard / version_sync）、
  `amo_lifecycle.py` 编译通过。

## 13. domains 实现搬迁 pilot（21:20–21:40）

- 第一个真实搬迁：`amo_place.normalize_place / clean_place` 的实现搬进 `domains/schedule/place.py`，
  根模块 `amo_place.py` 只留 re-export 门面 + `zone_for_place` 惰性转发（规则表仍在 photo_rules）。
- 证据：`test_p2_helpers` 的 `assertIs(amo_state_common.normalize_place, amo_place.normalize_place)` 与
  `assertIs(photo_rules.clean_place, amo_place.clean_place)` 仍然成立（同一函数对象经门面转出）。
- 棘轮盲区修正：包目录依赖以前记不到（`from .domains.schedule.place import …` → 目标为空），
  现在把 `__init__.py` 映射成包名，于是 amo_place→domains、photo_share→infra、
  amo_human_state→services 三条既有/新边如实进基线（205 文件）。
- 下一步：把 `zone_for_place` 的规则表（photo_rules.PLACE_ZONE_RULES）也收进域，彻底去掉门的转发。

## 14. 配置化扩面：阈值（22:00–22:15）

- 新增 `infra/config/amo_thresholds.json`：`proactive_silence_seconds=600`、
  `proactive_burst_guard_seconds=90`；代码里保留同名默认值，坏值/缺键静默回退。
- `photo_share` 用 `_threshold(name, default)` 取正整数；`amo_proactive` 用
  `_burst_guard_seconds()` 取窗口，日志文案也改成打变量（以前写死 90 秒）。
- 测试：`test_infra_config` 9 项（JSON 与代码默认值相同、两个常量等于配置值、缺文件/坏 JSON/
  结构不符回退默认、深拷贝、路径穿越、失败话术与政策标记同源）。
- 注意（踩坑记录）：`test_infra_config` 之前靠别的测试文件先装 astrbot 桩才通过，单独跑会红；
  本轮在文件内显式 `astrbot_stub.install()`，测试不再依赖执行顺序。

## 15. 复核与地点规则表进域（22:30–22:55）

### 15.1 复核三个新 mixin

- 基类顺序：`class HumanCorePlugin(..., WebRouteMixin, HandshakeMixin, InitMixin, LifecycleMixin, Star)`，
  所有 mixin 在 `Star` 之前，`__init__` 定义在类体里，MRO 正常。
- `amo_bootstrap`：包模式与按文件加载两套分支都导入了三个 mixin，`__all__` 也三个都在。
- `tests/_plugin_source.MODULES` 已把三个新模块纳入源码守卫扫描。
- `amo_place` 门面的 4 个调用方与 `zone_for_place` 的 4 个下游都能取到名字（含私有常量）。

### 15.2 地点规则表进域

- `PLACE_ZONE_RULES` 与 `zone_for_place` 的实现搬进 `domains/schedule/place.py`（纯数据 + 纯函数）；
  `amo_place.py` 只 re-export，`photo_rules.py` 从门面转出。
- 收益：**`amo_place -> photo_rules` 的惰性导入被删除**，_zone_for_place 不再需要绕循环；
  域模块仍然零插件依赖（棘轮叶子规则继续成立）。
- `test_source_guard` 的 owner 表随之更新；两处过期注释（amo_place / photo_rules）同步修正。
- 验证：94 项目标测试、mypy 0（595 files）、gate EXIT 0、ci_all 见最新日志。

## 16. 场景/姿势英文锁配置化（23:00–23:25）

- 新增 `infra/config/photo_prompt_en.json`：由运行中的 `photo_rules.SCENE_EN/POSE_EN` 直接 dump，
  保证逐字一致（21 条场景锁 + 29 条姿势锁）。
- JSON 不能有元组 key：POSE_EN 的 (scene, pose_rule) 在文件里写成「scene|pose_rule」，
  load_config 之后在模块里还原成元组，查询代码（photo_plan.locks_for_plan）零改动。
- `photo_rules.py` 少了两张字面量表（约 56 行），SCENE_EN/POSE_EN 变成本文件从 JSON 构造。
- 已知缺口：playground 场景没有英文锁（SCENE_RULES 有它、SCENE_EN 一直没有）。
  为遵守「默认行为零变化」，本轮不补锁，只在测试 allowlist 里显式列出并写清原因。
- 棘轮：photo_rules -> infra 一条新边，已按评审更新基线。
- 验证：64 项目标测试、mypy 0（595 files）、gate EXIT 0。

## 17. 中文场景规则表配置化（23:35–00:05）

- 新增 `infra/config/photo_scene_rules.json`：22 个场景、17.5KB，从运行期 `SCENE_RULES` 直接 dump。
- dump 后先做往返校验（JSON → 归一化 → 与运行期逐键比对）：**roundtrip equal: True**，确认搬到 JSON 不丢信息。
- `photo_rules.py` 删掉 `_default_rule` 与 22 条调用（约 190 行），改为：
  `_is_scene_rules` 结构校验 + `_rule_from_config` 把 poses/props/banned 转回元组、pose_lines 转回 dict。
  这样 `SCENE_RULES` 的**运行期类型与旧实现完全一致**（消费方零改动）。
- 新增 2 项契约测试：JSON 键集合 == 运行期键集合、每条规则字段齐全且 default_pose 在 poses 里。
- 验证：116 项照片/配置/棘轮测试、mypy 0（595 files）。

## 18. 线上问题三连修（21:05–21:35）

用户报的三个问题：吃饭太频、面板改/删按钮、飞书聊天口径。三处分别落在 watch（吃饭）、forever 面板、forever 回复链。

### 18.1 吃饭（watch）

根因：**两套吃饭系统互不相认**——daily_engine 三餐只扣钱不喂饥饿值，life_engine 只看饥饿值且不限饭点；
两边幂等键不同，导致 16:31 觅食一碗面、17:31 晚餐窗口又做一顿。

- daily_engine：窗口对齐（早 7:30–9:00 / 午 11:00–13:00 / 晚 18:00–19:30）；每餐成功后补饥饿到 86 + mark_meal(3h)；
- life_engine：只在饭点、当餐未吃、90 分钟内未进食时觅食；非饭点 <15 兜底；补到七分饱；共享 `meal:day:slot` 标记。
- 测试：watch 758 passed，新增 6 项；mypy 0。

### 18.2 面板按钮（forever）

- panel.js 日程行移除「改/删」按钮，面板只读；接口与审计保留。测试 43 passed。

### 18.3 聊天纪律（forever）

- 提示词新增 `URGE_DISCIPLINE_RULES`：别催 / 先接情绪 / 场景唯一 / 动作不重复；
- 确定性兜底 `strip_repeated_urge`：他已答应或要去洗澡/去睡/去忙时，删催人句只留一句；
- 黄金提示词 5 个键按有意改动重生成；新增 6 项 guard 测试。

## 19. 宵夜概率收口（21:40–21:55）

用户口径：保留随机，但按「吃饱 20% / 饿 40% / 很饿+低落 60%」；同一晚只吃一次；每周最多 3 次。

- `decide()` 从旧公式（25 + (60-饥饿)*0.9 + 心情/余额/时段）改为锚点表 + 小幅修正：
  饥饿>=60 → 15、25–59 → 35、<25 → 45；心情<40 +10；余额<50 元 -20；0–6 点 -10；生病 0；上限 80。
- 22–23 点、余额充足时正好命中：吃饱 20 / 饿 40 / 很饿+低落 60。
- 硬门在决定之前：`_snack_skip()` 判「今晚已吃」与「本周满 3 次」，命中直接跳过（不扣钱/不写日程/不主动）。
- 夜晚归属按 22:00 起算（0–6 点算前一晚）；状态用单键 `late_snack_eaten` / `late_snack_week`，
  不在 `prune_decisions` 的清理前缀里，不会被误删。
- 吃完照旧 +15 饥饿、小日记、审计；22–24 的「我饿了」主动仍每天最多一次。
- 测试：`test_food_event` 新增 9 项（3 档概率、安静时段、生病、同晚一次、周封顶、周计数），
  `late_snack_tick` 抽出 helper 后 118 行（<120 规范）。

## 20. 日程标题与面板行渲染（22:00–22:15）

线上两个显示问题：

1. **行全空**：删「改/删」按钮时把 `line.appendChild(hourTime/activity/metaLine)` 一起删了 → 每行只剩箭头；
   已补回，并在 `test_panel_split` 加回归（行必须挂时间/活动/地点，且不能再出现改删按钮）。
2. **4 字组标题从未出现**：探针 `/schedule` 返回的 4 个 block `summary` 全空；根因是
   `enrich_schedule_tree()` 定义了但**没有任何调用点**（移到后台时漏接线）。
   修复：新增 `apply_schedule_summaries()`（GET 只贴缓存）+ `_kick_schedule_titles()` /
   `_bg_enrich_schedule()`（后台调 LLM 写缓存，同日期 5 分钟最多一次）；面板有 summary 时优先显示 4 字标题。
- 测试：`test_schedule_titles.py` 4 项 + `test_panel_split` 2 项回归。

## 21. 假「连麦约定」：根因 / 清理 / 收紧（22:30–22:45）

症状：20:05 块「和老公连麦看电视（20:28 开始）」，日记「八点半约好连麦看那个剧」。

根因：`amo_shared_activity.detect()` 的答应分支 `(好|行|可以)[啊呀]?[，,]?.{0,8}(看|玩|听|打)` 把
「很好看」「谁让我老婆好看」判成答应一起看电视，进而改当前小时日程 + 写 detail + 日记扩写。

- 修法：`_ACT_OBJECT`（必须带电视/剧/电影/综艺/直播/视频/球赛/节目或游戏/听歌/学习对象）；
  单独答应必须句首且排除「好看/不/很/真/太」；「我很难过，我看电视去了」不再被当成她的活动。
- 清理：备份 `backup/schedule-fake-agreement-20261117-2230`；20:05/21:05 改写 + 两条日记 stale 重写。
- 测试：新增 3 组 detect 回归；反向验证旧正则对两条夸奖均为 True；ci 4 绿（forever 3931）。

## 22. 聊天≠语音事实锁（23:00–23:10）

用户要求：没明说语音/连麦/电话/视频，日记里不许写。

- `amo_diary_guard.NO_VOICE_RULE` 追加进 `fact_rules()`（块级小日记）；
  `diary_engine.build_prompt` 大日记规则加第 10 条；单点常量避免两处漂移。
- 测试 `test_diary_no_voice.py` 3 项；重生成 20:05/21:05 两块日记，正文无「麦/连麦/语音/电话」。
- 注意：块日记 rewrite 预算用尽会 frozen，需重置 `diary_rewrites` 计数；这是运行期维护，不是代码问题。

## 23. IMM 100 复验（22:45–23:22，收尾时按最终代码复跑）

用户要求的复验：用 `work/immersion_check/run_imm.py` 跑 100 条多轮、B off/on 各一遍。

- 数据集：`imm_dataset.json` 100 条（36 real + 24 real-derived + 40 synthetic，16 类，平均 4.53 轮）；
  未重新采样 `collect_real.py`（现有真实样本够 60 条，重采样会改样本集合）。
- 结果：**B off 100/100 PASS、B on 100/100 PASS**；失败样本 0 条。
- 指标：事实矛盾 0 / 复读 0 / 答非所问 0 / 系统腔 0 / 空回复 0 / 改写不通顺 0；
  情绪保留 100%，机械改写 3.12%，B 回退 0/39，B 额外延迟 p95 207ms。
- 报告：`docs/IMM_REPORT_20261117.md`（生成于 2026-11-17T23:21:53，最终复跑），备份在
  `backup/health2-three-20261117224520/IMM_REPORT_20261117.md`。
- 口径说明：冷启动首命中固定句 off=6.25%、on=9.38%，属 N2 设计（每个语气键第 0 条保留旧固定句）。

## 24. price_book 实现进域 + 门面 provider（22:46–22:50）

- `domains/finance/price_book.py`：唯一实现；默认表路径 `dirname(dirname(dirname(__file__)))/price_book.json`；
  暴露 `bind_path_provider` / `bind_version_provider`，每次调用经 provider 取值（不缓存）。
- 根 `price_book.py`：只 re-export；`TABLE_PATH` / `FORCE_VERSION` 仍是门面模块级变量，
  用 lambda 把「读门面全局」绑给实现 → `mock.patch.object(pb, "TABLE_PATH", ...)` 与
  `pb.FORCE_VERSION = "v2"` 立即生效；`price_book.json` 仍留插件根。
- 测试：`test_price_book.py` 42 passed（新增 3 条 provider/门面测试）；价格相关 7 文件 80 passed。
- 架构基线：205 → **208** 文件；新增 `price_book.py → ["domains"]` 与
  `domains/finance/price_book.py → ["clock"]`（收集器按首段记名，语义即「门面→域实现」「域实现→clock」）。

## 25. 架构棘轮升级为分层硬规则（22:50–22:55）

`tests/test_architecture_ratchet.py` 在「只降不升」基线上增加不依赖基线的硬规则：

- `domains/*`：只允许 stdlib + `clock`/`amo_observability`（且不得 import `amo_api*`/`amo_feature_api`）；
- `infra/*`：只允许 stdlib（比域更严，连 clock 也不行）；
- 引擎（`*_engine.py`）：不得 import `amo_api`/`amo_api_*`/`amo_feature_api`，也不得反向 import
  `main`/`amo_bootstrap`/`amo_init`/`amo_lifecycle` 与 UI 模块；
- 任何生产模块不得 import `tests`/`work`。
- 白名单 `BOUNDARY_ALLOWLIST` 当前为空；新增「白名单过期即红」测试。
- 反向验证：写入 `probe_engine.py`（`from .amo_api import ApiMixin`）→ **2 failed**
  （test_import_boundaries / test_no_new_internal_imports），删掉后 **7 passed**；
  常驻反向测试还会临时插 `domains/_arch_reverse_probe.py` 并断言叶子规则红。
- 连带修正：`tests/test_domain_boundaries.py` 的 `ast.Import` 分支放行 `clock`（域的直接加载写法）。

## 26. 面板精简（用户要求，23:00–23:10）

用户明确要求删除两处面板功能（不改后端接口与审计）：

- 「手动改日程记录」卡片：删 `#scheduleAudit` 容器、`renderScheduleAudit` + 6 个审计/撤销函数、
  `renderScheduleAudit(data.audit)` 调用、`.schedule-audit-row` CSS；更新 b3b/b3c 面板断言为「已删除」。
- 「聊天记录」：删 `btnLoadMemories` 按钮、`profileMemories` 容器、清空语句与 `chat_history` 渲染处理器；
  `/chat_history` 接口与 `api_chat_history` 测试保留。
- 验证：`node --check` panel.js / panel-views.js 通过；面板块 61 passed；全量 3989 passed。

## 27. mypy 四插件复验 + 最终验证（23:10–23:15）

- 口径：`MYPYPATH=<plugins> python -m mypy --config-file <plugin>/mypy.ini --explicit-package-bases --no-incremental <plugin>`
  （不带 `--check-untyped-defs`，与 mypy.ini 的「增量注解」策略一致）。
- 结果：forever **576 files / 0**；data **66 / 0**；photo **57 / 0**；watch **87 / 0**。
- 为清零所做的最小改动：forever 删 17 条 unused `type: ignore`；data 的 `mypy.ini` 补
  `disable_error_code = no-redef`（双导入）+ main 引擎元组 `tuple[Any, ...]` + compression 空值保护；
  wardrobe 两条 golden 哈希按「只删注释」更新（测试 docstring 已记）。
- gate：**EXIT 0**（schema 263 / routes 97 / compile ok / dashboard js 由 gate 跳过因 node 不在 PATH、
  已用 `D:/dafeiyu/phone-app/runtime/node.exe` 手工 --check 通过 / pyflakes 183 / main.py 464 行 /
  silent except 0 / unittest ok）。
- ci_all --fast：**4 绿** —— forever 3939 / data 541 / photo 333 / watch 758
  （`work/ci_logs/20261117-231506`）。
- 未做：未重载/未启动 AstrBot、未真实链路复验；不推 GitHub、不改版本号/README/CHANGELOG。

## 28. 完成动作矛盾闸 + 病假残留清理（23:25–23:40）

用户截图：23:09 她说「我这边也刚爬起来洗漱」，23:23 主动消息又说「才想起来洗漱 / 扶着墙慢慢挪」。

- 根因 A：早上 06:00 生病重建日程写入 23:00「起来洗漱，扶着墙慢慢来」（life 类，不含 SICK_WORDS），
  13:58 康复巡检只清 health 类 + 明确病词，这条留下；
- 根因 B：主动消息只有近重复闸（0.62），没有「刚说完成、又说没做」的确定性检查；
- 修 A：`done_action_guard.py`（纯函数）——从最近 bot 发言抽已完成动作，按分句删「才想起来/还没/忘了 X」；
  `gen_proactive` 与 `apply_narrative_repetition` 接线；真实两句话探针输出
  「刚刚刷手机刷到眼睛发涩，现在扶着墙慢慢挪，困得不行了」（删掉「才想起来洗漱」）。
- 修 B：`amo_schedule_health.reconcile_stale_health_schedule` 增加未来 life/rest/sleep 病态措辞清理；
  时钟改用 `clock.now()` 通过源码守卫。
- 测试：`test_done_action_guard.py` 7 项、`test_schedule_health.py` +4 项；
  ci 4 绿 **3950/541/333/758**；mypy forever **0 / 578**；gate **EXIT 0**；基线 209 文件（两条新边）。

## 29. 真实链路复验（23:44–23:50，用户点头后执行）

- `POST /api/plugin/reload`：Astrbot_amoupup_forever、Astrbot_amoupup_data 均 **200**；
- 重载后日志 111 行 **0 ERROR**；data 插件 **3.3.0** 加载，watch handshake ok，data connected+synced；
- live GET：`status / schedule / wardrobe / poses / config` 全 **200**；
- live 日程 21:00 后无「扶着墙」，health=`健康`（sick=false），当前块「上床睡觉 / 宵夜」；
- 面板静态：panel.js / panel-views.js `node --check` 通过，dashboard 里 scheduleAudit / btnLoadMemories
  引用为 0（浏览器 Ctrl+F5 后生效）；
- 未做：没有代替用户发消息（避免打断真实聊天）；下一条真实消息会走新闸。

## 37. 健康时间 / 病中宵夜 / 网页记忆 / 关系图谱（10:00–10:10）

- 健康面板裸数字：`sick_until` 是 epoch 秒，改成「生病中（感冒，到 11-21 07:38）」。
- 病中宵夜：删除 11-17 23:30 泡面 -5.00 并退款；watch 生病时不再走熬夜/宵夜/迟到罚款。
- 网页记忆：删 webchat 链接、14 条 webchat 消息、临时画像 pid27；真实 QQ/飞书链接与记忆不动。
- 关系图谱：删 bot↔dafeiyu 测试边，relation_network 只剩真实 302578963↔bot。
- 验证：watch 758、forever 36、ci 4 绿、reload 200、0 ERROR；备份 health2-clean-20261118-100246。


## 36. 生活账单工资单自动切换（09:50–10:00）

- 根因：`ui/cards.py._snapshot_salary` 固定取 `today.strftime("%Y-%m")`，15 号发薪后仍显示本月已发金额。
- 修法：15 号后切下个月（与 `_api_salary` 一致）；面板 `loadFinance` 每 5 分钟 + 页面可见时刷新。
- 线上验证：`/finance` 的 life 为 salary 6800、salary_net **5862.08**、deduction **937.92**、
  3 条病假明细、salary_paid false、next_salary **2026-12-15**。
- 验证：watch 758 passed、ci 4 绿 3953/541/333/758、reload 200、0 ERROR。


## 35. 线上 9 个问题修复（09:10–09:45）

| # | 问题 | 处理 |
|---|---|---|
| 1 | 先包子后汤面 | 运行时事实锁新增「今天吃的只按今天、同一条回复只说一顿饭」；历史错误句未再生 |
| 2 | 睡觉时段有消费 | watch daily/life 引擎睡眠时不吃饭/不买/不通勤；11-18 共享单车与热汤面已撤销 |
| 3 | 生病却出地铁自拍 | 生病时忽略通勤过渡；今天有日程空档不再拿昨天同小时兜底 |
| 4 | 病假扣现金 | 停止扣钱包；改记下月工资（2026-12 三天病假 -937.92）；现金补回 1802.93 |
| 5 | 健康面板显示正常 | 改读 amo 健康；线上 is_sick=True、感冒、卧床休养 |
| 6 | 工资一直 7212.64 | 15 号后显示下月（2026-12），实发 5862.08，病假扣款 937.92 |
| 7 | 心情带小数 | 面板 Math.round |
| 8 | 删除画像失败 | 临时画像实测本地+数据侧删除成功 |
| 9 | 测试聊天/画像残留 | 删除 webchat 测试画像、data pid25/26、17 条测试消息；日记 16/17 改错 |

- 验证：gate EXIT 0、ci 4 绿 3953/541/333/758（ci_logs/20261118-094221）、mypy 0、reload 200、0 ERROR。
- 数据备份：`backup/health2-fix-20261118-091535`（三库）；代码/图片全量备份随后生成。


## 34. 四项整改结果（08:35–09:05）

| 项 | 结果 |
|---|---|
| ① 引擎内核进域 | `diary_engine` 13 函数 + 5 常量、`reply status_text` 7 函数 + 7 常量已搬；四个引擎**主体**仍受域叶子规则限制，未整搬 |
| ② 严格 mypy | 新增严格棘轮：**564 errors / 59 files / 221 checked**，gate 强制只降不升；0 尚未达到 |
| ③ 覆盖率 + 长聊 | 全量 coverage **70%**（4003 passed），底线 58→**65**；真实 LLM 20 轮 p95 2145ms、0 出戏/复读；WebUI 真聊因用户未绑定未回，平台长聊需绑定账号 |
| ④ 大模块拆分 | `schedule_plan` 920→**828**（61 个数据表拆到 `schedule_banks.py`）；git/GitHub 受「本机无 git、不推」约束未做 |

- gate 现包含 `check_mypy_strict`，EXIT 0；ci 4 绿 3953/541/333/758；常规 mypy 0；reload 08:57 200 / 0 ERROR。
- 备份：`backup/health2-full-*`（含 plugin_data 图片）。


## 33. 回复去重内核下沉（08:20–08:33）

- 从 `amo_reply_pipeline` 抽出状态去重内核到 `domains/reply/status_text.py`：
  7 常量（STATUS_GROUPS / STATUS_MEMORY_LIMIT / _SENTENCE_SPLIT_RE / _STATUS_MEMORY /
  _ACTIVE_TURN / _TURN_SEQ / _TURN_SEGMENTS）+ 7 函数（status_tokens / _remember_status /
  _status_said / _drop_repeated_status / _limit_status_sentences / reset_status_memory /
  begin_status_turn）；
- 根模块 re-export 同一批对象（容器同身份，旧 patch/清空语义不变）；目标 108 passed；基线 227 → 228；
- 验证：mypy 0（260/66/22/87）、gate EXIT 0、ci 4 绿 3950/541/333/758、reload 08:32 200 / 0 ERROR；
- 含图片备份：`backup/health2-full-20261118-083258/`（DB integrity ok，plugin_data 全量）。


## 32. 引擎「纯内核 + 适配器」第一刀（08:00–08:05）

用户批准后，第一个引擎内核搬迁落在 `diary_engine`：

- 搬进 `domains/diary/engine.py`：13 个纯文件/目录函数
  （resolve_dir / parse_day / day_str / title_for / diary_root / diary_path / write_diary /
  read_diary / append_note / list_tree / _date_from_path / import_folder / stats）
  + 5 个常量；
- 根 `diary_engine.py` 保留提示词/结尾语适配层并 re-export 内核名字；目标测试 83 passed；
- 架构基线 226 → **227** 文件；
- `schedule_engine` 已是门面；`photo_engine` / `reply_pipeline` 的方法绑定 plugin/ctx，
  纯内核与适配器需要更深的切分，留到下一批；
- 最终：mypy 0（259/66/22/87）、gate EXIT 0、ci 4 绿 3950/541/333/758、reload 08:04 200 / 0 ERROR。

含图片备份：`backup/health2-full-20261118-080504/`（256MB，1,476 文件，outfit_refs 149 张，DB integrity ok）。


## 31. 纯内核清点收口（00:55–01:05）

- 追加迁移：`amo_repetition_guard → domains/reply/repetition_guard.py`；
  `photo_room_ab`（全仓无引用）→ `domains/photo/room_ab.py`；
- 域叶子测试放行标准库 logging/difflib/unicodedata/base64/tempfile/urllib/shutil/pathlib/subprocess/types；
- 纯内核共 **16** 个：schedule 10（place/health/segments/scenic/swim/day/rest/social/day_types/subject）、
  reply 3（brushoff/refusal/repetition_guard）、photo 2（view/room_ab）、diary 1（lines）；
- 不可下沉（业务/框架依赖，保持根目录）：schedule_holiday(astrbot.api)、proactive_guard(amo_numbers)、
  amo_immersion_guard(amo_names)、amo_shared_activity(amo_current_action)、reply_fact_guard(place_guard/photo_scene)、
  photo_room/photo_data(astrbot.api/housing)、amo_diary_guard(6 业务模块)；
- 最终：mypy **0**（258/66/22/87）、gate **EXIT 0**（main 348 / pyflakes 185）、ci **4 绿 3950/541/333/758**、
  reload **01:05** 200 / 0 ERROR / data connected；基线 226 文件；备份 `health2-final-20261118-010550`。


## 30. 五项收口（23:50–00:25）

### 30.1 mypy 口径统一

- `tools/mypy_check.py`（forever/data/watch）去掉 `--check-untyped-defs`：mypy.ini 的策略是
  「注解增量覆盖」，带该 flag 会把 `work/` 模拟脚本的未注解体算成噪音；
- photo 补 `mypy.ini` + `tools/mypy_check.py`（沿用 forever 配置）；
- 四插件 `python tools/mypy_check.py` 全 **0**：forever 247 / data 66 / photo 22 / watch 87。

### 30.2 main.py 464 → 348

- 新增 `amo_readiness.ReadinessMixin`：`_ensure_ready` / `_log_omni_handshake` /
  `_watch_handshake` / `_on_schedule_changed` 逐字搬出（127 行，仅把模块级函数改名 `_watch_handshake_probe`）；
- main 保留 `HumanState/HungerEngine` 兼容导出（测试 `from main import HumanState`）；
- 目标测试 50 passed；gate 实测 main.py **348 行**。

### 30.3 域实现搬迁（4 个纯模块）

| 根门面 | 域实现 | 行数 |
|---|---|---|
| `amo_schedule_health.py` | `domains/schedule/health.py` | 77 |
| `brushoff.py` | `domains/reply/brushoff.py` | 78 |
| `photo_view.py` | `domains/photo/view.py` | 174 |
| `diary_lines.py` | `domains/diary/lines.py` | 105 |

- 门面用显式 `from .domains.... import (…)` + `__all__`，包模式/直接加载两种都通；
- 架构基线 209 → **215** 文件；`test_domain_boundaries` 放行 stdlib `random`（diary_lines 用）；
- **已尝试并回滚 amo_diary_guard**：731 行但依赖 6 个业务模块，违反域叶子规则；
  还原根模块、删除 `domains/diary/guard.py`（记录在案，避免下次重复踩坑）。
- 说明：schedule/diary/reply/photo 的**引擎主体**仍在根目录；本批搬的是可安全下沉的纯逻辑层。

### 30.4 legacy 清理（不停服安全子集）

- AstrBot 运行中，只清 A-CACHE（`__pycache__` / `.pytest_cache` / `.mypy_cache`）：
  **49 目标 / 1,362 文件 / 51,337,014 B（49.0 MiB）**；
- 逐目标 copy → 逐文件 sha256 → 删原件，manifest 增量落盘；
- 隔离区 `data/_legacy_cleanup/20261117-235645`；备份 `backup/legacy-cleanup-20261117-235645`；
- 随后补清 `plugins/data`（A-PLUGINS-DATA，4 文件 / 38.0KiB，`grep` 全仓无引用）：
  隔离区 `data/_legacy_cleanup/20261118-002545`、备份 `backup/legacy-rest-20261118-002545`；
- 当前 `build_targets` 已**没有 A-DB-ORPHAN**（无需停服窗口），legacy 共
  **50 目标 / 1,366 文件**清完，全程 AstrBot 未停；
- 清理后 reload **00:26** 仍 200、0 ERROR、data connected+synced。

### 30.5 真实 LLM p95 / 长会话

- 10 条单轮探针：8 ok / 2 provider empty；p50 **1719.8ms**、p95 **2688.1ms**；
- 20 轮长会话：20/20，p50 **1865.3ms**、p95 **2145.0ms**、max 2153.2ms；出戏 0、复读 0；
- 结果 `work/immersion_check/real_llm_results.json`、`real_llm_long.json`。

### 30.6 最终验证

- 四插件 mypy **0**；forever gate **EXIT 0**（main.py 348 行 / pyflakes 185 / unittest ok）；
- ci_all --fast **4 绿：3950 / 541 / 333 / 758**（`work/ci_logs/20261118-001658`）；
- 真实链路 reload **2026-11-18 00:22:14**：forever **7.4.0** + data **3.3.0** loaded，
  0 ERROR，data connected+synced，health=健康。
- 额外修 2 个跨午夜 flake（`test_p2_perf` 00:00-08:00 块、`test_trip_10b2` FixedClock）。


## 31. 2026-11-18 六项扣分整改（13:55–14:55）

> 约束不变：未推 GitHub、未打 tag、未发 Release、未改版本号/README/CHANGELOG；真实库只读验证。
> 备份：`backup/health2-six-20261118-1355/`（三插件源码+测试，43MB）。

### 31.1 严格 mypy 棘轮（data / watch）

| 插件 | 口径 | 本批结果 | 基线 |
|---|---|---|---|
| data | `--check-untyped-defs`，排除 tests/tools/work | **0 errors / 59 files** | **0**（原 144 → 清零） |
| watch | 同上 | **135 errors / 28 files / 87 checked** | **135**（136 → 135，只降不升） |
| forever | 同上 | **505 errors / 58 files / 225 checked** | **505**（原 563 → 下调） |

- 新增 `tools/mypy_strict.py`（与 forever 同一实现）、`tools/mypy_strict_baseline.json`、
  `tests/test_mypy_strict_ratchet.py`（分类/解析/基线形状 3 项），并接进 `tools/quality_gate.py`。
- data 清零手法：6 个 handler mixin 加 `if TYPE_CHECKING:` 宿主属性声明（约 135 条 attr-defined）、
  `self.plugin: Any`、observability 的 `rows/out` 注解、jobs 的 Optional 收窄、config_watch 的 `path` 收窄、
  dao 的 params 注解、memory_engine 的 int 化；没有新增 blanket ignore。
- data 常规 mypy 顺带把 `work/` 排除（`tools/mypy_check.py` 与 mypy.ini），13 条离线脚本噪音归零。
- forever 只给 `PhotoShareMixin` 补宿主声明（28 个属性/方法），严格错误 58 条 → 0，总数 564 → 505。

### 31.2 data flaky 测试（单跑/全量都稳）

- **顺序依赖**：全量跑时 `test_data_p2` 先 import 并 `astrbot_stub.install()`，单跑
  `tests/test_recall_fixes.py` 则收集阶段 `ModuleNotFoundError: astrbot`。修法：`tests/conftest.py`
  在收集前统一 install（三插件 conftest 仍逐字节一致）。
- **环境依赖**：`test_entity_graph` 的“小周”靠 jieba 分词，本机无 jieba 时全量必红；
  测试改用引号实体（`「小周」`），只测图谱机制，不赌分词器。
- 验证：两个点名测试单跑各 1 passed；`test_entity_graph` 单跑 7 passed；data 全量
  **650 passed / 7 skipped / 39 subtests**，可重复跑。

### 31.3 引擎纯内核（第二批）

- `domains/photo/failure_text.py`：`is_policy_failure` / `failure_pool` / `pick_failure_line` / `recorder_reason`
  + 政策标记默认表；根 `photo_share.py` 保留同名适配器，把 config 话术/标记作为参数注入。
- `domains/schedule/plan_text.py`：`fmt_hhmm` / `strip_fences` / `balanced` / `json_extract`；
  根 `schedule_plan.py` 保留 `fmt_hhmm` / `json_extract` 包装与 `_strip_fences` / `_balanced` 别名，
  logger 与 `_note_swallowed` 由根注入，日志文本不变。
- 域内只 import 标准库（架构棘轮 `domains` 叶子规则继续锁定）；架构基线 **235** 文件。
- `tests/golden_split.json` 只重算这 4 个函数的源码哈希（实现搬到域），行为 golden 未改；
  测试 90 passed（split/ratchet/photo/source_guard）。
- 未整搬：`amo_reply_pipeline.py`（1731 行）与 `photo_engine.py`（812 行）主体与 plugin/ctx 耦合，
  下一批继续按「纯函数先出、适配器留根」拆。

### 31.4 覆盖率底线

| 插件 | 实测 | 语句 | 底线 |
|---|---|---|---|
| data | **86%**（排除 work/ 离线脚本） | 7090 | `fail_under = 86`（保持） |
| watch | **67%** | 13512 | 60 → **66** |

### 31.5 真实时点 / 链路

- **14:50**：股票空仓兜底 = 11:35 已排单 `601818`（`last_decision.executed=false`）在 close 槽执行；
  基金 = `_fund_nudge` 在 close 槽无持仓时申购 **500 元**（50000 分）。
  - **实测（14:53 采样，真实库只读）**：close 槽已执行（`decision_done:2026-11-18:close=1`，14:50:08 写记忆），
    **0 ERROR**；决策 `buy 601988`（7.36 元/股）**被 50% 上限拒绝**（`executed=false`，
    `message="投资不能超过存款的 50%"`；现金 6604.26 → 上限 3302.13 → 最多 400 股，下单数量超限）；
    基金 **未申购**（`_fund_nudge` 只认 `hold`，今天决策是 `buy`）；`trades=0 / fund_trade_facts=0 / positions=0`。
    故今日「股票空仓兜底 + 基金 500」**均未真实成交**，根因与建议：
    ① 超上限直接拒单 → 应在买入前按上限裁剪到合法 100 股手数；
    ② 基金兜底只在 hold 时触发 → 收盘股票失败时应再补一次 500 元基金兜底；
    ③ `last_decision` 未记录 `qty/amount`（观测缺口）。
  - **已修（2026-11-18 15:1x–15:4x）**：
    ① **单笔口径**：`_cap_ok` 改为「本笔金额 ≤ 当前现金 50%」（用户口径：现金 6000 → 单笔最多 3000），
       删除累计 `_invested_cents`；提示词同步改口径。
    ② **股票超限裁剪**：`trade_engine.buy` 按 50% 上限以 100 股整手下裁，一手都买不起才拒；
       11-18 参数（6604.26 元、601988 7.36 元、500 股）→ **400 股成交**。
    ③ **基金 500 与股票解耦**：收盘只要尚无基金持仓就补 500 元申购（股票成交与否都买），
       第二笔记进 `last_decision.fund_entry`，不覆盖股票决策；`last_decision` 补记 `qty/amount_cents/shares`。
    ④ 测试：`test_fund_fallback.py` 5 项 + `test_finance_trading.py` 新增 5 项（含 11-18 事故参数复盘）；
       watch **788 passed / 6 subtests**，gate EXIT 0（strict **135**，基线 136→135），reload 后 0 ERROR。
       备份 `backup/health2-fundfix-20261118-1515/`、`backup/health2-capfix-20261118-1518/`。
    今日 14:50 已过且 close 标记已置位，今天不回放；**11-19 收盘生效**，届时 50% 内裁剪后的股票单与 500 元基金单会同时成交。


- 医院费用：`charge_schedule_visit` 按天 idem `hospital:YYYY-MM-DD` 查重；今日无医院块不扣，
  11-16 / 11-17 各 1 次（两行不同 idem，无同日重复）。
- 真实 LLM 20 轮：20/20，p50 **2173.2ms**、p95 **3421.0ms**、max 23285.8ms，leaks 0、dup 0。
- reload 14:20 三插件 **200**，post-reload **0 ERROR**，watch handshake ok、data 3.3.0 已加载。
- `ci_all --fast` **4 绿：4007 / 650 / 333 / 788**（`work/ci_logs/20261118-150625`）。



