# 7.6.2 稳定版验收报告（P0/P1/P2）

- 生成时间：2026-11-22 17:5x（Asia/Shanghai）
- 版本：**7.6.2**（metadata / main.PLUGIN_VERSION / CHANGELOG / README 四件套一致，release_check 通过）
- 提交：forever `3db3f8f`（tag `v7.6.2-live-202611221748`）、watch `a0f3f13`、photo `8c1c441`、data `9d797bc`
- 口径：不 push；所有生产库写操作前均有 WAL-safe 热备 + manifest 校验；未全绿不 reload。

---

## 0. 一页结论

| 项 | 结果 | 判定 |
|---|---|---|
| forever 全量门禁 | **6289 passed / 7 skipped / 244 subtests，0 failed**；myPy **0 errors**；work unittest OK | ✅ |
| watch 全量门禁 | **1153 passed / 6 subtests，0 failed**；mypy 62/62 基线 | ✅ |
| photo / data 测试 | photo 347 passed；data **785 passed / 7 skipped**（修了 1 个全量跑必红的隔离问题） | ✅ |
| 固定回归 | daily **50/50、614/614**；share **500/500、damaged 0**；remind **240/240**（clean 120/120、defect 120/120） | ✅ |
| 新 seed holdout | daily 5 批：47/48/45/47/47（单轮 99.3-99.7%）；share 5 批：500/500 ×4 + **499/500** | ✅（见 P1-3） |
| 生产 184 回放 | 176 条文本（8 条媒体占位跳过）；修复 25、仍错 2；**clean 新增误伤 = 0**（对照 task40 前基线）；旁白 101→0 | ✅ |
| 39 条对抗 | **39/39 = 100%** | ✅ |
| 真模型 272/397 | **98.53% / 98.49%**；两层 **guard_introduced = 0** | ✅ |
| 日程/日记 3/5/7 天 | 0/0/0 失败、0/0/0 问题；改日程 S1/S2/S2b/S3 一致性全 True | ✅ |
| 历史脏数据 | 42 日记孤儿→stale；23 条纯运维记忆归档；钱包/快照未动 | ✅ |
| reload 四插件 | data/photo/watch/forever 全部「重载成功」 | ✅ |
| 观察窗口 | 见第 9 节 | 见第 9 节 |

**P0：0 项。**

---

## 1. 关键修复

1. **候选还原层假通过（P0 隐患，已修）**：崩溃点里 `amo_rewrite_safety.recover()` 把 `guard_mod` 用在赋值之前，任何「有候选且与现行文本不同」的调用都会 `NameError`，被 `recover_rewrite_damage` 的 `except Exception` 吞掉——候选还原层整层失效；固定 seed 的 50/50 是「这层死了」的结果。现在先取 registry，且只在现行文本命中 `FALLBACKS` 或多池拼装时才还原候选。新增 `tests/test_rewrite_safety_recover.py`（5 项）。
2. **统一时钟**：`amo_rewrite_safety` 的 TTL 改走 `clock.now_ts()`，过 `test_source_guard`。
3. **「出去吃」不再折回「在家」**：`schedule_change._sync_zone` 认不出地点时回落 `EXPLICIT_PLACE_ZONES`；修前 S2b「日程在家、日记写出去吃」，修后一致。新增回归测试。
4. **门禁修复**：严格 mypy **53 → 0**（23 文件，纯注解/窄化，无 `type: ignore`）；架构棘轮补登检查点新增 8 条内部依赖边；IMM 机械改写率改回文档定义的稳态口径；到货重试测试钉死随机 roll；watch 约会改期测试去掉时间炸弹；data embedding 离线测试与遗留 worker 线程隔离。

---

## 2. 门禁证据

- forever：`work/audit/gate_forever_r5.txt`（6289 passed / mypy 0 / unittest OK）
- watch：`work/audit/gate_watch_r2.txt`（1153 passed / 全绿）
- photo：347 passed；data：785 passed（日志在 `_diag/photo_pytest.log`、`_diag/data_pytest.log`）

## 3. 固定回归

| 项 | 结果 |
|---|---|
| daily 固定 seed 20261121 | 50/50 组、614/614 轮、residual 0、guard_introduced 0 |
| share 固定 seed | 500/500、fixed 230、damaged 0、persistent 0 |
| remind | 240/240：clean 120/120、defect caught 120/120、leaked 0、误伤 0 |

## 4. 新 seed holdout（禁止拿固定 seed 100% 当泛化）

daily（5 批 × 50 组）：

| seed | 组通过 | 单轮 | residual | guard_introduced |
|---|---|---|---|---|
| 20261201 | 47/50 | 594/597 (99.50%) | 3 | 0 |
| 20261202 | 48/50 | 602/604 (99.67%) | 2 | 0 |
| 20261203 | 45/50 | 593/598 (99.33%) | 4 | 1 |
| 20261204 | 47/50 | 587/590 (99.49%) | 2 | 1 |
| 20261205 | 47/50 | 591/595 (99.33%) | 2 | 2 |

- 4 个 guard_introduced 全是同一形态：候选「…，哈哈。」的正文被整句去重删掉、只剩「哈哈。」→ `degenerate`（P2-1）。均为 `drift=null`，与候选还原层无关。
- share（5 批 × 50 组）：20261201/02/03/05 各 500/500、damaged 0；20261204 **499/500**，1 轮 `opener_repeat`（「我刚刷到」重复开场未被去重，P1-3）。

## 5. 生产 184 回放

