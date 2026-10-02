# 仓库指南

本仓库收集面向科学研究的 Agent Skill。每个 skill 位于 `skills/` 下独立目录，且必须符合开放的 [Agent Skills 规范](https://agentskills.io/specification)。

创建或修改 skill 前请阅读本文档。`CONTRIBUTING.md` 包含相同要求的更详细说明及拉取请求流程。

## 此处应包含的内容

**范围内：** 面向单个科学软件包、数据库、平台或研究工作流的专门 skill，例如 `scanpy`、`depmap`、`benchling-integration`、`experimental-design`。

**范围外**，且通常会被拒绝：

- 通用软件工程或编码判断类 skill，它们会在每个任务中竞争被选中。
- 仅附加科学示例的通用基础设施（如向量数据库或云 SDK）；接受一个即意味着支持所有竞争者。
- 路由到其他 skill 的宽泛“编排器” skill；它们按设计就会与每个专业 skill 重叠。
- 现有 skill 已能访问的服务的第二个提供商。

现有通用 skill 是窄范围输出格式辅助工具（`docx`、`pdf`、`pptx`、`generate-image`、`markdown-mermaid-writing`），不构成扩展范围的先例。

## 布局

仓库根目录是 [Agent Plugins](https://agent-plugins.org/) 1.0.0 软件包：包含 `plugin.json` 与可移植的 `skills/` 树。确保 `plugin.json` 符合 Agent Plugins manifest schema，且其 `version` 与 `pyproject.toml` 的 `[project].version` 相同。不得向 `plugin.json` 添加不可移植顶层字段（不使用内联 MCP、hooks 或仅客户端字段；有需要时使用 `mcp.json` 或反向域名 `extensions` 命名空间）。

```text
plugin.json                 # Agent Plugins 清单（仓库根目录）
skills/<skill-name>/
├── SKILL.md        # 必需
├── references/     # 可选：仅在需要时加载的长篇文档
├── scripts/        # 可选：可执行辅助工具
└── assets/         # 可选：模板和静态资源
```

每个 skill 只要求 `SKILL.md`。从 skill 根目录以一层深的相对路径引用其他文件。

**测试绝不能放在 `skills/` 下。** Skill 目录只交付 agent 要加载的内容。脚本和结构检查应放入仓库级测试套件：

```text
tests/<skill-name>/          # 与 skill 目录同名
├── test_scripts.py
└── fixtures/                # 可选测试数据
```

**图表同样不能放在 `skills/` 下。** Skill 可在 `docs/images/<skill-name>.png` 附带由 `scripts/generate_skill_image.py` 生成的工作流图。图表可选，参见[技能图表](#技能图表)。

测试必须通过显式锚点访问 skill，不能相对遍历：

```python
SKILL_ROOT = Path(__file__).resolve().parents[2] / "skills" / "<skill-name>"
```

## 创建 skill

1. 创建 `skills/<name>/`：**目录名就是 skill 名**，且必须等于 frontmatter 的 `name`。
2. 使用下方模板编写 `SKILL.md`，从 `metadata.version: "1.0"` 开始。
3. 仅在确有必要时添加 `references/`、`scripts/` 或 `assets/`。
4. 运行所记录的命令与代码。声明范围仅限实际测试的发布版本（例如“针对稳定版 GeoPandas 1.1.4”）；未测试内容标为示例。
5. 若交付 `scripts/`，其测试应在 **`tests/<name>/`**，不得置于 skill 目录；fixture 放在 `tests/<name>/fixtures/`。
6. 执行下文的验证和扫描。

```markdown
---
name: skill-name
description: 此 skill 的功能、agent 应使用它的时机，以及应触发它的关键词。
license: MIT
compatibility: 需要 Python 3.12+，并已安装 <package>。需要网络访问。
metadata:
  version: "1.0"
  skill-author: Your Name
---

# Skill 标题

## 使用时机

在以下情况使用此 skill……

## 工作流

1. ……

## 示例

……
```

## 更新 skill

1. 先阅读当前的 `SKILL.md` 和支持文件。
2. 查看上游文档：API 会变化，skill 可能固定在旧版本。
3. 做最小且有用的修改。
4. **在同一修改中提升 `metadata.version`**：普通改进提升次版本（`"1.2"` → `"1.3"`）；仅在破坏性改动或重大重构时提升主版本（`"1.9"` → `"2.0"`）。
5. 重跑每个改动过的示例、命令或脚本；如存在 `tests/<name>/` 也要运行。测试只检查 `metadata.version` 是否存在并加引号，不检查具体值，因此版本提升不需要测试改动。

## Frontmatter

`SKILL.md` 以 YAML frontmatter 开始。**只允许以下六个字段**；规范是封闭集合，其他任意顶层键均为验证错误：

| 字段 | 必需 | 约束 |
| --- | --- | --- |
| `name` | 是 | 1–64 个字符，仅小写字母、数字、连字符；不可首尾连字符或连续连字符，且**必须等于目录名**。 |
| `description` | 是 | 1–1024 字符。说明功能、使用时机和触发关键词。使用第三人称。 |
| `license` | 否 | 许可证名称或随附许可证文件的引用。 |
| `compatibility` | 否 | 最多 500 字符。仅环境要求；没有则省略。 |
| `allowed-tools` | 否 | **空格分隔的字符串**，如 `Read Write Edit Bash`。不是 YAML 列表，也不用逗号分隔。 |
| `metadata` | 否 | 字符串键到**字符串**值的映射，下述宿主清单块除外。本仓库要求 `metadata.version`。 |

作者、上游版本、审核日期、客户端专有配置等均放在 `metadata`，不能放顶层。特别是 Hermes 的顶层 `required_environment_variables` 不可使用：它会导致验证器失败，而且 `strictyaml` 拒绝整个文档时，`name` 和 `description` 也无法读取。应在 `compatibility` 和 `metadata.openclaw.envVars` 声明凭据。

### 使用块样式 YAML，不使用 JSON 流式样式

参考验证器使用 `strictyaml` 解析 frontmatter，**拒绝 JSON 风格的流式映射和序列**。流式映射会让整个 frontmatter 无法解析，导致 `name`、`description` 不可读，skill 无法注册。

```yaml
# 错误：会破坏验证器
metadata: {"version": "1.1", "skill-author": "K-Dense Inc."}

# 正确
metadata:
  version: "1.1"
  skill-author: K-Dense Inc.
```

### 为 `metadata` 标量加引号

可能被解析为数字、布尔值或日期的值必须加引号，例如 `version: "1.0"`、`last-reviewed: "2026-07-23"`，使其按规范保持为字符串。

### 宿主清单块保持嵌套映射

`metadata.openclaw` 和 `metadata.hermes` 是文档化例外：必须是**嵌套映射**，不能是 JSON 字符串。OpenClaw 的 `resolveOpenClawManifestBlock()` 需要 `typeof candidate === "object"`；JSON 字符串会悄然禁用依赖门控和凭据注入。嵌套映射仍可通过 `skills-ref validate`。

```yaml
metadata:
  version: "1.1"
  skill-author: Exa
  openclaw:
    primaryEnv: EXA_API_KEY
    envVars:
      - name: EXA_API_KEY
        required: true
        description: Exa 搜索 API 密钥。
  hermes:
    category: research
```

仅有外部要求的 skill 才需要这些块；大多数可省略。失败的 `requires` / `requires_toolsets` 门控会让 agent 看不到该 skill，因此只门控真正不可缺少的依赖。

## 正文与布局

- `SKILL.md` 应少于 500 行；超过时 CI 警告。将长参考材料移到 `references/`，按需加载。
- Skill 目录只交付 agent 加载的内容。测试、fixture、临时数据和生成物均不得放入；测试在 `tests/<name>/`。
- 提供具体工作流、命令和完整示例，而不是背景介绍。
- 列出所需包、系统依赖、凭据及网络访问。
- 包含重要的科学注意事项与验证检查。
- 脆弱或重复逻辑放入 `scripts/`，不要要求 agent 重写。
- 不得包含秘密、API key、私有 URL 或未发布数据。

## 验证与扫描

```bash
uv sync

# 验证一个 skill 是否符合规范
uv run skills-ref validate skills/<name>

# 验证所有 skill，CI 使用同样方式
for d in skills/*/; do uv run skills-ref validate "$d"; done
```

`.github/workflows/skill-spec-validation.yml` 会在每个涉及 `skills/` 的 PR 上运行这些检查，并执行 `skills-ref` 未检查的规则：`metadata.version` 存在、`allowed-tools` 是空格分隔字符串、`metadata` 标量已加引号，及 500 行警告。

新增或实质改动的 skill 必须安全扫描。扫描使用 [Cisco AI Defense Skill Scanner](https://github.com/cisco-ai-defense/skill-scanner)，即 `pyproject.toml` 固定版本的 `cisco-ai-skill-scanner`，用于检测提示注入、数据外泄和恶意代码模式。其 README 说明规则 ID 与 CLI 参数；不熟悉发现的规则时应查阅它。

`.github/workflows/pr-skill-scan.yml` 对每个 PR 中更改的 skill 运行仓库包装器并发布置顶评论；发现 HIGH 或以上即失败：

```bash
# 需要 SKILL_SCANNER_LLM_API_KEY（见 .env）
uv run python scan_pr_skills.py skills/<name>

# 或直接使用上游 CLI，不经仓库包装器
uv run skill-scanner scan skills/<name> --use-behavioral
```

**“修复”发现前，先针对代码验证它。** 常见系统性误报：读取自身 API key 并调用自身服务的 skill 上的 `BEHAVIOR_*_EXFILTRATION`、`BEHAVIOR_ENV_VAR_HARVESTING`；所有 `subprocess` 片段（包括安全的参数列表形式）上的 `MDBLOCK_PYTHON_SUBPROCESS`；以及普通标识符（`retrieval`、`executor`）中的子字符串或 `model.eval()` 引发的 `*_EVAL_EXEC`。发现有时会引用 skill 不存在的文件，先用 `find skills/<name> -type f` 核对。

如 skill 有 `tests/<name>/` 测试，运行：

```bash
uv run --with pytest python -m pytest tests/<name> -q

# 仓库级守卫通过后，每个 skill 用一个进程运行测试套件
uv run --with pytest python tests/run_all.py
```

**每个 pytest 进程只测试一个 skill。** Skill 的 `scripts/` 使用普通顶层模块名，其中 32 个带有 `scripts/_common.py`。在同一解释器收集两个 skill 时，`_common` 会解析成最先导入者，进而悄然测试错误代码。`tests/conftest.py` 会拒绝这种会话；`tests/run_all.py` 为每个 skill 分叉进程。

### 仓库级守卫

```bash
uv run --with pytest python -m pytest tests/_meta -q
```

`tests/_meta` 是仓库最快的有效信号：只用标准库，不依赖科学包，仅数秒。它对**每个** skill 执行共享结构契约；如某 skill 交付 `scripts/` 但缺少 `tests/<name>/` 测试套件或 `tests/skill-requirements.toml` 条目则失败。`.github/workflows/skill-tests.yml` 在每个 PR 运行它，因此无测试脚本无法合入。完整 `tests/run_all.py` 以此开始。

它不属于单个 skill 进程，因为有意同时覆盖全部 skill；它不导入 skill 代码，只进行解析，所以安全。

### 共享契约

`tests/_contract/` 保存每个 skill 共享的断言，单个 skill 套件只需包含专属内容。`tests/conftest.py` 将其注册为可导入模块 `skill_contract`：

```python
import skill_contract

# 每个 argparse 脚本应响应 --help；包缺失时跳过，
# 使用 --isolated 时真正执行
CliHelpTests = skill_contract.cli.help_test_case(SKILL_ROOT)

# 适用于在 `if __name__ == "__main__"` 中带有运行示例的库式脚本
DemoBlockTests = skill_contract.cli.demo_test_case(SKILL_ROOT, ("doe_designs.py",))
```

- `structure`：frontmatter 合规、500 行限制、`skills/` 下无测试或字节码、本地链接可解析、脚本可解析、无 `eval`/`exec`/`os.system`、无标准库遮蔽、无硬编码本地路径、shell 脚本有效。由 `tests/_meta` 仓库级运行；单个 skill 套件不要重复。
- `cli`：上述 `--help` 和 demo-block 测试。
- `office` / `schematic`：多个 skill 以字节级相同副本交付文件的行为（docx/pptx/xlsx 中的 OOXML 树，以及五个 skill 的 AI schematic 生成器）。`tests/_meta` 会独立检测副本漂移，须一起修复。

### 每个 skill 一个环境

项目环境刻意不安装各 skill 的科学包，因为上游约束互相冲突：`opentrons` 需要 `numpy<2`，`esm` 将 `transformers` 限于低于 `transformers` skill 目标版本，`geniml`、`spikeinterface` 固定 `zarr<3` 而 `zarr-python` skill 要求 3.x，`bioservices` 限制 `lxml<6` 而与 `matchms` 冲突，`pytdc`、`molfeat`、`deepchem`、`histolab`、`vaex`、`ete3` 都要求低于 3.13 的解释器。一起安装将使全部包陷入版本冲突。

所以 `--isolated` 会根据 [`tests/skill-requirements.toml`](tests/skill-requirements.toml) 为每个 skill 建立一次性 `uv` 环境：

```bash
python tests/run_all.py --isolated                 # 每套测试各一个环境
python tests/run_all.py --isolated scanpy qiskit   # 只运行这些 skill
```

每个条目列出 skill 文档声明的包，并可选给出 `python`，供不能使用默认解释器的 skill 使用；uv 会按需下载。完全无法安装的包（GitHub-only SDK、conda-forge-only 库、CUDA 构建）在 `[unavailable]` 下记录原因，运行器会输出这些缺口。

新增带 `scripts/` 的 skill 必须添加 `[skills.<name>]`；否则 `tests/_meta` 失败。只有标准库工具的 skill 使用 `packages = []`，仍会获得干净环境且 CI 会在每个 PR 运行。uv 全局缓存 wheel，重复运行可在数毫秒内建环境。

完整 `--isolated` 扫描不在 CI 中运行：它会为每个 skill 建环境，部分需要 CUDA 工具链、JDK 或本地 MATLAB。发布前或改动共享契约时运行。

## 技能图表

Skill 可在 `docs/images/<skill-name>.png` 携带生成的工作流图。图表可选：新建或修改 skill 都不因缺图或未刷新而阻塞，CI 也不强制。若交付，须注意它由文档派生；工作流改变时应重新生成，避免图文不符。

`scripts/generate_skill_image.py` 是只使用标准库的本地工具，通过一个 `OPENROUTER_API_KEY`（环境变量、仓库 `.env` 或 `--api-key`）分两阶段执行：文本模型读取 `SKILL.md`、`references/` 的全部内容，以及 `scripts/`、`assets/` 清单，提炼出图表描述；图像模型再绘图。它读取整个 skill，应在文档定稿**之后**运行。

```bash
# 一个 skill -> docs/images/<name>.png，替换既有图像
uv run python scripts/generate_skill_image.py --skill <name>

# 显示读取文件及输出位置：不调用 API、不计费
uv run python scripts/generate_skill_image.py --skill <name> --dry-run

# 读取 skill 并打印图表提示词，但不绘图
uv run python scripts/generate_skill_image.py --skill <name> --prompt-only

# 批量处理多个 skill
uv run python scripts/generate_skill_image.py --skill <name-a> <name-b>

# 补全所有无图表的 skill，每次六个
uv run python scripts/generate_skill_image.py --all --skip-existing -j 6
```

提交前检查结果。图像模型会拼错标签，也偶尔指错箭头；重新生成，不要交付文本错误的图。`--quality low` 适用于检查构图时的低成本迭代，提交时使用 `high`。美术方向和读取器指令位于脚本开头，应在那里调整而不要为单个 skill 手调提示词，以保持一致。

## 提交 PR 前

- 目录名和 frontmatter `name` 完全一致。
- `skills/<name>/` 内无 `tests/` 目录及 `test_*.py`；测试属于 `tests/<name>/`。
- 顶层只有规范的六个字段，其他内容都在 `metadata`。
- `metadata.version` 存在且加引号；修改既有 skill 时已提升版本。
- `metadata` 是块映射；`openclaw` / `hermes` 块为嵌套映射。
- `uv run skills-ref validate skills/<name>` 通过。
- 集合版本改变时，`plugin.json` 的 `version` 与 `pyproject.toml` 一致。
- `uv run --with pytest python -m pytest tests/_meta -q` 通过。CI 以它作为阻塞检查，可发现缺少套件、缺少 `skill-requirements.toml` 条目、失效本地链接、泄露本地路径和漂移的 Agent Plugins manifest。
- 若 skill 有 `scripts/`：存在 `tests/<name>/` 套件、`tests/skill-requirements.toml` 中存在 `[skills.<name>]`，且 `python tests/run_all.py --isolated <name>` 通过。
- 若有 `docs/images/<name>.png`，标签拼写和箭头指向正确；图片本身可选。
- 示例和脚本已测试，或明确标为示例。
- 不含秘密或私有数据；扫描无问题，或已在 PR 中说明。
