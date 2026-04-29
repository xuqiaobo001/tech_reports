# OpenClaw 内置 Skills 完整分析报告

> 分析日期：2026-04-28
> 技能总数：68 个（53 个核心 Skills + 15 个扩展 Skills）

---

## 1. 概览

OpenClaw 的 Skill 系统是其核心扩展机制之一。每个 Skill 由一个 `SKILL.md` 文件定义，包含技能描述、触发条件、可用工具和使用说明。Agent 在运行时会根据用户意图自动激活匹配的 Skill。

Skills 分布在两个位置：
- **核心 Skills**：`skills/` 目录下，共 53 个
- **扩展 Skills**：`extensions/*/skills/` 目录下，共 15 个

---

## 2. 技能分类总览

```
┌─────────────────────────────────────────────────────────┐
│                  OpenClaw Skills 生态 (68)               │
├──────────────┬──────────────┬──────────────┬────────────┤
│  生产力工具   │  通信与消息   │  开发者工具   │  智能家居   │
│    (14)      │    (10)      │    (10)      │    (5)     │
├──────────────┼──────────────┼──────────────┼────────────┤
│  多媒体处理   │  信息获取     │  系统与运维   │  AI 与语音  │
│    (8)       │    (8)       │    (6)       │    (7)     │
└──────────────┴──────────────┴──────────────┴────────────┘
```

---

## 3. 核心技能详解（53 个）

### 3.1 生产力工具类（14 个）

#### 1password
- **描述**：1Password CLI 集成，用于登录、桌面集成、读取或注入密码和密钥
- **核心工具**：`op signin`, `op whoami`, `op vault list`, `op run`, `op inject`
- **场景**：安全地管理凭据，向命令注入密钥而不落盘

#### apple-notes
- **描述**：通过 memo CLI 在 macOS 上创建、查看、编辑、删除、搜索、移动和导出 Apple Notes
- **核心工具**：`memo notes` (list/create/edit/delete/move/export)
- **场景**：管理 macOS 原生备忘录

#### apple-reminders
- **描述**：通过 remindctl 管理 Apple 提醒事项和列表
- **核心工具**：`remindctl` (today/tomorrow/week/add/complete/delete)
- **场景**：管理 macOS/iOS 原生提醒事项

#### bear-notes
- **描述**：通过 grizzly CLI 创建、搜索和管理 Bear 笔记
- **核心工具**：`grizzly` (create/open-note/add-text/tags/open-tag)
- **场景**：管理 Bear 笔记应用

#### obsidian
- **描述**：通过 obsidian-cli 操作 Obsidian 知识库（纯 Markdown 笔记）
- **核心工具**：`obsidian-cli` (search/search-content/create/move/delete)
- **场景**：管理 Obsidian 知识库

#### notion
- **描述**：Notion API 集成，管理页面、数据库和内容块
- **核心工具**：`curl /v1/*` (search/pages/databases/blocks CRUD)
- **场景**：读写 Notion 工作空间

#### trello
- **描述**：通过 Trello REST API 管理看板、列表和卡片
- **核心工具**：`curl` + `jq` (boards/lists/cards/comments)
- **场景**：管理 Trello 项目看板

#### things-mac
- **描述**：macOS Things 3 任务管理工具
- **核心工具**：`things` (inbox/today/search/projects/areas/tags)
- **场景**：管理 Things 3 待办事项

#### himalaya
- **描述**：终端邮件客户端，支持 IMAP/SMTP 收发邮件
- **核心工具**：`himalaya` (folder/envelope/message read/reply/forward/write/move/copy)
- **场景**：在终端中管理邮件

#### gog
- **描述**：Google Workspace CLI，涵盖 Gmail、日历、Drive、联系人、Sheets、Docs
- **核心工具**：`gog` (gmail/calendar/drive/contacts/sheets/docs 子命令)
- **场景**：全功能 Google Workspace 集成

#### taskflow
- **描述**：协调多步骤持久化任务，支持状态管理、等待、子任务
- **核心工具**：`api.runtime.tasks.flow` (createManaged/runTask/setWaiting/resume/finish)
- **场景**：构建复杂的多步骤自动化工作流

#### taskflow-inbox-triage
- **描述**：TaskFlow 模式示例，用于收件箱分类、意图路由、等待回复和后续摘要
- **核心工具**：`api.runtime.tasks.flow`
- **场景**：自动化的邮件/消息收件箱处理

#### summarize
- **描述**：汇总或转录 URL、YouTube/视频、播客、文章、PDF 和本地文件
- **核心工具**：`summarize` CLI
- **场景**：快速获取长内容摘要

