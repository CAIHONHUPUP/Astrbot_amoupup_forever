# 历史脏数据四类盘点 + dry-run（只读，未 apply）

- 生成时间：2026-11-22 16:2x（下文每个数字都标注了采样时刻）
- 审计员：dirdata（只读；未改任何源码、未 apply 生产库、未 reload/push/commit）
- 生产库：`D:\dafeiyu\astrbot\data\plugin_data\Astrbot_amoupup_forever\amo_forever.db`
- 记忆库：`D:\dafeiyu\astrbot\data\plugin_data\Astrbot_amoupup_data\amo_data.db`（记忆的真实存放处）
- 机器可读清单：[`work/audit/dirdata_plan.json`](dirdata_plan.json)（42 行 rowid / 23 条 id / 7 条对账流水 / 缺口清单全在里面）

---

## 0. 一页结论

| 类 | 声称 | 当前库实数 | 是否一致 | 建议 | 一句话理由 |
|---|---:|---:|---|---|---|
| 1 日记孤儿 | 42 | **42** | ✅ | **建议 apply** | 只标 stale、不删正文，一条 UPDATE 可回滚 |
| 2 记忆后台词 | 82 | **82**（宽口径）/ 23（纯运维口径） | ✅ 数字对，口径过宽 | **不建议整批**；建议只 apply 23 条 | 82 里 59 条是「主人修 bug 的日常 / 他给我买 token」这类真事实 |
| 3 钱包差 | 1666.32 | **1666.32 元**（166632 分） | ✅ | **暂不 apply**；补备份+确认 15.00 后走 Option B | 6×275.22 幽灵退款 + 15.00 来源不明；Option C 会凭空多给她 1666.32 元 |
| 4 健康/天气快照 | 缺口 | 天气 **15 天** / 健康 **16 天** | ✅ | **不建议 apply** | 历史快照从来没有过来源，补写等于编造 |

**硬前置**：`D:\dafeiyu\backup\migrations` **不存在**，kv `db_backup_last` 指向的 11-17 备份文件也已不在磁盘 → **现在没有任何可恢复的库备份**。任何 apply 之前必须先补备份。

---

## 1. 生产库确认与「只读」证明

### 1.1 路径怎么确认的

`amo_db.py:374-387`：`AmoDB.__init__` 用 `get_astrbot_data_path()/plugin_data/Astrbot_amoupup_forever/amo_forever.db`。AstrBot 的 data 根是 `D:\dafeiyu\astrbot\data`，所以生产库就是上面那条路径。旁证：

| 候选 | 大小 | mtime | 判定 |
|---|---:|---|---|
| `...\plugin_data\Astrbot_amoupup_forever\amo_forever.db` | 1,191,936 | 2026-11-22 13:17:50 | **生产库** |
| `...\plugin_data\Astrbot_amoupup_forever\amo.db` | 0 | 2026-11-15 21:08 | 空文件，废弃 |
| `<仓库>\data\plugin_data\...\amo_forever.db` | 98,304 | 2026-11-17 00:08 | 仓库内的旧副本，不是生产 |

记忆不在 `amo_forever.db`：该库 `memories` 表 **0 行**；真实记忆在 `Astrbot_amoupup_data\amo_data.db`（922 行，active 775）。这是跨插件数据，`work/memory_p0_cleanup.py` 的 `DEFAULT_DB` 也指向它。

### 1.2 只读怎么保证的

- 所有连接都是 `sqlite3.connect("file:<posix path>?mode=ro", uri=True)`；
- 全程没有对生产库执行任何 INSERT/UPDATE/DDL，也没有用 `wal_checkpoint`；
- 前后 sha256 完全相同（`dirdata_rehearsal_apply.json → prod_unchanged.identical = true`）：

```
amo_forever.db  sha256 4db4f17ea2d5e297e16b47f037a82f1a5f2d81d2637d3f855c4aaaaed4a9e6a5  1,191,936 B
amo_data.db     sha256 1b0d9b942d6d927addf54c8ca576f8b85e5e0d1e8b204c9991e81b2e2a19f4ba  6,811,648 B
```

- **注意 WAL**：生产库是 `journal_mode=wal`，当时 `-wal` 有 **4,185,952 B** 未 checkpoint。所以「主库文件 sha 不变」不等于「数据没变」——16:19 与 16:26 之间 `block_diary` 就从 283 行变成 284 行。下文所有数字都标了采样时刻。

