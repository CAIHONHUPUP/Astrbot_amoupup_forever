# 四插件 Legacy 盘点与清理清单（task-38）

- 日期：2026-11-17
- 范围：Astrbot_amoupup_data / Astrbot_amoupup_forever / Astrbot_amoupup_photo / Astrbot_amoupup_watch
- 数据根：D:/dafeiyu/astrbot/data
- 方式：只读盘点（OPS 文件工具 + 校验和），**未删除任何文件、未改生产代码、未推送**
- 配套脚本：D:/dafeiyu/work/legacy_cleanup.py（默认 dry-run）

## 0. 结论（先看这里）

1. 旧插件代码目录 astrbot_plugin_omnidraw **已不在 plugins/**，只剩旧数据目录、旧配置、旧配置备份和代码里的 legacy 回退分支。
2. 旧数据目录 plugin_data/astrbot_plugin_omnidraw 仍在，共 6 个文件 4.4 MiB；其中 3 张三视图与新目录 **sha256 完全一致**，配置/穿搭/统计是旧版本。
3. 四插件内部 **没有 .bak-* 文件**；唯一 .bak-* 在非四插件的 petlink。旧配置备份集中在 data/config/。
4. 旧配置 astrbot_plugin_omnidraw_config.json 及其 .bak 含 **3 组明文 API key**，属合规/回滚保留项（C 类），必须先轮换密钥再人工处理。
5. **AstrBot 当前正在运行（astrbot.exe），amo_data.db-wal 非空（4,152 B）**；现在执行任何清理都不安全，必须先停服并备份。
6. 四插件当前**不是 git 仓库**，删错不能 git revert；本方案用“隔离区 + manifest + --restore”做回滚，7 天后再 purge。
7. 旧回退分支仍有读取旧目录的能力，但旧目录删除后它们只是死代码；**本轮不删代码**，等旧数据清理并观察 30 天后再单独做生产改动。

分类口径：

- **A 可安全删**：无读取方 / 仅历史 / 可重建；先移入隔离区。
- **B 需数据验证**：旧库、旧 sidecar、旧客户端可能仍用；验证通过后才可移入隔离区。
- **C 必须保留**：迁移、回滚、合规、含密钥、当前 live 数据；脚本永不自动删除。

优先级：

- **P0 阻断/保留**：停服、备份、密钥轮换、C 类保留。
- **P1 低风险清理**：A 类（含 work/ 需单独开关）。
- **P2 数据验证后清理**：B 类。

## 1. 盘点方法与环境

- 四插件版本：data 3.3.0、forever 7.4.0、photo 3.5.0、watch 1.1.0。
- 当前目录名与 metadata.name 一致（见各插件 metadata.yaml）。
- 盘点项：旧名、旧路径、legacy fallback 分支、.bak-*、work/、tmp/、旧配置、旧备份、旧 DB。
- 校验：旧三视图与新三视图逐文件 sha256；旧/新 persist config 的 provider id/base_url/model 结构对比（不读取/不输出密钥值）。
- 盘点快照时间约 2026-11-17 17:35（Asia/Shanghai）；active 任务可能继续往 work/ 写文件，数量以脚本当次输出为准。

当前环境事实（脚本 --verify 实测）：

~~~text
PASS  旧插件代码目录已移除
PASS  模板 front/side/full 新旧 sha256 一致
PASS  三个 live DB 存在（amo_data / amo_forever / social）
PASS  四插件当前配置存在
FAIL  amo_data.db-wal 已收敛(0B)   4152 bytes
FAIL  AstrBot 未运行               astrbot.exe
PASS  旧数据目录仍在（6 files / 4.4MiB）
PASS  旧配置仍在（api_keys blocks=3）
== 13/15 passed ==
~~~

两个 FAIL 是“现在不能清理”的证据，不是脚本错误。

## 2. 事实清单

### 2.1 旧名总表

命名契约 tests/test_naming_contract.py 中登记的历史目录名共 7 个：

| 插件 | 当前目录名 | 历史目录名 |
|---|---|---|
| photo | Astrbot_amoupup_photo | astrbot_plugin_omnidraw |
| forever | Astrbot_amoupup_forever | astrbot_plugin_Astrbot_amoupup_forever、astrbot_plugin_Astrbot_Amo_forever、astrbot_plugin_amo_forever |
| data | Astrbot_amoupup_data | Astr_Amo_data、astrbot_plugin_amo_data、AstrAmoData |
| watch | Astrbot_amoupup_watch | （契约未登记独立旧名） |

另外 amo_db.py 还会迁移两个旧数据目录：plugin_data/amo_forever、plugin_data/astrbot_plugin_amo_forever。当前磁盘上这两个目录都不存在。

### 2.2 旧路径 plugin_data/astrbot_plugin_omnidraw（6 文件，4,617,988 B / 4.40 MiB）

| 文件 | 字节 | 本地时间 | sha256 前 12 位 | 说明 |
|---|---:|---|---|---|
| amo_templates/amo_persona_front.png | 1,705,160 | 2026-11-11 13:28 | 63cee6aeb37c | 与新目录同 sha256 |
| amo_templates/amo_persona_side.png | 1,530,321 | 2026-11-11 13:28 | f61bcbdf2b0a | 与新目录同 sha256 |
| amo_templates/amo_persona_full.png | 1,376,865 | 2026-11-11 13:28 | a021568be371 | 与新目录同 sha256 |
| omnidraw_persist_config.json | 4,937 | 2026-11-17 09:07 | 47ff5cd593e8 | 旧运行配置，含 3 组 api_keys |
| amo_outfit.json | 314 | 2026-11-17 13:43 | 3458c3b143e7 | 旧今日穿搭 sidecar，已被新目录 17:05 版取代 |
| omnidraw_usage_stats.json | 391 | 2026-11-16 21:39 | ccc67a9f4215 | 旧日限额统计，无读取方 |

新目录 plugin_data/Astrbot_amoupup_photo 对照：

- amo_templates 三张图 sha256 与旧目录**逐张一致**；
- omnidraw_persist_config.json 5,300 B（比旧版多 363 B，provider id/base_url/model 结构一致）；
- amo_outfit.json 287 B，修改时间 2026-11-17 17:05（旧版 13:43，已过期）；
- omnidraw_usage_stats.json 391 B，已更新到新日期。

结论：三视图是“完全重复”；persist config 是“旧版 + 新字段”；outfit 是“旧快照”；usage stats 是“旧统计”。

### 2.3 旧配置（data/config，5 个文件，24,202 B）

| 文件 | 字节 | 行数 | 密钥 | 判定 |
|---|---:|---:|---|---|
| astrbot_plugin_omnidraw_config.json | 4,211 | 119 | 3 组 api_keys | C：旧插件仍在用的配置名；含明文密钥 |
| astrbot_plugin_omnidraw_config.json.bak-20261115-1900 | 3,802 | 104 | 3 组 api_keys | C：回滚/合规 |
| Astr_Amo_data_config.json.bak-upgrade-20261110-1520 | 715 | - | 无 | B：旧名配置备份，当前配置已覆盖 |
| Astrbot_Amo_forever_config.json.bak-4.0.0 | 6,335 | - | 无 | B：旧版本配置备份 |
| Astrbot_Amo_forever_config.json.bak-upgrade-20261110-1520 | 9,139 | - | 无 | B：升级前备份 |

旧配置与新 photo 配置的 provider 结构一致：node_25、node_26、deepseek_opt、amo；base_url 为 aiapi.syuan.org / api.syuan.org / api.deepseek.com。旧配置不是“缺少配置”，而是“旧名字 + 旧快照”；删除前重点是密钥轮换与备份，而不是功能。

### 2.4 .bak-* 盘点

- 四插件内部：**0 个**（glob **/*.bak* 只命中非四插件）。
- 非四插件：astrbot_plugin_dafeiyu_petlink/main.py.bak-194928（11,790 B）。
- data/config：14 个 .bak-*/.before-*（含 2.3 的 5 个；其余 9 个属于 group_chat_plus、livingmemory、memory_companion、private_companion、proactive_chat，不在本次四插件范围）。
- 旧插件代码备份：未发现 plugins/astrbot_plugin_omnidraw；D:/dafeiyu/backup 是当前代码快照（见 2.9）。