#### nano-pdf
- **描述**：使用自然语言指令编辑 PDF 页面
- **核心工具**：`nano-pdf edit <pdf> <page> "<instruction>"`
- **场景**：AI 驱动的 PDF 编辑

---

### 3.2 通信与消息类（10 个）

#### discord
- **描述**：Discord 操作工具，支持消息、反应、投票、Pin、线程等
- **核心工具**：`message` tool (channel=discord) — send/react/read/edit/delete/poll/pin/thread-create/search
- **场景**：Discord 服务器管理自动化

#### slack
- **描述**：Slack 操作工具，支持消息发送/编辑/删除、反应、Pin、成员信息
- **核心工具**：`slack` tool (react/sendMessage/editMessage/deleteMessage/readMessages/pinMessage/memberInfo)
- **场景**：Slack 工作空间自动化

#### imsg
- **描述**：macOS iMessage/SMS CLI，查看聊天、历史记录和发送消息
- **核心工具**：`imsg` (chats/history/watch/send)
- **场景**：通过终端管理 iMessage

#### bluebubbles
- **描述**：通过 BlueBubbles 收发 iMessage，支持附件、Tapback、编辑、回复、群组
- **核心工具**：`message` tool (channel=bluebubbles) — send/react/edit/unsend/reply/sendAttachment/sendWithEffect
- **场景**：跨平台 iMessage 集成

#### wacli
- **描述**：WhatsApp CLI，发送第三方消息、同步/搜索历史记录
- **核心工具**：`wacli` (auth/sync/chats/search/send)
- **场景**：WhatsApp 消息管理和搜索

#### xurl
- **描述**：X (Twitter) API 集成，支持发帖、回复、搜索、DM、媒体上传、关注
- **核心工具**：`xurl` (post/reply/search/timeline/follow/DM/media)
- **场景**：X/Twitter 自动化操作

#### voice-call
- **描述**：通过 OpenClaw voice-call 插件发起语音通话
- **核心工具**：`voice_call` (initiate_call/continue_call/speak_to_user/end_call/get_status)
- **场景**：AI 语音通话

---

### 3.3 开发者工具类（10 个）

#### github
- **描述**：GitHub CLI 集成，管理 PR、Issue、CI/工作流、评论、Review、Release
- **核心工具**：`gh` (pr/issue/run/api 子命令)
- **场景**：GitHub 仓库管理自动化

#### gh-issues
- **描述**：自动修复 GitHub Issues — 获取 Issue、委托修复、创建 PR、监控 Review
- **核心工具**：`/gh-issues` 命令 + `curl` (GitHub REST API)
- **特色**：支持并行子 Agent、Fork 模式、Watch 模式、Cron 模式
- **场景**：自动化的 Issue 处理和 PR 创建

#### coding-agent
- **描述**：将编码任务委托给 Codex、Claude Code、OpenCode 或 Pi 等 AI 编码 Agent
- **核心工具**：`codex exec` / `claude --print` / `opencode run` / `pi`
- **特色**：后台执行，通过 `process` 工具监控
- **场景**：AI 驱动的代码生成和审查

#### canvas
- **描述**：在连接的 OpenClaw 节点上展示 HTML 内容（游戏、可视化、仪表盘）
- **核心工具**：`canvas` tool (present/hide/navigate/eval/snapshot)
- **场景**：可视化展示和交互式演示

#### mcporter
- **描述**：MCP 服务器/工具的管理和调用，支持 HTTP 和 stdio
- **核心工具**：`mcporter` (list/call/auth/config/daemon/generate-cli/emit-ts)
- **场景**：MCP 协议工具的统一接入

#### clawhub
- **描述**：ClawHub 技能市场 CLI — 搜索、安装、更新、发布 Agent 技能
- **核心工具**：`clawhub` (search/install/update/list/login/publish)
- **场景**：技能包管理和分发

#### skill-creator
- **描述**：创建、编辑、改进、审查或重构 AgentSkill 和 SKILL.md 文件
- **核心工具**：`scripts/init_skill.py` (初始化) / `scripts/package_skill.py` (打包)
- **场景**：技能开发辅助

#### node-connect
- **描述**：诊断 OpenClaw Android/iOS/macOS 节点配对、QR 码、路由和连接问题
- **核心工具**：`openclaw qr/devices/nodes` + `tailscale status`
- **场景**：设备连接问题排查

#### session-logs
- **描述**：搜索和分析会话日志（历史对话记录）
- **核心工具**：`jq` + `rg` (在 session JSONL 文件中搜索)
- **场景**：审计和分析 AI 对话历史

