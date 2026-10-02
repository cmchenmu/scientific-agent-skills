# Scientific Agent Skills 使用指南

## 1. 适用对象和前提

本仓库为支持开放 Agent Skills 标准的 AI Agent 提供科研工作流知识。它适合让 Agent 在生物信息学、化学与药物发现、医学研究、科研数据库、可视化、实验设计、科研写作等任务上使用经过整理的步骤、示例与科学约束。

使用前准备：

- 一个兼容 Agent Skills 或 Agent Plugins 的客户端，例如 Codex、Cursor、Claude Code、Gemini CLI 等。
- 对目标技能所需的软件包、系统依赖、网络和凭据的访问权。每项技能的要求不同，应先阅读其 `SKILL.md` 的安装和兼容性说明。
- 合法授权的数据。涉及临床、动物实验、实验室设备、远程平台写入或其他高风险场景时，应遵守目标技能声明的研究边界、人工复核和授权要求。

仓库根目录要求 Python 3.13+，其开发依赖由 `uv` 管理；这不是所有被使用技能的统一运行环境要求。实际科研包依赖会按技能分别处理。

## 2. 安装技能

### 方式 A：标准安装器

在支持该安装器的主机中运行：

```bash
npx skills add K-Dense-AI/scientific-agent-skills
```

适合希望由客户端/安装器处理技能目录与发现配置的场景。

### 方式 B：GitHub CLI

GitHub CLI 2.90.0+ 可交互安装全部或指定技能：

```bash
gh skill install K-Dense-AI/scientific-agent-skills
gh skill install K-Dense-AI/scientific-agent-skills scanpy
gh skill install K-Dense-AI/scientific-agent-skills --agent codex
```

需要可复现安装时可固定版本标签或提交：

```bash
gh skill install K-Dense-AI/scientific-agent-skills --pin v2.69.0
```

### 方式 C：从本地检出安装插件

当前工作区已经是完整的 Agent Plugins 包。支持插件标准的客户端可直接从其根目录安装，例如 Codex：

```bash
codex plugins install .
```

Cursor 可将当前仓库链接到其本地插件目录后重新加载窗口：

```bash
mkdir -p ~/.cursor/plugins/local
ln -s "$(pwd)" ~/.cursor/plugins/local/scientific-agent-skills
```

其他主机的目录规则不同。常见手动安装方式是将仓库克隆到主机配置的 `~/.agents/skills/` 或项目内 `.agents/skills/`，随后在该主机中确认发现路径。对于技能数量较多的客户端，优先安装与当前研究主题相关的子集，以控制常驻上下文。

## 3. 发现并调用技能

安装后，兼容客户端会从 `skills/` 的直接子目录中发现含 `SKILL.md` 的技能。可以让 Agent 按任务自动匹配，也可以在提示词中直接指定技能名称。

### 自动匹配

用任务、数据格式、软件名和预期产物清楚描述需求。例如：

```text
分析一个 10x 单细胞 RNA-seq 数据集：完成质量控制、归一化、聚类、UMAP、
marker 基因与结果图。使用 Scanpy；保留原始计数，说明阈值和所有质量检查。
```

### 显式指定

当任务跨多个领域时，直接列出技能可以减少歧义：

```text
使用 database-lookup、rdkit、diffdock 和 scientific-writing 技能。
针对 EGFR 抑制剂候选物生成可追溯的研究优先级报告：先检索证据，再做结构
分析和对接；区分预测、实验事实与不确定性，并给出每一步的验证结果。
```

技能名称必须与 `skills/<name>/` 目录一致。技能的 `description` 通常包含触发词和适用范围；可浏览 [技能目录](skills.md) 来选择名称，或直接打开 `skills/<name>/SKILL.md` 阅读完整工作流。

## 4. 一次可靠的使用流程

1. 选择技能：根据研究问题、数据类型、目标平台或软件包确定一项或多项技能。
2. 核对环境：阅读 `SKILL.md` 的安装、`compatibility` 和凭据说明，安装目标技能需要的依赖，不要假设仓库开发环境包含它们。
3. 明确输入与授权：说明输入文件位置、数据格式、研究目标、输出位置，以及是否允许网络调用或远程写入。
4. 执行技能工作流：优先采用技能给出的命令、脚本和验证步骤。存在 `scripts/` 时，先运行 `python scripts/<tool>.py --help` 确认参数。
5. 检查输出：保留来源、参数、质量控制结果、模型/数据局限性和失败项。将技能中的科学 caveat 视为结果的一部分。
6. 进行必要复核：临床、诊断、治疗、动物福利阈值、物理设备执行和远程写入等场景，技能产物仅支持授权人员的复核流程，不应越过技能明确的安全边界。