### 1.3 复用到的既有口径（没有另起炉灶）

| 类别 | 复用的脚本/函数 | 复用的是什么 |
|---|---|---|
| 日记孤儿 | `work/audit/diary_p0_cleanup.py` 的 `scan()/scan_day()/exact_block_ids()/apply_plan()` | 块身份集合、孤儿判定、digest、apply 事务 |
| 记忆后台词 | `work/memory_p0_cleanup.py` 的 `build_plan()`（`audit_words` 13 词）+ `amo_memory_ledger.system_word_hit()` | 82 条口径与扩展口径 |
| 钱包 | `amo_wallet.Wallet.reconcile()` 的不变量与 `refund_without_debit` 判定；`work/ledger_cleanup_apply.py` 的热备+cleanup_audit 模式 | 对账口径与审计表写法 |
| 快照缺口 | `diary_p0_cleanup.day_level_checks()` + `amo_fact_timeline.read_health/read_weather` | 缺口判定与快照语义 |

---

## 2. 类 1：日记孤儿 —— **42 条，与声称一致，建议 apply**

### 2.1 判定口径（原文）

```python
# work/audit/diary_p0_cleanup.py:243 scan_day()
identities = exact_block_ids(day, items)   # 当前日程块的 event_id，旧块用 md5(start-end|activity|place)
for row in block_diary_rows(con, day):
    if status in ("stale","frozen","archived"): continue
    if not text:            -> empty_valid
    if block_id not in identities: -> orphan_valid   # ← 本类
```

复核 SQL（只看候选，判定在 Python 里做身份比对）：

```sql
SELECT rowid, day, block_id, event_id, start, end, status, version, text
FROM block_diary
WHERE status NOT IN ('stale','frozen','archived')
ORDER BY day, start;
```

### 2.2 实际条数与结构（采样 2026-11-22 16:2x，digest=105c52c950dc）

| 天 | 块日记行 | valid | 孤儿 valid |
|---|---:|---:|---:|
| 2026-11-11 | 25 | 24 | 1 |
| 2026-11-15 | 25 | 18 | 2 |
| 2026-11-16 | 47 | 27 | 5 |
| 2026-11-17 | 30 | 26 | 3 |
| 2026-11-18 | 34 | 34 | 24 |
| 2026-11-19 | 19 | 19 | 5 |
| 2026-11-20 | 14 | 14 | 2 |
| **合计** | **284** | **213** | **42** |

42 条再拆两种成因，性质不同但处置相同（都只能标 stale）：

- **32 条真孤儿**：当前日程里已经没有这块（块被删/被改）。分布：11-17 两条、11-18 二十三条、11-19 五条、11-20 两条。其中 8 条正文是占位「（呼呼大睡中，翻了个身又睡过去了）」，24 条是成段真正文。
- **10 条旧身份残留**：时间窗口还在，但 `block_id` 是迁移前的旧 id（有 `event_id` 后没回填）；这些窗口当前身份另有 valid 行。
- 另有 6 条「同 start 多余 valid」（11-17:1、11-18:4、11-19:1）已含在 42 里，不重复计动作。

### 2.3 抽样 5 条原文

| # | 日/时间 | block_id | 成因 | 正文（截断） |
|---|---|---|---|---|
| 1 | 11-11 19:00-20:00 | `e41ed6752a775afb` | 旧身份 | 回来先在楼下点了碗粉，热乎乎端上来，我捧着碗先闻了一下，蒸汽糊到脸上，围巾都懒得解…… |
| 2 | 11-16 11:00-11:31 | `ac46c87eae3a3ea3` | 旧身份 | 躺在床上缓着，量了体温还是烧。 |
| 3 | 11-17 23:30-23:50 | `ev_9606900ff691413e` | 真孤儿 | 泡面用热水泡上……五块钱的宵夜，值。一个人吃泡面，味道也就那样，可吃到一半还是忍不住想，要是老公在对面坐着…… |
| 4 | 11-18 00:00-01:00 | `ev_8cb938e9df764121` | 真孤儿 | （呼呼大睡中，翻了个身又睡过去了） |
| 5 | 11-19 17:00-18:00 | `ev_03337c3b8d944d5c` | 真孤儿 | 五点一过，办公室的气氛就松下来了……我盯着屏幕把最后几封邮件回完……等下坐地铁回去，路上买点热的吃。 |