#### model-usage
- **描述**：汇总 CodexBar 本地成本日志，按模型分类统计
- **核心工具**：`python scripts/model_usage.py` / `codexbar cost`
- **场景**：AI 模型使用成本分析

---

### 3.4 智能家居与 IoT 类（5 个）

#### openhue
- **描述**：通过 OpenHue CLI 控制飞利浦 Hue 灯光和场景
- **核心工具**：`openhue` (get/set light/room/scene, brightness/temperature/color)
- **场景**：智能灯光控制

#### blucli
- **描述**：BluOS CLI — Bluesound/NAD 播放器的发现、播放、分组和音量控制
- **核心工具**：`blu` (devices/status/play/pause/volume/group/tunein)
- **场景**：BluOS 音响系统控制

#### sonoscli
- **描述**：Sonos 音箱控制 — 发现、状态、播放、音量、分组
- **核心工具**：`sonos` (discover/status/play/volume/grouping/favorites/queue)
- **场景**：Sonos 音响系统控制

#### eightctl
- **描述**：Eight Sleep 智能床垫控制 — 状态、温度、闹钟、日程
- **核心工具**：`eightctl` (status/on/off/temp/alarm/schedule/audio/base)
- **场景**：智能床垫/睡眠系统控制

#### camsnap
- **描述**：从 RTSP/ONVIF 摄像头捕获画面或录制片段
- **核心工具**：`camsnap` (add/discover/snap/clip/watch/doctor)
- **场景**：安防摄像头管理

---

### 3.5 多媒体处理类（8 个）

#### openai-whisper
- **描述**：本地语音转文字（Whisper CLI，无需 API Key）
- **核心工具**：`whisper` (本地模型: turbo/base/small/medium/large)
- **场景**：离线语音识别

#### openai-whisper-api
- **描述**：通过 OpenAI Audio API 进行语音转文字
- **核心工具**：`scripts/transcribe.sh` (调用 Whisper API)
- **场景**：云端高质量语音识别

#### sherpa-onnx-tts
- **描述**：本地文字转语音（sherpa-onnx，离线，无需云端）
- **核心工具**：`sherpa-onnx-tts` CLI
- **场景**：离线语音合成

#### sag
- **描述**：ElevenLabs TTS，macOS say 风格 UX，支持富情感语音
- **核心工具**：`sag` (speak/voices/-o save) + 情感标签 [whispers/shouts/laughs/sings]
- **场景**：高质量情感语音合成

#### songsee
- **描述**：从音频生成频谱图和特征可视化
- **核心工具**：`songsee` (spectrogram/mel/chroma 可视化)
- **场景**：音频分析和可视化

#### video-frames
- **描述**：使用 ffmpeg 从视频提取帧或短片段
- **核心工具**：`frame.sh` + `ffmpeg`
- **场景**：视频帧提取和分析

#### gifgrep
- **描述**：搜索 GIF、下载结果、提取静帧和精灵图
- **核心工具**：`gifgrep` (search/tui/download/still/sheet)
- **场景**：GIF 搜索和处理

#### spotify-player
- **描述**：终端 Spotify 播放控制（搜索、播放、设备管理）
- **核心工具**：`spogo` (首选) / `spotify_player` (备选)
- **场景**：Spotify 音乐播放控制

---

### 3.6 信息获取与搜索类（8 个）

#### weather
- **描述**：获取天气、降雨、温度和预报信息
- **核心工具**：`curl wttr.in`
- **场景**：天气查询

#### goplaces
- **描述**：查询 Google Places API — 地点搜索、详情、评论、地理编码
- **核心工具**：`goplaces` (search/details/resolve, --json)
- **场景**：地点搜索和周边信息查询

#### blogwatcher
- **描述**：监控博客和 RSS/Atom Feed 更新
- **核心工具**：`blogwatcher` (add/blogs/scan/articles/read/remove)
- **场景**：博客/内容订阅追踪

#### oracle
- **描述**：将提示词和文件打包发送给第二模型进行调试、重构、设计或审查
- **核心工具**：`oracle` (--dry-run/--engine/--model/--file/--render)
- **场景**：多模型协作和交叉验证

#### gemini
- **描述**：Gemini CLI 单次问答、摘要和生成
- **核心工具**：`gemini` (oneshot Q&A, --model, --output-format json)
- **场景**：使用 Google Gemini 模型

#### ordercli
- **描述**：Foodora 外卖订单查询（查看历史订单和活跃订单状态）
- **核心工具**：`ordercli` (foodora: countries/login/orders/history/reorder)
- **场景**：外卖订单追踪