### 2.5 work/ 与 tmp/（122 文件，4,840,842 B / 4.62 MiB）

| 目录 | 文件数 | 字节 | 主要内容 | 引用情况 |
|---|---:|---:|---|---|
| Astrbot_amoupup_data/work | 29 | 1,109,787 | 记忆召回实验脚本；report_final.json 494,002 B、report_fixA2b.json 439,189 B、samples.txt 40,903 B | 生产/测试未 import；仅历史实验 |
| Astrbot_amoupup_forever/work | 90 | 3,706,331 | immersion_check 53 文件 2,945,293 B（imm_dataset 1,215,008 B、real_seed 633,825 B、imm_results 583,951 B）；__pycache__ 13 文件 425,756 B | 生产/测试未 import；RESTORE_CHECKLIST 明确不打包 work/immersion_check |
| Astrbot_amoupup_forever/tmp | 3 | 24,724 | _golden.json、_report.json、t1_baseline.log | RESTORE_CHECKLIST 明确不打包 tmp/ |

说明：

- work/ 不是“旧名”产物，但属于本次 legacy work/tmp 清扫范围；因为今天的 task-37/task-39 可能还要用，脚本默认**不选 work/**，需要 --include-work。
- work/ 下没有旧插件名引用；naming contract 只扫 .py 文件，work 脚本不含旧名。
- 删除 work/ 不影响 AstrBot 运行，只影响历史实验复现；建议在 task-37/39 收口后再清。

### 2.6 插件内 data/ 与 plugins/data（测试/兜底产物）

| 路径 | 文件数 | 字节 | 内容 |
|---|---:|---:|---|
| Astrbot_amoupup_data/data | 4 | 38,939 | cmd_config.json + t2i_templates |
| Astrbot_amoupup_forever/data | 7 | 170,011 | cmd_config.json + t2i_templates + 98,304 B 的 amo_forever.db |
| Astrbot_amoupup_photo/data | 4 | 38,942 | cmd_config.json + t2i_templates |
| Astrbot_amoupup_watch/data | 4 | 38,942 | cmd_config.json + t2i_templates |
| plugins/data（四插件根） | 4 | 38,942 | 同一组 cmd_config + t2i_templates |

这些是插件目录内的本地 data/，不是 AstrBot 的 data/；疑似测试或“data_dir 拿不到”时的兜底产物。forever 的那份还带一个小 DB，不能按纯缓存处理，归 B 类。

### 2.7 旧 DB 备份（plugin_data/Astrbot_amoupup_data/backups）

| 文件 | 字节 | 日期 |
|---|---:|---|
| amo_data-20261109-135326.db-shm | 32,768 | 2026-11-09（无对应 .db，孤立） |
| amo_data-20261109-135326.db-wal | 0 | 2026-11-09（无对应 .db，孤立） |
| amo_data-20261111-084432.db | 3,960,832 | 2026-11-11 |
| amo_data-20261112-091552.db | 3,964,928 | 2026-11-12 |
| amo_data-20261113-082745.db | 4,329,472 | 2026-11-13 |
| amo_data-20261114-002553.db | 4,329,472 | 2026-11-14 |
| amo_data-20261115-010934.db | 4,349,952 | 2026-11-15 |
| amo_data-20261116-002926.db | 4,665,344 | 2026-11-16 |
| amo_data-20261117-003853.db | 4,886,528 | 2026-11-17 |

- 7 个完整 .db 共 30,486,528 B；插件策略“保留最近 7 份”，当前正好 7 份，**没有到期可删项**。
- 20261109 只有 shm/wal 没有 .db，是孤立文件，A 类。

### 2.8 legacy fallback 分支（代码级，C 类）

| 文件:行 | 作用 | 备注 |
|---|---|---|
| forever/amo_image_common.py:59,68 | OMNI_DATA_DIR_NAME_LEGACY / OMNI_PLUGIN_NAME_LEGACY | 旧 data_dir 与旧插件名常量 |
| forever/amo_image_common.py:329-338 | template_dirs 新目录在前、旧 omnidraw 只读兜底 | 旧目录删除后返回不存在，不会报错 |
| forever/photo_engine.py:582-616 | _migrate_template_config 启动迁移三视图路径 | 只写新配置，不写旧目录 |
| forever/wardrobe_seed.py:46-59 | 今日穿搭 sidecar 新名优先、旧名回退 | 新目录存在时写新目录 |
| forever/amo_config.py:104-140 | _migrate_legacy_config 合并 amo_forever_config.json / astrbot_plugin_amo_forever_config.json | 当前磁盘无这两个文件 |
| forever/amo_db.py:383-405 | 旧 DB 路径迁移（amo_forever / astrbot_plugin_amo_forever） | 当前磁盘无旧 DB |
| forever/amo_api.py:104-107 | 注册 Web API 时清理旧前缀路由 | 只影响路由别名 |
| forever/main.py:423,583 | 旧生图插件名提示 + 旧数据插件名回退 | 握手失败时打 legacy fallback 提示 |
| forever/memory_hooks.py:281 | 旧数据插件名回退 | Astr_Amo_data 等 |
| watch/core/amo_bridge.py:66 | 旧数据插件名回退 | Astr_Amo_data |
| watch/services/nightly_memory.py:40 | 旧数据插件名回退 | Astr_Amo_data |

相关测试（C 类，保留）：

- 四插件 tests/test_naming_contract.py（旧名白名单/禁止 import 旧目录）；
- forever/tests/test_b2b_outfit_lock.py（造旧 plugin_data/astrbot_plugin_omnidraw）；
- forever/tests/test_wardrobe.py（旧 sidecar 回退）；
- forever/tests/test_photo_dedupe.py、test_photo_engine.py（旧 sibling 目录探测）；
- data/tests/test_cross_platform.py（包名注释）。

### 2.9 其他旧备份/旧入口

| 项 | 规模 | 判定 |
|---|---:|---|
| D:/dafeiyu/backup（代码快照，含旧版本） | 139,799 文件 / 9,696,192,067 B（约 9.03 GiB） | C：回滚用；不属清理范围 |
| D:/dafeiyu/旧版/开机自启-...lnk | 1,529 B | 非插件，忽略 |
| D:/dafeiyu/plugins-removed/data_v4.db.bak | 991,232 B | 旧核心 DB 备份，B/C，另案处理 |
| data/plugins.json 市场缓存 | 约 3.7 MB | 含 astrbot_plugin_omnidraw 市场条目，但这是可重建缓存，不删、不改 |
| data/cmd_config.json.bak-* | 3 个 | 核心配置备份，C，另案处理 |

## 3. 分类清单（A/B/C）

### 3.1 A 类：可安全删（P1，移入隔离区）

| ID | 路径 | 规模 | 依据 | 前置 |
|---|---|---:|---|---|
| A-01 | 四插件 **/__pycache__、.pytest_cache、.mypy_cache；plugins/.mypy_cache | 42 目录 / 1,010 文件 / 94,175,635 B（89.8 MiB，其中 4 个 .mypy_cache 约 73.9 MiB） | 纯缓存，可重建 | 停服；可随时重建 |
| A-02 | forever/tmp/ | 3 文件 / 24,724 B | 临时 golden/report/log | 无 |
| A-03 | data/work/、forever/work/ | 119 文件 / 4,816,118 B | 历史实验；生产/测试不引用 | **--include-work**；task-37/39 收口后 |
| A-04 | plugins/data/ | 4 文件 / 38,942 B | 插件目录内 data 残留副本 | 确认 AstrBot 不读取 |
| A-05 | petlink/main.py.bak-194928 | 1 文件 / 11,790 B | 非四插件旧代码备份 | --include-other |
| A-06 | legacy/omnidraw_usage_stats.json | 1 文件 / 391 B | 旧统计，无读取方 | 无 |
| A-07 | backups/amo_data-20261109-135326.db-shm/.db-wal | 2 文件 / 32,768 B | 无对应 .db 的孤立文件 | 无 |

