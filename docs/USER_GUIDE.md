# WinScreen 使用说明

版本：1.0.1。适用于希望在 Windows 上长时间运行训练或其他命令行程序，并能分离、重连终端的用户。

## 1. 安装前确认

- Windows 10 1809+、Windows 11 或 Windows Server 2019+，64 位 Python。
- 固定 pywinpty 2.0.15 的预编译轮子覆盖 Python 3.9–3.13。推荐仍受维护的 Python 3.11–3.13；并非所有这些组合均已实测。
- 首次安装需联网访问 Python 包源；安装成功后前端、终端和面板不需要外部 CDN。
- 不必为了使用 WinScreen 安装 PyTorch；实际训练使用你自己的 Python / Conda 环境。

将完整项目放到固定目录，例如 `C:\Tools\WinScreen`，双击 `install.cmd`。不要只复制 `screen.exe`：它需要同目录源码、资源和运行环境。

安装器：

1. 检查 Python 最低版本与 64 位架构。
2. 在项目内创建独立 `.runtime`，只安装固定的 pywinpty 依赖。
3. 使用 .NET Framework C# 编译器构建原生 `screen.exe`；发布 ZIP 已含该启动器。
4. 将项目目录加入当前用户 PATH，不改系统 PATH、SSH、VS Code 或原 Conda 环境。

如果不希望更改用户 PATH，可在 PowerShell 中运行 `./install.ps1 -NoPath`；之后在安装目录使用 `./screen.exe` 或完整路径。本项目的全新安装验收采用这个选项，不污染用户 PATH。

安装批处理只为本次安装进程设置 PowerShell ExecutionPolicy Bypass，不修改机器或用户的持久执行策略。交互会话本身保留用户 profile，不通过关闭执行策略来启动 shell。

重新打开终端，执行 `screen --version`。如提示找不到命令，可先在项目目录运行 `.\screen.exe --version`。用 `where.exe screen` 检查是否被其他同名程序遮蔽。

## 2. 最推荐的训练方式

在普通终端输入：

```powershell
screen -S train
```

进入 WinScreen 后再运行：

```powershell
conda activate myenv
cd C:\Work\project
python -u train.py
```

开始正常输出后，按 Ctrl+A，松开，再按 D。现在可以关闭外层 CMD / PowerShell 或浏览器。再次查看：

```powershell
screen -ls
screen -r train
```

分离不发送中断，也不停止任务。它只是让客户端退出。不要把 Ctrl+C、会话中的 `exit` 或 `screen -S train -X quit` 当作分离操作。

仅打开面板，不会保活你在普通 CMD 里已经启动的训练。任务必须由 WinScreen 新建的会话启动。

## 3. 一条命令直接后台执行

```powershell
screen -dmS train --cwd 'C:\Work\project' python -u train.py
```

这里的 `python` 通过该会话继承的 PATH 查找。如果普通终端激活环境后启动 WinScreen，环境会继承；也可以显式选择环境：

```powershell
screen -dmS train --cwd 'C:\Work\project' --python 'C:\Miniconda3\envs\myenv\python.exe' python -u train.py
```

`--python` 设置环境 Python 所在目录、Scripts 和 Library/bin 的搜索路径及 CONDA_PREFIX，不等同于完整 `conda activate`。依赖 activate.d、特殊 DLL 或其他自定义环境变量的任务，应使用交互方式真实激活环境。

直接执行程序时，位置参数不会再经 CMD 解析。`%NAME%`、`&`、`|` 等只是参数内容；如需 shell 运算，请使用 `--command`：

```powershell
screen -dmS ps-job --command 'Write-Output "starting"; python -u train.py'
screen -dmS cmd-job --shell cmd --command 'python -u train.py > train.log 2>&1'
```

批处理 `.cmd/.bat` 或 PowerShell 脚本也使用显式 shell，例如 `--shell cmd --command 'call job.cmd'`。外层 PowerShell 中整条命令用单引号，防止 `$变量` 提前展开。

## 4. 命令与参数速查

