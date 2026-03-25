# OpenCode Server API 参考文档

> 分析日期: 2026-03-25
> 基于 opencode 代码仓库分析

---

## 概述

OpenCode Server 是一个基于 **Hono** 框架构建的 HTTP 服务器，提供完整的 REST API 用于：
- 会话管理
- 文件操作
- 项目管理
- 认证授权
- AI 提供者集成
- MCP (Model Context Protocol) 支持

所有 API 端点使用 OpenAPI 规范进行描述，支持 JSON 格式的请求和响应。

---

## API 端点总览

| 模块 | 端点数量 | 功能 |
|------|----------|------|
| 认证 (Auth) | 2 | 提供者认证管理 |
| 项目 (Project) | 4 | 项目管理 |
| 文件 (File) | 6 | 文件操作 |
| 会话 (Session) | 26 | AI 会话管理 |
| 终端 (PTY) | 6 | 终端会话管理 |
| 配置 (Config) | 3 | 配置管理 |
| 权限 (Permission) | 2 | 权限请求处理 |
| 问题 (Question) | 3 | 问题处理 |
| 全局 (Global) | 5 | 全局功能 |
| 提供者 (Provider) | 4 | AI 提供者管理 |
| MCP | 7 | Model Context Protocol |
| 实验性 (Experimental) | 8+ | 实验性功能 |
| TUI | 12 | 终端用户界面 |
| 系统 | 10+ | 系统功能 |

---

## 1. 认证管理 (Auth)

管理 AI 提供者的认证凭据。

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| PUT | `/auth/:providerID` | 设置认证凭据 |
| DELETE | `/auth/:providerID` | 移除认证凭据 |

### 路径参数

| 参数 | 类型 | 描述 |
|------|------|------|
| `providerID` | string | AI 提供者标识符 |

---

## 2. 项目管理 (Project)

管理项目实例和 Git 仓库。

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| GET | `/project/` | 列出所有项目 |
| GET | `/project/current` | 获取当前项目 |
| POST | `/project/git/init` | 初始化 git 仓库 |
| PATCH | `/project/:projectID` | 更新项目信息 |

### 路径参数

| 参数 | 类型 | 描述 |
|------|------|------|
| `projectID` | string | 项目标识符 |

---

## 3. 文件操作 (File)

提供文件系统操作功能。

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| GET | `/file/find` | 搜索文本 |
| GET | `/file/find/file` | 搜索文件 |
| GET | `/file/find/symbol` | 查找符号 |
| GET | `/file/file` | 列出文件 |
| GET | `/file/file/content` | 读取文件内容 |
| GET | `/file/file/status` | 获取文件状态 |

### 查询参数

| 参数 | 类型 | 描述 |
|------|------|------|
| `path` | string | 文件路径 |
| `search` | string | 搜索关键词 |
| `limit` | number | 结果数量限制 |
| `cursor` | string | 分页游标 |

---

## 4. 会话管理 (Session)

核心 API，管理 AI 对话会话。

### 4.1 会话基础操作

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| GET | `/session/` | 列出会话 |
| GET | `/session/status` | 获取会话状态 |
| POST | `/session/` | 创建会话 |
| DELETE | `/session/:sessionID` | 删除会话 |
| PATCH | `/session/:sessionID` | 更新会话 |

### 4.2 会话生命周期

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| POST | `/session/:sessionID/init` | 初始化会话 |
| POST | `/session/:sessionID/fork` | 分支会话 |
| POST | `/session/:sessionID/abort` | 中止会话 |
| POST | `/session/:sessionID/share` | 共享会话 |
| DELETE | `/session/:sessionID/share` | 取消共享 |
| POST | `/session/:sessionID/summarize` | 摘要会话 |

### 4.3 消息操作

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| GET | `/session/:sessionID/message` | 获取消息列表 |
| GET | `/session/:sessionID/message/:messageID` | 获取特定消息 |
| DELETE | `/session/:sessionID/message/:messageID` | 删除消息 |
| DELETE | `/session/:sessionID/message/:messageID/part/:partID` | 删除消息部分 |
| PATCH | `/session/:sessionID/message/:messageID/part/:partID` | 更新消息部分 |
| POST | `/session/:sessionID/message` | 发送消息 |
| POST | `/session/:sessionID/prompt_async` | 异步发送消息 |

