# task-26：P0 日志密钥脱敏 + 后台话术统一拦截（2026-11-22）

> **只读声明**：未 reload、未 commit/push、未写生产库。生产日志清理**先备份**（logs/backup/），
> 报告与命令输出只含命中行数/sha256，**不打印任何密钥原文**。
> 依据：work/audit/cross_plugin.md §3.1（后台话术进聊天 8 条）、§5.3（密钥/隐私泄漏）、§6.2–6.3。

## 1. 统一日志脱敏（进程级 logging.Filter）

实现位置：observability.py（本模块原本就是三插件的 PII 脱敏层，不新增模块依赖，架构棘轮不涨边）。

| 能力 | 实现 |
|---|---|
| 规则 | _SECRET_PATTERNS：Authorization / x-api-key / Bearer / sk-… / api_key / token / secret / password；_OPEN_ID_PATTERNS：ou_… 与 open_id=…（可 redact_open_id=False 关闭） |
| 覆盖 record | record.msg、record.args（含 dict 的敏感 key 整值打码）、record.exc_text、异常 traceback（带密钥时折成脱敏文本，不丢栈） |
| 覆盖 logger | install_log_redaction() 给**当前所有 logger 的 logger 级 + handler 级**加 filter，并在 logging.Logger.addHandler 上挂钩子；之后 watch/data/photo 新建的 astrbot.plugin.<name> logger 自动接上。显式点名 astrbot / astrbot.api / astrbot.plugin / 四个插件 logger。 |
| 幂等 | install_log_redaction() 重复调用返回 False；redaction_installed() 可查 |
| 接线 | main.py HumanCorePlugin.__init__ 里 install_log_redaction()（在 _init_basic 之前），import 走已有的 observability 依赖边，不新增内部 import |

生产日志里 23 行 Bearer sk-… 的直接来源（provider traceback locals）也在这一层兜住：即使框架打印异常栈，record.exc_info 里的密钥会被折成 <redacted>。

## 2. 生产日志备份 + 一次性脱敏

工具：tools/redact_prod_logs.py（--dry-run 可只统计；--logs-dir 可指定目录）。
流程：逐行统计命中 → 复制备份到 astrbot/data/logs/backup/<文件名>.<时间戳>.bak → 写脱敏副本
→ 未被占用时 os.replace 原子替换；当前 astrbot.log 被运行中 AstrBot 的 loguru append 打开
（Windows 不允许 replace），退回原地重写并把并发追加的尾部搬过来。

| 文件 | 命中行 | 规则命中 | 备份 sha256 | 脱敏后 sha256 | 大小 before→after | 模式 | 复检剩余 |
|---|---:|---|---|---|---|---|---:|
| astrbot.log | 1,161 | authorization 23 / open_id 1,138 | a014c0d5b4376a9060786525cfdca40b070da2900195ecb1e597e065662001cb | 0343ceabe6a4ed024e3c11117f58e80199ffadefafd6baa33f65c9fe90e9e91b | 11,890,604 → 11,864,476 | in-place-rewrite | 0 |
| astrbot.2026-11-06_21-51-07_929893.log（轮转） | 1,899 | authorization 7 / open_id 1,908 | 58f0a298b3e0efb79c8f898d03665c40504cc6e67adeb9eae6e508cf9f0cfaaa | a9b22b8c82df8600f95269e659dc5f327a76bee8f7df069361fcffc3e1c86e21 | 19,999,964 → 19,957,965 | atomic-replace | 0 |
| event_loop_watchdog.log | 0 | — | 236e6cf1a6985fef265659851e16154183f56535eeb15e9a035d46f993c3953f | 同备份（内容未变） | 165,592 → 165,592 | atomic-replace | 0 |

脱敏后复检（原始正则计数，不打印内容）：Bearer sk-… 0 行、裸 sk-… 0 处、ou_… 0 处、
<redacted> 1,161（astrbot.log）/ 1,923（轮转）处；文件末尾换行完整，脱敏后 5 秒文件仍可正常追加。

**边界说明**：运行中的 AstrBot 仍是 reload 前的旧代码，若旧代码再次打印 traceback locals，新行可能
再次带密钥；本任务禁止 reload，新日志的长期保证依赖下一次加载后生效的 install_log_redaction()。

## 3. 出站统一技术词黑名单

唯一词表：amo_shared_text.py。

