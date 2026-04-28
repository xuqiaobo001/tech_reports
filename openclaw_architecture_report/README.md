# OpenClaw 源码架构分析报告

> 分析日期：2026-04-27

## 1. 项目定位

**OpenClaw** 是一个**本地优先的个人 AI 助手网关**（Personal AI Assistant Gateway）。它作为 AI 模型与各种通信平台之间的智能中间层，让用户可以通过 WhatsApp、Telegram、Slack、Discord 等 20+ 消息平台与 AI 助手交互。

核心理念：**单用户、本地部署、多通道、插件化**。

---

## 2. 整体架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                        用户接入层 (Clients)                          │
│                                                                     │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐  │
│  │ CLI  │ │ iOS  │ │macOS │ │Andrd │ │ Web  │ │Canvas│ │WebSocket│ │
│  └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘  │
└─────┼────────┼────────┼────────┼────────┼────────┼────────┼───────┘
      │        │        │        │        │        │        │
      ▼        ▼        ▼        ▼        ▼        ▼        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Gateway 网关层 (WebSocket + RPC)                   │
│                                                                     │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐                │
│  │  Auth 认证    │ │ Method Router│ │ Conn Mgmt    │                │
│  │  (Token+设备) │ │ (JSON-RPC)   │ │ (连接生命周期)│                │
│  └──────────────┘ └──────────────┘ └──────────────┘                │
└─────────────────────────┬───────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     Agent 代理执行层                                  │
│                                                                     │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐  │
│  │ Session 管理 │ │ Model 选择  │ │ Tool 执行   │ │ Fallback    │  │
│  │ (会话上下文) │ │ (Provider路由)│ │ (工具调用)  │ │ (降级策略)  │  │
│  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘ └─────────────┘  │
└─────────┼───────────────┼───────────────┼──────────────────────────┘
          │               │               │
          ▼               ▼               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Plugin 插件系统 (Manifest + Lazy Load)             │
│                                                                     │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │ Channel 插件 (25+)│  │ Provider 插件(40+)│  │ Skill 插件 (55+) │  │
│  │                  │  │                  │  │                  │  │
│  │ • WhatsApp      │  │ • OpenAI         │  │ • GitHub         │  │
│  │ • Telegram      │  │ • Anthropic      │  │ • Notion         │  │
│  │ • Slack         │  │ • Google         │  │ • Filesystem     │  │
│  │ • Discord       │  │ • DeepSeek       │  │ • Code Analysis  │  │
│  │ • Signal        │  │ • Ollama(本地)   │  │ • Web Search     │  │
│  │ • iMessage      │  │ • LM Studio      │  │ • Memory         │  │
│  │ • WeChat/QQ/LINE│  │ • Groq/Cerebras  │  │ • ...            │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    基础设施层                                         │
│                                                                     │
│  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐          │
│  │ Config    │ │ Security  │ │ Storage   │ │ Daemon    │          │
│  │ (YAML+校验)│ │ (SSRF/沙箱)│ │ (SQLite) │ │ (后台服务) │          │
│  └───────────┘ └───────────┘ └───────────┘ └───────────┘          │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. 核心模块详解

### 3.1 Gateway 网关系统 (`src/gateway/`)

**职责**：中央控制面板和通信枢纽。

| 组件 | 说明 |
|------|------|
| Gateway Server | WebSocket 服务器，提供实时双向通信 |
| Protocol | 基于 JSON-RPC 的协议，支持版本管理 |
| Authentication | 基于 Token 的认证，绑定设备身份 |
| Client Management | 处理多种客户端类型（CLI、移动端、桌面端） |
| Method Router | 请求路由，支持操作者作用域 |

**架构模式**：事件驱动的 WebSocket 服务器 + RPC 协议。

### 3.2 Agent 代理系统 (`src/agents/`)

**职责**：AI 模型交互与 Agent 生命周期管理。

| 组件 | 说明 |
|------|------|
| Agent Command | Agent 执行入口点 |
| Model Selection | Provider 和模型路由逻辑 |
| Auth Profiles | 模型提供商的凭证管理 |
| Model Fallback | Provider 故障时的优雅降级 |
| Tool Execution | Agent 工具调用框架 |
| Session Management | 对话状态和上下文管理 |
| Thinking Modes | 扩展推理支持 |

**架构模式**：Provider 抽象 + Fallback 链。

### 3.3 Plugin 插件系统 (`src/plugins/`)

**职责**：第三方集成的可扩展框架。

| 组件 | 说明 |
|------|------|
| Plugin Loader | 动态插件发现和加载 |
| Manifest Registry | 插件元数据和能力声明 |
| Runtime Registry | 活跃的插件实例管理 |
| Activation Planner | 延迟加载策略 |
| Provider Discovery | 模型提供商插件发现 |
| Channel Discovery | 消息通道插件发现 |

