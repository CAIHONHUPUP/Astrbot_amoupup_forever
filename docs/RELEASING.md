# 发版说明（能陪）

> 只在用户明确说「上传」时发版；本机代码就是主干，不再对齐上游。

## 一、命令

```bash
python work/refactor/upload.py amo --dry   # 只打包 + 检查仓库，不推送
python work/refactor/upload.py amo         # 真上传：推 main + 建 tag/Release + 传两个包
```

脚本会从 `metadata.yaml` 读版本号，**不要手写版本**；发版前先跑 `python tools/check_version.py`。

## 二、Release 里放什么（2026-11-13 起）

| 附件 | 内容 | 用途 |
|---|---|---|
| `Astrbot_amoupup_forever-<版本>.zip` | **只含运行时文件** | AstrBot 直接安装 |
| `Astrbot_amoupup_forever-<版本>-source.zip` | **全量代码**（运行时 + 测试 + 工具 + CI） | 阅读/二次开发 |

安装包**排除**：`tests/`、`tools/`、`.github/`、`.coveragerc`、`mypy.ini`、
`requirements-dev.txt`、`run_tests.py`（以及所有 `__pycache__` / `.db` / `.log` 等运行时产物）。

所以两个包大小一定不同；如果发现一样大，说明打包脚本退回旧逻辑了，要检查 `upload.py` 的 `INSTALL_SKIP_*`。

## 三、每个版本只留一个 Release

- 同一个版本号（如 7.0.0）只允许存在**一个** Release；
- 发版过程中反复修的，上传新版后要把上一个**同版本**的 tag + Release 删掉（不同版本的历史保留）；
- 删除方式：GitHub API `DELETE /repos/<owner>/<repo>/releases/<id>` + `DELETE /git/refs/tags/<tag>`。

## 四、CI 会检查什么

| 步骤 | 内容 |
|---|---|
| Version | `metadata.yaml` / `CHANGELOG.md` / `README.md` / `main.py` 四处版本一致 |
| Type check | `tools/mypy_check.py`（严格模式 `--check-untyped-defs`，失败即红） |
| Quality gate | schema 键数、全目录编译、路由数（84）、面板 JS 语法、pyflakes |
| Run tests | 1465 项单测 + 覆盖率（`.coveragerc` 底线 58%） |

`mypy` 版本钉在 `2.4.1`，不要随手升；本地跑法：

```bash
python tools/mypy_check.py
python tools/quality_gate.py --fast
python -m coverage run --rcfile=.coveragerc -m unittest discover -s tests -t tests
python -m coverage report
```

## 五、发完核对

1. `GET /repos/CAIHONHUPUP/Astrbot_amoupup_forever/commits/main` → 新 commit；
2. Release 里有**两个**附件且大小不同；
3. CI 的 `test` job 是 success；
4. 线上重载后日志出现 `我能永远陪伴你 <版本> loaded`，且无 ERROR；
5. 每个版本只有一个 Release。