A 类合计：1,140 文件 / 99,100,368 B（94.5 MiB）；默认（不含 work、petlink）1,020 文件 / 94,272,460 B（89.9 MiB）。

### 3.2 B 类：需数据验证（P2）

| ID | 路径 | 规模 | 验证条件 |
|---|---|---:|---|
| B-01 | legacy/amo_templates/ | 3 文件 / 4,612,346 B | 新目录 3/3 sha256 一致（脚本 guard 强校验） |
| B-02 | legacy/amo_outfit.json | 1 文件 / 314 B | 新目录 amo_outfit.json 存在且更新时间更新 |
| B-03 | 四插件 data/（本地 data） | 4 目录 / 19 文件 / 286,834 B | live DB 在 plugin_data 且插件启动后不读本地 data |
| B-04 | config 的三个旧名配置备份 | 3 文件 / 16,189 B | 当前四插件配置存在，且对比无独有配置项 |
| B-05 | data 插件 backups 中超 7 份的 .db | 当前 0 个（7 份正好保留线） | 最新备份可恢复、PRAGMA 校验通过 |

B 类合计（不含 B-05）：9 目标 / 26 文件 / 4,915,683 B（4.69 MiB）。

### 3.3 C 类：必须保留（P0，脚本永不自动删）

| ID | 路径 | 规模 | 原因 |
|---|---|---:|---|
| C-01 | legacy/omnidraw_persist_config.json | 4,937 B | 含 3 组明文 API key；合规/轮换前保留 |
| C-02 | config/astrbot_plugin_omnidraw_config.json + .bak-20261115-1900 | 8,013 B | 含密钥；回滚/合规 |
| C-03 | legacy fallback 代码 10 个文件 | 333,860 B | 迁移/回读旧数据；本轮不改生产代码 |
| C-04 | 相关测试 8 个文件 | 77,874 B | 命名契约与旧路径回退测试 |
| C-05 | D:/dafeiyu/backup 代码快照 | 139,799 文件 / 9.03 GiB | 回滚用 |
| C-06 | 当前四插件配置、live DB、RESTORE_CHECKLIST/CHANGELOG 等文档 | - | 当前生产/迁移/恢复依据 |