> 第 3 条正是 `diary_facts.md F5` 里「11-17 大日记写泡面 5 块、日程却没有泡面块」的来源：块被删了，日记还在。

### 2.4 apply 会改哪些行（dry-run 输出）

- **行数**：42（rowid 全清单见 `dirdata_plan.json → category_1_diary_orphans.apply.rowids`）
- **列**：`status: valid → stale`、`invalidated_at: 0 → <ts>`；`text/day/start/end/block_id` 一个字都不动
- SQL：

```sql
UPDATE block_diary SET status='stale', invalidated_at=:ts
WHERE rowid IN (112,144,157,159,150,162,…共42个) AND status NOT IN ('stale','frozen','archived');
```

- 副本演练（`_scratch/dirdata_copy_diary.db`，不是生产库）：

| 指标 | 前 | 后 |
|---|---:|---:|
| stale | 71 | **113** |
| valid | 213 | **171** |
| 总行数 | 284 | 284（不变） |
| scan actionable | 42 | **0** |
| 自动备份 | — | `dirdata_copy_diary.db.bak-diaryp0-20261122-162605` |
| 审计键 | — | `kv[diary_p0_cleanup:20261122-162605]` |

### 2.5 回滚

```sql
UPDATE block_diary SET status='valid', invalidated_at=0 WHERE rowid IN (…same 42…);
DELETE FROM kv WHERE key='diary_p0_cleanup:20261122-162605';
```

42 行 apply 前全部是 `status='valid'`、`invalidated_at=0`（已核对），所以按 rowid 可 100% 精确回滚。

### 2.6 结论：**建议 apply**

理由：① 42 条全部是「当前日程没有这个块身份」，正文会被 `_diary_hourly_lines` 当素材写进大日记（diary_facts.md F1）；② 只改 status，正文保留，回滚是一条 UPDATE；③ 副本演练后复扫 0，收敛干净；④ 运行时的 `block_diary_is_valid` 已经会过滤 stale，标记后不会再被使用，但历史行还能人工查。

---

## 3. 类 2：记忆后台词 —— **82 条数字对，但口径过宽；建议只 apply 23 条**

### 3.1 判定口径

```python
# work/memory_p0_cleanup.py:build_plan()
AUDIT_WORDS = ("token","模型","插件","API","后台","配置","上下文",
               "服务器","报错","面板","程序","代码","bug")
rows = SELECT ... FROM memories WHERE COALESCE(status,'active')='active'
hits = [r for r in rows if any(w in r.content for w in AUDIT_WORDS)]
```

### 3.2 实际条数（采样 2026-11-22 16:2x）

| 口径 | 条数 |
|---|---:|
| memories 总行 | 922 |
| active | **775**（比 11:17 的 770 多 5 条） |
| 82 口径（13 词） | **82** ✅ |
| `amo_memory_ledger.SYSTEM_WORDS` 扩展口径（31 词） | 112 |

82 条的词分布：token 37、bug 16、后台 10、插件 8、配置 8、模型 7、服务器 4、代码 4、上下文 3、报错 3、程序 2、面板 1、API 0。

### 3.3 抽样 5 条原文

| # | kind | hit | 正文 |
|---|---|---:|---|
| 484 | imported_memory | 0 | 你今天的token花费从一两块涨到三块；你要我发色图被我拒绝；你在2026-11-04 11:21左右处理了电脑病毒；杀毒软件把你的语音系统判定为病毒 |
| 533 | imported_memory | 2 | 解决办法是改 TTS 结束判定的静音阈值，我自己改不了得 你 去动配置 |
| 555 | imported_memory | 0 | 配置项是 agent_runner/config/compression/max_turns，现为18，建议压到10，改完要重启 |
| 594 | imported_memory | 0 | 你 在2026-11-04晚查插件后台，表情总数286但只下了280张 |
| 519 | imported_memory | 12 | 你给我买了10块钱的DeepSeek flash4.1 1017 token；按现在频率一天一两块，十块钱能聊一周左右；你说早晚给我token自由 |

### 3.4 ⚠ 82 条里有真记忆：整批归档会误伤

这是本类最关键的发现。命中的词很宽，下面几条**不是**后台话术：