### 4.4 其他会话操作

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| GET | `/session/:sessionID/children` | 获取子会话 |
| GET | `/session/:sessionID/todo` | 获取待办事项 |
| GET | `/session/:sessionID/diff` | 获取消息差异 |
| POST | `/session/:sessionID/command` | 发送命令 |
| POST | `/session/:sessionID/shell` | 执行 shell 命令 |
| POST | `/session/:sessionID/revert` | 撤消消息 |
| POST | `/session/:sessionID/unrevert` | 恢复撤消 |

### 路径参数

| 参数 | 类型 | 描述 |
|------|------|------|
| `sessionID` | string | 会话标识符 |
| `messageID` | string | 消息标识符 |
| `partID` | string | 消息部分标识符 |

---

## 5. 终端会话 (PTY)

管理终端会话，支持实时交互。

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| GET | `/pty/` | 列出 PTY 会话 |
| POST | `/pty/` | 创建 PTY 会话 |
| GET | `/pty/:ptyID` | 获取 PTY 会话 |
| PUT | `/pty/:ptyID` | 更新 PTY 会话 |
| DELETE | `/pty/:ptyID` | 删除 PTY 会话 |
| GET | `/pty/:ptyID/connect` | 连接 PTY 会话 (WebSocket) |

### WebSocket 连接

`GET /pty/:ptyID/connect` 返回 WebSocket 升级响应，用于实时终端交互。

---

## 6. 配置管理 (Config)

管理项目配置。

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| GET | `/config/` | 获取配置 |
| PATCH | `/config/` | 更新配置 |
| GET | `/config/providers` | 列出配置提供者 |

---

## 7. 权限管理 (Permission)

处理权限请求。

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| GET | `/permission/` | 列出待处理权限 |
| POST | `/permission/:requestID/reply` | 回应权限请求 |

---

## 8. 问题管理 (Question)

处理用户问题。

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| GET | `/question/` | 列出待处理问题 |
| POST | `/question/:requestID/reply` | 回应问题 |
| POST | `/question/:requestID/reject` | 拒绝问题 |

---

## 9. 全局功能 (Global)

全局级别的操作。

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| GET | `/global/health` | 健康检查 |
| GET | `/global/event` | 全局事件订阅 (SSE) |
| GET | `/global/config` | 获取全局配置 |
| PATCH | `/global/config` | 更新全局配置 |
| POST | `/global/dispose` | 释放实例 |
| POST | `/global/upgrade` | 升级 opencode |

---

## 10. 提供者管理 (Provider)

管理 AI 提供者。

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| GET | `/provider/` | 列出提供者 |
| GET | `/provider/auth` | 获取提供者认证方法 |
| POST | `/provider/:providerID/oauth/authorize` | OAuth 授权 |
| POST | `/provider/:providerID/oauth/callback` | OAuth 回调 |

---

## 11. MCP (Model Context Protocol)

Model Context Protocol 相关操作。

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| GET | `/mcp/` | 获取 MCP 状态 |
| POST | `/mcp/` | 添加 MCP 服务器 |
| POST | `/mcp/:name/auth` | 开始 MCP OAuth |
| POST | `/mcp/:name/auth/callback` | 完成 MCP OAuth |
| POST | `/mcp/:name/auth/authenticate` | 认证 MCP OAuth |
| DELETE | `/mcp/:name/auth` | 移除 MCP OAuth |
| POST | `/mcp/:name/connect` | 连接 MCP 服务器 |
| POST | `/mcp/:name/disconnect` | 断开 MCP 服务器 |

---

## 12. 实验性功能 (Experimental)

### 12.1 工具管理

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| GET | `/experimental/tool/ids` | 列出工具 ID |
| GET | `/experimental/tool` | 列出工具 |