| 命令 | 行为 |
| --- | --- |
| `screen -S NAME` | 创建命名交互会话并进入 |
| `screen -dmS NAME` | 后台创建交互会话，不进入 |
| `screen -d -m -S NAME` | 分开书写的后台选项 |
| `screen -ls` / `screen -list` | 查看活动会话，不包括已完成历史 |
| `screen -r NAME` | 进入名称、ID 前缀或完整 ID 对应的会话 |
| `screen -r` | 恰好一个活动会话时自动选择，否则提示指定名称 |
| `screen -d -r NAME` | 请求其他客户端分离后接管 |
| `screen -S NAME -d` | 分离当前已连接的客户端 |
| `screen -S NAME -X stuff TEXT` | 向会话发送原样文本，不补回车 |
| `screen -S NAME -X quit` | 停止会话，清理正常继承 Job 的子进程 |
| `screen --panel` | 打开认证后的本机面板 |
| `screen --panel --no-browser --port 8876` | 无浏览器启动面板，可自选端口 |
| `screen --cwd PATH` | 指定工作目录，放在程序位置参数前 |
| `screen --shell powershell/cmd` | 指定交互/命令字符串使用的 shell |
| `screen --python PATH` | 指定环境 Python，放在位置参数前 |
| `screen --help` / `screen --version` | 帮助与版本 |

同名活动会话不能重复创建；已结束的名称可以再次使用。名称有歧义时使用完整 ID。

PowerShell 中需要发送 Enter 的例子：

```powershell
screen -S train -X stuff "Write-Output 'hello'`r"
```

上例最后的反引号 r 表示实际回车。原生 `screen.exe` 正确传递这个字符；后备 `screen.cmd` 不适合传输含实际回车的参数。发送 `stuff` 相当于在目标终端输入命令，使用前确认会话和正在运行的程序。

## 5. 网页面板

双击 `start-panel.cmd` 或输入 `screen --panel`。正常入口会打开一次带访问令牌的本机 URL，再跳转到不含令牌的页面。

面板可以创建交互会话或执行 shell 命令，查看实时终端、分离/重连、下载日志、结束任务和复制重跑命令。完成、失败、主动停止的任务保留在历史中。归档接口隐藏历史记录，但不删除日志。

关闭浏览器不停止会话。停止面板服务也不停止 worker；随后重新启动面板可发现原会话。重新启动面板会轮换面板令牌，旧浏览器需通过 `screen --panel` 重新认证。

默认监听 `127.0.0.1:8765`。不要将它绑定或直接转发到公网。端口冲突时可用 8876；已有面板运行时会重用原实例，不会自动迁移它的端口。

## 6. 远程 Windows 服务器

服务器上必须安装同一项目。本地安装不能替代服务器安装。先在服务器验证 `screen --version`、新建、分离和重连，再用于真实长时间训练。

SSH 交互连接应分配终端，例如 `ssh -t my-server`。Windows SSH 可能把子进程放在断线即清理的 Job 中：

- 父 Job 允许 breakaway：worker 会主动脱离启动端 Job。
- 父 Job 不允许：明确报错，不会把可能随断线退出的任务假装成安全后台任务。

遇到 Job Object 错误，从服务器 Windows 桌面双击 `start-panel.cmd`，在桌面启动的面板内创建会话；再用 SSH 查看或重连。远程桌面仅断开连接与注销不同；不要注销承载训练的 Windows 用户。

本机查看服务器面板，先在服务器启动它，再在本机运行：

```powershell
ssh -N -L 18765:127.0.0.1:8765 my-server
```

用服务器 `%USERPROFILE%\.winscreen\panel.json` 中的令牌打开 `http://127.0.0.1:18765/?key=令牌`。设置了 WINSCREEN_DIR 时使用相应目录。不要把实际令牌粘贴到公开截图、Issues 或文档。SSH 隧道关闭只影响面板访问；后台存活仍受服务器实际 Job 与用户会话限制。

本项目发布前没有在用户真实服务器上完成 SSH 断线或 GPU 训练验收，不能宣称已验证。服务器验收清单见第 10 节。

