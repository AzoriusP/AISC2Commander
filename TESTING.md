# 构建与测试指南

本项目目前面向 Windows 源码测试。仓库不提交预构建 EXE；测试者需要在完整项目目录中创建运行环境并自行构建。

## 环境要求

- Windows 10/11 64 位
- PowerShell 5.1 或 PowerShell 7
- Git
- Python 3.11 或更高版本（64 位），并确保 `python` 命令可用
- 已安装并可正常运行的 StarCraft II
- 网络连接，用于首次安装 Python 依赖和获取 Blizzard 官方仓库
- 可选：麦克风、本地 Ollama，或者测试者自己的 OpenAI API Key

不要在 Issue、截图或日志中提交 API Key、访问令牌、个人地图路径或其他敏感信息。

## 从零安装

```powershell
git clone https://github.com/AzoriusP/AISC2Commander.git
cd AISC2Commander
.\scripts\bootstrap.ps1
```

如果 PowerShell 阻止本次会话运行脚本，可以仅为当前进程临时放行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

需要本地 Whisper 语音转写时，再执行：

```powershell
.\scripts\setup-voice.ps1
```

需要使用 Ollama 或 OpenAI 时，从对应的 `.example` 文件创建本地配置。真实配置文件已经被 `.gitignore` 排除：

```powershell
Copy-Item config\llm.env.example config\llm.env
Copy-Item config\openai.env.example config\openai.env
```

本地规则模式不需要 API Key。不要把真实 Key 提交到 Git。

## 构建 Windows GUI

先完成 `bootstrap.ps1`，然后执行：

```powershell
.\scripts\build-gui.ps1
```

成功后会在仓库根目录生成：

```text
AISC2CommanderGUI.exe
```

该 EXE 只打包桌面 GUI，不是完全独立的绿色发行包。运行和启动对局时仍需要完整仓库、`.venv`、脚本、配置以及 StarCraft II。EXE 和构建目录已被 Git 忽略，不应提交或再发布。

## 第一级：不启动游戏的自动测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

预期结果：全部测试通过，命令以退出码 `0` 结束。

## 第二级：GUI 人工检查

1. 运行 `AISC2CommanderGUI.exe`。
2. 确认“设置”菜单可以打开 API Key、多语言和关于窗口。
3. 切换简体中文、繁體中文和 English，重启 GUI 后确认设置仍然保留。
4. 不填写真实 Key 也可以检查配置窗口；如需填写，只使用测试者自己的 Key。
5. 打开地图点位、战术指令集和对局配置窗口，确认界面无异常退出。
6. 关闭 GUI；如果已经启动 Commander/SC2，请先使用“强制停止”。

## 第三级：真实 StarCraft II smoke test

以下测试会启动 StarCraft II，并使用官方 Debug API 准备隔离测试场景：

```powershell
.\.venv\Scripts\python.exe -m aisc2commander smoke --verbose
.\.venv\Scripts\python.exe -m aisc2commander smoke --race protoss --no-opponent
.\.venv\Scripts\python.exe -m aisc2commander smoke --race zerg --no-opponent
```

超过一分钟的连接稳定性测试：

```powershell
.\.venv\Scripts\python.exe -m aisc2commander smoke --soak-seconds 75
```

测试完成或失败时，程序会尝试通过官方 API 退出并回收自己启动的进程。

## 提交 Issue

提交前请搜索是否已有相同问题，并包含：

- 简短、明确的问题标题
- 可稳定复现的步骤
- 预期结果与实际结果
- Windows、Python、StarCraft II 版本
- 使用的规则/Ollama/OpenAI 模式，但不要提供 Key
- 已删除敏感路径、玩家信息和令牌的相关日志片段

此仓库只接受 Issue 形式的错误报告和建议，不接受未经邀请的代码补丁或 Pull Request。许可边界见 [LICENSE](LICENSE)。