- 176 条文本（184 条 bot 消息中 8 条是「[我发的照片]」媒体占位，跳过）。
- before_bad 27 → 修复 25、仍错 2（#2078 memory_meta「我跟你说过了」；#2223 候选还原把重复句还原回来）。
- **clean 新增误伤 = 0**（逐 id 对照 `replay_prod_before_task40.json`：没有任何原本干净的消息在 current 里新增问题）；clean_dropped 0；动作旁白 101/176 → 0/176。
- 干净但被改写 105 条：其中 71 条是删旁白（预期），34 条是 minor/other rewrite（与基线 42 条相比减少，存量问题见 P1-1）。

## 6. 对抗 39 条

收件人 14/14、角色反转 4/4、地点 4/4、精确数字 4/4、重复 2/2、接住分享 5/5、系统话术 6/6，合计 **39/39 = 100%**。

## 7. 真模型

- 272 条（22 组）：**268/272 = 98.53%**，4 失败，**guard_introduced 0**。
- 397 条（32 组）：**391/397 = 98.49%**，6 失败，**guard_introduced 0**。
- 失败全部是候选本身与场景冲突（`cand_codes` 非空：health/place/meal/probe），不是守卫改坏。
- 证据：`work/audit/real_272.json`、`work/audit/real_397.json`、`real_two_corpora_summary.json`。

## 8. 日程 / 日记 / 数据

- 日程 3/5/7 天：0/0/0 失败；日记 3/5/7 天：0/0/0 真实问题（5d 首跑 1 条是核对器把「没什么阳光」误判成晴，重跑 0）。
- 改日程：S1 下午外出→在家休息、S2 晚饭→餐厅、S2b 对话「我们出去吃吧」、S3 加班，场景块/场景账本/提醒/日记提示词与正文全部一致（11 项检查 ×4 全 True）。
- 数据（写前热备 + manifest verify OK）：
  - 日记孤儿 **42 → 0 actionable**：只改 status（valid 172 / stale 113），正文不动；审计键 `diary_p0_cleanup:20261122-171934`；回滚一条 UPDATE。
  - 记忆：只归档 **23** 条纯运维（active 775→752、archived 26→49、82 口径残留 82→59），59 条生活/关系记忆不动。
  - **未动**：钱包差 1666.32（6×275.22 无原扣退款 + 15.00 来源不明）、withdrawable 差 1,700,379 分、天气 15 天/健康 16 天快照缺口（无历史来源，补写等于编造）。

## 9. 观察窗口（飞书）

- reload 时间：2026-11-22 17:48（四插件「重载成功」）。
- 观察窗口：17:50 起；消息实际落在 amo_data.db 的 messages 表（跨插件共享），
  钱包在 amo_forever.db 的 wallet_ledger 表。
- 截至 18:05 的真实流量：6 条（3 user / 3 bot），最大连续 bot = **1**；
  日记质检 / “妈，我” / 精确报账 / 重复扣费：**0 命中**。
- **发现 1 条可见缺陷（P1-5）**：17:58:21「嘴角一下翘起来）我也想你了…」——动作旁白跨行，
  分段器在换行处把括号劈开，残片被发出。根因与修复方案见
  _diag/ACTION_NARRATION_FIX_PLAN.md（未动生产代码，待确认方案）。
- 结论：观察窗口**未发现 P0**（无数据错、无错事实、无重复扣费、无 7 连发）；
  这条是展示层残句，按 P1 修，不触发整版回滚。

## 10. P0 / P1 / P2

### P0（阻塞发布）
- 无。

### P1（发布后可排期，不阻塞本次）
1. **回放存量改写**：34 条干净消息仍被改写（与基线持平、新增 0）。典型：#2053 整条换成「我在休息。」、#2155「还是老样子。」、#2243 内容丢失、#2061「我在回去的了」、#2247「看衣服服」。修法方向：fallback 整条替换前先判候选是否完整、地点词二次校验。
2. **真模型候选冲突**：4/272 + 6/397。模型自己说了与场景不符的地点/健康/吃饭话，守卫只做到不放大。方向：prompt 注入当前事实 + 生成期 critic。
3. **share holdout 1/2500 opener_repeat**：seed 20261204 G26，「我刚刷到」重复开场未被去重；固定 seed 覆盖的 39 种注入仍 100%。
4. **#2223 候选还原把重复句还原**：相比 task40 前多 1 条 persistent（repeat_exact）。方向：还原前先查近聊近似重复，命中则不还原。
5. **动作旁白跨行残句（线上 17:58 实发）**：候选「（听到这句话，\n嘴角一下翘起来）…」被
   _split_by_force_symbols 在换行处切开（该入口不看成对符号保护），第一段被丢、第二段带着
   孤立「）」发出。修法：发送出口先剥旁白再分段 + 分段器单入口保护 + 出口括号不变量
   （方案 A，未动生产代码）。

### P2（记录，不影响本次稳定性）
1. daily 新 seed 4 例「哈哈。」残句（正文被当复读删掉、只剩笑尾），0.2% 轮次；同一形态在 seed 03 出现 1 次。
2. 钱包差额与 withdrawable 差额未修（需要原始扣款凭证 / 产品决策）。
3. 健康/天气历史快照缺口未回填（无来源）。
4. `work/sim/provider.env` 曾被提交进本地 git 历史：本次已 `git rm --cached` + `.gitignore`；**未 push**，如未来推送需先轮换 key。
5. watch/photo/data 三个仓库此前未提交的检查点改动已随本次提交；data 的 embedding 测试隔离问题已修。