## 5. 典型示例

### 5.1 单细胞 RNA-seq

安装 Scanpy 及 Leiden 聚类依赖：

```bash
uv pip install "scanpy[leiden]"
```

阅读 `skills/scanpy/SKILL.md`，再让 Agent 使用该技能。该技能还提供可串联的 CLI 脚本；一个典型入口是：

```bash
python skills/scanpy/scripts/run_pipeline.py raw.h5ad -o processed.h5ad
```

执行前先用 `--help` 核对脚本支持的参数和输出设置。针对 R 的 `.rds`、Seurat 或 SingleCellExperiment 数据，应遵循 Scanpy 技能中 `references/r_interop.md` 的转换流程，先生成 `.h5ad`。

### 5.2 多技能药物发现研究

在对话中给出目标、证据标准和结果格式，而不是只要求“找药”：

```text
使用 database-lookup、rdkit、datamol、medchem、diffdock 和 scientific-writing。
面向 EGFR 抑制剂建立候选清单：每项外部证据给出来源；活性数据不能混合不同
实验类型；对接置信度不等于结合亲和力；把超出模型适用域的候选标记为未评分。
交付排序表、可追溯报告和各阶段的质量检查。
```

这种写法将研究问题、技能、限制条件和交付物同时传达给 Agent，能使其按技能中的科学验证步骤协作，而不是把多个工具的输出直接拼接。

### 5.3 仅浏览可用技能

```bash
find skills -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort
sed -n '1,160p' skills/scanpy/SKILL.md
```

第一条命令列出技能名称；第二条用 Scanpy 作为示例查看一个技能的元数据、安装说明和工作流。也可以浏览 [技能目录](skills.md)，它按研究领域列出技能及用途。

## 6. 长期自动化文献库

仓库新增 `literature-archive`，用于建立可审计的本地 SQLite 文献库。它不是通用网页爬虫：每次运行仅查询 OpenAlex 与 Europe PMC，优先以 DOI、PMID、PMCID、arXiv ID 去重；仅下载被 OpenAlex 或可选 Unpaywall 标记为开放获取的 PDF，并验证内容类型、PDF 文件头与大小限制。

初始化一个运行目录：

```bash
mkdir -p research/literature-archive
cp skills/literature-archive/assets/config.example.json \
  research/literature-archive/config.json
```

编辑 `config.json` 中的绝对路径和 `queries`。先离线检查配置：

```bash
python skills/literature-archive/scripts/literature_archive.py \
  --config research/literature-archive/config.json --dry-run
```

确认后执行一次真实归档：

```bash
UNPAYWALL_EMAIL='researcher@example.org' \
python skills/literature-archive/scripts/literature_archive.py \
  --config research/literature-archive/config.json
```

`UNPAYWALL_EMAIL` 是可选项，用于为 DOI 记录补充开放 PDF 地址；不得写入 JSON 或提交到仓库。运行摘要会输出到标准输出，SQLite 数据库记录每次检索的计数、来源失败、元数据、去重关系及下载状态。PDF 和数据库位于 `skills/` 外，避免混入 Agent 的技能上下文。

在手工运行且检查结果后，才使用宿主调度器。以下 cron 样例每天 03:15 执行；请替换为已验证的绝对路径，并通过系统环境文件或秘密管理器传入邮箱，而不是把凭据写入 crontab：

```cron
15 3 * * * /usr/bin/python3 /absolute/path/to/scientific-agent-skills/skills/literature-archive/scripts/literature_archive.py --config /absolute/path/research/literature-archive/config.json >> /absolute/path/research/literature-archive/archive.log 2>&1
```

脚本使用锁文件阻止重叠运行；当所有配置来源都失败时返回非零。可用以下命令测试该技能：

```bash
python tests/run_all.py --isolated literature-archive
```

## 7. 数据分析方法提取 CLI 与 GUI

`literature-method-extraction` 将长期归档扩展为“指定领域论文的数据分析方法调研”。它先运行 `literature-archive`，然后只对有 PMCID 的 Europe PMC 开放 JATS 全文读取标有 Methods 的章节，提取数据与研究设计、预处理、分析、统计、验证和软件等证据句。它不会把摘要、题目或无法解析的 PDF 当作方法学证据。

执行完整 CLI 流程：

```bash
python skills/literature-method-extraction/scripts/literature_methods.py run \
  --archive-config research/method-survey/archive.json \
  --output research/method-survey/methods.csv
```

首次运行建议增加 `--limit 25`。后续仅处理现有归档时使用 `--skip-archive`；重新抓取已有全文时使用 `--refresh`。CSV 会同时保留论文标识、提取状态、JSON 结构化证据、原文 Methods 摘录、来源 URL 与人工复核状态。