**架构模式**：Manifest 驱动的插件架构 + 延迟加载。

每个插件通过 `manifest.json5` 声明能力：

```json5
{
  openclaw: {
    format: "code" | "bundle",
    extensions: ["./index.ts"],
    channel: { ... },           // 通道插件
    provider: { ... },          // 模型提供商插件
    skills: [...],              // 技能插件
    setupEntry: "./setup.ts"    // 可选的设置向导
  }
}
```

### 3.4 Channel 通道框架 (`src/channels/`)

**职责**：消息平台抽象层。

| 组件 | 说明 |
|------|------|
| Channel Plugins | 平台特定实现 |
| Transport Layer | 底层协议处理 |
| Message Normalization | 将平台消息转换为内部格式 |
| Thread Bindings | 会话线程映射 |
| Presence & Typing | 实时状态指示器 |
| Media Handling | 文件和媒体上传/下载 |

**架构模式**：适配器模式 + 平台特定插件。

### 3.5 配置系统 (`src/config/`)

**职责**：层级化配置管理。

- 多来源配置加载（文件、环境变量、默认值）
- 基于 Schema 的配置校验（Zod）
- 配置 Schema 版本管理和迁移
- 环境变量替换
- 支持 `$include` 引用
- 安全凭证存储

### 3.6 安全系统 (`src/security/`)

**职责**：安全策略与执行。

- **SSRF 防护**：请求过滤和验证
- **沙箱隔离**：基于 Docker 的容器隔离
- **审批工作流**：敏感操作需人工批准
- **DM 配对**：未知发送者默认需要配对验证

---

## 4. 核心业务逻辑 — 消息流转

```
用户在 WhatsApp 发消息 "帮我总结今天的邮件"
        │
        ▼
① Channel 插件接收原始消息
        │
        ▼
② 消息标准化 → 内部统一格式 (Channel Normalization)
        │
        ▼
③ Gateway 认证 + 路由到正确的 Agent
        │
        ▼
④ 查找/创建 Session (会话上下文 + 历史记录)
        │
        ▼
⑤ Agent 选择合适的 Model Provider (根据配置、可用性、Fallback 链)
        │
        ▼
⑥ 调用 LLM API (如 Anthropic Claude)
   ┌─ 如果 LLM 请求使用 Tool ─────────┐
   │  ⑦ 执行 Skill/Tool (如读取邮件)    │
   │  ⑧ 将 Tool 结果返回给 LLM          │
   └──────────────────────────────────┘
        │
        ▼
⑨ 生成最终回复
        │
        ▼
⑩ 回复经 Channel 插件发回 WhatsApp
```

---

## 5. 插件发现与加载流程

```
Gateway 启动
    ↓
扫描 extensions/ 目录，发现插件
    ↓
解析并验证 Manifest (manifest.json5)
    ↓
注册能力声明 (channels, providers, tools)
    ↓
按需延迟加载 (仅在实际使用时加载运行时)
    ↓
首次使用时初始化运行时
```

---

## 6. 关键架构决策

| 决策 | 说明 |
|------|------|
| **插件化架构** | 核心 Gateway 不依赖任何具体实现，所有通道、模型、技能都是插件 |
| **Manifest-First** | 插件通过 manifest 声明能力，系统发现但不立即加载 |
| **Lazy Loading** | 插件按需加载，减少启动时间和内存占用 |
| **Provider 抽象** | 统一的 Model Provider 接口，屏蔽不同 LLM 厂商的 API 差异 |
| **Security-First** | 默认需要 DM 配对、沙箱隔离、SSRF 防护、敏感操作需人工审批 |
| **Session 模型** | 无状态 Agent + 持久化对话历史 (SQLite) |
| **Docker 沙箱** | 非主会话在容器中隔离执行 |
| **多 Agent 路由** | 不同通道/账号可路由到不同 Agent 配置 |
| **Type Safety** | 全面 TypeScript + Zod 运行时校验 |
| **配置即代码** | YAML 配置 + Schema 校验 + 环境变量替换 |

---

## 7. 核心类型抽象

### Plugin 接口

```typescript
interface OpenClawPlugin {
  id: string;
  format: PluginFormat;
  manifest: PluginManifest;
  runtime?: PluginRuntime;
}
```

### Channel 接口

```typescript
interface ChannelPlugin {
  channelId: string;
  start: () => Promise<void>;
  stop: () => Promise<void>;
  send: (message: OutboundMessage) => Promise<void>;
}
```

### Model Provider 接口

```typescript
interface ModelProvider {
  providerId: string;
  models: ModelCatalogEntry[];
  createChatCompletion: (params) => AsyncIterable<ChatChunk>;
}
```