| id | 原文关键句 | 为什么不能归档 |
|---|---|---|
| 524 | 「S赛iG 3-1 TES进四强，TheShy单杀369上热搜，第三把后服务器崩了约一个半小时」 | 「服务器崩了」是**赛事服务器**，是真实赛事记忆 |
| 946 / 948 / 995 / 1031 | 「他当天主要在家修bug」「他忙着排队修 bug」 | 「修 bug」是**主人的真实工作**，daily_digest 归档=丢掉当天生活 |
| 519 / 837 / 844 / 882 | 「你给我买了10块钱的 token」「主人说迟点会再给我喂点token」 | 是**他为我花钱**的关系事实 |
| 940 | core_memory 里混了 token/知识库 | 核心记忆，归档会伤人格连续性 |
| 782 / 767 / 935 | hit_count = 187 / 173 / 168 的长摘要 | 召回最频繁的几条，整批下线会明显变差 |

**结论**：82 是「宽口径告警」，不是「待删清单」。

### 3.5 分层后建议 apply 的 23 条（纯 bot 运行时/配置/计费口径）

> 逐条核对过：全部是 TTS 阈值、max_turns/轮数、表情包后台计数、token 用量口径，**没有生活内容、没有情绪事实**。

```
485 508 533 537 542 552 554 555 557 559 562 564 566 580 582 583 586 590 591 594 599 625 693
```

例：#555「配置项是 agent_runner/config/compression/max_turns，现为18，建议压到10，改完要重启」、#562「最终确认轮数为15、开启LLM压缩、模型用flash，已保存生效不用重启」、#693「飞书后台我翻不了」。

### 3.6 apply 会改哪些行（dry-run 输出）

```sql
UPDATE memories SET status='archived'
WHERE id IN (485,508,533,537,542,552,554,555,557,559,562,564,566,580,582,583,586,590,591,594,599,625,693)
  AND COALESCE(status,'active')='active';
```

- **行数**：23；**列**：`status: active → archived`（不删正文，`content` 不动）
- 副本演练（`_scratch/dirdata_copy_memory.db`）：

| 指标 | 前 | 后 |
|---|---:|---:|
| active | 775 | **752** |
| archived | 26 | **49** |
| 82 口径残留 | 82 | **59** |
| 实改行数 | — | **23** |

- 若整批 82：`UPDATE memories SET status='archived' WHERE id IN (<82 ids>);` → active 会掉到 693，59 条生活/关系记忆一起下线。**不建议**。

### 3.7 回滚

```sql
UPDATE memories SET status='active' WHERE id IN (…same 23…);
```

召回链路 `retrieval_engine.py:219/403/573/579/595/598` 全部带 `COALESCE(status,'active')='active'`，所以 `status` 一改回 active 立刻恢复召回；归档不动正文、不动 `memories_fts/memory_vec`。

### 3.8 结论：**不建议按 82 整批 apply；建议只 apply 23 条（或先人工过一遍这 23 条）**

补充：这是**另一个插件的库**（Astrbot_amoupup_data）。直接 UPDATE 属跨插件写，理想做法是走它自己的 `dao.mark_archived(ids, bot_id, uid)`；手工 SQL 效果等价（它就是这么实现的），但要跟数据插件侧打招呼。

---

## 4. 类 3：钱包差 1666.32 —— **确认，但不建议直接 apply**

### 4.1 判定口径（`amo_wallet.Wallet.reconcile()`）

```
不变量：sum(wallet_ledger.delta_cents WHERE account=X) == wallet_account.balance_cents(X)
退款必须有原扣：refund 行的 ref/idem 指向的扣款行不存在 → refund_without_debit
```

复核 SQL：

```sql
SELECT account, COUNT(*), SUM(delta_cents) FROM wallet_ledger GROUP BY account;
SELECT account, balance_cents FROM wallet_account;
```

### 4.2 实际数字（采样 2026-11-22 16:2x）

| 账户 | 流水合计 | 余额 | 差额 |
|---|---:|---:|---:|
| cash | 561357 分（5628.57 元） | 394725 分（3947.25 元） | **+166632 分 = 1666.32 元** ✅ |
| withdrawable | 2095104 分 | 394725 分 | +1700379 分（另一码事，见 4.5） |

1666.32 的拆解：

