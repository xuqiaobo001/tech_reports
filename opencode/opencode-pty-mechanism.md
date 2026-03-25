# OpenCode PTY 终端会话管理机制

> 分析日期: 2026-03-25
> 基于 opencode 代码仓库分析

---

## 概述

PTY (Pseudo-Terminal) 是 OpenCode 中一个关键的抽象层，它为不同类型的客户端（Web、Desktop、CLI）提供了一致的终端体验。本文档详细分析 PTY 的设计原理、核心功能和实现机制。

---

## 1. 什么是 PTY？

### 1.1 定义

**PTY (Pseudo-Terminal)** 是一种操作系统级别的虚拟终端技术，允许程序模拟真实的终端行为。它创建了一个虚拟的终端环境，让程序以为自己在真实的终端中运行。

### 1.2 在 OpenCode 中的作用

PTY 模块允许 OpenCode 在服务器端运行终端进程，并通过 WebSocket 将终端的输入输出实时传递给各种客户端。

```
┌─────────────┐     WebSocket      ┌─────────────┐     PTY      ┌─────────────┐
│   客户端     │ ◄────────────────► │   Server    │ ◄─────────► │  Shell 进程  │
│  (浏览器)    │    实时双向通信      │  (OpenCode) │             │  (bash/zsh) │
└─────────────┘                    └─────────────┘             └─────────────┘
```

---

## 2. 架构设计

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         客户端层                                 │
│   ┌─────────┐    ┌─────────────┐    ┌─────────────────────┐    │
│   │   Web   │    │   Desktop   │    │    CLI (Remote)     │    │
│   │ 浏览器   │    │  Tauri App  │    │   远程连接模式       │    │
│   └────┬────┘    └──────┬──────┘    └──────────┬──────────┘    │
│        │                │                      │                │
│        └────────────────┼──────────────────────┘                │
│                         │ WebSocket                              │
│                         ▼                                        │
└─────────────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────┼───────────────────────────────────────┐
│                         ▼                                        │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │                    Pty.Service                          │   │
│   │                                                         │   │
│   │   核心功能:                                              │   │
│   │   • create()  - 创建终端会话                             │   │
│   │   • connect() - 建立 WebSocket 连接                      │   │
│   │   • write()   - 向终端写入数据                           │   │
│   │   • resize()  - 调整终端大小                             │   │
│   │   • remove()  - 销毁终端会话                             │   │
│   │   • list()    - 列出所有会话                             │   │
│   └──────────────────────────┬──────────────────────────────┘   │
│                              │                                   │
│                      服务端 PTY 层                                │
│                              │                                   │
│                              ▼                                   │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │              PTY 进程 (bun-pty)                          │   │
│   │                                                         │   │
│   │    spawn(bash/zsh/fish)  →  运行在服务器上的真实 Shell    │   │
│   │                                                         │   │
│   └─────────────────────────────────────────────────────────┘   │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 核心组件

| 组件 | 位置 | 职责 |
|------|------|------|
| `Pty.Service` | `packages/opencode/src/pty/index.ts` | PTY 会话管理服务 |
| `PtyRoutes` | `packages/opencode/src/server/routes/pty.ts` | HTTP/WebSocket 路由 |
| `bun-pty` | 外部依赖 | 底层 PTY 进程管理 |

---

## 3. 数据结构

### 3.1 会话信息 (Info)

```typescript
interface PtyInfo {
  id: string           // 会话唯一标识
  title: string        // 会话标题
  command: string      // 执行的命令 (如 /bin/bash)
  args: string[]       // 命令参数
  cwd: string          // 工作目录
  status: "running" | "exited"  // 运行状态
  pid: number          // 进程 ID
}
```

### 3.2 创建输入 (CreateInput)

```typescript
interface PtyCreateInput {
  command?: string              // 要执行的命令
  args?: string[]               // 命令参数
  cwd?: string                  // 工作目录
  title?: string                // 会话标题
  env?: Record<string, string>  // 环境变量
}
```

### 3.3 更新输入 (UpdateInput)

```typescript
interface PtyUpdateInput {
  title?: string                // 新标题
  size?: {
    rows: number                // 行数
    cols: number                // 列数
  }
}
```