### Gateway Protocol

```typescript
interface GatewayRequest {
  method: string;
  params?: unknown;
  id?: string | number;
}

interface GatewayResponse {
  result?: unknown;
  error?: GatewayError;
  id: string | number;
}
```

---

## 8. 支持的平台与通道

### 消息通道 (25+)

| 类别 | 通道 |
|------|------|
| 主流 | WhatsApp, Telegram, Slack, Discord, Signal |
| 企业 | Microsoft Teams, Google Chat, Matrix |
| 社交 | Discord, Twitch, Twitter/X |
| 亚洲市场 | WeChat, QQ, LINE, Zalo |
| 其他 | IRC, Mattermost, Nextcloud Talk, iMessage |

### 模型提供商 (40+)

| 类别 | 提供商 |
|------|--------|
| 主流 | OpenAI, Anthropic, Google, DeepSeek |
| 开源推理 | Groq, Together, Cerebras |
| 专业化 | Perplexity, Exa, GitHub Copilot |
| 本地部署 | Ollama, LM Studio, vLLM, SGLang |

### 原生应用

| 平台 | 特性 |
|------|------|
| macOS | 菜单栏应用，支持 Voice Wake |
| iOS | 语音触发 + Canvas 界面 |
| Android | 后台服务 + 通知推送 |

### 接入方式

- **CLI**：`openclaw` 命令行工具
- **WebSocket**：实时 Gateway 协议
- **HTTP/REST**：管理 API
- **原生应用**：iOS / Android / macOS

---

## 9. 目录结构

```
openclaw/
├── src/                    # 核心源码
│   ├── agents/            #   AI Agent 执行引擎 (模型选择、Fallback、工具调用)
│   ├── gateway/           #   WebSocket 网关 (认证、路由、RPC协议)
│   ├── plugins/           #   插件系统 (发现、加载、注册)
│   ├── channels/          #   通道框架 (消息标准化、线程绑定)
│   ├── tools/             #   工具执行框架
│   ├── skills/            #   技能系统
│   ├── config/            #   配置管理 (YAML + 校验 + 迁移)
│   ├── security/          #   安全策略 (SSRF、沙箱、审批)
│   ├── sessions/          #   会话管理
│   ├── cli/               #   命令行界面
│   ├── canvas-host/       #   Canvas 可视化界面
│   ├── daemon/            #   守护进程管理
│   ├── hooks/             #   钩子系统
│   ├── acp/               #   Agent Client Protocol 实现
│   └── plugin-sdk/        #   插件开发 SDK
├── extensions/             # 127+ 扩展插件
│   ├── anthropic/         #   Anthropic 模型提供商
│   ├── openai/            #   OpenAI 模型提供商
│   ├── telegram/          #   Telegram 通道
│   ├── whatsapp/          #   WhatsApp 通道
│   ├── discord/           #   Discord 通道
│   ├── signal/            #   Signal 通道
│   ├── ollama/            #   Ollama 本地模型
│   └── ...                #   更多通道/提供商/技能插件
├── apps/                   # 原生应用
│   ├── ios/               #   iOS (支持 Voice Wake)
│   ├── android/           #   Android (后台服务)
│   ├── macos/             #   macOS (菜单栏应用)
│   └── shared/            #   共享代码 (OpenClawKit)
├── skills/                 # 55+ 内置技能
├── ui/                     # Web 界面
├── packages/               # 共享包
├── scripts/                # 构建和工具脚本
├── vendor/                 # 第三方依赖
│   └── a2ui/              #   A2UI 规范和渲染器
└── docs/                   # 多语言文档
```

---

## 10. 测试与质量保障

| 方面 | 说明 |
|------|------|
| 单元/集成测试 | Vitest 框架 |
| 插件契约测试 | 确保插件与核心接口一致 |
| Live Model 测试 | 可选的真实模型调用测试 |
| 性能分析 | 热路径性能剖析 |
| 代码质量 | Oxlint + SwiftLint + CodeQL |
| 安全扫描 | detect-secrets + CodeQL Security |
| CI/CD | GitHub Actions 全流程自动化 |

---

## 11. 总结

OpenClaw 是一个设计精良的**个人 AI 助手网关**，通过插件化架构将 25+ 消息通道、40+ AI 模型、55+ 技能能力连接在一起。其核心价值在于：

- **统一入口**：一个 AI 助手覆盖所有通信平台
- **插件扩展**：127+ 扩展插件，覆盖主流通道和模型
- **本地部署**：数据留在本地，隐私优先
- **安全设计**：多层安全保障，从认证到沙箱隔离
- **开发者友好**：完善的 Plugin SDK，TypeScript 全栈类型安全
- **多端覆盖**：CLI + Web + iOS + Android + macOS 全平台支持