- TECH_HARD_TERMS：服务器 / 后台 / 接口 / 报错 / 超时 / 权限 / 面板 / 异常 / 重试 / 网络连接 /
  数据库 / 日志 / 错误码 / 节点 / 降级 / 软件 / 系统 / traceback / timeout / exception / api key / error code；
- TECH_SOFT_TERMS（失败 / 卡住）：日常也可能说（减肥失败、门卡住了），只有同句带技术上下文才算后台话；
- strip_tech_terms(text, source, kind)：删命中分句；chat 全命中 → 角色化短句；system/push/command
  全命中 → 不发（空串）；photo/caption 命中 → 丢说明只发图；
- sanitize_caption()：照片说明专用；looks_like_tech_talk() 供测试/别的守卫用。

接线（全走同一词表，不各写一份）：

| 路径 | 位置 |
|---|---|
| 聊天回复（LLM 结果，含分段/直发） | amo_runtime_reply.on_decorating_result：strip_tech_terms(text, source=chat) |
| 命令返回（非 LLM 结果，含 /amo 与所有子命令） | amo_runtime_reply.on_decorating_result → _sanitize_command_result() |
| 主动 / push / 系统通知 / 后台补话 | amo_message._send_to_umo（source 透传；system/push 全命中直接不发） |
| 聊天直发（seg 关闭/接管） | amo_message._send_now |
| 照片 caption（draw/photo_share 组件） | amo_message._send_media_to_umo（纯文本组件命中即剥掉）+ draw_engine._send_image |
| draw 工具返回（喂给模型的字符串） | draw_engine.send_photo_now / manual 改为角色化短句，不再返回「权限不足/draw_enable=false/触发了发图限制/上一张还在生成」 |
| 照片失败提示词 | amo_prompt_registry.py 不再教模型复述「报错/超时」 |

词表外的模型名/插件名/API 泄漏仍由原 looks_like_model_leak 兜底；技术词黑名单是它没覆盖的
「服务器/报错/超时/权限/面板/接口/异常/重试/网络连接」这一层。

## 4. 测试

新增（29 passed）：

- tests/test_log_redaction.py：13 项 —— Bearer/sk-/api_key/token/Authorization/open_id 脱敏；
  正常文本一字不改；msg/args(dict)/异常栈三条路径都不出密钥；forever/watch/data/photo 四个插件
  logger（以及 install 之后新建的 handler）都生效；安装幂等；open_id 可选开关。
- tests/test_backend_talk.py：16 项 —— 任务点名 9 词全命中；正常聊天/「减肥失败」/「门卡住了」不误伤；
  4 条生产样例清洗后不含技术词；混合句只删命中句；系统通知整条不发；caption 丢说明保留图；
  _send_to_umo 真实发送路径（技术话不发出、正常话照发）、_send_media_to_umo（技术 caption 剥离、
  图片保留）、命令结果清洗；源码级接线断言（聊天/push/caption/命令/draw 都调同一词表）。

既有测试适配（并行任务报过来的旧话术断言）：
- tests/test_photo_force.py::DirectCommandTests::test_force_photo_bypasses_hourly_limit 原断言「不发图」，改为拒答非空且不含「不发图/限制」（test_photo_force.py 13 passed）。
- tests/test_draw_engine.py::RunTests::test_manual_disabled 原断言「未开启」，
改为断言新话术（非空 + 不含「未开启/draw_enable」）——已与 fix-media-gateway 同步归属。


### 回归结果（本任务改动相关，2026-11-22 11:2x）

- 基线（改动前）：tests/test_obs_pii.py test_text_pipeline.py test_message_helpers.py test_draw_engine.py
  test_chat_safety.py test_outbound_gateway.py test_outbound_guard.py test_shared_core.py → **178 passed**。
- 改动后聚焦回归：test_log_redaction + test_backend_talk + test_obs_pii + test_shared_split +
  test_shared_core + test_text_pipeline + test_message_helpers + test_chat_safety + test_draw_engine +
  test_cov2_commands + test_cov2_message + test_reply_pipeline → **467 passed, 9 subtests passed**。
- 扩面回归（18 个文件）→ 509 passed；3 个失败全部来自并行任务的未完成改动，与本任务无关：
  test_architecture_ratchet（amo_media/amo_persona_guard/amo_scene_ledger/amo_memory_ledger 等新增依赖边）、
  test_lint（amo_schedule.py:1823 undefined `_row_timestamp`）、
  test_source_guard（amo_media.py:95 裸 time.time）。本任务没有新增内部 import 边、没有裸时钟调用。
