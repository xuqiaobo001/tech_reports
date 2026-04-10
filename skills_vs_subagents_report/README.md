# Claude Code Skills 与 Subagents 技术调研报告

> **报告日期**：2026-04-09
> **调研对象**：Claude Code 中 Skills（技能）与 Subagents（子代理）两种能力扩展机制
> **核心问题**：本质区别、场景侧重点、关系定位、最佳封装形式

---

## 目录

1. [概念定义与技术本质](#1-概念定义与技术本质)
2. [架构层面的本质区别](#2-架构层面的本质区别)
3. [场景侧重点对比](#3-场景侧重点对比)
4. [关系定位：协同、替代还是其他？](#4-关系定位协同替代还是其他)
5. [是否存在领先关系？](#5-是否存在领先关系)
6. [能力封装的最佳形式](#6-能力封装的最佳形式)
7. [生产环境最佳实践](#7-生产环境最佳实践)
8. [结论与建议](#8-结论与建议)
9. [参考资料](#9-参考资料)

---

## 1. 概念定义与技术本质

### 1.1 Skill（技能）是什么

Skill 是一段结构化的提示词（markdown 文件），存储在 `.claude/commands/` 或 `.claude/skills/` 目录中，用于向 **主 agent 的上下文** 注入专业知识、工作流程或行为规范。

**技术本质**：**上下文注入（Context Injection）**

```
Skill 文件 (.md)
    ↓ 加载到
主 Agent 的 System Prompt / Context Window
    ↓ 效果
主 Agent 获得了新的"知识"或"行为模式"
```

**关键特征**：
- 运行在主 agent 的上下文窗口内，**共享主 agent 的所有工具和状态**
- 可被 **自动触发**（基于文件模式/任务匹配）或 **手动触发**（通过 `/skill-name` 斜杠命令）
- 是 **无状态** 的——每次调用都是一次全新的上下文注入
- 支持 `$ARGUMENTS` 参数化

### 1.2 Subagent（子代理）是什么

Subagent 是一个 **独立的 Claude 实例**，拥有自己的系统提示词、上下文窗口、工具访问列表和权限边界，由主 agent 通过 `Agent` 工具派生。

**技术本质**：**进程隔离（Process Isolation）**

```
主 Agent
    ↓ 通过 Agent 工具派生
Subagent（独立 Claude 实例）
    ├── 独立的 System Prompt
    ├── 独立的 Context Window
    ├── 独立的 Tool Access List
    ├── 独立的权限边界
    └── 执行完毕后返回一条最终消息
```

**关键特征**：
- 运行在 **独立的上下文窗口** 中，与主 agent 隔离
- 可拥有 **不同于主 agent 的工具集和权限**
- 执行完毕后 **仅返回一条最终消息**（不返回中间过程）
- 不能再派生子代理（单层嵌套限制）
- Claude Code 内置 3 种 subagent：**Explore**（代码库搜索）、**Plan**（规划研究）、**General-purpose**（通用）

---

## 2. 架构层面的本质区别

### 2.1 核心维度对比

| 维度 | Skill | Subagent |
|------|-------|----------|
| **本质** | 上下文注入 | 进程隔离 |
| **运行位置** | 主 agent 的上下文窗口内 | 独立的上下文窗口 |
| **上下文共享** | 与主 agent 完全共享 | 完全隔离，仅返回最终结果 |
| **工具访问** | 使用主 agent 的全部工具 | 可定义独立的工具集和权限 |
| **状态** | 无状态（每次调用重新注入） | 有状态（在执行期间维护独立状态） |
| **嵌套** | 不可嵌套 | 不可再派生子代理（单层） |
| **返回结果** | 直接影响主 agent 行为 | 仅返回一条最终消息 |
| **成本** | 主要消耗输入 token（上下文注入） | 消耗完整的一次 agent 会话 token |
| **执行模式** | 同步（阻塞主 agent） | 可后台并行（background 模式） |
| **自动触发** | 支持（基于文件模式/任务匹配） | 由主 agent 显式委派 |

### 2.2 类比模型

| 类比 | Skill | Subagent |
|------|-------|----------|
| **人类团队** | 给一个员工一本操作手册 | 雇一个专门的顾问来完成任务 |
| **操作系统** | 加载一个动态链接库（.so/.dll）到当前进程 | fork() 一个子进程 |
| **软件架构** | import 一个模块（共享内存空间） | 调用一个微服务（独立进程） |
| **厨房** | 贴一张菜谱在厨师面前 | 请一个专门做甜点的师傅 |

### 2.3 Token 消耗对比

```
Skill 调用：
┌──────────────────────────────────────┐
│ 主 Agent Context Window               │
│ ┌────────────┐ ┌──────────────────┐  │
│ │ 原有上下文   │ │ Skill 内容（注入） │  │
│ │            │ │ + 参数 $ARGUMENTS │  │
│ └────────────┘ └──────────────────┘  │
│         总 Token = 原有 + Skill 大小    │
│         ↑ 透明，主 agent 可访问所有      │
└──────────────────────────────────────┘

Subagent 调用：
┌──────────────────────────────────────┐
│ 主 Agent Context Window               │
│ ┌────────────┐ ┌──────────────────┐  │
│ │ 原有上下文   │ │ Subagent 返回结果  │  │
│ │            │ │ （一条最终消息）    │  │
│ └────────────┘ └──────────────────┘  │
│         总 Token ≈ 原有 + 返回结果      │
│         ↑ 中间过程对主 agent 不可见      │
└──────────────────────────────────────┘
         ↕ Agent 工具通信
┌──────────────────────────────────────┐
│ Subagent Context Window（独立）        │
│ ┌────────────────────────────────┐   │
│ │ System Prompt + 任务描述        │   │
│ │ + 完整的中间推理和工具调用过程    │   │
│ │ + 最终结果                      │   │
│ │ 独立 Token 消耗，对主 agent 不可见 │   │
│ └────────────────────────────────┘   │
└──────────────────────────────────────┘
```

**关键区别**：
- Skill 的全部内容都在主 agent 的上下文中，**消耗主 agent 的 token 预算**
- Subagent 的中间过程在独立窗口中，**保护主 agent 的上下文不被污染**，但产生额外的独立 token 消耗

---

## 3. 场景侧重点对比

### 3.1 Skill 的最佳场景

| 场景 | 为什么适合 Skill | 示例 |
|------|-----------------|------|
| **行为规范** | 需要持续约束主 agent 的行为 | 代码风格规范、提交消息格式、安全规则 |
| **角色定义** | 需要给主 agent 一个明确的专业身份 | "你是一个测试工程师"、"你是一个安全分析师" |
| **工作流程模板** | 可复用的任务执行模式 | 探索→规划→编码→测试→提交 |
| **领域知识注入** | 注入特定领域的专业知识 | 项目架构说明、API 约定、部署流程 |
| **快捷操作** | 高频重复任务的快捷方式 | `/commit`、`/review`、`/deploy` |
| **团队约定** | 团队共享的工作标准 | CLAUDE.md + Skills 定义团队标准 |

### 3.2 Subagent 的最佳场景

| 场景 | 为什么适合 Subagent | 示例 |
|------|---------------------|------|
| **深度代码搜索** | 不污染主上下文，专注搜索 | Explore agent 在代码库中定位文件/函数 |
| **长时间研究** | 独立上下文可承载大量信息 | Plan agent 在 plan mode 中做深度研究 |
| **并行任务** | 可后台运行，不阻塞主 agent | 同时派生 3 个 agent 分别搜索不同维度 |
| **权限隔离** | 限制特定任务的工具访问 | 审计 agent 只能读不能写 |
| **上下文保护** | 中间推理不占用主上下文 | 复杂分析任务，中间步骤繁多 |
| **专业领域** | 需要完全不同的 system prompt | 安全扫描 agent 用不同于编码 agent 的提示词 |

### 3.3 场景决策树

```
任务需求分析
│
├─ 需要持续约束主 agent 的行为？
│  └─ ✅ Skill（行为规范、角色定义）
│
├─ 任务简单、一次性、需要主 agent 直接执行？
│  └─ ✅ Skill（快捷操作、工作流模板）
│
├─ 任务需要大量中间推理，可能污染主上下文？
│  └─ ✅ Subagent（深度研究、复杂分析）
│
├─ 任务需要并行执行、不阻塞主 agent？
│  └─ ✅ Subagent（后台并行模式）
│
├─ 任务需要不同的工具集或权限边界？
│  └─ ✅ Subagent（权限隔离）
│
├─ 任务需要注入领域知识到主 agent？
│  └─ ✅ Skill（知识注入）
│
└─ 任务复杂，需要多步推理+上下文保护+并行？
   └─ ✅ Skill + Subagent 组合（见第 4 节）
```

---

## 4. 关系定位：协同、替代还是其他？

### 4.1 结论：互补协同关系（Complementary）

Skills 和 Subagents **不是竞争或替代关系**，而是 **互补协同关系**——它们在不同层面解决不同问题：

```
┌─────────────────────────────────────────────────┐
│                 Claude Code 扩展层               │
│                                                   │
│  ┌──────────────┐         ┌──────────────────┐   │
│  │   Skills     │         │   Subagents      │   │
│  │  （知识层）   │         │  （执行层）       │   │
│  │              │         │                  │   │
│  │ • 行为规范    │    →    │ • 深度执行       │   │
│  │ • 角色定义    │  传递上下文 • 上下文隔离    │   │
│  │ • 领域知识    │         │ • 并行处理       │   │
│  │ • 流程模板    │         │ • 权限控制       │   │
│  └──────────────┘         └──────────────────┘   │
│         ↓                         ↓               │
│  ┌──────────────────────────────────────────┐    │
│  │         主 Agent（协调层）                 │    │
│  │  • 接收 Skill 注入的知识和规范             │    │
│  │  • 决定何时委派给 Subagent                │    │
│  │  • 综合各 Subagent 的返回结果              │    │
│  └──────────────────────────────────────────┘    │
└─────────────────────────────────────────────────┘
```

### 4.2 四种协同模式

#### 模式一：Skill 定义角色，主 Agent 直接执行

```
Skill（"你是一个安全分析师"）→ 主 Agent（按安全视角执行任务）
```

**适用**：任务在主 agent 能力范围内，只需改变行为视角。

#### 模式二：Skill 定义流程，Subagent 执行深度工作

```
Skill（研究流程模板）→ 主 Agent（按流程协调）
                        → Subagent A（深度搜索维度 1）
                        → Subagent B（深度搜索维度 2）
```

**适用**：任务需要并行深度研究，主 agent 负责协调。

#### 模式三：Skill 作为 Subagent 的 System Prompt

```
主 Agent → Subagent（system prompt = Skill 内容）
```

**适用**：需要给 subagent 注入专业知识。**注意**：Subagent 不自动继承主 agent 的 Skills，需要通过 subagent_type 或 prompt 显式传递。

#### 模式四：Skill 定义团队规范，多个 Subagent 各司其职

```
Skill（团队规范 + 角色定义）
    → 主 Agent（Team Lead）
        → Subagent：Developer（编码）
        → Subagent：Code Reviewer（审查）
        → Subagent：Test Engineer（测试）
```

**适用**：复杂项目需要角色分工和团队协作。

### 4.3 关系矩阵

| | Skill | Subagent |
|---|---|---|
| **Skill** | 可组合多个 Skill 形成能力矩阵 | Skill 可作为 Subagent 的 prompt 来源 |
| **Subagent** | Subagent 不继承主 agent 的 Skills | 多个 Subagent 可并行形成团队 |
| **组合使用** | Skill 提供知识 + Subagent 提供隔离执行 = 最佳实践 | |

---

## 5. 是否存在领先关系？

### 5.1 结论：不存在领先关系，而是不同抽象层级

Skills 和 Subagents 处于不同的抽象层级，不存在谁"领先"谁的问题：

```
抽象层级（从低到高）：

Level 1: CLAUDE.md（项目级静态配置）
Level 2: Skills（动态上下文注入，可复用）
Level 3: Subagents（独立进程，上下文隔离）
Level 4: Agent Teams（多 agent 并行协作）
Level 5: Managed Agents（托管式 meta-harness）
```

### 5.2 演进关系

| 阶段 | 主要扩展方式 | 原因 |
|------|-------------|------|
| **早期（2024）** | CLAUDE.md + 手动 Prompt | 能力有限，简单配置足够 |
| **成长期（2025）** | Skills（Commands） | 需要可复用的能力模块 |
| **扩展期（2025 下半年）** | Subagents | 单 agent 上下文不够，需要隔离 |
| **成熟期（2026）** | Skills + Subagents + Agent Teams | 组合使用，各取所长 |

### 5.3 功能对比

| 能力 | Skill | Subagent | 谁更强？ |
|------|-------|----------|---------|
| **上下文效率** | 低（注入到主窗口） | 高（隔离窗口） | Subagent |
| **执行速度** | 快（无额外开销） | 慢（需要创建实例） | Skill |
| **Token 成本** | 低（仅注入内容） | 高（完整会话） | Skill |
| **上下文保护** | 无（共享主窗口） | 强（完全隔离） | Subagent |
| **并行能力** | 无（同步） | 有（可后台运行） | Subagent |
| **权限控制** | 无（继承主 agent） | 有（独立工具集） | Subagent |
| **行为约束** | 强（持续注入） | 弱（仅初始 prompt） | Skill |
| **知识复用** | 强（自动/手动触发） | 弱（每次需重建） | Skill |
| **团队协作** | 不适用 | 支持（多 subagent） | Subagent |
| **调试便利性** | 高（过程透明） | 低（仅返回结果） | Skill |

**结论**：Skill 在效率、成本和知识复用方面领先；Subagent 在隔离、并行和权限控制方面领先。互补而非竞争。

---

## 6. 能力封装的最佳形式

### 6.1 封装层次模型

根据不同的封装需求，推荐以下层次化封装策略：

```
┌─────────────────────────────────────────────────┐
│  Level 5: Agent Team（团队级封装）                │
│  多个 Subagent + 协调 Skill + 共享仓库            │
│  适用：大型项目、长期任务                          │
├─────────────────────────────────────────────────┤
│  Level 4: Orchestrator Pattern（编排级封装）      │
│  1 个 Skill 定义流程 + N 个 Subagent 执行         │
│  适用：多步骤复杂任务                              │
├─────────────────────────────────────────────────┤
│  Level 3: Specialized Subagent（专业级封装）      │
│  1 个 Subagent + 独立 System Prompt + 工具集      │
│  适用：需要上下文隔离的深度任务                    │
├─────────────────────────────────────────────────┤
│  Level 2: Composite Skill（复合级封装）           │
│  多个 Skill 组合 + 条件触发逻辑                    │
│  适用：多角色、多场景的复合能力                    │
├─────────────────────────────────────────────────┤
│  Level 1: Single Skill（基础级封装）              │
│  单个 .md 文件 + $ARGUMENTS 参数化                │
│  适用：单一职责、高频使用的操作                    │
└─────────────────────────────────────────────────┘
```

### 6.2 按场景推荐的最佳封装形式

| 场景 | 最佳封装 | 封装内容 |
|------|---------|---------|
| **代码风格规范** | Level 1: Single Skill | 行为规则 + 代码示例 |
| **快捷操作（commit/review）** | Level 1: Single Skill | 操作流程 + 参数模板 |
| **角色定义（测试工程师）** | Level 2: Composite Skill | 角色身份 + 工作流程 + 评估标准 |
| **开源项目分析** | Level 3: Specialized Subagent | 独立 system prompt + 文件搜索工具 |
| **数据集评估** | Level 3: Specialized Subagent | 独立 system prompt + Web 搜索工具 |
| **报告发布** | Level 1: Single Skill | Git 操作流程 + 目标仓库配置 |
| **完整研究工作流** | Level 4: Orchestrator Pattern | 协调 Skill + 研究 Subagent + 分析 Subagent |
| **大型项目开发** | Level 5: Agent Team | 开发/测试/审查 Subagent + 规范 Skills |

### 6.3 封装设计原则

#### 原则一：最小封装原则

```
能用 Skill 解决的，不用 Subagent。
能用单层 Subagent 的，不用 Agent Team。
```

**原因**：每增加一层隔离，增加成本、延迟和调试难度。

#### 原则二：知识与执行分离

```
知识/规范 → Skill（注入到需要的 agent）
执行/推理 → Subagent（独立上下文隔离执行）
```

#### 原则三：上下文预算意识

```
如果 Skill 内容 < 2000 token，优先用 Skill（注入成本低）。
如果任务中间过程 > 10000 token，优先用 Subagent（保护主上下文）。
```

#### 原则四：复用优先

```
高频操作 → Skill（一次定义，到处使用）
低频深度任务 → Subagent（按需创建，用完释放）
```

### 6.4 当前项目的封装实践

以本项目 `/root/software_team` 为例：

| 能力 | 封装形式 | 文件 |
|------|---------|------|
| 开源项目分析 | Level 3: Subagent (Skill 触发) | `.claude/commands/open-source-analyst.md` |
| 数据集评估 | Level 3: Subagent (Skill 触发) | `.claude/commands/dataset-analyst.md` |
| 报告发布 | Level 1: Single Skill | `.claude/commands/report-publisher.md` |
| 代码开发 | Level 1: Single Skill | `.claude/commands/developer.md` |
| 系统架构 | Level 1: Single Skill | `.claude/commands/system-architect.md` |
| 代码审查 | Level 1: Single Skill | `.claude/commands/code-reviewer.md` |
| 测试工程 | Level 1: Single Skill | `.claude/commands/test-engineer.md` |

---

## 7. 生产环境最佳实践

### 7.1 Skill 设计最佳实践

```markdown
✅ DO:
• 保持 Skill 内容精简（目标 < 3000 token）
• 使用明确的角色定义（"你是一个 X"）
• 提供具体的输出格式模板
• 使用 $ARGUMENTS 参数化
• 包含工作流步骤（Phase 1, Phase 2...）
• 包含约束和边界条件

❌ DON'T:
• 不要在 Skill 中放入大量示例数据
• 不要试图用 Skill 替代复杂的推理任务
• 不要定义过于模糊的角色（"你是一个助手"）
• 不要忽略成本（每次调用都消耗 token）
```

### 7.2 Subagent 设计最佳实践

```markdown
✅ DO:
• 给 Subagent 清晰的任务边界和完成标准
• 限制工具集（只给需要的工具）
• 预期并处理 subagent 返回不完整结果的情况
• 使用 background 模式并行化独立任务
• 在 system prompt 中注入必要的领域知识

❌ DON'T:
• 不要让 subagent 做超出其能力范围的任务
• 不要依赖 subagent 的中间状态（仅返回最终结果）
• 不要嵌套 subagent（当前不支持）
• 不要忽略 subagent 的独立 token 成本
```

### 7.3 组合使用最佳实践

```markdown
✅ 组合模式：
1. Skill 定义规范 → 主 Agent 按"规范"协调 → Subagent 执行深度任务
2. 多个 Skill 组合 → 形成团队能力矩阵 → 多个 Subagent 各司其职
3. Skill 作为 Subagent prompt → 实现专业化 + 上下文隔离

✅ 工作流：
1. 先用 Skill 定义项目标准和团队角色
2. 主 agent 根据任务类型选择 Skill 角色
3. 复杂任务委派给对应角色的 Subagent
4. Subagent 返回结果 → 主 agent 综合 → Skill 定义的格式输出
```

---

## 8. 结论与建议

### 8.1 核心结论

| 问题 | 答案 |
|------|------|
| **本质区别** | Skill = 上下文注入（共享空间），Subagent = 进程隔离（独立空间） |
| **场景侧重点** | Skill 侧重知识/规范/流程注入，Subagent 侧重隔离执行/并行/权限控制 |
| **关系定位** | **互补协同关系**，非竞争或替代关系 |
| **是否存在领先** | **不存在**，处于不同抽象层级，各有所长 |
| **最佳封装形式** | 按复杂度递进：Single Skill → Composite Skill → Specialized Subagent → Orchestrator → Agent Team |

### 8.2 一句话决策指南

> **知识注入用 Skill，执行隔离用 Subagent，组合使用是最佳实践。**

### 8.3 行动建议

1. **立即行动**：
   - 将所有角色定义、行为规范、工作流程封装为 Skill
   - 将深度研究、代码搜索等上下文消耗大的任务封装为 Subagent
   - 建立项目的 Skill 库，团队共享

2. **短期优化**：
   - 评估现有 Skill 是否应该升级为 Subagent（看上下文消耗）
   - 建立 Skill + Subagent 的组合模式模板
   - 监控各封装形式的 token 消耗和效果

3. **长期演进**：
   - 关注 Agent Teams 的成熟度（Research Preview → GA）
   - 关注 Skills Marketplace 生态发展
   - 关注 skill/subagent 互操作性标准化进程

---

## 9. 参考资料

### 官方文档
- [Claude Code Best Practices](https://code.claude.com/docs/en/best-practices)
- [Create Custom Subagents](https://code.claude.com/docs/en/sub-agents)
- [Slash Commands in the SDK](https://code.claude.com/docs/en/agent-sdk/slash-commands)

### 深度分析
- [A Mental Model for Claude Code: Skills, Subagents, and Plugins](https://levelup.gitconnected.com/a-mental-model-for-claude-code-skills-subagents-and-plugins-3dea9924bf05)
- [CLAUDE.md, Slash Commands, Skills, and Subagents Guide](https://alexop.dev/posts/claude-code-customization-guide-claudemd-skills-subagents/)
- [Claude Skills and Subagents: Escaping the Prompt Engineering Hamster Wheel](https://towardsdatascience.com/claude-skills-and-subagents-escaping-the-prompt-engineering-hamster-wheel/)
- [Agent Design Lessons from Claude Code](https://jannesklaas.github.io/ai/2025/07/20/claude-code-agent-design.html)

### 对比分析
- [Claude Skills vs Subagent: What's the Difference?](https://www.eesel.ai/blog/skills-vs-subagent)
- [Claude Code Skills vs Subagents - When to Use What?](https://dev.to/nunc/claude-code-skills-vs-subagents-when-to-use-what-4d12)
- [Skills vs Slash Commands: A Developer's Guide](https://rewire.it/blog/claude-code-agents-skills-slash-commands/)
- [Understanding Claude Code: Skills vs Commands vs Subagents vs Plugins](https://www.youngleaders.tech/p/claude-skills-commands-subagents-plugins)

### 社区讨论
- [Reddit: Understanding Claude Skills vs. Subagents](https://www.reddit.com/r/ClaudeAI/comments/1obq6wq/understanding_claude_skills_vs_subagents_its_not/)
- [Reddit: Are Skills and Slash Commands the Same Thing Now?](https://www.reddit.com/r/ClaudeAI/comments/1q7fzab/are_skills_and_slash_commands_the_same_thing_now/)
- [GitHub: Standardize interoperability between sub-agents and skills](https://github.com/agentskills/agentskills/issues/129)

### 生产实践
- [How to Build a Production-Ready Claude Code Skill](https://towardsdatascience.com/how-to-build-a-production-ready-claude-code-skill/)
- [Claude Code Extensions Explained: Skills, MCP, Hooks, Subagents](https://muneebsa.medium.com/claude-code-extensions-explained-skills-mcp-hooks-subagents-agent-teams-plugins-9294907e84ff)
- [Best Practices for Mastering AI Agents, Subagents, Skills & MCP](https://foojay.io/today/best-practices-for-working-with-ai-agents-subagents-skills-and-mcp/)
