# Harbor与Claude Code集成技术文档

## 概述

本文档详细说明Harbor框架如何集成Claude Code agent，包括架构设计、实现细节、数据流转和使用方法。

## 目录

- [架构设计](#架构设计)
- [核心组件](#核心组件)
- [安装流程](#安装流程)
- [运行机制](#运行机制)
- [轨迹转换系统](#轨迹转换系统)
- [MCP服务器集成](#mcp服务器集成)
- [配置说明](#配置说明)
- [使用示例](#使用示例)
- [数据收集与指标](#数据收集与指标)

---

## 架构设计

### 类继承关系

```
BaseAgent (抽象基类)
    ↓
BaseInstalledAgent (已安装agent基类)
    ↓
ClaudeCode (Claude Code具体实现)
```

### 核心特性

- **ATIF支持**: 完整支持Agent Trajectory Interchange Format轨迹格式
- **异步执行**: 基于async/await的异步操作模式
- **环境隔离**: 每个trial运行在独立的Docker/云环境中
- **指标收集**: 自动收集token使用、成本、执行时间等指标

---

## 核心组件

### 1. ClaudeCode类

**文件位置**: `src/harbor/agents/installed/claude_code.py`

**关键属性**:
```python
class ClaudeCode(BaseInstalledAgent):
    SUPPORTS_ATIF: bool = True  # 支持轨迹格式
    _max_thinking_tokens: int | None  # 思考token限制
    logs_dir: Path  # 日志目录
    model_name: str | None  # 模型名称
    mcp_servers: list[MCPServerConfig]  # MCP服务器配置
```

**核心方法**:

| 方法名 | 功能 | 说明 |
|--------|------|------|
| `name()` | 返回agent名称 | 返回"claude-code" |
| `setup()` | 安装和配置agent | 执行安装脚本 |
| `run()` | 执行任务 | 调用Claude CLI |
| `create_run_agent_commands()` | 生成执行命令 | 构建shell命令和环境变量 |
| `populate_context_post_run()` | 收集执行结果 | 转换轨迹并提取指标 |
| `_convert_events_to_trajectory()` | 轨迹转换 | JSONL → ATIF格式 |

---

## 安装流程

### 安装模板

**文件位置**: `src/harbor/agents/installed/install-claude-code.sh.j2`

**安装步骤**:

```bash
#!/bin/bash
set -euo pipefail

# 1. 安装依赖（curl、bash）
if command -v apk &> /dev/null; then
    apk add --no-cache curl bash
elif command -v apt-get &> /dev/null; then
    apt-get update && apt-get install -y curl
fi

# 2. 下载并安装Claude Code CLI
curl -fsSL https://claude.ai/install.sh | bash

# 3. 配置环境变量
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

### 版本控制

支持指定版本安装：
```bash
# 安装特定版本
curl -fsSL https://claude.ai/install.sh | bash -s -- v1.2.3
```

---

## 运行机制

### 命令生成流程

`create_run_agent_commands()` 方法生成两个执行命令：

#### 1. 环境准备命令

```bash
mkdir -p $CLAUDE_CONFIG_DIR/debug \
         $CLAUDE_CONFIG_DIR/projects/-app \
         $CLAUDE_CONFIG_DIR/shell-snapshots \
         $CLAUDE_CONFIG_DIR/statsig \
         $CLAUDE_CONFIG_DIR/todos

# 复制技能配置（如果存在）
if [ -d ~/.claude/skills ]; then
    cp -r ~/.claude/skills $CLAUDE_CONFIG_DIR/skills 2>/dev/null || true
fi

# 注册MCP服务器（如果配置）
echo '{"mcpServers": {...}}' > $CLAUDE_CONFIG_DIR/.claude.json
```

#### 2. Agent执行命令

```bash
claude --verbose \
       --output-format stream-json \
       --permission-mode bypassPermissions \
       -p "任务指令" \
       2>&1 </dev/null | tee /logs/agent/claude-code.txt
```

### 环境变量配置

| 环境变量 | 说明 | 示例值 |
|----------|------|--------|
| `ANTHROPIC_API_KEY` | API认证密钥 | `sk-ant-...` |
| `ANTHROPIC_MODEL` | 模型名称 | `claude-opus-4-1` |
| `ANTHROPIC_BASE_URL` | 自定义API端点 | `https://api.openrouter.ai/v1` |
| `CLAUDE_CONFIG_DIR` | 配置目录 | `/logs/agent/sessions` |
| `MAX_THINKING_TOKENS` | 思考token限制 | `10000` |
| `IS_SANDBOX` | 沙箱模式标识 | `1` |
| `FORCE_AUTO_BACKGROUND_TASKS` | 强制后台任务 | `1` |
| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | 禁用非必要流量 | `1` |

### 模型名称处理

```python
# 官方Anthropic API：去除provider前缀
"anthropic/claude-opus-4-1" → "claude-opus-4-1"

# 自定义API（OpenRouter等）：保留完整名称
"openrouter/anthropic/claude-opus-4-1" → "openrouter/anthropic/claude-opus-4-1"
```

---

## 轨迹转换系统

### 数据流程图

```
Claude Code执行
    ↓
生成JSONL日志文件
    ↓
读取并解析事件
    ↓
规范化事件数据
    ↓
转换为ATIF Step对象
    ↓
聚合指标和元数据
    ↓
生成Trajectory JSON
```

### 会话目录结构

```
/logs/agent/sessions/
└── projects/
    └── {project-hash}/
        ├── session-{timestamp}.jsonl  # 主会话日志
        └── session-{timestamp}-*.jsonl  # 子会话日志
```

### 事件类型映射

#### 1. 消息事件 (message)

**Claude Code格式**:
```json
{
  "type": "assistant",
  "message": {
    "role": "assistant",
    "content": "文本内容或content blocks",
    "model": "claude-opus-4-1",
    "usage": {
      "input_tokens": 1000,
      "output_tokens": 500,
      "cache_read_input_tokens": 200
    }
  },
  "timestamp": "2024-01-01T12:00:00Z"
}
```

**ATIF Step格式**:
```json
{
  "step_id": 1,
  "timestamp": "2024-01-01T12:00:00Z",
  "source": "agent",
  "message": "文本内容",
  "reasoning_content": "思考内容（如果有）",
  "model_name": "claude-opus-4-1",
  "metrics": {
    "prompt_tokens": 1200,
    "completion_tokens": 500,
    "cached_tokens": 200
  }
}
```

#### 2. 工具调用事件 (tool_call)

**Claude Code格式**:
```json
{
  "type": "assistant",
  "message": {
    "content": [
      {
        "type": "tool_use",
        "id": "toolu_123",
        "name": "bash",
        "input": {"command": "ls -la"}
      }
    ]
  }
}
```

**ATIF Step格式**:
```json
{
  "step_id": 2,
  "source": "agent",
  "message": "Executed bash toolu_123",
  "tool_calls": [
    {
      "tool_call_id": "toolu_123",
      "function_name": "bash",
      "arguments": {"command": "ls -la"}
    }
  ],
  "observation": {
    "results": [
      {
        "source_call_id": "toolu_123",
        "content": "[stdout]\ntotal 48\ndrwxr-xr-x ...",
        "subagent_trajectory_ref": null
      }
    ]
  }
}
```

### 转换核心逻辑

#### 提取文本、推理和工具调用

```python
def _extract_text_reasoning_tool_uses(content):
    """
    从content blocks中提取：
    1. 文本内容（text blocks）
    2. 推理内容（thinking/reasoning blocks）
    3. 工具调用（tool_use blocks）
    """
    text_parts = []
    reasoning_parts = []
    tool_blocks = []

    for block in content:
        if block["type"] == "tool_use":
            tool_blocks.append(block)
        elif block["type"] in {"thinking", "reasoning"}:
            reasoning_parts.append(block["text"])
        else:
            text_parts.append(block.get("text", ""))

    return text, reasoning, tool_blocks
```

#### 构建指标对象

```python
def _build_metrics(usage):
    """
    从usage对象构建Metrics：
    - prompt_tokens = input_tokens + cache_read_input_tokens
    - completion_tokens = output_tokens
    - cached_tokens = cache_read_input_tokens
    """
    return Metrics(
        prompt_tokens=usage["input_tokens"] + usage.get("cache_read_input_tokens", 0),
        completion_tokens=usage["output_tokens"],
        cached_tokens=usage.get("cache_read_input_tokens", 0),
        extra={k: v for k, v in usage.items()
               if k not in {"input_tokens", "output_tokens"}}
    )
```

#### 格式化工具结果

```python
def _format_tool_result(block, tool_use_result):
    """
    格式化工具执行结果：
    - stdout/stderr分别标注
    - exit_code非0时显示
    - 保留原始metadata
    """
    parts = []
    if tool_use_result:
        if tool_use_result.get("stdout"):
            parts.append(f"[stdout]\n{tool_use_result['stdout']}")
        if tool_use_result.get("stderr"):
            parts.append(f"[stderr]\n{tool_use_result['stderr']}")
        if tool_use_result.get("exitCode"):
            parts.append(f"[exit_code] {tool_use_result['exitCode']}")

    return "\n\n".join(parts), metadata
```

### 最终指标聚合

```python
final_metrics = FinalMetrics(
    total_prompt_tokens=sum(step.metrics.prompt_tokens for step in steps),
    total_completion_tokens=sum(step.metrics.completion_tokens for step in steps),
    total_cached_tokens=sum(step.metrics.cached_tokens for step in steps),
    total_cost_usd=None,  # 可选计算
    total_steps=len(steps),
    extra={
        "service_tiers": ["default"],
        "total_cache_creation_input_tokens": 5000,
        "total_cache_read_input_tokens": 2000
    }
)
```

---

## MCP服务器集成

### 配置格式

Harbor支持在task.toml中配置MCP服务器：

```toml
[[mcp_servers]]
name = "filesystem"
transport = "stdio"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]

[[mcp_servers]]
name = "github"
transport = "streamable-http"
url = "https://mcp.github.com"
```

### 注册流程

```python
def _build_register_mcp_servers_command(self):
    """
    生成MCP服务器注册命令
    写入~/.claude.json以便Claude Code加载
    """
    servers = {}
    for server in self.mcp_servers:
        if server.transport == "stdio":
            servers[server.name] = {
                "type": "stdio",
                "command": server.command,
                "args": server.args
            }
        else:
            # streamable-http → http (Claude Code格式)
            servers[server.name] = {
                "type": "http",
                "url": server.url
            }

    config = {"mcpServers": servers}
    return f"echo '{json.dumps(config)}' > $CLAUDE_CONFIG_DIR/.claude.json"
```

### 用户级vs项目级配置

- **用户级** (`~/.claude.json`): Harbor使用此方式，无需信任对话框
- **项目级** (`.mcp.json`): 需要用户手动启用，不适合自动化评估

---

## 配置说明

### Agent参数

通过`--agent-kwargs`传递：

```bash
harbor run \
  --agent claude-code \
  --agent-kwargs '{
    "max_thinking_tokens": 10000
  }'
```

### 模型配置

```bash
# 使用官方Anthropic API
harbor run --model anthropic/claude-opus-4-1

# 使用OpenRouter
export ANTHROPIC_BASE_URL=https://openrouter.ai/api/v1
harbor run --model openrouter/anthropic/claude-opus-4-1

# 使用自定义端点
export ANTHROPIC_BASE_URL=https://my-proxy.com/v1
harbor run --model custom/claude-model
```

### 认证方式

支持两种认证方式（优先级从高到低）：

1. **OAuth Token**: `CLAUDE_CODE_OAUTH_TOKEN`
2. **API Key**: `ANTHROPIC_API_KEY` 或 `ANTHROPIC_AUTH_TOKEN`

---

## 使用示例

### 基础用法

```bash
# 在Terminal-Bench上评估Claude Code
harbor run \
  --dataset terminal-bench@2.0 \
  --agent claude-code \
  --model anthropic/claude-opus-4-1 \
  --n-concurrent 4
```

### 高级用法

```bash
# 使用自定义环境和参数
harbor run \
  --dataset swebench \
  --agent claude-code \
  --model anthropic/claude-sonnet-4-5 \
  --environment daytona \
  --n-concurrent 10 \
  --agent-kwargs '{
    "max_thinking_tokens": 15000
  }' \
  --timeout 3600
```

### 本地测试

```bash
# 在本地Docker环境测试单个任务
harbor run \
  --dataset examples/tasks/hello-world \
  --agent claude-code \
  --model anthropic/claude-opus-4-1 \
  --environment docker \
  --n-concurrent 1
```

### 查看结果

```bash
# 查看trial结果
harbor trials list

# 查看轨迹
harbor traces view <trial-id>

# 查看汇总
harbor view <job-id>
```

---

## 数据收集与指标

### AgentContext填充

执行完成后，`populate_context_post_run()`方法会填充以下字段：

```python
context.cost_usd = final_metrics.total_cost_usd
context.n_input_tokens = final_metrics.total_prompt_tokens
context.n_cache_tokens = final_metrics.total_cached_tokens
context.n_output_tokens = final_metrics.total_completion_tokens
```

### 输出文件

| 文件路径 | 内容 | 格式 |
|----------|------|------|
| `/logs/agent/claude-code.txt` | 原始执行日志 | 文本 |
| `/logs/agent/sessions/projects/{hash}/*.jsonl` | Claude会话日志 | JSONL |
| `/logs/trajectory.json` | ATIF轨迹 | JSON |
| `/logs/verifier/reward.txt` | 验证结果 | 文本 |

### ATIF轨迹结构

```json
{
  "schema_version": "ATIF-v1.2",
  "session_id": "session-123",
  "agent": {
    "name": "claude-code",
    "version": "1.2.3",
    "model_name": "claude-opus-4-1",
    "extra": {
      "cwds": ["/workspace"],
      "git_branches": ["main"],
      "agent_ids": ["agent-456"]
    }
  },
  "steps": [
    {
      "step_id": 1,
      "timestamp": "2024-01-01T12:00:00Z",
      "source": "agent",
      "message": "...",
      "tool_calls": [...],
      "observation": {...},
      "metrics": {...}
    }
  ],
  "final_metrics": {
    "total_prompt_tokens": 10000,
    "total_completion_tokens": 5000,
    "total_cached_tokens": 2000,
    "total_cost_usd": null,
    "total_steps": 50,
    "extra": {
      "service_tiers": ["default"],
      "total_cache_creation_input_tokens": 3000,
      "total_cache_read_input_tokens": 2000
    }
  }
}
```

### 指标计算

- **Token使用**: 从每个step的metrics聚合
- **缓存效率**: `cached_tokens / (prompt_tokens + cached_tokens)`
- **步骤数**: 总step数量
- **成本**: 可选，基于模型定价计算

---

## 故障排查

### 常见问题

#### 1. 认证失败

```
Error: No authentication credentials found
```

**解决方案**:
```bash
export ANTHROPIC_API_KEY=sk-ant-...
# 或
export CLAUDE_CODE_OAUTH_TOKEN=...
```

#### 2. 模型不可用

```
Error: Model 'claude-opus-4-1' not found
```

**解决方案**:
- 检查模型名称是否正确
- 确认API key有权限访问该模型
- 使用`--model anthropic/claude-sonnet-4-5`等可用模型

#### 3. 权限问题

```
Error: Permission denied
```

**解决方案**:
- Harbor自动使用`--permission-mode bypassPermissions`
- 确保`IS_SANDBOX=1`环境变量已设置
- 检查Docker容器是否以root运行

#### 4. 轨迹转换失败

```
Failed to convert Claude Code events to trajectory
```

**解决方案**:
- 检查`/logs/agent/sessions/`目录是否存在
- 确认JSONL文件格式正确
- 查看详细错误日志

---

## 性能优化

### 并发执行

```bash
# 使用Daytona云环境并发运行100个trials
harbor run \
  --dataset swebench \
  --agent claude-code \
  --environment daytona \
  --n-concurrent 100
```

### 缓存优化

Claude Code自动使用prompt caching：
- 系统提示和工具定义会被缓存
- 长上下文会话可节省大量token成本
- Harbor自动收集缓存指标

### 资源限制

在task.toml中配置：

```toml
[resources]
timeout_sec = 3600
memory_gb = 8
cpu_cores = 4
```

---

## 扩展开发

### 自定义Agent

基于ClaudeCode创建自定义agent：

```python
from harbor.agents.installed.claude_code import ClaudeCode

class CustomClaudeCode(ClaudeCode):
    def __init__(self, custom_param, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.custom_param = custom_param

    async def setup(self, environment):
        await super().setup(environment)
        # 自定义设置逻辑

    def create_run_agent_commands(self, instruction):
        commands = super().create_run_agent_commands(instruction)
        # 修改命令或环境变量
        return commands
```

### 自定义轨迹处理

```python
def populate_context_post_run(self, context):
    super().populate_context_post_run(context)

    # 添加自定义指标
    trajectory = self._convert_events_to_trajectory(session_dir)
    if trajectory:
        context.custom_metric = calculate_custom_metric(trajectory)
```

---

## 参考资料

- [Harbor文档](https://github.com/laude-institute/harbor)
- [Claude Code CLI](https://claude.ai/docs)
- [ATIF规范](https://github.com/laude-institute/harbor/docs/rfcs/atif.md)
- [MCP协议](https://modelcontextprotocol.io)

---

## 更新日志

| 版本 | 日期 | 变更内容 |
|------|------|----------|
| 1.0 | 2024-01-01 | 初始版本 |

---

**文档维护**: Harbor团队
**最后更新**: 2024-01-01