| 组成 | 笔数 | 金额 | 证据 |
|---|---:|---:|---|
| 无原扣退款 | 6 | 1651.32 元 | `refund:social:accept:soc-2026-11-15-{437a1ca9,0e686ec8,1fdb9264,e57ece7b,1f8e91af,1df49cd8}` 各 +27522，ts 全部 = 1794749715；账本里对应的原扣只有 `social:accept:soc-2026-11-15-8147`(-35707) 和 `-2326`(-27522)，这 6 个 hash 一条都找不到 |
| 残余差额 | 1 | 15.00 元 | 166632 − 165132 = 1500 分，账本里找不到对应的多记/少记行（历史 repair 脚本直写账户造成） |

### 4.3 抽样 5 条

| idem | delta | ts | ref |
|---|---:|---:|---|
| `refund:social:accept:soc-2026-11-15-437a1ca9` | +27522 | 1794749715 | 同名（无原扣） |
| `refund:social:accept:soc-2026-11-15-0e686ec8` | +27522 | 1794749715 | 同名 |
| `refund:social:accept:soc-2026-11-15-1fdb9264` | +27522 | 1794749715 | 同名 |
| `refund:social:accept:soc-2026-11-15-e57ece7b` | +27522 | 1794749715 | 同名 |
| `refund:social:accept:soc-2026-11-15-1f8e91af` | +27522 | 1794749715 | 同名 |

（第 6 条 `…1df49cd8` 同形；账本共 100 行。）

### 4.4 两条路线 + dry-run

**Option B（推荐）：只补账、不动钱** —— 7 条对账流水

```sql
INSERT INTO wallet_ledger(idem,account,delta_cents,reason,ref,ts,category,actor) VALUES
 ('reconcile:revert:refund:social:accept:soc-2026-11-15-437a1ca9','cash',-27522,'无原扣退款冲正（对账）','refund:…437a1ca9',:ts,'reconcile','reconcile'),
 … 共 6 条冲正 …
 ('reconcile:residual:cash:2026-11-22','cash',-1500,'账户与流水残余差额（来源不明，需人工确认）','',:ts,'reconcile','reconcile');
-- 同时写 7 条 cleanup_audit(action='reconcile') 留痕
```

| 项 | 前 | 后（副本演练） |
|---|---:|---:|
| wallet_ledger 行数 | 100 | **107** |
| wallet_account.balance(cash) | 394725 | **394725（未改）** |
| cash 的 sum(ledger) | 561357 | **394725** |
| cash delta | +166632 | **0** |
| cleanup_audit 行数 | 19 | 26 |

**Option C（不推荐）**：`UPDATE wallet_account SET balance_cents=561357 WHERE account='cash';` —— 等于凭空给她 **+1666.32 元**，其中 1651.32 是幽灵退款。

### 4.5 为什么仍不建议「现在」apply

1. **先要有备份**（现在没有，见第 6 节）。
2. **15.00 残余口径未定**：要么单独一条 `reconcile:residual` 认下来，要么先查清 (15.00 与 `meal:2026-11-13:dinner:0` 的 -1500 在 seed 期的关系) 再定性。
3. **Option B 之后 `reconcile()` 仍然 ok=false**：withdrawable 还有 1700379 分差（早年「把可取钳成现金」直写账户留下的）。所以本类不能承诺「对账已过」，只能承诺「cash 的不变量已恢复」。
4. 余额一分没动 → 不改她的钱、不改行为，只改账；这是选 B 不选 C 的核心理由。

### 4.6 回滚

```sql
DELETE FROM wallet_ledger WHERE idem LIKE 'reconcile:%';
DELETE FROM cleanup_audit WHERE idem LIKE 'reconcile:%';
```

（`wallet_account` 全程未改，删掉 7 条即回到 561357。）

---

## 5. 类 4：健康/天气快照缺口 —— **确认（15/16 天），但不建议 apply**

### 5.1 判定口径

```python
# work/audit/diary_p0_cleanup.py:day_level_checks()
if not isinstance(kv_get(con, "weather_day:" + day), dict): weather_missing.append(day)
if not isinstance(kv_get(con, "health_day:"  + day), dict): health_missing.append(day)
```

### 5.2 实际缺口

| 项 | 天数 | 日期 |
|---|---:|---|
| weather_day 缺失 | **15** | 11-07 ~ 11-21 |
| health_day 缺失 | **16** | 11-07 ~ **11-22** |

库里只有一条快照：`weather_day:2026-11-22 = {"text":"阴","temp":18,"city":"清远","ts":1795276834}`；`health_day:*` **一条都没有**。