#### healthcheck
- **描述**：审计和加固运行 OpenClaw 的主机（SSH、防火墙、更新、安全态势）
- **核心工具**：`openclaw security audit` / `openclaw status/health`
- **场景**：系统安全健康检查

#### peekaboo
- **描述**：macOS UI 自动化 — 截图、视觉分析、点击、输入、应用/窗口控制
- **核心工具**：`peekaboo` (capture/image/see/click/type/drag/hotkey/app/window/menu/clipboard)
- **场景**：macOS 桌面 UI 自动化

---

### 3.7 系统与运维类（6 个）

#### tmux
- **描述**：远程控制 tmux 会话，发送按键和抓取面板输出
- **核心工具**：`tmux` (session control / pane capture / send-keys)
- **场景**：远程终端会话管理

#### diffs（扩展）
- **描述**：生成可分享的 Diff（查看器 URL、文件产物或两者）
- **核心工具**：`diffs` tool (view/file/both mode)
- **场景**：代码变更可视化分享

#### browser-automation（扩展）
- **描述**：使用 OpenClaw 浏览器工具控制网页，支持多步骤流程、登录检查、标签管理
- **核心工具**：Browser tool (status/profiles/tabs/open/close/snapshot/act)
- **场景**：网页自动化操作

#### lobster（扩展）
- **描述**：带审批检查点的多步骤工作流执行引擎
- **核心工具**：Lobster pipeline tool (run/resume/approve)
- **场景**：可审批的确定性自动化工作流

#### acp-router（扩展）
- **描述**：将自然语言请求路由到多种 AI 编码工具（Claude Code、Cursor、Copilot、Pi 等）
- **核心工具**：`sessions_spawn` (ACP 运行时会话)
- **场景**：多 AI 编码工具统一调度

#### prose（扩展）
- **描述**：OpenProse VM 技能包，编排多 Agent 工作流
- **核心工具**：`sessions_spawn` + `read/write` + `web_fetch`
- **场景**：.prose 程序执行和多 Agent 协作

---

## 4. 扩展技能详解（15 个）

### 4.1 飞书集成（4 个）

| 技能 | 描述 | 核心工具 |
|------|------|----------|
| feishu-doc | 飞书文档读写操作 | `feishu_doc` (read/write/append/create/list_blocks/create_table/upload_image) |
| feishu-drive | 飞书云盘文件管理 | `feishu_drive` (list/info/create_folder/move/delete) |
| feishu-wiki | 飞书知识库导航 | `feishu_wiki` (spaces/nodes/get/create/move/rename) |
| feishu-perm | 飞书文档权限管理 | `feishu_perm` (list/add/remove 协作者) |

### 4.2 QQ 机器人（3 个）

| 技能 | 描述 | 核心工具 |
|------|------|----------|
| qqbot-channel | QQ 频道管理（子频道、成员、发帖、公告、日程） | `qqbot_channel_api` (HTTP 代理) |
| qqbot-media | QQ 富媒体收发（图片/语音/视频/文件） | `<qqmedia>` 标签系统 |
| qqbot-remind | QQ 定时提醒（一次性/周期性） | `qqbot_remind` (add/list/remove) |

### 4.3 知识与记忆（2 个）

| 技能 | 描述 | 核心工具 |
|------|------|----------|
| wiki-maintainer | 维护 OpenClaw 记忆 Wiki（确定性页面、受管块、源备份更新） | `wiki_status/search/get/apply/lint` |
| obsidian-vault-maintainer | 维护 Obsidian 友好的记忆 Wiki（wikilinks、frontmatter） | `openclaw wiki obsidian` 系列命令 |

### 4.4 搜索与信息（1 个）

| 技能 | 描述 | 核心工具 |
|------|------|----------|
| tavily | Tavily 网络搜索、内容提取和研究工具 | `tavily_search` / `tavily_extract` / `web_search` |

### 4.5 其他扩展（5 个）

| 技能 | 描述 | 核心工具 |
|------|------|----------|
| acp-router | 多 AI 编码工具路由（Claude/Cursor/Copilot/Pi 等） | `sessions_spawn` |
| browser-automation | 网页自动化（多步骤、登录、标签管理） | Browser tool |
| diffs | 可视化 Diff 生成和分享 | `diffs` tool |
| lobster | 带审批检查点的多步骤工作流 | Pipeline tool |
| prose | OpenProse VM 多 Agent 协作 | `sessions_spawn` + file I/O |

---

## 5. 技能分类统计