注意：C-01/C-02 的密钥与 C-05 的备份不要放进任何普通压缩包；如必须归档，先加密并轮换密钥。

## 4. 清理脚本

路径：D:/dafeiyu/work/legacy_cleanup.py

默认行为：dry-run，只打印，不移动文件。--apply 也只是移动到 data/_legacy_cleanup/&lt;时间戳&gt;/quarantine/，并写 manifest（每个文件的路径/大小/sha256），不是删除。

~~~bat
REM 先看清单
D:/dafeiyu/uv/python/cpython-3.12.16-windows-x86_64-none/python.exe -B D:/dafeiyu/work/legacy_cleanup.py

REM 删除前/后数据校验
D:/dafeiyu/uv/python/cpython-3.12.16-windows-x86_64-none/python.exe -B D:/dafeiyu/work/legacy_cleanup.py --verify

REM 只清 A 类（不含 work）
D:/dafeiyu/uv/python/cpython-3.12.16-windows-x86_64-none/python.exe -B D:/dafeiyu/work/legacy_cleanup.py --apply --yes

REM 再清 work/（task-37/39 收口后）
D:/dafeiyu/uv/python/cpython-3.12.16-windows-x86_64-none/python.exe -B D:/dafeiyu/work/legacy_cleanup.py --apply --include-work --yes

