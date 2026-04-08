# OpenSpec 开源项目分析报告

## 目录
1. [项目概述](#1-项目概述)
2. [与 Claude Code 和 OpenCode 的集成方式](#2-与-claude-code-和-opencode-的集成方式)
3. [项目主要解决的问题](#3-项目主要解决的问题)
4. [项目优势与同类对比](#4-项目优势与同类对比)
5. [能力评估标准与测试方案](#5-能力评估标准与测试方案)

---

## 1. 项目概述

**OpenSpec** 是一个 AI 原生的规范驱动开发系统（AI-native system for spec-driven development），由 Fission AI 团队开发并开源。

### 基本信息
| 属性 | 值 |
|------|-----|
| **项目名称** | @fission-ai/openspec |
| **当前版本** | 1.2.0 |
| **许可证** | MIT |
| **运行环境** | Node.js 20.19.0+ |
| **包管理** | npm/pnpm/yarn/bun |

### 核心理念
```
→ fluid not rigid        (灵活而非僵化)
→ iterative not waterfall (迭代而非瀑布)
→ easy not complex       (简单而非复杂)
→ built for brownfield not just greenfield (支持现有项目，不仅是新项目)
```

---

## 2. 与 Claude Code 和 OpenCode 的集成方式

### 2.1 集成架构

OpenSpec 通过 **Skills + Commands** 双轨机制与 AI 编码助手集成：

```
┌─────────────────────────────────────────────────────────────┐
│                    OpenSpec 集成架构                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   Schema Definitions (YAML)                                  │
│          │                                                   │
│          ▼                                                   │
│   Artifact Graph Engine (依赖图引擎)                         │
│          │                                                   │
│          ▼                                                   │
│   ┌──────────────────────────────────────┐                  │
│   │  Skills (.claude/skills/openspec-*/  │                  │
│   │        SKILL.md)                      │                  │
│   │  Commands (.claude/commands/opsx/    │                  │
│   │        *.md)                          │                  │
│   └──────────────────────────────────────┘                  │
│          │                                                   │
│          ▼                                                   │
│   AI Coding Assistants (Claude Code, Cursor, etc.)          │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Claude Code 集成

**安装路径：**
```
.claude/
├── skills/
│   ├── openspec-propose/
│   │   └── SKILL.md
│   ├── openspec-apply/
│   │   └── SKILL.md
│   └── openspec-archive/
│       └── SKILL.md
└── commands/
    └── opsx/
        ├── propose.md
        ├── apply.md
        └── archive.md
```

**使用方式：**
```bash
# 初始化项目
openspec init --tools claude

# 使用斜杠命令
/opsx:propose add-dark-mode
/opsx:apply
/opsx:archive
```

**命令语法：** 使用冒号分隔 `opsx:<command>`

### 2.3 OpenCode 集成

**安装路径：**
```
.opencode/
├── skills/
│   ├── openspec-propose/
│   │   └── SKILL.md
│   └── ...
└── commands/
    ├── opsx-propose.md
    ├── opsx-apply.md
    └── opsx-archive.md
```

**与 Claude Code 的差异：**
| 特性 | Claude Code | OpenCode |
|------|-------------|----------|
| 命令文件路径 | `.claude/commands/opsx/<id>.md` | `.opencode/commands/opsx-<id>.md` |
| 命令语法 | `/opsx:propose` (冒号) | `/opsx-propose` (连字符) |
| Frontmatter | 无特殊要求 | YAML frontmatter + description 字段 |

### 2.4 支持的工具列表（25+）

| 工具 | Skills 路径 | Commands 路径 |
|------|-------------|---------------|
| Claude Code | `.claude/skills/openspec-*/SKILL.md` | `.claude/commands/opsx/<id>.md` |
| OpenCode | `.opencode/skills/openspec-*/SKILL.md` | `.opencode/commands/opsx-<id>.md` |
| Cursor | `.cursor/skills/openspec-*/SKILL.md` | `.cursor/commands/opsx-<id>.md` |
| Windsurf | `.windsurf/skills/openspec-*/SKILL.md` | `.windsurf/workflows/opsx-<id>.md` |
| Cline | `.cline/skills/openspec-*/SKILL.md` | `.clinerules/workflows/opsx-<id>.md` |
| Gemini CLI | `.gemini/skills/openspec-*/SKILL.md` | `.gemini/commands/opsx-<id>.toml` |
| GitHub Copilot | `.github/skills/openspec-*/SKILL.md` | `.github/prompts/opsx-<id>.prompt.md` |

### 2.5 集成初始化流程

```bash
# 交互式初始化（选择工具）
openspec init

# 非交互式（指定工具）
openspec init --tools claude,opencode

# 配置所有支持的工具
openspec init --tools all

# 跳过工具配置
openspec init --tools none
```

---

## 3. 项目主要解决的问题

### 3.1 核心问题

**AI 编码助手的不可预测性问题：**
> AI coding assistants are powerful but unpredictable when requirements live only in chat history.

当需求仅存在于对话历史中时，AI 编码助手虽然强大但不可预测。

### 3.2 具体解决的痛点

| 痛点 | 描述 | OpenSpec 解决方案 |
|------|------|-------------------|
| **需求漂移** | 需求在对话中不断变化，缺乏稳定记录 | 规范作为活文档（Living Documents）持久保存 |
| **现有项目改造困难** | 大多数工具只适合新项目 | Delta Specs 支持增量修改现有系统 |
| **工作流僵化** | 传统阶段式开发无法适应变化 | 流体工作流，可随时更新任何制品 |
| **工具碎片化** | 不同 AI 工具工作方式不同 | 统一接口支持 25+ AI 编码助手 |
| **上下文丢失** | 对话历史无法保留完整上下文 | 组织化的变更文件夹保存完整上下文 |

### 3.3 核心工作流

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              OPENSPEC 工作流                                 │
│                                                                              │
│   ┌────────────────┐                                                         │
│   │  1. 发起变更   │  /opsx:propose                                          │
│   └───────┬────────┘                                                         │
│           ▼                                                                  │
│   ┌────────────────┐                                                         │
│   │  2. 创建制品   │  proposal → specs → design → tasks                      │
│   └───────┬────────┘                                                         │
│           ▼                                                                  │
│   ┌────────────────┐                                                         │
│   │  3. 实现任务   │  /opsx:apply (边做边更新制品)                            │
│   └───────┬────────┘                                                         │
│           ▼                                                                  │
│   ┌────────────────┐                                                         │
│   │  4. 验证工作   │  /opsx:verify (可选)                                    │
│   └───────┬────────┘                                                         │
│           ▼                                                                  │
│   ┌────────────────┐     ┌──────────────────────────────────────────────┐   │
│   │  5. 归档变更   │────►│  Delta specs 合并到主规范                     │   │
│   └────────────────┘     │  变更文件夹移至 archive/                      │   │
│                          └──────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.4 Delta Specs 机制

OpenSpec 的核心创新是 **Delta Specs**（增量规范），用于描述变更而非完整重写：

```markdown
# Delta for Auth

## ADDED Requirements
### Requirement: Two-Factor Authentication
The system MUST support TOTP-based two-factor authentication.

## MODIFIED Requirements
### Requirement: Session Expiration
The system MUST expire sessions after 15 minutes of inactivity.
(Previously: 30 minutes)

## REMOVED Requirements
### Requirement: Remember Me
(Deprecated in favor of 2FA)
```

---

## 4. 项目优势与同类对比

### 4.1 核心优势

| 优势 | 说明 |
|------|------|
| **轻量级** | 几秒钟初始化，无需复杂配置 |
| **流体工作流** | 无阶段门槛，可随时更新任何制品 |
| **Brownfield 友好** | Delta Specs 专为现有项目设计 |
| **广泛工具支持** | 支持 25+ AI 编码助手 |
| **可定制** | YAML Schema 定义自定义工作流 |
| **上下文保持** | 完整的变更历史和审计追踪 |

### 4.2 与同类项目对比

#### vs. Spec Kit (GitHub)

| 维度 | OpenSpec | Spec Kit |
|------|----------|----------|
| **定位** | 轻量、灵活 | 全面、重量级 |
| **阶段门槛** | 无，流体工作流 | 有，严格的阶段门槛 |
| **设置复杂度** | 低（npm install） | 高（Python 环境） |
| **迭代方式** | 随时更新 | 需遵循阶段流程 |
| **适用场景** | 快速迭代项目 | 大型企业项目 |

#### vs. Kiro (AWS)

| 维度 | OpenSpec | Kiro |
|------|----------|------|
| **工具绑定** | 工具无关 | 锁定 AWS IDE |
| **模型支持** | 任意模型 | 仅限 Claude 模型 |
| **成本** | 开源免费 | AWS 定价 |
| **灵活性** | 高 | 低 |

#### vs. 无规范开发

| 维度 | 使用 OpenSpec | 不使用规范 |
|------|---------------|------------|
| **需求明确性** | 高（规范先行） | 低（模糊提示） |
| **结果可预测性** | 高 | 低 |
| **上下文管理** | 结构化 | 分散在对话中 |
| **团队协作** | 支持 | 困难 |

### 4.3 架构对比

**传统工作流 vs OpenSpec OPSX：**

```
传统工作流 (Phase-Locked):
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   PLANNING   │ ──► │ IMPLEMENTING │ ──► │   ARCHIVING  │
│    PHASE     │     │    PHASE     │     │    PHASE     │
└──────────────┘     └──────────────┘     └──────────────┘
     │                    │
     │                    ├── "设计有问题"
     │                    │
     │                    └── 无法回退，只能继续或重来

OpenSpec OPSX (Fluid Actions):
              ┌────────────────────────────────────────────┐
              │           ACTIONS (not phases)             │
              │                                            │
              │   new ◄──► continue ◄──► apply ◄──► archive │
              │    │          │           │           │    │
              │    └──────────┴───────────┴───────────┘    │
              │              any order                     │
              └────────────────────────────────────────────┘
```

---

## 5. 能力评估标准与测试方案

### 5.1 评估维度框架

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        OpenSpec 能力评估框架                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │  功能完整性  │  │  易用性     │  │  可扩展性   │  │  稳定性     │        │
│  │             │  │             │  │             │  │             │        │
│  │ • CLI 命令  │  │ • 文档质量  │  │ • Schema   │  │ • 错误处理  │        │
│  │ • 工作流    │  │ • 学习曲线  │  │ • 自定义   │  │ • 边界情况  │        │
│  │ • 工具集成  │  │ • 错误提示  │  │ • 插件     │  │ • 性能     │        │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘        │
│                                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                         │
│  │  AI 集成质量 │  │  协作能力   │  │  生态健康   │                         │
│  │             │  │             │  │             │                         │
│  │ • 上下文传递│  │ • 团队支持  │  │ • 社区活跃  │                         │
│  │ • 指令清晰度│  │ • 变更追踪  │  │ • 更新频率  │                         │
│  │ • 输出一致性│  │ • 归档历史  │  │ • 贡献者    │                         │
│  └─────────────┘  └─────────────┘  └─────────────┘                         │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 具体评估指标

#### 5.2.1 功能完整性 (30分)

| 指标 | 权重 | 评估方法 |
|------|------|----------|
| CLI 命令覆盖度 | 10分 | 验证所有文档命令可执行 |
| 工作流完整性 | 10分 | 端到端工作流测试 |
| 工具适配数量 | 10分 | 验证 25+ 工具适配 |

#### 5.2.2 AI 集成质量 (25分)

| 指标 | 权重 | 评估方法 |
|------|------|----------|
| 上下文传递准确性 | 10分 | AI 输出与规范一致性 |
| 指令清晰度 | 8分 | AI 首次理解成功率 |
| 输出格式一致性 | 7分 | 制品格式规范检查 |

#### 5.2.3 易用性 (20分)

| 指标 | 权重 | 评估方法 |
|------|------|----------|
| 文档完整性 | 8分 | 文档覆盖率审计 |
| 学习曲线 | 6分 | 新用户上手时间 |
| 错误提示质量 | 6分 | 错误信息可操作性 |

#### 5.2.4 可扩展性 (15分)

| 指标 | 权重 | 评估方法 |
|------|------|----------|
| Schema 自定义 | 8分 | 自定义工作流验证 |
| 模板覆盖盖 | 4分 | 模板可覆盖性测试 |
| 配置灵活性 | 3分 | 配置选项测试 |

#### 5.2.5 稳定性 (10分)

| 指标 | 权重 | 评估方法 |
|------|------|----------|
| 错误处理 | 5分 | 异常输入测试 |
| 边界情况 | 3分 | 极端场景测试 |
| 性能表现 | 2分 | 大规模变更测试 |

### 5.3 测试方案

#### 5.3.1 单元测试（项目已有）

项目使用 Vitest 作为测试框架，包含以下测试类别：

```bash
# 运行所有测试
pnpm test

# 测试覆盖率
pnpm test:coverage

# 监视模式
pnpm test:watch
```

**测试文件分布：**
```
test/
├── cli-e2e/           # 端到端测试
├── commands/          # CLI 命令测试
├── core/              # 核心逻辑测试
│   ├── artifact-graph/  # 依赖图引擎测试
│   ├── command-generation/  # 命令生成测试
│   ├── completions/    # Shell 补全测试
│   └── parsers/        # 解析器测试
└── utils/             # 工具函数测试
```

#### 5.3.2 集成测试方案

**测试环境准备：**
```bash
# 1. 安装依赖
pnpm install

# 2. 构建项目
pnpm build

# 3. 全局安装（测试 CLI）
npm link
```

**测试用例设计：**

##### 测试用例 1: 基础工作流测试
```bash
# 步骤 1: 初始化
openspec init --tools claude
# 验证: 检查 .claude/ 目录结构

# 步骤 2: 发起变更
/opsx:propose test-feature
# 验证: 检查 openspec/changes/test-feature/ 目录

# 步骤 3: 应用变更
/opsx:apply test-feature
# 验证: 检查任务执行状态

# 步骤 4: 归档
/opsx:archive test-feature
# 验证: 检查 archive/ 目录和 specs 更新
```

##### 测试用例 2: 多工具集成测试
```bash
# 初始化多工具
openspec init --tools claude,cursor,windsurf

# 验证各工具目录
ls -la .claude/skills/
ls -la .cursor/skills/
ls -la .windsurf/skills/
```

##### 测试用例 3: Delta Specs 合并测试
```bash
# 创建包含 ADDED/MODIFIED/REMOVED 的变更
# 执行 archive
# 验证主规范正确合并
```

#### 5.3.3 AI 输出质量评估

**评估方法：**
1. 使用相同输入运行 10 次
2. 检查输出格式一致性
3. 评估需求覆盖完整性
4. 检查 RFC 2119 关键词使用

**评估标准：**
```
┌─────────────────────────────────────────────────────────────────┐
│                    AI 输出质量评估表                             │
├─────────────────────────────────────────────────────────────────┤
│ 评估项                    │ 通过标准           │ 权重           │
├─────────────────────────────────────────────────────────────────┤
│ proposal.md 格式正确      │ 包含 Intent/Scope  │ 15%            │
│ specs.md 使用 RFC 2119    │ SHALL/MUST/SHOULD  │ 20%            │
│ design.md 技术方案完整    │ 包含架构决策       │ 15%            │
│ tasks.md 任务可执行       │ 包含 checkbox      │ 15%            │
│ 输出一致性 (10次)         │ >80% 格式一致      │ 20%            │
│ 无幻觉内容               │ 无虚构信息         │ 15%            │
└─────────────────────────────────────────────────────────────────┘
```

### 5.4 评估执行脚本

```bash
#!/bin/bash
# OpenSpec 能力评估脚本

echo "=== OpenSpec 能力评估 ==="

# 1. 功能完整性测试
echo "1. 运行单元测试..."
pnpm test

# 2. CLI 命令验证
echo "2. 验证 CLI 命令..."
openspec --help
openspec schemas
openspec status

# 3. 初始化测试
echo "3. 测试初始化..."
rm -rf test-project
mkdir test-project && cd test-project
openspec init --tools claude --profile core

# 4. 目录结构验证
echo "4. 验证目录结构..."
test -d .claude/skills && echo "✓ Skills 目录存在"
test -d .claude/commands && echo "✓ Commands 目录存在"
test -d openspec && echo "✓ openspec 目录存在"

# 5. 清理
cd ..
rm -rf test-project

echo "=== 评估完成 ==="
```

### 5.5 评估报告模板

```markdown
# OpenSpec 能力评估报告

## 评估信息
- 评估日期: YYYY-MM-DD
- 版本: x.x.x
- 评估人: XXX

## 评分摘要

| 维度 | 得分 | 满分 | 备注 |
|------|------|------|------|
| 功能完整性 | XX | 30 | |
| AI 集成质量 | XX | 25 | |
| 易用性 | XX | 20 | |
| 可扩展性 | XX | 15 | |
| 稳定性 | XX | 10 | |
| **总分** | **XX** | **100** | |

## 详细发现

### 优点
- ...

### 改进建议
- ...

## 结论
[评估结论]
```

---

## 附录

### A. 快速开始

```bash
# 安装
npm install -g @fission-ai/openspec@latest

# 初始化项目
cd your-project
openspec init

# 开始使用
/opsx:propose your-feature-name
```

### B. 参考链接

- **GitHub**: https://github.com/Fission-AI/OpenSpec
- **NPM**: https://www.npmjs.com/package/@fission-ai/openspec
- **Discord**: https://discord.gg/YctCnvvshC
- **文档**: docs/ 目录

### C. 术语表

| 术语 | 定义 |
|------|------|
| Artifact | 变更中的文档（proposal, design, tasks, delta specs） |
| Change | 对系统的提议修改，打包为包含制品的文件夹 |
| Delta Spec | 描述变更（ADDED/MODIFIED/REMOVED）的规范 |
| Schema | 制品类型及其依赖关系的定义 |
| Spec | 描述系统行为的规范，包含需求和场景 |
| Source of truth | openspec/specs/ 目录，包含当前商定的行为 |

---

*报告生成时间: 2026-04-08*
