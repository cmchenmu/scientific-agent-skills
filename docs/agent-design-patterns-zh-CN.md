# Scientific Agent Skills 的 Agent 设计模式

这个项目的核心不是让多个 Agent 自动辩论，或由一个总控 Agent 分派任务；它采用的是“技能库即运行手册”的设计：把科学工作流、工具用法、风险边界和可执行脚本打包成可发现的独立 skill。

## 1. 意图路由与按需加载

每个 skill 都有 YAML frontmatter，描述触发场景、依赖、权限和版本。Agent 根据用户需求选择少量相关 skill，再读取其 `SKILL.md`；不需要把全部 skill 同时放入上下文。这降低了大规模科学知识库带来的 token 和注意力消耗。

## 2. 将提示词变成程序化 SOP

一个 skill 不只是文字建议，通常还包含：

- `SKILL.md`：适用条件、流程和科学注意事项。
- `scripts/`：可直接执行、可串联的 CLI。
- `references/`：按需读取的长篇 API 或方法资料。
- `assets/`：模板、配置和静态资源。

例如 Scanpy skill 可让 Agent 先检查 `.h5ad`，再按步骤运行 QC、预处理、降维、聚类和 marker 分析脚本，而无需每次临时生成整套分析代码。

## 3. 验证优先，而非只生成结论

技能将输入验证、质量控制、中间产物、参数记录、来源和局限纳入流程。目标是产出可审计的分析过程，而不是仅给出看似合理的最终答案。数据库访问类 skill 强调端点、过滤条件、分页和来源记录；高风险临床和实验自动化类 skill 则加入研究用途或人工确认边界。

## 4. 可复现与供应链可追溯

每个 skill 有自己的 `metadata.version`，安装时可固定 Git tag 或提交 SHA。通过 GitHub CLI 安装的 skill 会保留类似如下的来源记录：

```yaml
github-pinned: v2.69.0
github-ref: refs/tags/v2.69.0
```

这可以区分模型当时的推理与所用工作流说明、脚本版本，并支持复现实验。

## 5. 将 Agent 行为作为软件交付物测试

带 `scripts/` 的 skill 必须有对应测试。仓库级契约会检查 frontmatter、链接、脚本语法、`--help`、危险调用、路径泄露和插件 manifest 一致性。这样 skill 不只是提示文本，也是有质量门槛的可维护软件组件。

## 6. 每个 skill 使用独立依赖环境

科学 Python 包经常存在版本冲突，因此项目不要求一个万能环境。测试运行器根据 `tests/skill-requirements.toml` 为每个 skill 建立隔离环境。这更适合科研软件生态，也避免一个包的版本约束破坏另一个 skill。

## 7. 跨 Agent 宿主的可移植性

仓库根目录的 `plugin.json` 使用 Agent Plugins 标准，skill 使用开放 Agent Skills 规范。Codex、Claude Code、Cursor 等可使用同一份 skill 内容；不同客户端的差异主要位于安装、发现和工具权限层。

## 8. 从重复行为形成新 skill

`autoskill` 是该项目的元能力：它可从本地观察到的重复工作模式中匹配已有 skill，或草拟新的 skill 和组合配方。这适合将反复进行的对话式工作逐步固化为可重复执行的流程。

## 工作模型

```text
用户目标
  -> Agent 选择少量领域 skill
  -> 读取可执行 SOP 与安全边界
  -> 运行脚本、API 和验证步骤
  -> 保存结果、参数、来源和版本
```

因此，推荐的使用方式是描述研究目标、输入数据、期望输出和约束，让 Agent 选择相关 skill，并将分析固化为可复现的代码与产物。