启动本地 GUI：

```bash
python skills/literature-method-extraction/scripts/literature_methods_gui.py \
  --archive-config research/method-survey/archive.json
```

浏览器打开终端输出的 `http://127.0.0.1:8765`。界面提供全流程运行、仅提取本地归档、方法证据浏览以及 `pending`、`accepted`、`revised`、`rejected` 四种复核状态。它只绑定本机回环地址；正式汇总前应人工核对证据句与来源全文。

需要向同事发放单文件启动器时，在专用打包环境执行：

```bash
uv tool run pyinstaller --onefile --name literature-methods \
  skills/literature-method-extraction/scripts/literature_methods_gui.py
```

打包产物仍须配套提供不含凭据的配置模板，且由使用者填写本地数据路径。测试命令：

```bash
python tests/run_all.py --isolated literature-method-extraction
```

## 8. 本地开发与维护技能

### 新建或修改

1. 先阅读 `AGENTS.md`、`CONTRIBUTING.md` 和目标技能的现有 `SKILL.md`。
2. 新技能创建在 `skills/<name>/`，目录名与 YAML 的 `name` 完全一致；使用小写字母、数字和连字符。
3. `SKILL.md` 必须包含标准前置元数据，并以带引号的 `metadata.version` 开始，例如 `"1.0"`。
4. 更新已有技能时同步递增其 `metadata.version`：普通改进递增小版本，破坏性变更或重大重构递增大版本。
5. 只在确有必要时添加 `references/`、`scripts/` 和 `assets/`。测试、fixtures、临时文件和生成结果不得放进 `skills/`。
6. 只要技能提供 `scripts/`，就在 `tests/<name>/` 增加测试，并在 `tests/skill-requirements.toml` 声明该技能需要的包和 Python 版本。

最小技能文件示例：

```markdown
---
name: skill-name
description: Describes a narrow scientific workflow and when an agent should use it.
license: MIT
compatibility: Requires Python 3.12+ with package installed. Needs network access.
metadata:
  version: "1.0"
  skill-author: Your Name
---

# Skill Title

## When to use

Use this skill when...
```

前置元数据只能使用规范定义的六个顶级字段。`metadata` 采用 YAML 块映射，不能写成 JSON 单行映射；数值、日期和布尔语义的元数据值应加引号。

### 验证命令

在仓库根目录执行：

```bash
uv sync
uv run skills-ref validate skills/<name>
uv run --with pytest python -m pytest tests/_meta -q
```

如果改动了某项技能的脚本，再运行对应测试。由于不同科研包存在互斥依赖，推荐使用隔离环境：

```bash
python tests/run_all.py --isolated <name>
```

测试全部技能时：

```bash
python tests/run_all.py --isolated
```

`tests/run_all.py` 为每个技能启动单独 pytest 进程，并依据 `tests/skill-requirements.toml` 建立临时 `uv` 环境。不要把所有技能脚本收集到一个 pytest 进程中运行，因为它们可能有相同的顶层辅助模块名。

### 安全扫描

新建或大幅修改技能后，在具备 `SKILL_SCANNER_LLM_API_KEY` 时执行：

```bash
uv run python scan_pr_skills.py skills/<name>
```

或使用上游工具：

```bash
uv run skill-scanner scan skills/<name> --use-behavioral
```

扫描发现必须结合实际代码验证。读取自身 API 密钥后访问其所属服务、普通 `subprocess` 用法，或标识符中偶然包含 `eval`/`exec` 字样，都可能产生已知类型的误报。不要为消除告警而破坏正确实现。

## 9. 常见问题

**Agent 没有发现技能**：确认安装位置是该 Agent 的扫描路径；插件方式要求根目录同时有 `plugin.json` 和 `skills/`；每个技能必须是 `skills/` 的直接子目录且含有 `SKILL.md`。

**某项工作流缺少包或命令**：不要在项目开发环境中盲目安装所有科研依赖。打开目标 `SKILL.md`，按其版本和系统要求在任务环境中安装；测试时使用 `--isolated`。

**API 调用失败**：检查该技能声明的环境变量、账户权限、网络策略和服务可用性。不要将 API 密钥写入技能文档、脚本或提交记录。

**规范校验失败**：首先检查目录名与 `name` 是否一致、前置元数据是否只含允许字段、`metadata.version` 是否存在且加引号，以及 `metadata` 是否为块式 YAML。

**脚本测试相互影响**：使用 `python tests/run_all.py --isolated <name>`，而非将多个技能套件合并为一次 pytest 收集。
