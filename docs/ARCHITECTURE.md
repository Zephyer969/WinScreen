# 架构与任务生命周期

## 组件边界

WinScreen 有三个独立生命周期：CLI 客户端、网页面板服务、每个会话的 worker。启动器 screen.exe 只正确转发参数；真正持有 ConPTY 和任务 Job 的是后台 Python worker。

```text
CMD / PowerShell → screen.exe → CLI 客户端 ─────────┐
                                                  ├→ worker → ConPTY → 程序或 shell
浏览器 → 127.0.0.1 面板 → 认证后的请求代理 ────────┘       └→ 独立会话日志和状态
```

客户端关闭或分离时不关闭 worker 的 Job。面板重启也不拥有或清理会话。worker 退出时，其 KILL_ON_JOB_CLOSE Job 清理正常继承的 shell 与子进程。

## 进程生命周期

- 启动后台使用 DETACHED_PROCESS，不继承外层控制台。没有使用 CREATE_NEW_PROCESS_GROUP，因为微软说明该标志会让新进程组默认忽略 Ctrl+C。
- 如果启动进程在父 Job 中，尝试 CREATE_BREAKAWAY_FROM_JOB。父 Job 禁止脱离时明确拒绝，并提示从 Windows 桌面创建会话。
- worker 持有自己的 KILL_ON_JOB_CLOSE Job，使用 PID 加进程创建时间辨别身份，避免 PID 复用误操作。
- 服务、WMI 等另起的外部进程不一定属于同一个 Job；这里不是任意 Windows 进程的终止器或权限沙箱。

以上依据 [Microsoft Process Creation Flags](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags) 与 [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)。在目标 SSH 服务中是否允许 breakaway，必须实测。

## 输入与输出

ConPTY 提供真实终端，不是 stdin/stdout 文本拼接。CLI 读控制台按键，浏览器使用本地 xterm.js。针对 ConPTY 协商的 Win32 输入模式，terminal_input.py 编码键按下/释放、虚拟键和 UTF-16 字符，保留焦点/光标等终端协议；Ctrl+C 保留 ConPTY 的 ETX 中断路径。

网页写入按 Promise 队列顺序提交，防止快速输入的独立 HTTP 请求乱序。重连读取 UTF-8 日志并由终端解释 ANSI；这不是完整终端屏幕快照，某些复杂全屏 TUI 可能需要重绘。

## 执行方式

- 交互模式：启动真实 PowerShell/CMD，继承用户环境与 shell profile。
- 程序位置参数：保存结构化 argv，查找可执行程序后直接由 ConPTY 启动，避免 CMD 对百分号和运算符再解析。
- 显式 shell 命令：PowerShell 用 EncodedCommand，CMD 用生成的 run.cmd。管道、重定向等 shell 语义由用户主动选择。

历史 1.0.0 的 command 字符串记录仍可读取。新结构化 argv 保留在 session.json 中，面板复制与 rerun 同样保留参数。

## 状态与持久化

创建、列表和面板发现使用用户私有会话目录。JSON 通过临时文件和原子替换更新，遇到短暂 Windows 文件读取冲突会重试。新建同名会话由有界锁串行检查。客户端租约定期更新，过期客户端不被视为继续附着；detach-all 更新分离 epoch。

状态包括 starting、running、completed、failed、stopped、interrupted。程序退出会保留日志，不把退出成功等同于应用业务成功。损坏且结构不合法的记录不会阻断整个列表，也不会被自动覆盖。

## 信任边界

面板和 worker 只监听 loopback。worker 请求需要 Bearer 令牌；面板以一次性 URL 引导 Cookie 认证，修改请求另需 CSRF 令牌，限制 localhost Host、请求体大小和连接空闲时间。令牌不出现在公开状态记录中。

这仍是可信 Windows 用户的任意命令执行工具。能读取会话文件、控制同一用户进程或操作本机浏览器的主体，不在它的隔离威胁模型之外。不要将服务作为公网、多租户终端。
