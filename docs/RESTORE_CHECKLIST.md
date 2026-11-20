# 大肥鱼换电脑恢复清单

> 适用：AstrBot 4.29.4 / Python 3.12 / Windows；四插件 `Astrbot_amoupup_forever`、`Astrbot_amoupup_data`、`Astrbot_amoupup_watch`、`Astrbot_amoupup_photo`；SnowLuma 仓库已关闭，运行时随包携带。
> 本清单只描述“备份/恢复”，不改生产代码；备份包含密钥，必须自行加密。

## 1. 备份分档

| 档 | 内容 | 估算大小 |
|---|---|---|
| `core` | AstrBot config/DB + 四插件代码/plugin_data + 参考图 + 恢复脚本 | ~250 MB |
| `full` | core + 其他全部 plugin_data（meme_manager、stickers、livingmemory、proactive_chat 等） | ~1.3 GB |
| `snowluma` | SnowLuma 运行目录（含 node.exe/index.mjs/config/data，排除 logs） | ~390 MB |

建议：`full + snowluma`，总包约 1.6–1.8 GB；只迁四插件可用 `core + snowluma`。

## 2. 必须打包

### AstrBot 核心
- `data/config/*.json`（四插件 + 相关插件配置；排除 `.bak-*` 旧配置）
- `data/cmd_config.json`（平台/模型/JWT/密码）
- `data/data_v4.db`（会话/平台状态；SQLite 热备）
- `data/plugins/Astrbot_amoupup_forever|data|watch|photo`（代码；排除 `__pycache__`、tests 临时产物）

### 四插件数据
- `data/plugin_data/Astrbot_amoupup_forever`：衣柜、`outfit_refs/`（白天/晚上/酒店/泳装/姿势库/家）、日记、照片缓存、`amo_forever.db`
- `data/plugin_data/Astrbot_amoupup_data`：`amo_data.db`（记忆/消息/摘要）、`backups/` 只留必要快照
- `data/plugin_data/Astrbot_amoupup_watch`：财务/社交 DB、events
- `data/plugin_data/Astrbot_amoupup_photo`：`omnidraw_persist_config.json`、`amo_templates/` 三视图、`persona_refs/`、`user_refs/`

### 其他插件数据（full 档）
- `meme_manager`、`astrbot_plugin_nekowhale_stickers`、`astrbot_plugin_livingmemory`、`astrbot_plugin_proactive_chat`、`astrbot_plugin_angel_memory`、`astrbot_plugin_dafeiyu_*` 等全部 `plugin_data`
- 插件代码 `data/plugins/*`（含非四插件；排除 `__pycache__`、`.pytest_cache`）

### SnowLuma（仓库已关，必须随包）
- `D:/dafeiyu/SnowLuma` 整个目录，**排除** `logs/`、`snowluma-run.log`
- 必含：`node.exe`、`index.mjs`、`client/`、`native/`、`launcher.bat`、`check-node-version.cjs`、`package.json`、`release.json`、`config/`、`data/`（QQ 登录态/聊天缓存）
- 新电脑恢复后仍可能需要重新扫码登录 QQ（登录态与设备绑定）

### 参考图（关键）
- `Astrbot_amoupup_forever/outfit_refs/白天搭配|晚上搭配|酒店睡衣|泳装|姿势库|家`
- 三视图：`Astrbot_amoupup_photo/amo_templates/amo_persona_front|side|full.png`
- 房间图：`家/home-rent-bedroom|living|bathroom|kitchen.png`
- 姿势库 9 张、泳装 7 套、酒店 8 套、白天/晚上按实际

### 工具与清单
- `work/portable_backup.py` / `portable_restore.py`（恢复时路径重写）
- `manifest.json`（相对路径/大小/sha256/mtime/版本）
- `VERSIONS.json`（AstrBot/四插件/SnowLuma/依赖版本）
- `RESTORE.md`（本清单的恢复步骤）

## 3. 不能打包，需新电脑 DSH 安装/配置