### 12.2 工作树管理

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| POST | `/experimental/worktree` | 创建工作树 |
| GET | `/experimental/worktree` | 列出工作树 |
| DELETE | `/experimental/worktree` | 移除工作树 |
| POST | `/experimental/worktree/reset` | 重置工作树 |

### 12.3 工作空间管理

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| POST | `/experimental/workspace/` | 创建工作空间 |
| GET | `/experimental/workspace/` | 列出工作空间 |
| DELETE | `/experimental/workspace/:id` | 移除工作空间 |

### 12.4 其他

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| GET | `/experimental/session` | 列出全局会话 |
| GET | `/experimental/resource` | 获取 MCP 资源 |

---

## 13. TUI (Terminal User Interface)

终端用户界面控制 API。

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| POST | `/tui/append-prompt` | 追加提示 |
| POST | `/tui/open-help` | 打开帮助对话框 |
| POST | `/tui/open-sessions` | 打开会话对话框 |
| POST | `/tui/open-themes` | 打开主题对话框 |
| POST | `/tui/open-models` | 打开模型对话框 |
| POST | `/tui/submit-prompt` | 提交提示 |
| POST | `/tui/clear-prompt` | 清除提示 |
| POST | `/tui/execute-command` | 执行命令 |
| POST | `/tui/show-toast` | 显示提示 |
| POST | `/tui/publish` | 发布 TUI 事件 |
| POST | `/tui/select-session` | 选择会话 |
| GET | `/tui/control/next` | 获取下一个 TUI 请求 |
| POST | `/tui/control/response` | 提交 TUI 响应 |

---

## 14. 系统功能

### 14.1 文档和元信息

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| GET | `/doc` | 获取 OpenAPI 文档 |

### 14.2 实例管理

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| POST | `/instance/dispose` | 释放实例 |

### 14.3 系统信息

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| GET | `/path` | 获取路径信息 |
| GET | `/vcs` | 获取 VCS 信息 |
| GET | `/command` | 列出命令 |
| POST | `/log` | 写入日志 |

### 14.4 功能列表

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| GET | `/agent` | 列出代理 |
| GET | `/skill` | 列出技能 |
| GET | `/lsp` | 获取 LSP 状态 |
| GET | `/formatter` | 获取格式化器状态 |

---

## 15. 事件订阅 (Event)

| 方法 | 路径 | 功能描述 |
|------|------|---------|
| GET | `/event` | 订阅事件 (SSE) |

### Server-Sent Events (SSE)

事件订阅使用 SSE 协议，客户端可以实时接收服务器推送的事件。

---

## 数据结构

### Session.Info

```typescript
interface SessionInfo {
  id: string
  title?: string
  createdAt: Date
  updatedAt: Date
  status: 'active' | 'completed' | 'aborted'
  messageCount: number
}
```

### MessageV2

```typescript
interface MessageV2 {
  id: string
  role: 'user' | 'assistant' | 'system'
  parts: MessagePart[]
  createdAt: Date
}
```

### File.Content

```typescript
interface FileContent {
  path: string
  content: string
  encoding: 'utf-8' | 'base64'
  size: number
}
```

### Project.Info

```typescript
interface ProjectInfo {
  id: string
  name: string
  path: string
  createdAt: Date
}
```

### Config.Info

```typescript
interface ConfigInfo {
  model?: string
  provider?: string
  temperature?: number
  maxTokens?: number
}
```

---

## 认证

API 支持 Basic Auth 认证方式。在请求头中添加：

```
Authorization: Basic <base64(username:password)>
```

---

## 错误处理

API 使用统一的错误响应格式：

```typescript
interface ErrorResponse {
  error: {
    code: string
    message: string
    details?: unknown
  }
}
```

### 常见错误码

| 状态码 | 描述 |
|--------|------|
| 400 | 请求参数错误 |
| 401 | 未认证 |
| 403 | 权限不足 |
| 404 | 资源不存在 |
| 500 | 服务器内部错误 |

---

## 代理功能

未匹配的路由会代理到 `app.opencode.ai`，支持 Web 应用的动态加载。

---

## 附录：完整 API 端点列表

### 按模块分类

#### Auth (2)
- `PUT /auth/:providerID`
- `DELETE /auth/:providerID`