REM 数据验证通过后清 B 类
D:/dafeiyu/uv/python/cpython-3.12.16-windows-x86_64-none/python.exe -B D:/dafeiyu/work/legacy_cleanup.py --apply --include-b --yes

REM 回滚
D:/dafeiyu/uv/python/cpython-3.12.16-windows-x86_64-none/python.exe -B D:/dafeiyu/work/legacy_cleanup.py --restore 20261117-180000

REM 7 天后确认无影响再清隔离区
D:/dafeiyu/uv/python/cpython-3.12.16-windows-x86_64-none/python.exe -B D:/dafeiyu/work/legacy_cleanup.py --purge --yes
~~~

脚本安全设计：

- AstrBot 运行时拒绝 --apply（需 --skip-process-check 才绕过；不建议）。
- 只处理 data 根内路径；拒绝符号链接/重解析点；C 类永不选中。
- B 类必须 --include-b，且模板要逐文件 sha256 一致；本地 data 要新 plugin_data 存在；旧配置要当前配置存在。
- 每次 apply 写 manifest；--restore 按 manifest 移回；--purge 只清隔离区且默认要求 7 天。
- 不修改任何源码、配置内容或数据库，只移动文件。

脚本 dry-run 快照：81 个候选目标，其中 A 类 50、B 类 9、C 类 22。

## 5. 删除后测试方案

### 5.1 P0：前置（不满足不许执行）

1. 停 AstrBot（确认 tasklist 无 astrbot.exe）与 SnowLuma；确认 amo_data.db-wal 收敛为 0。
2. 按 docs/RESTORE_CHECKLIST.md 打 core 档备份：data/config（排除 .bak）、四插件代码、四插件 plugin_data、参考图；校验 manifest/sha256。现有 D:/dafeiyu/backup 是 9 GiB 代码快照，不等同于四插件最新 core 备份。
3. 运行 --verify，记录 15/15（停服后 WAL 应为 0）。
4. 轮换旧配置中的 3 组 API key（旧配置在轮换前不得删除/外发）。
5. 通知 lead：task-37/39 完成前不执行 --include-work。

### 5.2 P1：A 类清理后（低风险回归）

~~~bat
REM 统一 CI（按现有文档）
D:/dafeiyu/uv/python/cpython-3.12.16-windows-x86_64-none/python.exe D:/dafeiyu/work/ci_all.py

REM 每个插件 gate（在各插件目录执行）
D:/dafeiyu/uv/python/cpython-3.12.16-windows-x86_64-none/python.exe tools/quality_gate.py
D:/dafeiyu/uv/python/cpython-3.12.16-windows-x86_64-none/python.exe tools/mypy_check.py

