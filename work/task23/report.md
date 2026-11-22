# task-23 报告：MediaGateway / MediaLedger / 姿势白名单 / 单 zone 断言 / 全出口 strip

- 时间：2026-11-22 10:56–11:38（Asia/Shanghai）
- 仓库：D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever
- 约束遵守：未 reload、未 commit/push、未写生产 DB；照片发送/配额/去重逻辑全部在代码与隔离测试库里跑。
- 协调：fix-photo-flow（task-3/16）已停手 photo_share/draw_engine，保留其统一出口 _send_media_to_umo 签名与 source=photo 语义；fix-scene-writers（task-25）插入的 verify_photo_scene 段原样保留；fix-security-redaction（task-26）话术改动导致的旧断言由它自己更新；fix-metrics（task-27）生图 principal 改动导致的 19 条 photo 测试失败已通知并修复。
- 未回退 task-15/16：媒体标记读侧剔除、分段前 validate、统一 OutboundGateway 均保持；本任务只在它们外面加了一层媒体账本/网关。

## 1. 单一 MediaGateway + MediaLedger（P0-1）

新增 amo_media.py（唯一媒体事实源）：

- MediaLedger：kv 键 media_ledger（滚动 400 条）结构化账本，字段 media_id/kind/source/status/scene_id/zone/place/pose_id/pose_name/pose_room/outfit/hash/event_id/umo/test/at/day。photo_count、find_recent（签名/事件）、coverage、stats、last_photo 都从账本派生；photo_engine.today_count() 改为 max(kv 计数, 账本当日照片数)，工具链路从此也占每日 cap。
- MediaGateway.send_photo 顺序固定：剥 caption 标记 → 内容签名去重（自动分享 6 小时）→ 每日配额（只拦 auto；用户明确要图只计数不拦）→ 事件占位（账本 12h + amo_presence.claim_photo_event，全链路只 claim 一次）→ 统一出站 _send_media_to_umo（保留 task-16 source=photo/kind）→ 先占 cap 再记一条账本 → 写 memory（messages + 私聊 bot_media）。
- 三条链路收编：
  1. photo_share：_send → gateway（origin=auto/test/resend/manual；_share 尾部不再自己 claim/bump/remember，事件占位挪到 gateway，避免双 claim）；
  2. draw_engine：_send_image → gateway（origin=tool，event_id=本轮 message_id，同轮最多一张，计数但不因 cap 拦截，用户明确要图除外）；
  3. 外部 generate_selfie：reconcile_external 按外部插件 omni._selfie_turn_store 的证据补一条 external_selfie 账本（占 cap + 写 memory）；在 on_decorating_result 每轮 + photo_guard_state 两处调用，同一证据只补记一次，超过 10 分钟的历史证据不补。
- 覆盖率：审计窗口三条链路真实发送 32 张只有 7 张有记录（21.9%）；现在三条链路各有集成测试，账本记录 3/3 = 100%（tests/test_media_gateway.py::ChainIntegrationTests）。

## 2. 单 zone / 单姿势断言（P0-2）

- photo_prompt._bed_like：沙发不再算床；只有 home_bed/hotel 允许“躺在床上盖被”句；其他 home_* 一律 False；文本判定仍先 strip_negated（“不是沙发、不是床”不命中）。
- 新增 photo_prompt.verify_photo_media_prompt / enforce_photo_media_prompt：发送前断言 ①prompt 只出现当前 zone（复用 amo_scene_ledger.detect_zones/verify_photo_prompt）；②非卧室/酒店不得出现床句；③家里房间家具不能串（浴室不能写客厅沙发、客厅不能写床铺）；④姿势 room 必须匹配 scene room。能清洗就先清洗（单 zone sanitize / 删床句 / 删错房间家具），清洗后仍不过 → photo_share 直接不生成不发（记 failed + note_photo_fail，绝不发互斥图）。
- 房间来源收敛：HOME_ROOM_ZONE 让 plan.zone 决定房间（home_bath→bathroom、home_bed→bedroom、home_sofa/home_meal/home_desk→living），不再用 home_room_for_plan 的粗粒度结果把客厅家具塞进浴室；selfie_state_hint 的“躺床盖被”在非卧室被删。
- void 相机与“举着手机自拍”话术不再互相打架：双手/躺/背对类姿势统一改成第三人称抓拍叙事（不出现手机），前置自拍姿势保持第一人称自拍叙事。
- 用生产裁剪后的姿势库跑 6 个历史场景（地铁/卧室/客厅/浴室/小馆/商场）：verify 全 ok、非卧室 prompt 无床句、地铁不出现客厅床句、浴室不出现客厅家具。

## 3. 姿势白名单 / MAX_POSES / room 分池（P0-3）