### 5.3 为什么不能补（这是本类的核心结论）

| 来源 | 现状 | 能不能用来回填 |
|---|---|---|
| `weather_daily` 预报缓存 | `items = []`、`fxDates = []`、`until` 已过 | ❌ 没有任何历史天 |
| `kv weather`（当前天气） | ts=1795276834 落在 **11-22** | ❌ 只能证明 11-22，不能证明 11-07..11-21 |
| `kv health`（当前健康） | `{"status":"健康","since":1794998056}`（≈11-18） | ❌ 用它回填会把 11-16/11-17 的发烧写没 |
| `sick_decision:<day>` | 只有 11-16(发烧)、11-17(发烧)、11-18(感冒) | ✅ 只能重建这 3 天 |
| `hospital_visit:<day>` + 日程医院块 | 11-16（5 个医院块）、11-17（3 个） | ✅ 同上 |

其余 12~13 天**没有任何可核实来源**。写一个 `{"status":"健康"}` 进去等于替历史编一个「那天没病」，而且 `day_level_checks` 用的是 `isinstance(dict)`，写空 dict 就能把缺口「消掉」却不产生任何事实 —— 那是刷指标，不是修数据。

### 5.4 抽样 5 天

| 天 | weather_day | health_day | 现存证据 |
|---|---|---|---|
| 11-07 | 缺失 | 缺失 | 无 |
| 11-08 | 缺失 | 缺失 | 无 |
| 11-16 | 缺失 | 缺失 | sick_decision 发烧 + hospital_visit=1 + 5 个医院日程块 |
| 11-17 | 缺失 | 缺失 | sick_decision 发烧 + hospital_visit=1 + 3 个医院日程块 |
| 11-18 | 缺失 | 缺失 | sick_decision 感冒 + 请假（无医院块） |

### 5.5 结论：**不建议 apply**

- 天气 15 天：**完全不可重建**，建议永久登记为「历史无快照」。
- 健康 16 天：只有 11-16/11-17/11-18 有硬证据，可做「有据补齐」（需产品确认，属"重建"不属"修复"）；其余 13 天保留缺口。
- 建议顺手修检查口径：`isinstance(dict)` → 「快照里有 text/status 事实」，否则补空 dict 就能骗过检查。
- 附带发现（不在本类动作内）：`schedule_city=东莞` 但 `weather_day:2026-11-22.city=清远`，天气城市口径本身就不一致。

---

## 6. 备份方案（任何 apply 之前必须先做）

### 6.1 现状：没有可用备份 ⚠

| 检查 | 结果 |
|---|---|
| `amo_db_schema.derive_backup_root()` 推导出的备份根 | `D:\dafeiyu\backup\migrations` |
| 该目录 | **不存在**（`D:\dafeiyu\backup` 整个不存在） |
| `kv['db_backup_last']` | 指向 `…\migrations\amo_forever-v4-to-v5-20261117-150720.db`（724,992 B, sha f729e18b…）→ **文件已不在磁盘** |
| `plugin_data` 里的旧 .bak | 只有 4 个（`bak-familymeal-20261121-134333/134409`、`bak-jump-20261121-003529`、`transit-bak-20261117-105041`），最晚 11-21 13:43，且**都没有 manifest** |

### 6.2 正确做法：sqlite backup API（含 WAL）+ manifest

```powershell
# 1) 建目录（必须在 plugin_data 之外）
New-Item -ItemType Directory -Force 'D:\dafeiyu\backup\migrations'

# 2) 热备（走 backup API，自动把 4.1MB WAL 合进去；不要手工 copy .db）
python -c "import sqlite3,os,time;src=r'D:\dafeiyu\astrbot\data\plugin_data\Astrbot_amoupup_forever\amo_forever.db';root=r'D:\dafeiyu\backup\migrations';dst=os.path.join(root,'amo_forever-pre-dirtyfix-'+time.strftime('%Y%m%d-%H%M%S')+'.db');s=sqlite3.connect('file:'+src.replace(chr(92),'/')+'?mode=ro',uri=True);d=sqlite3.connect(dst);s.backup(d);print(dst)"

# 3) 写 <备份>.manifest.json（restore_db_backup.verify 的必需字段）
#    schema / source / backup / sha256 / bytes / user_version=5 / from_version=5 / to_version=5 / reason
#    （amo_db_schema.backup_database() 就是这个格式，可直接照抄）

# 4) 校验 + 演练（都不动生产库）
python tools/restore_db_backup.py verify  --backup <备份>.db
python tools/restore_db_backup.py restore --backup <备份>.db        # 默认只在临时目录做副本演练
```