- 另两条并行任务相关失败（已私信归属）：tests/test_task15_reply_assembly.py 的 PhotoCaptionTests（amo_media.send_photo 给旧测试假宿主多传 kind=，TypeError 被吞成 send_error，归 fix-media-gateway 的 amo_media 改造）、tests/test_immersion_acceptance.py 4 条（重复检测/AI 自曝守卫，task-2/task-18 方向）。

### diff 摘要（逐处 before → after）

```
observability.py
+ REDACTED / _SECRET_PATTERNS / _OPEN_ID_PATTERNS / _SENSITIVE_LOG_KEYS
+ redact_text(value, open_id=True, stats=None)
+ _redact_value()  # msg/args 递归；dict 敏感 key 整值打码
+ class RedactionFilter(logging.Filter)  # msg/args/exc_text/traceback 折入 msg 后清 exc_info
+ install_log_redaction(redact_open_id=True, force=False)  # 全 logger+handler，钩 addHandler，幂等
+ redaction_installed()
+ import logging / threading / traceback

main.py
- from .observability import plugin_health
+ from .observability import install_log_redaction, plugin_health
  HumanCorePlugin.__init__:
+     try: install_log_redaction()
+     except Exception as _amo_exc: _note_swallowed(...)

amo_shared_text.py
+ TECH_HARD_TERMS / TECH_SOFT_TERMS / _TECH_CONTEXT_RE / TECH_FALLBACKS / _SENTENCE_KEEP_RE
+ _tech_hits() / looks_like_tech_talk() / strip_tech_terms() / sanitize_caption()
  chat: 全部命中 → 「唔…这个先不说了，陪我聊点别的嘛。」
  system/push/command: 全部命中 → ""（不发）
  photo/caption: 命中 → ""（只发图）

amo_shared.py
+ 门面转发 looks_like_tech_talk / sanitize_caption / strip_tech_terms

amo_message.py
+ _send_now:      text = strip_tech_terms(text, source="chat")
+ _send_to_umo:   text = strip_tech_terms(text, source=_tech_src, kind=_tech_kind)
+ _send_media_to_umo: Plain 文本组件 sanitize_caption，空则剥掉（图片保留）

amo_runtime_reply.py
+ on_decorating_result: 非 LLM 结果 → self._sanitize_command_result(result)
+ on_decorating_result: LLM 聊天文本 apply_chat_safety 之后 strip_tech_terms(source="chat")
+ def _sanitize_command_result(result)  # 命令返回到人话出口

draw_engine.py
- 画图功能未开启（draw_enable=false）    → 这会儿不方便拍，等会儿再说
- 权限不足：这个功能只对老公开放          → 这个只拍给你一个人看
- 上一张还在生成，还没出来                → 上一张还在拍，等它出来
- 这次不发图：触发了发图限制（…）          → 今天拍得够多啦，先歇会儿
- 合拍失败：%s，…不能生成两人同框的照片    → 这会儿拍不了合照：%s，他不在你身边，没法同框
- tail = （dry-run，只记提示词）           → （这次只记提示词）
+ _send_image: caption 先 sanitize_caption（命中只发图）
  _blocked 词表同步新话术（保留旧词兼容）

amo_prompt_registry.py
- 如果工具报错或超时，直接说…
+ 如果照片一时没出来，直接说…

tests/test_draw_engine.py::test_manual_disabled
- assertIn("未开启", engine.manual(...))
+ 返回非空 且 不含「未开启/draw_enable」
```

## 5. 改动文件

- observability.py：新增 redact_text / RedactionFilter / install_log_redaction / redaction_installed（+ 顶部 logging/threading/traceback import）。
- main.py：HumanCorePlugin.__init__ 安装日志脱敏。
- amo_shared_text.py：技术词表与出口清洗函数。
- amo_shared.py：门面转发 looks_like_tech_talk / sanitize_caption / strip_tech_terms。
- amo_message.py：_send_to_umo / _send_now / _send_media_to_umo 接入。
- amo_runtime_reply.py：聊天结果与命令结果接入。
- draw_engine.py：工具返回角色化 + caption 清洗 + _blocked 词更新。
- amo_prompt_registry.py：失败提示词不再含「报错/超时」。
- tests/test_log_redaction.py、tests/test_backend_talk.py（新增），tests/test_draw_engine.py（适配）、tools/redact_prod_logs.py（新增）。