```
分类                  数量    代表技能
─────────────────────────────────────────────────────
生产力工具             14     gog, notion, himalaya, apple-notes, obsidian
通信与消息             10     discord, slack, imsg, wacli, xurl
开发者工具             10     github, gh-issues, coding-agent, canvas, mcporter
智能家居/IoT            5     openhue, blucli, sonoscli, eightctl, camsnap
多媒体处理              8     whisper, sag, sherpa-onnx-tts, video-frames, gifgrep
信息获取/搜索           8     weather, goplaces, blogwatcher, tavily, oracle
系统/运维               6     tmux, healthcheck, diffs, browser-automation, lobster
AI/语音                7     gemini, voice-call, acp-router, prose
─────────────────────────────────────────────────────
合计                   68
```

---

## 6. 技能架构模式

### 6.1 技能定义方式

每个 Skill 由以下部分组成：

```
skills/<skill-name>/
├── SKILL.md              # 技能描述文件（必须）
│   ├── Name / Trigger     # 名称和触发条件
│   ├── Description        # 功能描述
│   ├── Tools              # 可用工具列表
│   └── Guidelines         # 使用指南
├── references/           # 参考文档（可选）
│   └── *.md
├── scripts/              # 辅助脚本（可选）
│   └── *.sh / *.py
└── bin/                  # 可执行文件（可选）
```

### 6.2 技能激活机制

```
用户消息 → Agent 解析意图 → 匹配 SKILL.md 中的触发条件
                                    ↓
                              激活对应 Skill
                                    ↓
                          加载工具定义和使用指南
                                    ↓
                          Agent 使用 Skill 中的工具执行任务
```

### 6.3 技能类型分布

| 类型 | 说明 | 示例 |
|------|------|------|
| **CLI 包装型** | 封装外部 CLI 工具 | himalaya, gog, gh, peekaboo |
| **API 调用型** | 封装 REST API | notion, trello, feishu-* |
| **平台集成型** | 通过 message tool 与通道交互 | discord, slack, bluebubbles |
| **本地工具型** | 本地文件/系统操作 | video-frames, nano-pdf, diffs |
| **AI 协作型** | 调用其他 AI Agent | coding-agent, acp-router, oracle |
| **工作流引擎型** | 多步骤任务编排 | taskflow, lobster, prose |

---

## 7. 技能生态亮点

### 7.1 全平台消息覆盖
覆盖 WhatsApp、Telegram、Discord、Slack、iMessage、Signal、QQ、X/Twitter 等主流消息平台，实现"一个 AI 助手管理所有消息"。

### 7.2 Apple 生态深度集成
macOS/iOS 原生应用支持（Apple Notes、Reminders、Bear Notes、Things、iMessage、Obsidian），通过 peekaboo 还能实现 UI 自动化。

### 7.3 智能家居全面覆盖
飞利浦 Hue 灯光、Sonos/BluOS 音响、Eight Sleep 智能床垫、RTSP 摄像头，覆盖主要智能家居品牌。

### 7.4 开发者工作流完整
从 GitHub Issue 自动修复、PR 管理、CI 监控到 AI 编码 Agent 委托，形成完整的开发自动化链条。

### 7.5 中国本地化支持
飞书文档/云盘/知识库、QQ 频道/媒体/提醒，适配中国用户常用平台。

### 7.6 多 AI Agent 协作
coding-agent、oracle、acp-router 等技能实现了 AI Agent 之间的协作和委托，可以同时利用多个 AI 模型的能力。

---

## 8. 与扩展(Extensions)的关系

Skills 是 OpenClaw 三大插件类型之一，与 Channel 和 Provider 并列：

```
OpenClaw 插件生态
├── Channel 插件 (25+)  → 消息通道接入（WhatsApp/Telegram/Slack/...）
├── Provider 插件 (40+) → AI 模型接入（OpenAI/Anthropic/Google/...）
└── Skill 插件 (68)     → 能力扩展（本文分析的技能）
```

Skills 依赖 Channel 插件收发消息，依赖 Provider 插件调用 AI 模型。三者共同构成了 OpenClaw 的完整功能矩阵。

---

## 9. 总结

OpenClaw 内置了 **68 个 Skills**，覆盖了从生产力工具、通信消息、开发者工具到智能家居、多媒体处理的广泛场景。其核心设计理念是：

- **声明式定义**：通过 SKILL.md 声明技能能力和使用方式
- **按需激活**：Agent 根据用户意图自动匹配和激活技能
- **CLI 优先**：大多数技能封装现有 CLI 工具，降低开发成本
- **生态丰富**：从密码管理到外卖订餐，从 AI 编码到智能家居，覆盖日常生活和工作的方方面面

这使得 OpenClaw 不仅是一个 AI 聊天机器人，而是一个真正意义上的**个人 AI 助手平台**。