### 3.4 内部状态 (Active Session)

```typescript
interface ActiveSession {
  info: Info                    // 会话信息
  process: IPty                 // PTY 进程实例
  buffer: string                // 输出缓冲区
  bufferCursor: number          // 缓冲区起始位置
  cursor: number                // 当前光标位置
  subscribers: Map<unknown, Socket>  // WebSocket 订阅者
}
```

---

## 4. API 端点

### 4.1 REST API

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| GET | `/pty/` | 列出所有 PTY 会话 |
| POST | `/pty/` | 创建新的 PTY 会话 |
| GET | `/pty/:ptyID` | 获取特定会话信息 |
| PUT | `/pty/:ptyID` | 更新会话（标题/大小） |
| DELETE | `/pty/:ptyID` | 删除并终止会话 |

### 4.2 WebSocket 端点

| 路径 | 功能描述 |
|------|---------|
| `GET /pty/:ptyID/connect` | 建立 WebSocket 连接，实时交互 |

### 4.3 WebSocket 协议

```
客户端 → 服务器: 键盘输入 (纯文本)
服务器 → 客户端: 终端输出 (纯文本)
服务器 → 客户端: 控制帧 (0x00 + JSON { cursor })
```

---

## 5. 核心功能详解

### 5.1 创建会话 (create)

```typescript
// 创建流程
async function create(input: CreateInput): Promise<Info> {
  // 1. 生成唯一 ID
  const id = PtyID.ascending()

  // 2. 确定命令和工作目录
  const command = input.command || Shell.preferred()  // 默认使用系统 shell
  const cwd = input.cwd || state.dir

  // 3. 准备环境变量
  const env = {
    ...process.env,
    ...input.env,
    TERM: "xterm-256color",
    OPENCODE_TERMINAL: "1"
  }

  // 4. 使用 bun-pty 启动进程
  const proc = spawn(command, args, { name: "xterm-256color", cwd, env })

  // 5. 设置输出监听
  proc.onData((chunk) => {
    // 更新光标位置
    session.cursor += chunk.length
    // 推送给所有订阅者
    for (const ws of session.subscribers.values()) {
      ws.send(chunk)
    }
    // 写入缓冲区
    session.buffer += chunk
  })

  // 6. 设置退出监听
  proc.onExit(({ exitCode }) => {
    session.info.status = "exited"
    Bus.publish(Event.Exited, { id, exitCode })
  })

  return session.info
}
```

### 5.2 连接会话 (connect)

```typescript
// 连接流程
async function connect(id: PtyID, ws: Socket, cursor?: number) {
  // 1. 获取会话
  const session = state.sessions.get(id)

  // 2. 注册订阅者
  session.subscribers.set(key, ws)

  // 3. 发送历史输出（从指定位置开始）
  const data = session.buffer.slice(offset)
  ws.send(data)

  // 4. 发送当前光标位置
  ws.send(meta(cursor))

  // 5. 返回消息处理器
  return {
    onMessage: (message) => session.process.write(message),
    onClose: () => session.subscribers.delete(key)
  }
}
```

### 5.3 缓冲机制

```
                    BUFFER_LIMIT (2MB)
                          │
    ┌─────────────────────┼─────────────────────┐
    │                     │                     │
    │     历史输出缓冲区    │     新输出          │
    │                     │                     │
    └─────────────────────┴─────────────────────┘
                          │
              bufferCursor (已丢弃的字节数)
                          │
              cursor (当前总位置)
```

**缓冲策略**：
- 最大缓冲 2MB 输出数据
- 超出限制时，丢弃最早的输出
- 新连接可以从任意历史位置开始读取

---

## 6. 事件系统

PTY 模块通过事件总线发布状态变化：

```typescript
export const Event = {
  Created: "pty.created",   // 会话创建
  Updated: "pty.updated",   // 会话更新
  Exited: "pty.exited",     // 进程退出
  Deleted: "pty.deleted",   // 会话删除
}
```

---

## 7. 使用场景

### 7.1 Web 界面嵌入式终端