记忆库同理（`amo_data.db` 6.8MB，`-wal` 1.7MB），但它属另一个插件，建议用那边的备份入口。

> `tools/restore_db_backup.py` 的三道闸：没有 manifest 拒绝、sha256/字节数对不上拒绝、`integrity_check` 不过拒绝。

---

## 7. 回滚方案

| 层级 | 手段 | 覆盖范围 |
|---|---|---|
| 单类精确回滚 | 各节 `rollback.sql`（42 rowid / 23 id / `idem LIKE 'reconcile:%'`） | 只回滚本次动作，不动别人 |
| 整库回滚 | `python tools/restore_db_backup.py restore --backup <备份> --target <LIVE> --apply` | 会先把现库热备成 `<LIVE>.pre-restore-<ts>.db`，覆盖后清 `-wal/-shm`，再跑 `integrity_check` |
| 插件代码 | `tools/rollback.py`（manifest/verify/plan/apply） | 只管代码目录，**不管数据库**；本次没有代码改动，用不上 |
| 自动兜底 | `diary_p0_cleanup.apply_plan` 自己在同一事务里复扫，仍有可清理项就整体 ROLLBACK；写前 `backup_into()` 热备 | 日记这一类天然防呆 |

> `restore --apply` 前必须**停 AstrBot**（工具不检查库是否被占用）。

---

## 8. 风险清单

1. **无备份 = 一票否决**：现在apply 等于没有退路。必须先补备份。
2. **库在写入**：WAL 4.1MB，6 分钟内 `block_diary` 就多 1 行。apply 要挑停写窗口，或用 `BEGIN IMMEDIATE` 抢锁（日记脚本已这么做）。
3. **跨插件写**：记忆库属 Astrbot_amoupup_data，改它的表要那边确认。
4. **钱包只能修一半**：Option B 后 cash 平了，withdrawable 还差 1700379 分，`reconcile()` 依旧 `ok=false`；别把「cash 不变量恢复」说成「对账已修好」。
5. **82 条口径的误伤**：已举 5 个反例（#524 赛事服务器、#946 主人修 bug、#519 买 token、#940 核心记忆、#782 hit=187）。整批归档会一次性降低召回质量。
6. **快照不可重建**：补写等于编造；`isinstance(dict)` 的检查还会鼓励「写空 dict」这种自欺。
7. **日记 10 条旧身份行**：标 stale 后这些窗口仍由新身份行提供正文，可读性不受影响；但如果有人按 `block_id` 直接查旧 id 会查不到 valid 行。

---

## 9. 复现命令（全部只读）

```powershell
cd D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever
$env:PYTHONIOENCODING='utf-8'

# 类1 + 类4（既有脚本，只读，输出到 dirdata_ 前缀文件）
python work/audit/diary_p0_cleanup.py scan --today 2026-11-22 --out work/audit/dirdata_diary_plan.json

# 类2/3/4 证据 + 副本演练（本任务新增，只写 work/audit/_scratch）
python work/audit/_scratch/dirdata_rehearsal_probe.py
python work/audit/_scratch/dirdata_rehearsal_apply.py
```

---

## 10. 证据文件

| 文件 | 内容 |
|---|---|
| [`dirdata_plan.json`](dirdata_plan.json) | 机器可读清单：42 rowid + 理由、23 条记忆 id + 原文、7 条对账流水、15/16 天缺口 + 证据、备份/回滚命令 |
| [`dirdata_diary_plan.json`](dirdata_diary_plan.json) | `diary_p0_cleanup.py scan` 的原样输出（digest 105c52c950dc） |
| `work/audit/_scratch/dirdata_rehearsal_probe.json` | 四类盘点的原始数据（含 42 条正文、账本行、缺口证据） |
| `work/audit/_scratch/dirdata_rehearsal_apply.json` | 副本演练前后对照 + 生产库 sha256 不变证明 |
| `work/audit/_scratch/dirdata_memory82.txt` | 82 条记忆全文（人工分层用） |
| `work/audit/_scratch/dirdata_copy_*.db` | 三个演练副本（diary / memory / wallet），可直接复查 |