| 项 | 说明 |
|---|---|
| AstrBot 4.29.4 | 新电脑安装同版本，不能用不同主版本恢复 DB |
| Python 3.12 | AstrBot 运行环境；`pip install -r requirements-dev.txt` |
| Node.js | 面板 JS/雪露玛检查；SnowLuma 已带 `node.exe` |
| FFmpeg | 语音/视频消息 |
| Ollama + bge-m3 | 能记语义召回；未装则退化为关键词召回 |
| 飞书应用凭据 | `app_id/app_secret`、权限、机器人发布；不随包 |
| QQ/NapCat | 若用 NapCat，需重装并扫码；SnowLuma 随包但仍需登录 |
| API keys | LLM/生图中转/天气/TTS；在配置里，恢复后核对 |
| 端口/防火墙 | WebUI 6185、SnowLuma WebUI 端口 |
| 开机启动/快捷方式 | 新电脑手动建 `astrbot.exe run` 与 SnowLuma `launcher.bat` |
| 时区 | 固定 `Asia/Shanghai`，否则日程/日记时间错 |

## 4. 不要打包/不要恢复

- `data/logs/`、`logs/`、`snowluma-run.log`、`__pycache__`、`.pytest_cache`、`*.pyc`
- 旧插件 `astrbot_plugin_omnidraw` 目录/配置（只做 legacy fallback，不恢复）
- 旧 `.bak-*` 配置、旧备份 DB（除非明确要历史）
- `data/plugins.json` 市场缓存（3.7MB，可重建）
- `D:/dafeiyu/private/github-token.txt`、DSH 本地 token、浏览器缓存
- `work/immersion_check/`、`tmp/` 临时脚本（含作者路径会触发测试红）

## 5. 恢复步骤

1. 新电脑安装 AstrBot 4.29.4、Python 3.12、Node、FFmpeg、Ollama（按需）。
2. 停止 AstrBot/SnowLuma，避免复制中的 DB/WAL。
3. 解包到目标根（如 `E:/dafeiyu`），保留 `astrbot/`、`SnowLuma/` 结构。
4. 运行 `portable_restore.py --target E:/dafeiyu`：
   - 校验 manifest/sha256；
   - 改写 config JSON 与 SQLite `kv` 内 `D:/dafeiyu/...` → 新根；
   - 还原 `plugin_data`/`amo_templates`/`outfit_refs`；
   - 不覆盖目标已有文件，先备份。
5. SQLite：`PRAGMA integrity_check`、`user_version`（当前 event-id v5）；旧库启动自动迁移，迁移前会热备。
6. 启动 `astrbot.exe run`；日志确认四插件 loaded、`Astrbot_amoupup_photo 3.5.0`、`已握手我能永远定格你`。
7. 启动 SnowLuma `launcher.bat`；QQ 若掉线重新扫码。
8. 恢复后验证：
   - 面板 6185 可登录；
   - `/status`、`/memories`、`/wardrobe`、`/schedule` 正常；
   - 真实自拍：参考图 4 张（在家：姿势+三视图+房间+穿搭），耳朵/房间/衣服对；
   - 能记写一条记忆并召回；
   - 飞书/QQ 收发一条消息。
9. 跑四插件 `D:/dafeiyu/work/ci_all.py`（或 `--fast`，不在插件目录）+ 各插件 `tools/quality_gate.py`；全绿再长期使用。
10. 旧电脑保留包至少 7 天，确认新电脑稳定后再删。

## 6. 路径重写清单

- 配置：`photo_persona_ref`、`photo_persona_refs`、`photo_template_dir`、`photo_persona_ref` 旧路径；
- `omnidraw_persist_config.json` 的 `data_dir`/模板路径；
- SQLite `kv` 中包含 `D:/dafeiyu` 的日程/照片/模板路径；
- 旧 `astrbot_plugin_omnidraw` → 新 `Astrbot_amoupup_photo`（只读回退，不写新路径）；
- SnowLuma config 内绝对路径/端口（如有）。

## 7. 安全

- 包含 API key、飞书 secret、JWT secret、QQ 登录态：**必须加密**（7z AES/ VeraCrypt），不要上传网盘公开链接；
- 恢复后轮换一次飞书/生图/LLM key 更稳；
- 旧电脑删除明文包。

## 8. 当前状态备注

- 四插件：forever 7.4.0 / data / watch / photo 3.5.0；schema 263、routes 97；
- event-id `user_version=5`；旧库 v2 启动会迁移并热备；
- 旧 omnidraw 目录已移出 plugins，仅 backup 保留；
- README/CHANGELOG/测试数字有历史陈旧，以 `VERSIONS.json` 与代码为准。