# WinScreen

[![Windows verification](https://github.com/Zephyer969/WinScreen/actions/workflows/windows-ci.yml/badge.svg)](https://github.com/Zephyer969/WinScreen/actions/workflows/windows-ci.yml)

Windows 持久交互终端与本机网页面板，沿用 GNU `screen` 的常用命令习惯。

在 WinScreen 会话中启动任务后，可以分离、关闭客户端，再重新连接。后台会话与启动 CMD、浏览器面板分别运行。它不是完整的 GNU screen 移植，也不是能够跨重启恢复进程的 Windows 服务。

## 快速开始

要求 Windows 10 1809+ / Windows 11 / Windows Server 2019+、64 位 Python。固定依赖 pywinpty 2.0.15 提供 Python 3.9–3.13 的 Windows 轮子；推荐 Python 3.11–3.13。当前本机实测版本为 Python 3.13.9；其他环境的验证状态见审查记录。

1. 将整个项目解压到固定目录，例如 `C:\Tools\WinScreen`。
2. 双击 `install.cmd`，首次安装需要联网。安装器创建独立 `.runtime`，编译原生启动器并追加当前用户 PATH。
3. 重新打开 CMD / PowerShell。VS Code 可能需要完全退出并重开。

```powershell
screen --version
screen -S train
```

进入新会话后运行自己的程序：

```powershell
conda activate myenv
cd C:\Work\project
python -u train.py
```

按 **Ctrl+A，松开，再按 D** 分离。此后关闭 CMD 窗口不会主动停止会话。再次连接：

```powershell
screen -r train
```

注意：仅打开面板，不会接管已经在普通 CMD 中运行的任务。关机、重启、注销、手动结束后台进程仍会终止任务；睡眠期间也不能持续计算。

## 常用命令

| 需求 | 命令 |
| --- | --- |
| 新建并进入交互会话 | `screen -S train` |
| 查看活动会话 | `screen -ls` |
| 重新进入 | `screen -r train` |
| 分离已有客户端后接管 | `screen -d -r train` |
| 后台执行程序 | `screen -dmS train python -u train.py` |
| 后台创建交互 shell | `screen -dmS train` |
| 停止会话及正常继承其 Job 的子进程 | `screen -S train -X quit` |
| 打开网页面板 | `screen --panel` |
| 查看完整参数 | `screen --help` |

程序位置参数直接作为 argv 传入，不再经过 CMD 的变量或运算符解析。要使用管道、重定向、`&&`、环境变量展开或批处理，必须显式选择 shell 命令：

```powershell
screen -dmS train --cwd 'C:\Work\project' python -u train.py
screen -dmS ps-job --command 'Write-Output "start"; python -u train.py'
screen -dmS cmd-job --shell cmd --command 'python -u train.py > train.log 2>&1'
```

`--cwd`、`--python`、`--shell` 等 WinScreen 选项放在程序名称前。不要在外层 PowerShell 用双引号包裹需要保留 `$变量` 的整条 shell 命令。

## 网页面板

`screen --panel` 或双击 `start-panel.cmd`，自动打开带本机认证的页面。可新建会话、交互输入、分离/重连、结束任务、下载日志、复制重跑命令。前端资源本地打包，不依赖运行时 CDN。

面板仅监听 `127.0.0.1:8765`。端口冲突时用 `screen --panel --port 8876`；已有面板运行时会重用已有实例。不要用公网隧道直接公开面板。

## 文档

- [完整使用说明](docs/USER_GUIDE.md)：安装、训练、Conda、命令、远程部署、故障排查、卸载与验收。
- [架构与生命周期](docs/ARCHITECTURE.md)：控制端、PTY worker、Job Object、日志、认证边界。
- [项目审查与验证](docs/PROJECT_REVIEW.md)：缺陷修正、测试对应关系、剩余限制。
- [安全说明](SECURITY.md)：可信用户模型、令牌、日志与报告边界。
- [开发与复现](CONTRIBUTING.md) / [更新记录](CHANGELOG.md) / [第三方组件](THIRD_PARTY.md)。

## 验证和打包

安装后运行：

```powershell
.\.runtime\Scripts\python.exe verify.py
.\.runtime\Scripts\python.exe package.py
```

真实结果保存在 [checks/verification.json](checks/verification.json) 和 [checks/test_output.log](checks/test_output.log)。测试使用独立临时目录，不结束用户会话。源码与日志哈希不一致、存在跳过测试或测试失败时，禁止打包。

CI 在 GitHub Windows runner 上执行安装、检查、测试与 ZIP 验证。新增 CI 配置不等于 CI 已通过；请以仓库 Actions 的实际运行状态为准。

## 明确不支持

- GNU screen 多窗口、分屏、`.screenrc` 与全部快捷键；`-R` 在本工具中只是 `-r` 的别名，不提供 GNU screen 的自动新建语义。
- 对普通终端既有任务的接管，跨重启/注销保活，训练自动保存/续训。
- 多用户公网托管、管理员权限隔离、任意 TUI 完整屏幕状态恢复。
- 保证所有服务器的 SSH 断线都不影响任务。限制 breakaway 的父 Job 会被明确拒绝，需要从服务器桌面启动面板。

默认数据在 `%USERPROFILE%\.winscreen`。日志不会自动轮转；不要输出密码、私钥或 token。源代码和发布 ZIP 不包含本机运行环境、实际会话、私有截图或个人服务器配置。

## 许可证

作者代码采用 [MIT License](LICENSE)。第三方组件保留各自的版权和许可，详见 THIRD_PARTY.md。