```javascript
// 1. 创建终端会话
const response = await fetch('/pty/', {
  method: 'POST',
  body: JSON.stringify({
    title: 'My Terminal',
    cwd: '/project/myapp'
  })
})
const { id } = await response.json()

// 2. 建立 WebSocket 连接
const ws = new WebSocket(`/pty/${id}/connect`)

// 3. 处理输出
ws.onmessage = (event) => {
  if (typeof event.data === 'string') {
    terminal.write(event.data)  // 写入 xterm.js
  }
}

// 4. 发送输入
terminal.onData((data) => {
  ws.send(data)
})
```

### 7.2 AI 代理执行长时间命令

```javascript
// 创建运行开发服务器的会话
const session = await fetch('/pty/', {
  method: 'POST',
  body: JSON.stringify({
    command: 'npm',
    args: ['run', 'dev'],
    cwd: '/project/myapp',
    title: 'Dev Server'
  })
})

// AI 可以监控输出、在需要时发送命令
```

### 7.3 远程开发场景

```javascript
// 本地 CLI 连接到远程 Server 的 PTY
// 实现远程终端访问
const ws = new WebSocket('wss://remote-server/pty/session123/connect')
```

---

## 8. 多客户端支持

### 8.1 支持的客户端类型

| 客户端 | 使用方式 | 典型场景 |
|--------|----------|----------|
| **Web 浏览器** | WebSocket + xterm.js | 在浏览器中使用终端 |
| **Desktop (Tauri)** | WebSocket + xterm.js | 桌面应用内嵌终端 |
| **CLI (远程)** | WebSocket | 远程连接到 Server |

### 8.2 统一接口的优势

```
┌────────────────────────────────────────────────────────────┐
│                    统一的 WebSocket API                     │
│                                                            │
│   GET /pty/:id/connect                                     │
│   • 发送: 键盘输入 (string)                                 │
│   • 接收: 终端输出 (string) + 控制帧 (binary)               │
│                                                            │
└────────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
    ┌─────────┐         ┌─────────┐         ┌─────────┐
    │   Web   │         │ Desktop │         │   CLI   │
    └─────────┘         └─────────┘         └─────────┘
```

---

## 9. 与普通命令执行的区别

| 特性 | 普通命令执行 | PTY 会话 |
|------|-------------|----------|
| **交互性** | 一次性执行 | 持续交互 |
| **输出方式** | 执行完毕返回结果 | 实时流式输出 |
| **适用程序** | 简单命令 | vim、top、htop 等交互式程序 |
| **连接方式** | HTTP | WebSocket |
| **生命周期** | 请求-响应 | 长连接 |
| **多客户端** | 不支持 | 支持多订阅者 |

---

## 10. 技术细节

### 10.1 依赖

- **bun-pty**: Bun 运行时的 PTY 实现
- **xterm-256color**: 终端类型，支持 256 色

### 10.2 环境变量

创建 PTY 时会自动设置：

```bash
TERM=xterm-256color
OPENCODE_TERMINAL=1
```

### 10.3 资源管理

- 进程退出时自动清理
- 实例销毁时清理所有会话
- WebSocket 断开时移除订阅者

---

## 11. 核心价值总结

PTY 的抽象为 OpenCode 带来了以下价值：

| 价值 | 说明 |
|------|------|
| **客户端无关** | Web、Desktop、CLI 使用统一的 API |
| **实时交互** | 支持交互式程序（vim、top 等） |
| **多订阅者** | 一个终端可被多人同时查看 |
| **断线重连** | 历史输出缓冲，支持从任意位置恢复 |
| **远程访问** | 终端进程在服务器运行，客户端可远程访问 |
| **一致性体验** | 所有客户端获得相同的终端体验 |

---

## 附录：关键文件路径

| 文件 | 描述 |
|------|------|
| `packages/opencode/src/pty/index.ts` | PTY 核心服务实现 |
| `packages/opencode/src/pty/schema.ts` | 数据结构定义 |
| `packages/opencode/src/server/routes/pty.ts` | HTTP/WebSocket 路由 |

---

*报告生成工具: Claude Code*
*基于 opencode 代码仓库分析*
