# 维护施工单（2026-11-24 收尾后）

这份是“以后怎么改”的固定流程，不再靠记忆。一次只切一刀，每刀都要留 P0/P1/P2 证据。

## 0. 开工前

1. 备份到 `D:\dafeiyu\backup\<主题>-<日期>-<时分>\`：
   ```powershell
   robocopy <插件目录> <备份>\Astrbot_amoupup_forever /E /XD __pycache__ .mypy_cache .git .pytest_cache
   ```
2. 确认回滚点：改的是哪几个文件、删了哪几个文件，写进本轮记录。
3. 默认不上传、不发版、不改版本号；只有用户明确说“上传”才走发布。

## 1. 每刀固定顺序

1. 只改一个主题，不混功能；
2. 先跑聚焦测试；
3. 跑全量（harness 的 `pytest.ini` 已默认排除 immersion）：
   ```powershell
   python -m pytest -q -n 8 --dist loadfile --timeout=60 --timeout-method=thread
   ```
4. 热重载，确认 0 ERROR；
5. 线上探针（至少一条）；
6. 写 P0/P1/P2。

## 2. 四条硬约束

- **单一事实源**：地点/zone、吃饭、兜底、动作旁白只允许一个 owner；新代码先看 `tests/test_fact_contract_20261124.py`。
- **缺守卫要报错**：关键模块/属性由 `amo_guard_health.py` 启动检查，`/status` 的 `guards` 字段必须 `ok=true`。
- **单文件 <1200 行**：`test_runtime_split` 已收紧；超了先拆，不靠注释糊。
- **不锁实现细节**：关键入口/行为金标优先；不再给全部方法加 co_code 哈希。

## 3. 已知入口

| 用途 | 命令/位置 |
|---|---|
| 全量测试 | `python -m pytest -q -n 8 --dist loadfile --timeout=60 --timeout-method=thread` |
| 只跑守卫健康 | `python -m pytest -q tests/test_guard_health_20261124.py` |
| 只跑事实契约 | `python -m pytest -q tests/test_fact_contract_20261124.py` |
| 只跑跨模块探针 | `python -m pytest -q tests/test_cross_module_probe_20261124.py` |
| 线上健康检查 | `python tools/check_guards.py --plugin <插件目录>` |
| 重载 | `python D:\dafeiyu\work\reload_plugin.py Astrbot_amoupup_forever` |
| 线上状态 | `GET /api/plug/Astrbot_amoupup_forever/status` → `data.guards` |

## 4. P0/P1/P2 模板

**P0（阻断/回归）**
- 备份路径：
- 回滚点：
- 全量结果：
- 热重载结果：

**P1（正确性/一致性）**
- 新增/修改的测试：
- 跨模块不变量：
- 反向验证（去掉规则会怎样红）：

**P2（性能/冗余/可读性）**
- 行数/依赖/重复调用前后：
- 未做项及原因：

## 5. 本轮八刀（2026-11-24）

1. 缺模块/缺守卫健康检查 + `/status guards`
2. 事实单一源契约表
3. 删 photo legacy imports / 标注兼容视图
4. 跨模块探针 v3（聊天后处理/照片场景/日记块）
5. `amo_runtime_context` 1394 → 1190
6. 测试分层：方法锁 56→16，日程哈希 71→8
7. 守卫失败留痕（outbound/persona/state）
8. 本施工单 + `tools/check_guards.py`

## 6. 下次开工建议

先跑 `tools/check_guards.py` 和全量；如果 `guards.ok=false`，先修守卫，不加新功能。  
如果全量低于 5900 passed 或出现非 19 skipped，先查是不是测试入口/依赖装错，不要直接改断言。