## 7. 会话、日志与存储

默认：`%USERPROFILE%\.winscreen`。

| 文件 | 内容 |
| --- | --- |
| `sessions/ID/session.json` | 启动命令、工作目录、状态、退出码、进程身份、worker 令牌 |
| `sessions/ID/terminal.log` | UTF-8 终端输出，包含 ANSI 控制序列 |
| `sessions/ID/worker.log` | 后台启动或运行错误 |
| `panel.json` / `panel.log` | 面板身份、访问令牌、启动错误 |

需要改变目录时设置 WINSCREEN_DIR 为固定绝对路径；所有客户端和服务器启动入口必须一致，否则会看到不同的会话池。默认用户目录避免某些打包应用对 AppData 的文件重定向。

输出日志不自动轮转，长期训练必须监控磁盘空间。日志可能保存你输入、回显或输出的敏感内容。共享前人工检查并脱敏。网页的滚动缓冲区与磁盘日志不是同一大小；重连默认回放日志尾部，不保证恢复任意全屏 TUI 的完整屏幕状态。

## 8. 故障排查

| 现象 | 首先检查 |
| --- | --- |
| 找不到 screen | 重开终端或重启 VS Code；检查用户 PATH 与 `where.exe screen` |
| 无法激活 Conda | 先在普通对应 shell 中完成 Conda 初始化；WinScreen 不代你修改 profile |
| 同名会话错误 | `screen -ls`，重连原任务或使用其他名称 |
| 会话已经进入 | 用 `screen -d -r NAME` 明确接管 |
| 找不到原会话 | Windows 用户、WINSCREEN_DIR 是否相同；任务是否已经结束 |
| 端口冲突 | 改面板端口，查看 panel.log；已有实例会被重用 |
| 请先运行 screen --panel | 认证已过期；通过正常启动入口重新打开 |
| 启动失败 | 查看 worker.log、执行程序路径、cwd 与环境；失败状态不能当作运行中 |
| Job Object 错误 | 使用服务器桌面启动面板；不要绕过后继续假定断线安全 |
| 终端输入异常 | 确认原生 screen.exe 优先；运行 verify.py，提供脱敏测试输出 |
| 训练停止或 failed | 查看退出码和终端错误，确认没有执行 quit、注销、重启或结束后台进程 |
| 记录损坏 | 错误结构的 session/panel 记录不会让整个列表崩溃；原文件保留，先备份再人工排查 |

退出码 0 通常对应 completed，程序非零退出对应 failed，明确结束对应 stopped，进程身份不匹配或意外消失显示 interrupted。completed 只说明被启动的程序正常退出，不代表训练指标正确或所有业务步骤完成。

## 9. 升级与卸载

升级前分离客户端、保存训练检查点。不要在长任务运行时移走旧安装目录、删除其运行环境或卸载 Python。已运行的 worker 不会自动换成新代码；新版本启动新会话后才使用新实现。新旧版本使用同一 WINSCREEN_DIR 时仍应先验收兼容性。

卸载前结束自己的 WinScreen 会话并停止面板，再从当前用户 PATH 移除安装目录，最后删除安装目录。会话目录与日志默认保留；用户确认不再需要后再单独清理。项目不注册 Windows 服务或开机任务，不修改 SSH/VS Code/原 Conda 环境。

## 10. 上服务器前的验收清单

1. 新建一个测试会话，连续输出计数，不运行真实训练。
2. Ctrl+A D 分离，关闭启动 CMD，再重连，确认计数继续增长。
3. 网页面板关闭、重新打开，确认会话仍可发现和交互。
4. 若通过 SSH 启动，真实断开 SSH 并等待，再重新连接确认任务存活。
5. Ctrl+C 中断测试程序，确认交互 shell 保留；quit 只清理测试会话子进程。
6. 确认使用同一 Windows 用户、工作目录、Conda 环境，且有足够磁盘空间。
7. 最后运行真实短训练，再决定是否投入长时间任务；应用自身仍需保存检查点。

本机自动化测试通过不能替代以上目标服务器验收。
