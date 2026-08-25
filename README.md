# StarCraft II AI Commander

这是一个只使用 Blizzard 官方 StarCraft II API 的 Windows 玩法验证原型。它以 realtime 模式读取 Observation，显示资源、单位、orders、API Action Error、当前选择和官方控制编组。独立 Agent Harness 已覆盖 Terran、Protoss、Zerg 标准 Melee 的移动、攻击、生产/变形、工人建造、科技、编组和官方当前可用能力，并提供带条件、重复、保持、并行、优先级和抢占的持续任务；桌面界面还提供地图点位编辑与本地 Whisper 中英文语音转写。

> **许可说明：** 本仓库源码公开可见，但不是开源软件。仅允许非商业下载、构建、测试和本地使用；禁止商业使用、修改授权和再发布。欢迎通过 GitHub Issues 提交错误报告与建议。完整条款见 [LICENSE](LICENSE)，构建和测试步骤见 [TESTING.md](TESTING.md)。

## 合规与架构

- 通信协议是 Blizzard 官方 [`s2client-proto`](https://github.com/Blizzard/s2client-proto) 的 protobuf，通过官方 `/sc2api` WebSocket 端点发送。
- 双人联机直接使用官方 `RequestCreateGame` / `RequestJoinGame`、`host_ip`、`server_ports` 和 `client_ports`：一名玩家的 SC2 实例创建游戏，另一名玩家的 SC2 实例加入，传输、锁步同步、数据校验和错误处理均由 SC2 完成。项目不实现自有网络协议。
- `s2clientprotocol` PyPI 包由 Blizzard 官方仓库生成；版本固定为 `5.0.16.97563.0`。
- 地图来自 Blizzard 官方 [`s2client-api`](https://github.com/Blizzard/s2client-api) 仓库。
- 不读取 SC2 进程内存，不注入 DLL，不使用逆向、外挂接口或社区 bot 框架。
- 所有进程启动和 SC2 网络通信均封装在 `src/aisc2commander/sc2/`。

## Agent Harness

Harness 位于 `src/aisc2commander/agent/`，与 SC2 通信模块分离：

```text
中文或英文文本 / 持续语音监听
        │
        ├─ 本地 VAD 静音切句
        ├─ 常驻 faster-whisper（默认）/ gpt-transcribe（可选）
        ▼
本地中英文规则快速路径 / Ollama Qwen / GPT-5.6 fallback
        │  只允许结构化工具调用
        ▼
AgentActionExecutor（最新 Observation 再校验）
        ▼
SC2Session（官方 protobuf / WebSocket）
```

目前有 12 个白名单工具：`move_units`、`attack_units`、`use_unit_ability`、`use_ability`、`toggle_autocast`、`train_units`、`build_structure`、`research_upgrade`、`operate_building`、`manage_control_group`、`schedule_task`、`control_tasks`。模型不能执行代码、不能直接发送 protobuf、不能提供数字 ability id，也不能调用 `DebugCreateUnit`。执行器还会：

- `selected` 在玩家提交指令的瞬间绑定完整 unit tags；模型处理期间即使玩家改选其他单位，执行仍使用这些固定 tags，并从动作执行瞬间的最新 Observation 读取它们的位置、生命、orders 与可用能力。已经死亡、变为敌方或不再存在的 tag 会被明确拒绝。
- `random` 从最新 Observation 中匹配玩家点名的单位类型，并用官方 `QueryAvailableAbilities` 排除当前不能移动的候选后只绑定一个 tag；这是动作主体解析，不会改变玩家在 SC2 画面中的鼠标选择。
- 无主语生产默认使用 `any_available`：执行器先用官方当前可用能力找出能生产目标单位的建筑或变形来源，再绑定其中一个；只有玩家明确说“所有”或“随机”时才扩展为全部或随机生产者。
- 校验世界坐标在地图 playable area 内。
- 限制一次模型响应最多 4 个动作；持续生产目标最多 200 个单位。
- 持续生产任务根据 Observation 中的现有单位、生产 orders、资源、人口和队列反复推进；支持 Terran 建筑、Protoss 建筑/折跃门落点以及 Zerg Larva/单位变形来源。
- 建造前重新选择匹配的 SCV、Probe 或 Drone，检查资源、科技、地图范围，并调用官方 `RequestQueryBuildingPlacement` 验证落点。
- 单位模式与建筑操作先调用官方 `QueryAvailableAbilities`，只发送当前单位真实可用的 ability；建筑降落还会额外查询官方落点。
- 通用能力通过 `RequestData.AbilityData` 的名称、remap、目标类型和 autocast 标志解析，只在 `QueryAvailableAbilities` 确认当前可用后执行。
- 持续任务由本地运行时按 Observation 判断条件，提供去重幂等、非冲突并行、冲突阻塞、优先级抢占、失败退避、最大次数与超时。
- 等待触发任务不会逐帧调用 LLM，也不会发送额外 SC2 查询；每个已有 Observation 只建立一次单位数量、单位 tag 和控制编组索引，所有等待条件共享该索引，满足后才派发一次后续动作。
- 任何即时或延迟 API Action Error 都写入详细日志。

模型接口采用 OpenAI 官方 [Responses API function calling](https://developers.openai.com/api/docs/guides/function-calling) 的严格 function tools。[`gpt-5.6`](https://developers.openai.com/api/docs/models/gpt-5.6-sol) 本身在此流程中接收文本；短语音先按官方 [speech-to-text](https://developers.openai.com/api/docs/guides/speech-to-text) 流程用 `gpt-transcribe` 变成界面所选语言的文本。模型与转写调用都在后台线程运行，不阻塞 realtime Observation 循环。

Harness 始终先使用 `zh-rules-v1` 尝试中英文确定性快速解析，即使已经配置 Ollama/OpenAI 也不会让所有指令都经过 LLM。规则能够完整生成白名单工具调用时直接执行，例如“选中的建筑制造 5 个农民”“来一个农民去 A1”“刷 5 个机枪兵”“去 A1 开二矿”，以及 `Move the selected Marines to A1`、`Train five Marines`、`Build a Supply Depot at A1`；规则无法完整覆盖的新口语才携带当前选择、单位计数、种族、地图点位和最近计划交给 Ollama/OpenAI 推断。LLM 可以省略具体 unit tag，并用 `random`、`any_available`、`nearest`、`nearby` 表达模糊主体；执行器随后在最新 Observation 中解析兼容对象并再次验证，避免模型臆造单位。只有动作、目标或多个高风险解释无法由游戏状态消歧时才询问玩家。当前仓库的 `config/llm.env` 已按本机需求显式设为 `ollama`。

模糊指令的当前语义包括：

- `来一个农民去A1` / `随机选择一个农民去A1`：从当前可移动的对应种族工人中随机绑定一个并移动。
- `刷5个机枪兵`：无需先选兵营，从官方能力确认能训练 Marine 的一个生产建筑创建持续生产任务。
- `去A1开二矿`：按玩家种族推断 `CommandCenter`、`Nexus` 或 `Hatchery`，选择具备建造能力且接近目标的工人，直接提交一条建造动作，不额外发送移动命令。
- `选择的农民在附近建一个精炼厂`：以提交指令时绑定的工人为中心，只在当前 Observation 可见的中立气矿中选择最近目标。附近普通建筑会在工人 6 格视野范围内生成候选，并把所有候选合并为一次 Blizzard `RequestQuery` 批量验证；没有合法落点时立即终止并显示官方放置错误。

等待触发型指令也会被编译为一次性的本地任务：

- `第一个农民造好后在路口（A1点）放下补给站`：创建任务时记录现有工人 tags；Observation 首次出现新增工人时，把新增 tag 动态绑定为“选中的工人”，随后在 A1 执行正常建造。已有工人不会误触发，也不会随机换成另一个工人。
- `1号部队包含5个女妖后前往B1点`：等待官方 `ObservationUI.groups` 显示 1 号编组的队长类型为 Banshee 且总数达到 5，再召回 1 队并移动到 B1。
- 两类事件任务默认 600 秒超时；等待期间任务状态显示当前新增数量或编组数量，满足、超时、动作失败和完成都会进入现有消息反馈流。

Blizzard 的被动控制编组 UI 只提供组号、队长类型和总数量，不提供完整成员 tags。因此“5 个女妖”的编组条件对纯女妖编组是精确的；如果 1 队是混合编组，程序只能确认“队长是女妖且总数达到 5”，无法被动证明其中恰有 5 个女妖。程序不会为补齐这个缺口读取内存，也不会持续召回编组干扰玩家选择。

## 标准 Melee 三族操作层

操作层不维护一份脆弱的固定 ability id 表。生产、建造和升级从官方 `RequestData` 取得 ability，特殊技能从当前单位的官方 `QueryAvailableAbilities` 中按名称和 remap 解析，再按 `AbilityData.target` 检查它需要无目标、世界坐标还是单位目标。

| 范围 | 当前支持 | 指令示例 |
| --- | --- | --- |
| Terran | 全套标准单位和建筑生产、SCV 建造、附件、起降/模式、常用科技和主动能力 | `选中的坦克进入攻城模式` |
| Protoss | Probe 建造、Gateway/Robot/Stargate 生产、Warpgate 指定落点折跃、建筑变形、攻防科技和主动能力 | `折跃3个狂热者到A2` |
| Zerg | Drone 建造、Larva 生产、Zergling/Roach/Corruptor 等单位变形、Hatchery/Lair/Hive 与 Spire 变形、攻防科技和主动能力 | `孵化场升级为虫穴` |
| 通用作战 | 移动、攻击、停止、坚守、巡逻、单位/点位目标技能、装卸、取消和自动施法 | `虫后给我方孵化场注卵` |
| 官方编组 | 设置、追加、召回、设置并移除、追加并移除 1–10 队 | `把选中的单位编为2队` |
| 持续任务 | 条件触发、固定间隔、重复次数、保持数量、最多 4 个非冲突并行任务、优先级抢占与暂停/恢复/取消 | `保持70个农民` |

这里的“完整”边界是 Blizzard 标准 Melee 中由当前官方数据明确提供的操作。未知 Arcade 地图的自定义 ability 名称和语义无法可靠预置；只有当当前 `RequestData` 和目标类型可以明确解析时才会执行，绝不通过内存读取或注入补齐。

形态变化后的单位仍按同一单位族解析。例如 `SiegeTank`/`SiegeTankSieged`、`WidowMine`/`WidowMineBurrowed`、`VikingFighter`/`VikingAssault`、`Liberator`/`LiberatorAG`、`SupplyDepot`/`SupplyDepotLowered` 和建筑的 Flying 形态不会因为类型名变化而失去控制。

玩家正常选中带附件的 Barracks、Factory 或 Starport 时，程序读取官方 raw Unit 的 `add_on_tag`，自动把相连的 Tech Lab 纳入科技能力查询。因此可以直接说“选中的兵营研究兴奋剂”，不需要另建一套附件选择系统。

执行与反馈规则：

- 多条指令获得独立 `CMD-xxxx` 编号并按 FIFO 进入模型队列；新模型请求不会静默打断前一条。
- 每条指令在界面记录“提交时单位 ID”，并显示排队、规则优先解析（必要时调用 LLM）、规则预检、执行、持续等待、完成或终止；计划生成后会标明实际使用的是本地规则还是模型。
- 生产根据最新 Observation 显示 `已完成/目标数`；建筑根据 raw `build_progress` 显示百分比。
- 科技根据研究建筑的 UnitOrder 显示百分比，并以 `completed_upgrade_ids` 作为最终完成依据。
- 建筑和降落位置使用官方 `RequestQueryBuildingPlacement` 预检；资源、科技、类型、形态或落点不满足时立即返回失败原因。
- SC2 返回的即时和 Observation Action Error 都会写入日志并反馈到对应任务。

当前 133 项回归测试覆盖模糊中英文解析、固定界面语种转写、随机可移动主体、隐式/随机生产者、三族开二矿推断、附近可见气矿、批量官方落点查询、新单位完成事件、动态 tag 绑定、控制编组数量触发、环境噪声校准与持续噪声强制切句、三族单位/建筑/科技别名、官方 ability remap 与目标约束、Warpgate 落点、Zerg Larva 刷新、建筑变形、`add_on_tag` 研究路由、生产/建筑/科技进度、持续任务幂等/并行/抢占、控制接口、多电脑与多人官方 `PlayerSetup`/`JoinGame` 拓扑、GUI 多语言设置与资源打包、菜单轮询隔离，以及官方 SC2Map 内嵌截图解析。最新桌面程序构建为 `AISC2CommanderGUI.exe`。

### 本地 Ollama / Qwen3.5

本地模型配置位于 `config/llm.env`：

```text
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
OLLAMA_MODEL=qwen3.5:9b
OLLAMA_API_KEY=ollama
OLLAMA_TIMEOUT=120
```

Ollama 本地 endpoint 不验证 `OLLAMA_API_KEY`，但 OpenAI Python 客户端要求提供一个非空值，所以使用占位值 `ollama`。该配置使用 Ollama 0.13.3+ 原生支持的非状态 `/v1/responses` 和 function tools；不会把 Qwen 的自由文本当作 SC2 命令，只有结构化白名单 tool call 才可能执行。

启动前检查：

```powershell
setx OLLAMA_MODELS "D:\OllamaModels"
ollama serve
ollama list
```

执行 `setx` 后需要重启旧的 Ollama 服务。示例将模型目录改为 `D:\OllamaModels`；请按自己的磁盘位置调整。

`OLLAMA_MODEL` 必须与 `ollama list` 显示的完整 tag 一致。当前项目配置使用 `qwen3.5:9b`；如果本机修改了模型 tag，需要同步修改 `config/llm.env`。确认后直接运行：

```powershell
.\scripts\run.ps1 --map 'D:\Maps\MyCustom.SC2Map'
```

程序会在启动 SC2 之前检查 Ollama endpoint 和模型 tag。无法连接或模型不存在时会立即停止并给出已检测到的模型，不会先打开游戏。Qwen 推理在 Agent 后台线程运行，不阻塞 realtime Observation。

本地 Ollama 只替换“自然语言文本 → 游戏工具计划”这一段；当前 Qwen 模型没有音频输入能力。桌面 GUI 默认先用隔离运行环境中的 `faster-whisper small` 在本机转写，再把文字交给 Qwen。它不使用 Windows `System.Speech`，也不需要 OpenAI Key。需要云端转写时可在 `config/voice.env` 中改为 `VOICE_TRANSCRIPTION_PROVIDER=openai`。

## 安装与运行

仓库不提交预构建 EXE。测试者需要使用 Windows、Git 和 64 位 Python 3.11 或更高版本，在完整项目目录中运行以下 PowerShell 命令：

```powershell
git clone https://github.com/AzoriusP/AISC2Commander.git
cd AISC2Commander
.\scripts\bootstrap.ps1
.\scripts\setup-voice.ps1
.\scripts\build-gui.ps1
.\AISC2CommanderGUI.exe
```

`setup-voice.ps1` 只在需要本地 Whisper 语音转写时执行。命令行方式仍可直接运行：

```powershell
.\scripts\run.ps1 --map 'D:\Maps\MyCustom.SC2Map'
```

完整的环境要求、分级测试和 Issue 提交格式见 [TESTING.md](TESTING.md)。

### Windows 桌面界面

执行 `build-gui.ps1` 后会在当前本地仓库根目录生成：

```text
AISC2CommanderGUI.exe
```

这个 EXE 不会提交到 GitHub，也不是完全独立的绿色发行包；运行时仍需保留完整项目目录、`.venv`、脚本和配置。双击后可以：

- 点击“开启对局”：先选择单机、创建联机对局或加入联机对局。单机与联机主机继续选择本地 `.SC2Map` 或已发布/缓存的 Battle.net 地图，并选择种族；加入方只填写主机 IPv4、联机起始端口和自己的种族，地图由主机的官方 `RequestCreateGame` 决定。
- 选择地图后，界面会通过官方 `RequestCreateGame` 容量校验动态生成电脑槽位；首次读取会临时启动并关闭一个 SC2 API 进程，结果按地图文件签名缓存在 `config/map_capacity.json`。每个有效电脑槽位可选择种族、官方难度和官方 AI Build（Random/Rush/Timing/Power/Macro/Air）；种族选择“无”时不创建该电脑。
- 启动时界面会持续探测本地控制接口；首次连接失败不会让状态卡死，成功后显示“已连接”，超过 60 秒仍未就绪会明确显示启动失败。
- 主界面的“指令运行状态”按编号显示每条命令的排队、规则优先解析、规则预检、执行、持续等待、完成或终止状态；计划生成后会显示“本地规则”或实际模型 provider。短时间连续发送的命令按 FIFO 顺序执行，不会静默互相打断。
- 点击“强制停止”：先请求 Commander 正常退出并通过官方 SC2 API 关闭游戏；8 秒内未退出时，只按本次启动所记录并校验过的精确 PID 终止 Commander、SC2 和对应 PowerShell 进程。
- 从顶部“设置 → 配置 API Key”打开密钥弹窗；保存位置仍为 `config/openai.env`，保存后弹窗自动关闭，消息区显示脱敏后的配置和生效提示，已经运行的项目需停止并重新启动。主界面不再重复显示 API Key 按钮。
- “设置 → 多语言”可以切换简体中文、繁體中文和 English；选择保存在 `config/gui_settings.json`，重新打开程序后仍会生效。语音不做中英自动检测：English 界面固定传给转写器 `en`，简体/繁体中文界面固定传入 `zh`；切换后从下一次录音或监听会话生效。
- “设置 → 关于”显示项目介绍、GitHub 地址和支付宝／微信支持二维码。项目仓库地址填写在 `config/about.json` 的 `github_url`；二维码资源位于 `assets/about/`，并会随单文件 GUI EXE 一同打包。
- 在底部输入自然语言，点击“发送指令”或按 `Ctrl+Enter`。
- 点击“开始录音”：手动开始录制一段语音，再次点击“停止录音”后转写到文字输入框；普通指令不会自动发送，可以先修改再点击“发送指令”。精确匹配的战术指令集名称、别名或计划控制口令会直接发送。
- 对局连接后点击“开始监听”：麦克风保持监听，按钮切换为“停止监听”。本地 VAD 检测到默认约 0.8 秒静音后自动切分一句，不需要再次点击按钮。
- 每句语音由常驻 Whisper 依次转写，并通过和键盘“发送指令”相同的 `/command` 队列自动提交；消息区显示识别文字、玩家指令和对应任务编号。后续仍然先走本地规则快速路径，规则无法完整解析时才调用 Ollama/OpenAI。
- 点击“停止监听”：立即停止采集；已经切分以及停止时尚未结束的最后一句仍会完成转写和发送。
- “开始录音”和“开始监听”并排显示，两个模式互斥；一个模式正在使用麦克风或转写时，另一个按钮会暂时禁用。
- 点击“地图点位”无需启动项目：先选择本地 `.SC2Map` 或 Battle.net 地图名，再进入点位编辑器。每张地图可以创建多套点位预设，通过下拉框切换当前预设，并可新增、重命名和保存；当前启用预设会提供给 Agent。
- 在消息框中分别以蓝色“玩家”、绿色“AI”和灰色“系统”显示事件。

GUI 通过仅绑定 `127.0.0.1:8765` 的本地控制接口连接 Commander；SC2 通信仍只存在于 `src/aisc2commander/sc2/`。直接关闭 GUI 窗口不会关闭正在运行的 SC2/Commander；需要同时关闭游戏和项目时请点击“强制停止”，也可以在运行终端输入 `quit` 正常结束。

需要重新构建 EXE 时运行：

```powershell
.\scripts\build-gui.ps1
```

构建使用 PyInstaller 单文件 `--windowed` 模式，结果会复制到项目根目录，且不会删除或替换 `run.ps1` 的逻辑。

要启用 GPT-5.6 或可选的 OpenAI 云端语音转写，打开 `config/openai.env`，把 Key 写在等号后面：

```text
OPENAI_API_KEY=sk-你的API-Key
```

然后直接运行：

```powershell
.\scripts\run.ps1 --map 'D:\Maps\MyCustom.SC2Map' --agent-provider openai --model gpt-5.6
```

`config/openai.env` 已加入 `.gitignore`，程序不会把密钥内容写入日志。系统环境变量 `OPENAI_API_KEY` 的优先级更高；这也符合 OpenAI SDK 默认从环境变量读取 Key 的方式。

没有 `config/llm.env` 覆盖时，CLI 默认 `--agent-provider auto`：有 Key 时使用 OpenAI，没有时使用本地规则。当前项目的 `config/llm.env` 已显式选择 Ollama。也可以显式离线运行：

```powershell
.\scripts\run.ps1 --map 'D:\Maps\MyCustom.SC2Map' --agent-provider rules
```

程序会优先从 `%USERPROFILE%\Documents\StarCraft II\ExecuteInfo.txt` 和 Windows 常用安装目录寻找 SC2。自定义安装位置也可显式指定：

```powershell
.\scripts\run.ps1 --map 'D:\Maps\MyCustom.SC2Map' --sc2 'D:\Games\StarCraft II\Versions\Base97579\SC2_x64.exe'
```

SC2 窗口出现后，不需要点“加入”或“开始”：API 会使用所选自定义地图创建 realtime 游戏并以界面选择的种族作为 participant 加入。直接在游戏窗口中正常使用鼠标：

- 单击 Command Center 等建筑，终端会显示 `CommandCenter x1`。
- 框选或点击多个单位，终端会按类型显示数量并输出对应 unit tags。
- 每秒输出 minerals、gas、supply 和所有我方单位的 tag、type、position、health、orders。
- 完整 DEBUG 记录写入 `logs/aisc2commander.log`。

## 交互测试命令

```text
list
move 80 40 all
move 80 40 selected
move 80 40 123456789,123456790
move 80 40 123456789,123456790 queue
quit
```

`move` 只接受当前我方 Marine；显式 tag 中出现非 Marine 或过期 tag 时，整条命令会拒绝，避免误操作其他单位。

方便玩法验证的两个附加命令也只走官方 API：

```text
spawn-marines 8 80 40
select-army-test
```

前者使用官方 `DebugCreateUnit`，后者使用官方 `ActionSelectArmy`。它们只用于测试，不是 selection workaround。

## 中英文和语音控制

游戏运行后，在同一个终端输入：

```text
ai 让这些枪兵移动到坐标 36 134
ai 让选中的单位向右走10格
ai 所有枪兵攻击敌人
ai 生产4个枪兵
ai 来一个农民去A1
ai 刷5个机枪兵
ai 去A1开二矿
ai 第一个农民造好后在路口（A1点）放下补给站
ai 1号部队包含5个女妖后前往B1点
ai 让最近的农民在坐标 36 134 建造补给站
ai 让选中的农民在坐标 42 128 建造兵营
ai 让最近的农民在最近气矿建造精炼厂
ai 选择的农民在附近建一个精炼厂
ai 让一队移动到A1
ai 选中的建筑生产19个农民
ai 选中的工程站升级步兵武器
ai 选中的坦克进入攻城模式
ai 所有寡妇雷钻地
ai 一队巡逻到A1
ai 所有兵营把集结点设到A2
ai 选中的兵营起飞
ai 选中的兵营降落到A1
ai 所有补给站下降
ai 选中的基地升级为轨道指挥部
ai 选中的兵营建造科技实验室
ai 选中的工程站升级建筑护甲
ai 折跃3个狂热者到A2
ai 所有追猎者使用闪现到A1
ai 孵化场升级为虫穴
ai 研发跳虫速度
ai 把选中的单位编为2队
ai 召回2队
ai 保持70个农民
ai 当矿物达到500时生产4个追猎者
ai 每10秒生产1个王虫
ai 重复3次：所有跳虫攻击A2
ai 查看持续任务
```

中文行也可以不写 `ai` 前缀。`生产` 使用正常 Train ability。数量目标会持续保留：资源、人口或生产队列暂时不足时显示“等待”，条件满足后继续，直到 Observation 确认新增数量达到目标；不会生成 CHEAT 提示。

常用英文命令也会先走本地规则快速路径，例如：

```text
Move the selected Marines to A1
Train five Marines
Build a Supply Depot at A1
Attack the nearest enemy with all Marines
Research Stimpack
Control group 1 move to B1
When the first worker is ready, build a Supply Depot at A1
When control group 1 has 5 Banshees, move to B1
```

`建造` 同样使用 SCV、Probe 或 Drone 的正常 Build ability，不会 Debug 创建建筑。普通建筑可以给世界坐标/地图点位，也可以说“附近”让执行器在已解析工人的当前视野内搜索；Refinery、Assimilator 或 Extractor 可以给气矿坐标、说“最近气矿”，或说“附近”选择该工人附近当前可见且未占用的中立气矿。“选中的农民”只使用提交这条指令时选中的工人；玩家随后可以立刻选择其他单位，不会改变已排队指令绑定的 tag。没有明确指定主体时会从官方能力确认可建造的工人中选择接近目标的一个。不可放置的位置会在发出动作前被拒绝并输出 Blizzard `ActionResult` 名称。

每条 GUI 指令都有 `CMD-0001` 形式的任务编号。生产任务根据最新 Observation 显示 `已完成/目标数`，资源、人口或生产队列不足时会显示具体等待原因。建筑命令在 SCV 出发前完成资源、科技、地图范围和官方 `RequestQueryBuildingPlacement` 预检；通过后继续显示“前往建造点”和建筑 `build_progress`。若农民被重新操作、死亡，或 180 秒仍未完成，任务会明确终止，不会无限保持无反馈状态。

作战单位覆盖标准 Melee 三族单位。Terran 的常用形态提供快速中文规则；Protoss/Zerg 以及装卸、取消、注卵、时空加速、菌毯、范围技能和单位目标技能走通用官方能力解析。每次动作都必须同时满足：当前单位存在、`QueryAvailableAbilities` 返回该能力、`AbilityData.target` 与指令目标一致。

建筑操作覆盖集结点、Terran 可起飞建筑的起飞/降落、补给站升降、Command Center 升级为 Orbital Command/Planetary Fortress，以及 Barracks/Factory/Starport 的 Tech Lab/Reactor。玩家选中带附件的生产建筑时，程序会通过官方 raw `add_on_tag` 自动把对应 Tech Lab 纳入科技能力查询。科技研发从官方 RequestData 中匹配当前可研究的等级，并通过建筑 order 与 `completed_upgrade_ids` 显示真实研发进度；资源、前置科技、建筑类型或当前状态不满足时会立即反馈。

持续任务不是让 LLM 每帧重复推理。LLM 或本地规则只创建一次结构化任务，本地运行时随后直接读取 Observation：条件不满足时等待，条件满足时派发确定性动作；相同任务不会重复创建；相同单位/生产/建筑/研究资源发生冲突时阻塞；互不冲突的动作最多并行 4 个；高优先级任务或玩家即时命令可以抢占低优先级任务。单次失败按指数退避重试，连续 3 次失败后明确终止。可输入 `暂停任务 <名称>`、`恢复任务 <名称>`、`取消任务 <名称>` 或 `查看持续任务`。

也可以直接在 SC2 游戏聊天框输入，但为了避免普通聊天或对手消息触发动作，游戏内必须带 `ai` 前缀：

```text
ai 让选中的枪兵移动到坐标 36 134
```

游戏内聊天只接受当前我方 `player_id` 发出的消息。默认终端不再每秒打印完整单位列表，避免覆盖输入行；完整 Observation 和单位位置仍按原频率写入 `logs/aisc2commander.log`。需要主动查看终端快照时输入 `list`，或使用 `--verbose` 恢复 DEBUG 控制台输出。

桌面 GUI 不使用 Windows 语音识别。“开始录音”适合先说、再检查和编辑文字；“开始监听”适合对局中免点击连续下令。语音识别语言完全跟随“设置 → 多语言”，不会额外运行自动语言检测：English 只识别英文，简体/繁体中文只识别中文；当前监听中的转写器保持不变，切换界面语言后下一次开始录音或监听时会按新语言重建。持续监听时，麦克风采集到项目内置的自适应 RMS 切句器；它不是 faster-whisper 提供的第三方 VAD。开始监听后的前 1 秒会校准环境噪声，此时界面提示保持安静；之后使用分离的开始/结束阈值，避免风扇或游戏外放一旦触发后始终无法断句。VAD 只在检测到语音并遇到句尾静音时生成 WAV，因此监听静音不会请求 LLM 或消耗云端 Token。本地 Whisper 在隔离进程中只加载一次，随后逐句转写；Whisper 与 Blizzard proto 使用不同虚拟环境，避免 protobuf 版本冲突。监听模式的每句转写结果会自动进入和键盘发送相同的顺序队列。

切句参数位于 `config/voice.env`：

```text
VOICE_SILENCE_SECONDS=0.7
VOICE_MIN_SPEECH_SECONDS=0.25
VOICE_MAX_UTTERANCE_SECONDS=10
VOICE_VAD_RMS=0.008
VOICE_VAD_CALIBRATION_SECONDS=1.0
VOICE_VAD_NOISE_MULTIPLIER=2.5
VOICE_VAD_RELEASE_MULTIPLIER=1.6
```

点击“开始监听”后的校准时间内应保持安静。环境噪声或游戏外放仍导致误触发时，先提高 `VOICE_VAD_NOISE_MULTIPLIER`，再考虑提高 `VOICE_VAD_RMS`；说完后持续显示“检测到说话”时可小幅提高 `VOICE_VAD_RELEASE_MULTIPLIER`，但它必须低于开始阈值倍数。轻声说话经常漏检时反向调低；一句话中的自然停顿经常被拆开时提高 `VOICE_SILENCE_SECONDS`。即使持续噪声始终高于句尾阈值，`VOICE_MAX_UTTERANCE_SECONDS=10` 也会强制切句。修改配置后需要重启 GUI。建议使用耳机，避免游戏声音被麦克风识别为玩家指令。

终端仍保留固定时长的快捷测试命令：

```text
devices
voice 5
```

输入 `voice 5` 后立即对默认麦克风说 5 秒。程序完成录音后异步转写、规划并执行，期间 SC2 观察循环继续运行。选择指定设备可在启动时使用：

```powershell
  .\scripts\run.ps1 --map 'D:\Maps\MyCustom.SC2Map' --voice-device 1
```

终端命令的数字只影响终端快捷模式；GUI 的单次录音保持到玩家点击“停止录音”，持续监听保持到玩家点击“停止监听”。处于监听状态时，检测到的所有有效语音都会自动作为指令发送，应避免把扬声器游戏声或普通交谈输入当前麦克风。

## 快捷指令集与语音计划

语音适合触发高层计划，不适合代替鼠标微操。桌面 GUI 的“指令集”窗口可编辑 `config\command_plans.json`：计划文本每行一步，玩家只需要说“执行计划1”“启动一号计划”或计划的自定义别名。

监听模式下，普通语音和计划口令都会在切句转写后自动发送。单次录音模式下，普通语音只填入输入框，计划名/别名以及“暂停计划”“继续计划”“取消计划”等精确口令则自动发送。模糊的普通指令进入 Agent Harness，规则无法完整生成动作时才交给 LLM；执行前仍会经过官方 Observation、可用能力、资源和位置校验。

计划触发后不会把每一行再次发给 Ollama/GPT。程序使用本地确定性规则直接生成现有的 Move、Attack、Train、Build、Research 工具调用，因此触发延迟只包含一次短语音转写。默认提供的“计划1”是可修改示例：

```text
# 触发时使用当前选择
选中的建筑生产19个农民
等待生产完成
```

计划文本支持现有的移动、攻击、生产、建造、科技升级、编组和地图点位语法，并增加以下流程控制行：

```text
等待 3 秒
等待矿物 400
等待气体 100
等待人口 50
等待空闲人口 5
等待生产完成
等待任务完成
# 这一行是注释
```

运行中可以说或输入：

```text
计划状态
暂停计划
继续计划
取消计划
```

每帧最多推进一行，普通动作之间至少留出一个 Observation 间隔。资源或生产条件未满足时计划保持等待；某一行动被 SC2 拒绝或无法由快速指令集解析时，计划立即停止并显示具体失败行。再次说“执行计划2”会明确中止当前计划并切换到计划2。

需要从开局一直验证到击破电脑对手时，使用 [人族完整对局流程测试](docs/terran_full_game_flow_test.md)。文档按时间轴列出玩家的鼠标操作、逐句语音、预期反馈、通过标准和常见失败判定。

## 地图与自定义模式边界

启动 GUI 时必须明确选择地图：

- 本地自定义地图：选择 `.SC2Map` 文件，使用官方 `RequestCreateGame.local_map`。
- 已发布/缓存地图：输入 Battle.net 完整地图名，使用官方 `RequestCreateGame.battlenet_map_name`。

Blizzard 官方协议没有“附着到一个已经由普通客户端进入的 Battle.net 自定义房间”请求。项目因此不能加入玩家已经手动创建好的普通大厅；支持的稳定流程是让 API 使用所选本地或缓存地图创建该局。这个结论来自官方 `RequestCreateGame` / `RequestJoinGame` 状态机，而不是技术规避。

### 官方双人联机（玩家主机）

当前固定版本的 Blizzard API 只支持一个远端 client，也就是一名主机和一名加入者。程序严格按官方多人状态机运行：主机先发送带两个 `Participant` 的 `RequestCreateGame`，随后双方各自向本机 SC2 API 发送携带同一拓扑的 `RequestJoinGame`。双方都收到 `ResponseJoinGame` 后才进入游戏；退出时先发送官方 `RequestLeaveGame`。Observation、Action、实时模拟、对等传输、锁步同步与校验不经过 Commander 自建网络层。

GUI 中双方需要填写完全相同的两项：

- 主机可达 IPv4：局域网使用主机局域网地址；跨公网使用另一位玩家能够访问的地址。
- 联机起始端口：默认 `5001`，SC2 会使用连续五个端口 `5001–5005`，分别填入官方 `shared_port`、`server_ports` 和唯一一组 `client_ports`。

官方协议不提供大厅发现、邀请服务、中继或 NAT 穿透。双方需要允许这五个端口通过系统防火墙；跨 NAT 时还需要在主机网络设备上配置对应端口转发。双方应使用相同的 SC2 版本和地图内容；本地 `.SC2Map` 需要在两台机器上可用且内容一致，否则 SC2 会通过官方 `MapDoesNotExist`、`ChecksumError` 或 `NetworkError` 返回失败。优先使用双方都已缓存的已发布 Battle.net 地图。

命令行主机示例：

```powershell
.\scripts\run.ps1 --map 'D:\Maps\MyCustom.SC2Map' --multiplayer host --game-host 192.168.1.10 --network-port 5001 --race terran --no-opponent
```

加入方不传地图参数：

```powershell
.\scripts\run.ps1 --multiplayer join --game-host 192.168.1.10 --network-port 5001 --race zerg
```

主机先启动或加入方先启动都可以；两边的官方 `JoinGame` 会阻塞等待另一位玩家。这里创建的是 SC2 API 对局，不是普通 Battle.net 客户端大厅。

官方 `RequestCreateGame.PlayerSetup` 只提供 `type`、`race`、`difficulty`、`player_name` 和 `ai_build`，没有队伍或玩家颜色字段；官方协议也没有创建游戏前直接返回本地 `.SC2Map` 最大玩家数的元数据请求。因此启动界面中的“队伍”和“颜色”明确显示为“地图 / SC2 自动”，不会保存看似可选但实际无效的值。地图容量使用最小 workaround：在独立临时 SC2 API 进程中以一个 Participant 和一个 Computer 创建并加入地图，从官方 `ResponseGameInfo.start_raw.start_locations` 读取所有可能的敌方出生点，再加上玩家自己的出生点得到可用人数；随后立即发送官方 `RequestQuit` 并缓存结果。之所以不依赖 `InvalidPlayerSetup`，是因为 SC2 会在部分地图上接受超过实际出生点数的 Computer 配置。这个过程不读取内存、不解析地图包、不注入游戏。

地图点位按“地图 → 预设 → 点位”保存到 `config\map_points.json`；旧版每张地图单套点位的数据会兼容读取并在首次修改时迁移。编辑器优先使用实时的官方 `ResponseGameInfo.start_raw.pathing_grid`，并把地图边界/pathing 预览缓存在本机 `config\map_previews.json`，因此项目停止后仍可继续编辑。如果地图从未通过官方 API 加载过，编辑器先显示 0–256 世界坐标网格；首次启动该地图后会自动替换为官方预览。地图画布保持世界坐标的真实宽高比，X 向右、Y 向上，并在四周标出地图边界以及每 50 世界单位的刻度。画布支持 100%–400% 缩放：可使用滑块、加减按钮或鼠标滚轮缩放，使用滚动条或按住鼠标中键拖动查看放大区域，“适合窗口”可恢复完整地图。地图图片、坐标网格和点位标记始终使用同一个缩放坐标系。指令“移动到 A1”“攻击 A2”“在 A1 建补给站”只解析该地图当前启用预设中的点位，并在执行前再次检查 playable area。

### 点位编辑器高清地图图像

`.SC2Map` 是 MPQ 地图包。选择本地地图时，点位编辑器会只读解析包内的 `DocumentInfo`，优先自动显示发布信息中登记的第一张 `Screenshot`；没有发布截图时，再尝试常见预览图、其他内嵌地图图片和 `Minimap.tga`。例如官方 `(2)Bel'ShirVestigeLE (Void).SC2Map` 会自动使用包内的 `BelShirVestigeLE_01.jpg`，也就是 SC2 自定义地图详情页显示的第一张图片。地图包不会被修改。

Blizzard 官方 `ResponseGameInfo.start_raw` 只公开 map size、playable area、pathing grid、placement grid 和 terrain height，并不直接返回这些彩色图片。因此内嵌图片只作为点位编辑器背景，坐标边界和合法性仍来自官方 API；读取不到支持的包内图片时回退到官方 pathing grid。Battle.net 地图名没有对应的本地 `.SC2Map` 路径时也使用这一回退流程。

为地图配置高清图：

1. 打开主界面的“地图点位”，选择对应的本地 `.SC2Map`；如果包内存在支持的发布截图或预览图，界面会直接加载。
2. 如果希望覆盖包内图片，点击“改用外部图片…”或“设置高清图…”，选择 PNG、JPG、JPEG 或 WebP 图片。
3. 程序会把外部图片副本保存到 `assets\map_images\`，并在 `config\map_images.json` 中用当前地图的唯一 profile key 建立关联。外部图片优先于包内图片，以后再次选择该地图会自动加载，不需要手工修改 JSON。

建议外部图片规格：长边至少 1600 像素（推荐 2048 像素以上）；俯视方向保持北/地图上方朝上；图片左侧对应较小的 X，右侧对应较大的 X，底部对应较小的 Y，顶部对应较大的 Y；裁剪范围应尽量只包含官方 playable area。图片宽高比与 playable area 相差超过 8% 时界面会提示，继续使用会将图片拉伸到官方坐标边界，因此地形与点击坐标可能存在偏差。小于 1024 像素长边的图片也会提示清晰度不足。SC2 发布截图常采用透视视角，它适合辨认地形但不保证与世界坐标逐点对齐；需要精确落点时仍建议使用正交俯视图。

本地地图关联包含 `.SC2Map` 的绝对路径；如果之后移动或重命名地图文件，程序会把它视为另一张地图，需要重新选择图片。Battle.net 地图则按输入的完整地图名关联。图片只作为可视背景，坐标边界、点位合法性和游戏命令仍以 Blizzard 官方 API 数据为准。

## 官方控制编组

`ObservationUI.groups` 能可靠给出 1–10 队的 `control_group_index`、领队单位类型和数量，但协议不提供成员 tags。程序实时输出这些摘要。玩家明确说“一队/编组1/1队”时，程序使用官方 `ActionControlGroup.Recall`（等同按数字键），再从下一帧 `raw Unit.is_selected` 获取成员 tags 后执行动作。

这个 workaround 只在指令明确引用编组时发生；它会像玩家按数字键一样改变当前选择。设置/追加编组也是官方 UI 动作，协议不能直接用 raw unit tags 写入控制组，因此如果模型处理期间玩家已经改选，程序会安全取消该次编组写入，避免把错误单位编入；移动、建造、生产、攻击、升级等 raw unit 指令不受这个限制。项目不读取内存、不截图识别，也不维护第二套自制编组系统。

## Current Selection Context

每次选择变化会生成并记录：

```json
{
  "unit_tags": [123, 456],
  "unit_types": ["Marine"],
  "counts": {"Marine": 2},
  "category": "units",
  "timestamp": "2026-08-18T12:34:56.789Z",
  "source": "raw.is_selected"
}
```

实现优先使用官方 raw `Unit.is_selected`，它能把真人当前选择映射到完整 unit tag。程序同时启用低分辨率 feature layer，使官方 `ObservationUI.single/multi/cargo/production` 可用，并用它交叉校验类型与数量。`raw_affects_selection=false` 保证程序发 raw action 时，SC2 会恢复玩家原选择，避免抢走鼠标上下文。

协议本身的明确限制是：`ObservationUI.UnitInfo` 没有 tag。若某个游戏版本暂时未在 raw units 上设置 `is_selected`，最小 fallback 会继续从 `ObservationUI` 输出可靠的类型和数量，但 `unit_tags` 为空，`source=ui_data_fallback_no_tags`；不会截图识别，也不会自建 RTS 选择系统。相关官方定义见 [`ui.proto`](https://github.com/Blizzard/s2client-proto/blob/master/s2clientprotocol/ui.proto)、[`raw.proto`](https://github.com/Blizzard/s2client-proto/blob/master/s2clientprotocol/raw.proto) 和 [`sc2api.proto`](https://github.com/Blizzard/s2client-proto/blob/master/s2clientprotocol/sc2api.proto)。

## 测试

完整的首次安装、GUI 人工检查、Issue 信息要求和敏感信息注意事项见 [TESTING.md](TESTING.md)。下面是已有自动测试的快捷命令。

不启动游戏的测试：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

真实 SC2 smoke test（会打开窗口，先验证 RequestData 中三族代表单位、ability 与 upgrade 元数据，再用官方 Debug API 搭建隔离测试场景，通过完整规则 Harness 执行 Move、Attack、正常 Train Marine 和正常 SCV 建造）：

```powershell
.\.venv\Scripts\python.exe -m aisc2commander smoke --verbose
.\.venv\Scripts\python.exe -m aisc2commander smoke --race protoss --no-opponent
.\.venv\Scripts\python.exe -m aisc2commander smoke --race zerg --no-opponent
```

smoke test 在 `finally` 中发送官方 quit 并回收自己启动的进程。

测试场景的 Marine、Zergling、Barracks、SupplyDepot 和资源由官方 Debug API准备，因此会显示 CHEAT；被验证的移动、攻击和训练动作本身全部是正常官方 Action API。普通玩法中的 `ai 生产...` 不使用 Debug API。

超过一分钟的连接稳定性测试：

```powershell
.\.venv\Scripts\python.exe -m aisc2commander smoke --soak-seconds 75
```

WebSocket 客户端的协议层 keepalive 已禁用：SC2 官方端点会在收到该库默认的
20 秒 `PING` frame 时直接关闭连接；持续的 protobuf Observation 请求本身已提供
连接活性检测。

## 反馈与许可

- 欢迎使用 GitHub Issues 提交可复现的错误报告、功能建议和文档问题。
- 不接受未经邀请的代码补丁、Pull Request、修改版或重新打包的 EXE。
- 提交前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，并删除日志中的 API Key、令牌、用户名、IP 和个人路径。
- 项目采用 [AISC2Commander Source-Available Testing License 1.0](LICENSE)：仅允许非商业下载、构建、测试和本地使用，禁止商业使用及再发布。
- 许可只覆盖项目作者拥有版权的内容，不授予 StarCraft II、Blizzard Entertainment、地图、模型或第三方依赖的任何额外权利。

## 连接已手动启动的 API 客户端

普通 Battle.net 启动的 SC2 不会监听 API 端口。若已用官方参数启动：

```text
SC2_x64.exe -listen 127.0.0.1 -port 5000 -displayMode 0
```

可连接：

```powershell
.\scripts\run.ps1 --attach --port 5000 --map 'D:\Maps\MyCustom.SC2Map'
```

连接 API 端口仍不等于加入普通 Battle.net 大厅；目标进程必须处在官方 API 状态机允许的 `launched`、`ended` 或 `in_game` 状态。
