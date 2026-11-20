# 四插件代码健康计划（2026-11-17）

## 目标（今天可完成的部分）

- 工程化底座：git 基线、CI/预检、mypy 只降不升、photo 重构、legacy 只读盘点；
- **不承诺**今天清完 1668 个 mypy、不删 legacy 分支（需逐项数据验证）。

## 阶段

### P0 冻结与基线（30–45 分钟）
- 等 `photo-polish` 交回，四插件全量/gate 绿；
- 备份四插件 + 配置；
- 每个插件 `git init`、`.gitignore`（logs/cache/__pycache__/backup/work/tmp/.env），首次 commit；不 push。

### P1 CI 与预检（30 分钟）
- 四插件统一 `.github/workflows/test.yml`（已在 task-29 做）；
- 文档明确 `D:/dafeiyu/work/ci_all.py`，CI 与本地同一 pytest；
- 推送仍等用户“上传”。

### P2 mypy 只降不升（1 小时）
- 锁当前基线：forever 1668、data 12、watch 89、photo 408；
- `tools/mypy_ratchet.py`：超过基线 fail，低于基线自动更新；
- 不批量修，按文件逐步降。

### P3 photo 重构（进行中，1–2 小时）
- 3273 行测试拆 4–6 文件 + helpers；
- 拆 4 个 >120 函数；
- 326 tests 不变、gate、结构 0/0、真实 A/B。

### P4 legacy 只读盘点（45 分钟）
- 搜索旧名 `astrbot_plugin_omnidraw`、旧路径、`.bak-*`、`work/`、tmp；
- 输出“可删/需保留/需数据验证”三类；
- **今天不删**，下一轮按清单删。

### P5 最终验证与文档（1 小时）
- 四插件 `ci_all` + gate + IMM + 真实自拍；
- 更新 `HEALTH_CHECK.md`、`RESTORE_CHECKLIST.md`；
- 给出今日可交付/未完成结论。

## 今天时间线（从 17:30 起）

| 时间 | 事项 |
|---|---|
| 17:30–18:15 | P0 git 基线 + 备份 |
| 18:15–18:45 | P1 CI/预检文档 |
| 18:45–19:45 | P2 mypy ratchet |
| 19:45–21:30 | P3 photo 重构（并行等待） |
| 21:30–22:15 | P4 legacy 盘点 |
| 22:15–23:15 | P5 验证与文档 |

## 今天做不到的

- mypy 1668 → 0（需按文件分批，数天）；
- legacy 分支删除（需旧数据/旧客户端验证）；
- 推 GitHub/发版（等用户“上传”）。

## 验收

- 四插件 git 有基线 commit；
- `ci_all` 4 绿 + 四 gate 绿；
- mypy 不高于基线；
- photo 测试/函数拆分完成且 326 tests 不变；
- legacy 盘点到文件级；
- HEALTH_CHECK 更新。

---

## 进度更新（2026-11-17 19:40）

- P2 mypy 只降不升：**forever 1672 → 0、photo 407 → 0**、data/watch 0（不用 blanket ignore；
  双导入回退分支用 \`if not TYPE_CHECKING:\` 收口，脚本 \`work/dual_import_fix.py\`）。
- P3 photo 重构：测试拆分 + 4 个长函数拆分已完成（上一轮）；photo gate 全绿 335 passed。
- P4 legacy 只读盘点：已完成（\`LEGACY_INVENTORY.md\`）；A 类不停服子集可重放（46 目标 / 94.7 MiB），
  B/C 类仍按清单保留。
- 新增（本轮计划外）：compat adapter（\`amo_compat.py\`）、配置化骨架（\`infra/config\`）、
  域契约骨架（\`domains/memory\`、\`domains/finance\`）、测试时钟 flake 修复。
- 详细数据：\`docs/CODE_HEALTH_2_REPORT_20261117.md\`。
- 下一步：legacy A 重放 → 域实现搬迁（schedule/diary、reply/photo）→ 配置化扩面 → main.py ≤400 行。