#### Project (4)
- `GET /project/`
- `GET /project/current`
- `POST /project/git/init`
- `PATCH /project/:projectID`

#### File (6)
- `GET /file/find`
- `GET /file/find/file`
- `GET /file/find/symbol`
- `GET /file/file`
- `GET /file/file/content`
- `GET /file/file/status`

#### Session (26)
- `GET /session/`
- `GET /session/status`
- `GET /session/:sessionID`
- `GET /session/:sessionID/children`
- `GET /session/:sessionID/todo`
- `GET /session/:sessionID/diff`
- `GET /session/:sessionID/message`
- `GET /session/:sessionID/message/:messageID`
- `POST /session/`
- `POST /session/:sessionID/init`
- `POST /session/:sessionID/fork`
- `POST /session/:sessionID/abort`
- `POST /session/:sessionID/share`
- `POST /session/:sessionID/summarize`
- `POST /session/:sessionID/message`
- `POST /session/:sessionID/prompt_async`
- `POST /session/:sessionID/command`
- `POST /session/:sessionID/shell`
- `POST /session/:sessionID/revert`
- `POST /session/:sessionID/unrevert`
- `DELETE /session/:sessionID`
- `DELETE /session/:sessionID/share`
- `DELETE /session/:sessionID/message/:messageID`
- `DELETE /session/:sessionID/message/:messageID/part/:partID`
- `PATCH /session/:sessionID`
- `PATCH /session/:sessionID/message/:messageID/part/:partID`

#### PTY (6)
- `GET /pty/`
- `GET /pty/:ptyID`
- `GET /pty/:ptyID/connect`
- `POST /pty/`
- `PUT /pty/:ptyID`
- `DELETE /pty/:ptyID`

#### Config (3)
- `GET /config/`
- `GET /config/providers`
- `PATCH /config/`

#### Permission (2)
- `GET /permission/`
- `POST /permission/:requestID/reply`

#### Question (3)
- `GET /question/`
- `POST /question/:requestID/reply`
- `POST /question/:requestID/reject`

#### Global (5)
- `GET /global/health`
- `GET /global/event`
- `GET /global/config`
- `PATCH /global/config`
- `POST /global/dispose`
- `POST /global/upgrade`

#### Provider (4)
- `GET /provider/`
- `GET /provider/auth`
- `POST /provider/:providerID/oauth/authorize`
- `POST /provider/:providerID/oauth/callback`

#### MCP (8)
- `GET /mcp/`
- `POST /mcp/`
- `POST /mcp/:name/auth`
- `POST /mcp/:name/auth/callback`
- `POST /mcp/:name/auth/authenticate`
- `DELETE /mcp/:name/auth`
- `POST /mcp/:name/connect`
- `POST /mcp/:name/disconnect`

#### Experimental (8+)
- `GET /experimental/tool/ids`
- `GET /experimental/tool`
- `POST /experimental/worktree`
- `GET /experimental/worktree`
- `DELETE /experimental/worktree`
- `POST /experimental/worktree/reset`
- `GET /experimental/session`
- `GET /experimental/resource`
- `POST /experimental/workspace/`
- `GET /experimental/workspace/`
- `DELETE /experimental/workspace/:id`

#### TUI (12)
- `POST /tui/append-prompt`
- `POST /tui/open-help`
- `POST /tui/open-sessions`
- `POST /tui/open-themes`
- `POST /tui/open-models`
- `POST /tui/submit-prompt`
- `POST /tui/clear-prompt`
- `POST /tui/execute-command`
- `POST /tui/show-toast`
- `POST /tui/publish`
- `POST /tui/select-session`
- `GET /tui/control/next`
- `POST /tui/control/response`

#### System (10+)
- `GET /doc`
- `POST /instance/dispose`
- `GET /path`
- `GET /vcs`
- `GET /command`
- `POST /log`
- `GET /agent`
- `GET /skill`
- `GET /lsp`
- `GET /formatter`
- `GET /event`

---

*报告生成工具: Claude Code*
*基于 opencode 代码仓库分析*