REM 命名契约 + 旧路径回退测试
D:/dafeiyu/uv/python/cpython-3.12.16-windows-x86_64-none/python.exe -m pytest -q tests/test_naming_contract.py
D:/dafeiyu/uv/python/cpython-3.12.16-windows-x86_64-none/python.exe -m pytest -q tests/test_wardrobe.py tests/test_b2b_outfit_lock.py
~~~

通过标准：四插件测试全绿、四 gate 全绿、mypy 不高于基线、命名契约不出现旧名回调。

### 5.3 P2：B 类清理后（数据/功能回归）

1. 重新启动 AstrBot，日志确认四插件 loaded，出现“已握手「我能永远定格你」”，无 traceback、无 legacy fallback warning。
2. WebUI 6185 登录；检查 /status、/memories、/wardrobe、/schedule 正常。
3. 真实自拍 1 次：确认参考图 4 张（姿势 + 三视图 + 房间 + 穿搭），耳朵/房间/衣服正确；三视图来自新目录。
4. 换一次穿搭，确认 plugin_data/Astrbot_amoupup_photo/amo_outfit.json mtime 更新，旧目录不重建。
5. 连续观察 24 小时：生图、记忆召回、日程、watch 社交定时器；日志无 “astrbot_plugin_omnidraw” 文件访问错误。
6. SQLite 校验（三个 live DB）：

~~~text
PRAGMA integrity_check;   -> ok
PRAGMA user_version;      -> 现有版本
~~~

7. 运行 --verify：旧数据目录已清理；新三视图/live DB/当前配置存在；WAL 为 0。
8. 再跑 5.2 的全量 CI。

### 5.4 回滚方案

- 任一测试失败：先 --restore &lt;时间戳&gt;，再启动 AstrBot；必要时用 core 备份恢复 DB/配置。
- 隔离区默认保留 7 天；确认稳定后再 --purge，删除后不可恢复。
- 如果旧目录被 fallback 重建：检查 photo 插件 data_dir/模板配置；wardrobe 只在新目录不存在时才回退写旧目录。

### 5.5 验收标准

- 四插件 ci_all 全绿、四 gate 全绿、mypy 不高于基线；
- --verify 全 PASS；
- 真实自拍 4 张参考图正确；
- 24 小时无 legacy 相关报错；
- 旧目录未重建；
- 旧配置密钥已轮换（人工签字）。

## 6. 风险与待确认

1. **AstrBot 正在运行**：当前 WAL 4,152 B；任何 --apply 都会先被脚本拒绝，这是预期保护。
2. **四插件不是 git 仓库**：没有 git revert；回滚完全依赖隔离区和 core 备份。建议先做 P0 git init + 首次 commit（另一任务），再清理。
3. **work/ 可能仍被今天任务使用**：task-37、task-39 还在进行；--include-work 必须等它们收口。
4. **旧配置是否唯一密钥副本**：新旧 provider 结构一致，但密钥值未比对（不输出）；删除旧配置前必须确认新配置可用并轮换。
5. **D:/dafeiyu/backup 9.03 GiB**：包含大量历史快照，不是“旧插件备份”，不要当垃圾清；是否瘦身另案。
6. **plugins.json 市场缓存**：含旧插件市场条目，是可重建缓存，不是安装状态；不要手改。
7. **旧回退代码删除**：旧数据清理并观察 30 天后，再单独提生产改动；本轮不动源码。

## 附录 A：旧数据目录文件级清单（6）

- amo_outfit.json  (314 B)
- amo_templates/amo_persona_front.png  (1705160 B)
- amo_templates/amo_persona_full.png  (1376865 B)
- amo_templates/amo_persona_side.png  (1530321 B)
- omnidraw_persist_config.json  (4937 B)
- omnidraw_usage_stats.json  (391 B)

## 附录 B：Astrbot_amoupup_data/work（29）

- work/REPORT.md  (10567 B)
- work/__pycache__/recall_common.cpython-312.pyc  (9080 B)
- work/__pycache__/sim_lib.cpython-312.pyc  (9398 B)
- work/analyze.py  (2366 B)
- work/analyze2.py  (2361 B)
- work/corpus_real.json  (13083 B)
- work/dbg_embed.py  (1878 B)
- work/dbg_fail.py  (3331 B)
- work/diff_arms.py  (1150 B)
- work/dump2.py  (1513 B)
- work/extract_corpus.py  (1551 B)
- work/golden_diff.py  (1524 B)
- work/make_report.py  (4456 B)
- work/more.txt  (25357 B)
- work/probe_db.py  (2118 B)
- work/probe_jaccard.py  (851 B)
- work/probe_terms.py  (891 B)
- work/recall_common.py  (5915 B)
- work/recall_eval.py  (17328 B)
- work/report_final.json  (494002 B)
- work/report_fixA2b.json  (439189 B)
- work/samples.txt  (40903 B)
- work/sim_lib.py  (5732 B)
- work/simulate_24h.py  (967 B)
- work/simulate_day.py  (981 B)
- work/simulate_weeks.py  (995 B)
- work/smoke.py  (1515 B)
- work/snapshot.py  (1828 B)
- work/tables.md  (8957 B)

