# Bootstrap 输出重定向复验（2026-11-02）

> 历史资料。保留当时的计划、版本和验收范围，后续状态以实际发布/安装证据为准。当前操作入口见[文档导航](README.md)。

结论：当前 `bootstrap.ps1` 已在执行 uv 的局部范围内使用 `ErrorActionPreference=Continue`，无需再修改启动器。Windows PowerShell 5.1 下，`2>&1 | Select-Object -Last 20` 的受控回归与真实全新环境配置均通过。原生命令非零退出仍按原退出码报告，退出后恢复调用方的错误偏好和 uv 环境变量。

## 原因与回归

Windows PowerShell 5.1 将重定向后的原生 stderr 包装为 ErrorRecord。uv 的正常下载进度也使用 stderr；若此时 `ErrorActionPreference=Stop`，启动器会进入 catch，即使命令本身退出 0。不能仅凭 stderr 是否出现判定下载失败。

新增 `tests/test_bootstrap_ps_redirect.py`，明确调用系统 `System32/WindowsPowerShell/v1.0/powershell.exe`，并检查运行版本为 5.1。编译的独立原生 fixture 输出 35 行下载进度，经启动器和准确的 `2>&1 | Select-Object -Last 20` 管线验证：

| 场景 | 实际结果 |
| --- | --- |
| 原生退出 0，保留现有保护 | 启动器退出 0，保留末尾进度，恢复调用环境 |
| 原生退出 7，保留现有保护 | 启动器退出 7，保留末尾进度，恢复调用环境 |
| 仅在临时副本将局部 Continue 改回 Stop | 原生退出 0，启动器仍退出 1，错误内容为首条 stderr 进度；负对照复现原问题 |

stdout/stderr 的交错次序由两个流的读取调度决定；Last 20 只保留最后 20 个管线对象，不能承诺任意早先 stdout 或全部 JSON 必定在末尾。回归不依赖跨流先后顺序，也不把显示尾部当作完整配置结果。

## 真实冷启动

运行环境为 Windows x64、Windows PowerShell **5.1.26100.9711**。使用既有已发布 `0.4.0-preview.2` 源码 ZIP 和同版 Windows preview 组件 ZIP，逐个比对发布 `SHA256SUMS`；源码与组件均对应此版，未混用候选版组件。已确认当前 `0.4.0-preview.3` 候选的 `bootstrap.ps1` 与该已发布源码内启动器规范化换行后完全一致，因此此次验证覆盖当前启动器。

在新建源码目录及独立 `LOCALAPPDATA` 中执行：

```powershell
& "$root\bootstrap.ps1" --apply --expected-root $root --bundle $bundle --configure-desktop 2>&1 | Select-Object -Last 20
$bootstrapExit = $LASTEXITCODE
```

未预置 uv、项目 Python、虚拟环境或依赖缓存。实际下载 uv 0.9.26、托管 Python 3.12.12 和锁定依赖，完成配置耗时约 30.47 秒，退出 0。核对结果：

- uv 下载 ZIP SHA-256 与启动器固定值一致；本地 Python 3.12.12、SQLite 3.50.4、openpyxl 3.1.5 可用。
- 独立桌面绑定指向该新根及其 `.venv\Scripts\python.exe`。
- `.runtime/helpers.json` 选择 `0.4.0-preview.2`，三项 helper 路径正确；组件清单四个文件逐个哈希一致。
- 调用前的 `UV_CACHE_DIR`、`UV_PYTHON_INSTALL_DIR` 和 `ErrorActionPreference=Stop` 均恢复。
- 未创建账本；未请求 App 安装、身份初始化或发布。该检查不代表已安装/打开桌面 App，也不代表云端已验证。

首轮经过宿主 PowerShell 7 → Python → PowerShell 5.1 的测试进程继承了 PowerShell 7 的模块搜索路径，导致 `Get-FileHash` 无法自动加载，尚未运行 uv 即失败。失败目录和日志保留。随后仅在第二个全新测试进程内使用 Windows PowerShell 的标准模块目录，真实冷启动通过；未改变系统配置或生产脚本。这属于测试进程环境差异，不是 NativeCommandError。专用回归也显式使用标准 5.1 模块目录，避免宿主运行时影响。

本轮未操作真实桌面安装、真实绑定、任何云端目录、凭据、个人账本或 `account-test`；未清空已有运行目录。组件从已校验的本机 Release 文件读取，因此本轮不包含在线下载 Account Release 组件这一环节；uv/Python/依赖为真实新下载。

## 复验命令与本机证据

在仓库根，用可用 Python 执行：

```text
python -m unittest discover -s tests -p "test_bootstrap*.py" -v
```

结果：**28 tests，全部通过**（包括 2 个新增专用回归）。本机证据保留在该轮独立 evidence 目录，具体路径由本机开发记录保存，不提交。

- `bootstrap-tests.log`：相关回归完整结果。
- `controlled-summary.json`、`controlled-exit-0.log`、`controlled-exit-7.log`、`controlled-no-guard.log`：受控原生 stderr 和负对照的实际输出。
- `run_cold_start.py`、`stock-ps51.ps1`：哈希校验、全新目录准备与真实调用脚本。
- `stock-ps51-summary.json`、`stock-ps51.stdout.log`、`stock-ps51.stderr.log`：真实执行结果。
- `verify_cold_start.py`、`verification.json`：绑定、运行环境与组件哈希复核。
- `asset-checks.json`：两份已发布 ZIP 的校验结果及启动器一致性。
- `cold-start-summary.json`、`cold-start.stdout.log`、`cold-start.stderr.log`：首轮宿主模块路径差异的失败证据。
