# 开发、测试与发布

1. 克隆仓库并运行 install.cmd。开发环境与用户训练环境分开。
2. 修改后运行以下检查，不在真实训练会话中测试停止/中断：

```powershell
.\.runtime\Scripts\python.exe -m pip check
.\.runtime\Scripts\python.exe -m compileall -q screen.py winprocess.py terminal_input.py release.py verify.py package.py tests
node --check static/app.js
.\.runtime\Scripts\python.exe verify.py
.\.runtime\Scripts\python.exe package.py
```

Node 仅用于开发时的语法检查，不是运行依赖。verify.py 执行真实 Windows 集成测试与平台独立单元测试，使用各自的临时 WINSCREEN_DIR。测试创建的子进程由测试清理；不要把测试目录改成生产会话目录。

源码、终端资源、安装器、启动器、测试与发布工具的 SHA-256 在验证前后比较。源码变更、跳过测试、错误测试输出或日志校验失败均阻止发布。修改源码后必须重跑 verify.py，不能仅更改 passed 字段。

ZIP 不包含 .runtime、实际 sessions、令牌、个人截图或服务器配置。screen.exe 从 launcher.cs 构建，仅包含在发布 ZIP；Git 仓库保留可审查的源码。第三方前端资源和许可证一起打包。

提交前检查 git diff、git status 和待发布文件列表，排除个人路径与真实日志。不要提交任何密码、token、私钥；缺陷复现用虚构数据。

CI 配置在 .github/workflows/windows-ci.yml。它使用固定提交的官方 Actions、只读仓库权限，在 Windows 上分别测试 Python 3.11 和 3.13，保留真实验证与打包产物。CI 结果以 Actions 页面实际记录为准；本机通过不等于 CI 或远程服务器通过。