## 附录 C：Astrbot_amoupup_forever/work（90）

- work/__pycache__/clean_schedule.cpython-312.pyc  (67763 B)
- work/__pycache__/diary_audit.cpython-312.pyc  (14343 B)
- work/__pycache__/diary_audit_apply.cpython-312.pyc  (24397 B)
- work/__pycache__/preview_day.cpython-312.pyc  (16493 B)
- work/__pycache__/reverse_checks.cpython-312.pyc  (18720 B)
- work/__pycache__/schedule_7day.cpython-312.pyc  (6989 B)
- work/__pycache__/schedule_drift.cpython-312.pyc  (11271 B)
- work/__pycache__/sim_lib.cpython-312.pyc  (16481 B)
- work/__pycache__/sim_weeks_env.cpython-312.pyc  (27697 B)
- work/__pycache__/simulate_24h.cpython-312.pyc  (64413 B)
- work/__pycache__/simulate_day.cpython-312.pyc  (32655 B)
- work/__pycache__/simulate_weeks.cpython-312.pyc  (103426 B)
- work/__pycache__/transit_consistency_repair.cpython-312.pyc  (21108 B)
- work/capture_prompt_golden.py  (946 B)
- work/clean_schedule.py  (48305 B)
- work/diary_audit.py  (8147 B)
- work/diary_audit_apply.py  (14355 B)
- work/immersion_check/__pycache__/build_dataset.cpython-312.pyc  (27158 B)
- work/immersion_check/__pycache__/imm_harness.cpython-312.pyc  (20866 B)
- work/immersion_check/__pycache__/run_imm.cpython-312.pyc  (43847 B)
- work/immersion_check/__pycache__/synthetic_40.cpython-312.pyc  (15947 B)
- work/immersion_check/backup/IMM_REPORT_20261117-154906.md  (15092 B)
- work/immersion_check/backup/IMM_REPORT_20261117-155113.md  (12181 B)
- work/immersion_check/backup/IMM_REPORT_20261117-155150.md  (12181 B)
- work/immersion_check/backup/IMM_REPORT_20261117-155404.md  (14990 B)
- work/immersion_check/backup/IMM_REPORT_20261117-155445.md  (14664 B)
- work/immersion_check/backup/IMM_REPORT_20261117-155734.md  (15017 B)
- work/immersion_check/backup/IMM_REPORT_20261117-155806.md  (17898 B)
- work/immersion_check/backup/IMM_REPORT_20261117-161409.md  (10166 B)
- work/immersion_check/backup/IMM_REPORT_20261117-161505.md  (10142 B)
- work/immersion_check/backup/IMM_REPORT_20261117-161956.md  (10156 B)
- work/immersion_check/backup/IMM_REPORT_20261117-162221.md  (10157 B)
- work/immersion_check/backup/IMM_REPORT_20261117-162552.md  (10212 B)
- work/immersion_check/backup/IMM_REPORT_20261117-164916.md  (10212 B)
- work/immersion_check/backup/IMM_REPORT_20261117-171234.md  (10213 B)
- work/immersion_check/backup/imm_results_20261117-154906.json  (10814 B)
- work/immersion_check/backup/imm_results_20261117-155113.json  (8224 B)
- work/immersion_check/backup/imm_results_20261117-155150.json  (8224 B)
- work/immersion_check/backup/imm_results_20261117-155404.json  (11645 B)
- work/immersion_check/backup/imm_results_20261117-155445.json  (11357 B)
- work/immersion_check/backup/imm_results_20261117-155734.json  (11357 B)
- work/immersion_check/backup/imm_results_20261117-155806.json  (11357 B)
- work/immersion_check/backup/imm_results_20261117-161409.json  (4883 B)
- work/immersion_check/backup/imm_results_20261117-161505.json  (4883 B)
- work/immersion_check/backup/imm_results_20261117-161956.json  (4883 B)
- work/immersion_check/backup/imm_results_20261117-162221.json  (4884 B)
- work/immersion_check/backup/imm_results_20261117-162552.json  (4882 B)
- work/immersion_check/backup/imm_results_20261117-164916.json  (4883 B)
- work/immersion_check/backup/imm_results_20261117-171234.json  (4884 B)
- work/immersion_check/build_dataset.py  (20850 B)
- work/immersion_check/cfg_probe.py  (842 B)
- work/immersion_check/collect_real.py  (3497 B)
- work/immersion_check/db_probe.py  (1351 B)
- work/immersion_check/diag_probe.py  (1971 B)
- work/immersion_check/imm_dataset.json  (1215008 B)
- work/immersion_check/imm_harness.py  (12776 B)
- work/immersion_check/imm_results.json  (583951 B)
- work/immersion_check/inspect2.py  (1120 B)
- work/immersion_check/inspect_results.py  (686 B)
- work/immersion_check/kv_probe.py  (614 B)
- work/immersion_check/names_probe.py  (688 B)
- work/immersion_check/real_llm_probe.py  (7388 B)
- work/immersion_check/real_llm_results.json  (3296 B)
- work/immersion_check/real_probe.py  (1127 B)
- work/immersion_check/real_sample.json  (18142 B)
- work/immersion_check/real_seed.json  (633825 B)
- work/immersion_check/run_imm.py  (32136 B)
- work/immersion_check/smoke.py  (1522 B)
- work/immersion_check/synthetic_40.py  (15873 B)
- work/immersion_check/tail_msgs.py  (371 B)
- work/ledger_cleanup_apply.py  (13386 B)
- work/ledger_cleanup_plan.py  (9118 B)
- work/panel_smoke.js  (3042 B)
- work/panel_split.py  (12357 B)
- work/persona_consistency.py  (2012 B)
- work/preview_day.py  (9209 B)
- work/probe_diary_bg.py  (3484 B)
- work/reverse_checks.py  (15099 B)
- work/schedule_7day.py  (4317 B)
- work/schedule_drift.py  (6772 B)
- work/sim_lib.py  (9318 B)
- work/sim_weeks_env.py  (16092 B)
- work/simulate_24h.py  (39984 B)
- work/simulate_day.py  (20191 B)
- work/simulate_weeks.py  (68362 B)
- work/structure_audit.py  (4057 B)
- work/test_sim_lib.py  (2929 B)
- work/tone_ab.py  (5435 B)
- work/transit_consistency_repair.py  (14961 B)
- work/wallet_audit_today.py  (3404 B)