- pose_library.APPROVED_POSES：58 条白名单（≤MAX_POSES=60），按 room 分池：bedroom 8 / living 26 / any 24；成人向（挤胸/揉胸/顶胯/开腿/M字/倒立/桥式/团身/劈叉/缠绵/双人/特殊…）和杂技姿势全部剔除（BANNED_WORDS 47 个，白名单外双保险）。
- random_pose 只从白名单 + 当前 zone 的房间池取：home_bed→bedroom+any，home_sofa/home_meal/home_desk/home_generic→living+any，home_bath→any，公共/未知场景返回 None（由场景规则给姿势，不再把床姿套到地铁/商场）。pick_pose_for_plan 改传 scene_key（不再用 base_zone="home"）。
- MAX_POSES 在加载处强制（白名单优先保留、其余补到 60）；add/save_all 也过白名单；图库分类前缀（“上半身单人·性感系：”）在判黑名单/写提示词前剥掉，避免分类词进 prompt。
- 生产 plugin_data/Astrbot_amoupup_forever/poses.json 已一次性收敛 300 → 58 条（原文件备份 poses.json.bak-task23-20261122，70588 字节；新文件 14133 字节），每条带 approved/room 字段；参考图仍在 outfit_refs/姿势库。
- 拍脚专用池同样只从白名单取（M字开腿/挤胸类不再进）。

## 4. [我发的照片] 全出口 strip（P0-4）

在已有读侧剔除/分段前 validate/strip_media_markers 之上补最后一道：

- amo_outbound.send / send_media：文本与 Plain 组件里的 [我发的照片]/[我发的语音] 统一剥掉（所有走 OutboundGateway 的 chat/主动/push/photo 都经过）；
- amo_persona_guard.prepare_outbound：OutboundGateway 的语义卫生入口同样 strip（覆盖 segment_engine 聊天分段、amo_message._send_now 等）；
- amo_media.send_photo caption 先剥；photo_share._send 兜底 transport、draw_engine._send_text 兜底、amo_commands say 兜底直发也都再 strip 一次；
- 测试：amo_outbound.send / send_media / gateway caption 三处断言不带占位符。

## 5. hash/event_id 去重 + 失败话术（P0-5）

- 去重：图片内容签名（本地文件/bytes/data_url 用 sha1；URL 退化为引用哈希）；自动分享同签名 6 小时内不重发；同一 event_id 最多一张（账本窗口 12h + amo_presence.claim_photo_event 持久占位）；工具链路按“本轮 message_id”防同轮连发；用户明确重发（resend_last/source=chat）不占事件额度。
- 失败话术：amo_media.failure_line(fixed=...) + sanitize_failure_text 统一出口；含 服务器/报错/接口/API/节点/模型/超时/网络/软件/异常/HTTP/error/4xx/5xx 等词一律替换成温柔兜底句；draw_engine._notify_failure 与 photo_share._notify_failure 都已接入。
- 另外修掉一个会挡住 lint 的既有 bug：amo_outbound.py 缺 import time（_day_of 里 NameError）。

## 6. 测试证据

- 必跑集合（文件名匹配 photo|outfit|draw|message 的 38 个文件）：611 passed, 0 failed（62.34s）。
- 新增 tests/test_media_gateway.py：27 passed（账本字段/计数/签名与事件去重/每日 cap/caption strip/外部回执/单 zone 断言/床句/家具串房/姿势白名单与 room 池/MAX_POSES/三链路记录覆盖率/失败话术/photo_engine 配额接线）。
- 受影响模块精选集（36 个文件，含 outbound/persona/scene/runtime/lint）：890 passed, 2 failed；2 条失败均非本任务代码：
  1) tests/test_architecture_ratchet.py：task-27/task-30 在我更新基线之后又新增了 amo_scene_ledger/amo_state_snapshot/services 依赖；收尾时需重跑一次并更新 tests/architecture_baseline.json（我已按 11:33 时点重建过一次）。
  2) tests/test_runtime_split.py：_dynamic_context 方法体被 task-27/30 改动，需其更新 CODE_MD5/FINGERPRINTS（我改的 on_decorating_result 哈希已同步）。
- tests/test_naming_contract.py 仍被历史审计快照污染失败：work/audit/_scratch/repo_pin/ 下有含旧插件目录名的副本，不在本任务写入范围，建议清理。
- test_lint.py（pyflakes F821）：passed。

## 7. 已知风险 / 交接

- 生产 bot 进程内存里仍是裁剪前的 300 条姿势（禁止 reload），磁盘 poses.json 已是 58 条；下次重启生效。备份文件未删，可随时回滚。
- 外部插件（Astrbot_amoupup_photo）失败时回传 LLM 的 tool result 原文不在本仓库写范围；本侧已保证“给她说的人话”无技术词、成功图 100% 记账。
- 全量 474 个测试文件的全跑未完成（耗时过长被中止），改跑必跑集合 + 受影响模块精选集；收尾集成时建议再跑一次全量。
- 并发建议：task-27 的 per-principal 生图额度只做“定格插件侧权限/用量”，forever 每日照片张数仍以 amo_media.photo_count 为唯一 cap，不要重复计数。