## 附录 D：Astrbot_amoupup_forever/tmp（3）

- tmp/_golden.json  (18987 B)
- tmp/_report.json  (468 B)
- tmp/t1_baseline.log  (5269 B)

## 附录 E：data/config 的 14 个旧备份

- Astr_Amo_data_config.json.bak-upgrade-20261110-1520  (715 B)
- Astrbot_Amo_forever_config.json.bak-4.0.0  (6335 B)
- Astrbot_Amo_forever_config.json.bak-upgrade-20261110-1520  (9139 B)
- astrbot_plugin_group_chat_plus_config.json.bak-195921  (28983 B)
- astrbot_plugin_group_chat_plus_config.json.bak-idle-201247  (28981 B)
- astrbot_plugin_livingmemory_config.json.bak-194302  (4166 B)
- astrbot_plugin_memory_companion_config.json.before-budget  (10230 B)
- astrbot_plugin_memory_companion_config.json.before-debug  (10559 B)
- astrbot_plugin_memory_companion_config.json.before-toneoff  (10229 B)
- astrbot_plugin_omnidraw_config.json.bak-20261115-1900  (3802 B)
- astrbot_plugin_private_companion_config.json.bak-205658  (5393 B)
- astrbot_plugin_private_companion_config.json.bak-qq-205920  (5302 B)
- astrbot_plugin_proactive_chat_config.json.bak-before-schedule  (8179 B)
- astrbot_plugin_proactive_chat_config.json.bak-interval  (7834 B)

## 附录 F：data 插件 backups（9）

- backups/amo_data-20261109-135326.db-shm  (32768 B)
- backups/amo_data-20261109-135326.db-wal  (0 B)
- backups/amo_data-20261111-084432.db  (3960832 B)
- backups/amo_data-20261112-091552.db  (3964928 B)
- backups/amo_data-20261113-082745.db  (4329472 B)
- backups/amo_data-20261114-002553.db  (4329472 B)
- backups/amo_data-20261115-010934.db  (4349952 B)
- backups/amo_data-20261116-002926.db  (4665344 B)
- backups/amo_data-20261117-003853.db  (4886528 B)
