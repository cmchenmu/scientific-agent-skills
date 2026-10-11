我会把整个项目拆成**6 个阶段**，每个阶段都给出“做什么、用什么工具、具体怎么跑”。你照着一步步做，就能从零搭出一个能跑通的多智能体协作平台。

> **前置准备**：一台能跑 Docker 的电脑（Windows/Mac/Linux 都行），Python 3.10+，以及一个 OpenAI 兼容的 API Key（OpenAI、SiliconFlow、DeepSeek 都行）。

---

## 阶段一：搭骨架——跑通“单 Agent + 单工具”的最小闭环

**目标**：让一个 Agent 能调用一个工具并返回结果。这是所有后续功能的基础。

### 1.1 安装核心依赖

```bash
pip install langgraph langchain-openai redis
```

### 1.2 写一个最简单的“查天气”Agent

创建 `step1_minimal_agent.py`：

```python
import os
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from typing import TypedDict, Annotated, Sequence
from langchain_core.messages import BaseMessage, HumanMessage
import operator

# 配置你的 API（换成你自己的）
os.environ["OPENAI_API_KEY"] = "你的key"
os.environ["OPENAI_BASE_URL"] = "https://api.siliconflow.cn/v1"

@tool
def get_weather(city: str) -> str:
    """查询指定城市的天气"""
    # 这里先写死，后面阶段会换成真实 API
    return f"{city}今天晴，25°C"

class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]

model = ChatOpenAI(model="Qwen/Qwen2.5-7B-Instruct", temperature=0)
tools = [get_weather]
model_with_tools = model.bind_tools(tools)

def agent_node(state: AgentState):
    response = model_with_tools.invoke(state["messages"])
    return {"messages": [response]}

def should_continue(state: AgentState):
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END

graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("tools", ToolNode(tools))
graph.set_entry_point("agent")
graph.add_conditional_edges("agent", should_continue)
graph.add_edge("tools", "agent")

app = graph.compile()

# 测试
result = app.invoke({"messages": [HumanMessage(content="北京天气怎么样")]})
for msg in result["messages"]:
    print(f"[{msg.type}] {msg.content[:100]}")
```

**跑通标志**：终端输出里能看到 Agent 先调用 `get_weather` 工具，然后基于结果给出回答。


## 阶段二：Redis 会话管理——让状态“活”在多实例之外

**目标**：把 Agent 状态从内存搬到 Redis，实现多会话并发下的状态一致性。

### 2.1 启动 Redis（Docker）

```bash
docker run -d --name agent-redis -p 6379:6379 redis:7-alpine
```

### 2.2 实现 Redis 状态管理器

创建 `step2_redis_state.py`：

```python
import json
import redis
import uuid

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

def save_session(session_id: str, state: dict, ttl: int = 1800):
    """保存会话状态到 Redis，30 分钟过期"""
    r.setex(f"session:{session_id}:state", ttl, json.dumps(state, ensure_ascii=False))

def load_session(session_id: str) -> dict:
    data = r.get(f"session:{session_id}:state")
    return json.loads(data) if data else {}

def append_message(session_id: str, message: dict):
    """用 Redis List 追加消息历史"""
    r.rpush(f"session:{session_id}:messages", json.dumps(message, ensure_ascii=False))
    r.expire(f"session:{session_id}:messages", 1800)
```

### 2.3 用乐观锁防止并发覆盖

在 Agent 写回状态时，用 `WATCH` 机制保证一致性：

```python
def update_session_safely(session_id: str, update_fn):
    """乐观锁：读-改-写，冲突则重试"""
    key = f"session:{session_id}:state"
    with r.pipeline() as pipe:
        while True:
            try:
                pipe.watch(key)
                current = json.loads(pipe.get(key) or "{}")
                updated = update_fn(current)
                pipe.multi()
                pipe.set(key, json.dumps(updated, ensure_ascii=False))
                pipe.execute()
                return updated
            except redis.WatchError:
                continue  # 被其他请求改过，重试
```

**验证方法**：开两个终端同时调用 `update_session_safely`，看最终状态是否丢失更新。


## 阶段三：MCP 工具集成——让 Agent 访问真实外部世界

**目标**：用 MCP 协议连接真实数据库和第三方 API。

### 3.1 用 Higress 的 MCP Server 快速接入（最简单的方式）

Higress 是阿里开源的 AI 网关，内置了 MCP Server 托管能力，可以将任何 REST API 转换成 MCP 工具。

**最简做法**：部署 Higress（支持 Docker Compose 一键启动），在控制台里配置一个 MCP 服务，指向你要的 API。Agent 侧只需连接 Higress 的 SSE 端点。

```bash
# 如果不想自己搭，直接连接社区已有的 MCP 服务器
# 比如用 Claude Code 的方式添加远程 MCP：
claude mcp add --transport http notion https://mcp.notion.com/mcp \
  --header "Authorization: Bearer your-token"
```

### 3.2 在 LangGraph Agent 里接入 MCP 工具

用 `langchain-mcp-adapters` 把 MCP 服务器暴露的工具转成 LangChain Tool：

```bash
pip install langchain-mcp-adapters
```

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

async def create_agent_with_mcp():
    client = MultiServerMCPClient({
        "weather": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-weather"]
        },
        "database": {
            "url": "http://localhost:8080/sse",  # Higress 或自建 MCP Server
            "transport": "sse"
        }
    })
    tools = await client.get_tools()
    # 把 tools 绑定到你的 model，和阶段一的 get_weather 用法完全一样
    return tools
```

**验证方法**：让 Agent 说“查一下数据库里有多少用户”，看它是否能正确调用 MCP 工具并返回真实数据。


## 阶段四：三层记忆——让 Agent 记住该记的，忘掉该忘的

**目标**：工作记忆（Redis）、短期记忆（压缩后的会话）、长期记忆（本地向量库）。

### 4.1 直接用现成的本地记忆库

推荐 `consolidation-memory`，它内置了 SQLite + FAISS，本地运行，无需 API Key（用 FastEmbed）。

```bash
pip install consolidation-memory[fastembed]
consolidation-memory init
```

它的工作方式是：Agent 存入“episodes”（对话、事实），后台线程每 6 小时自动聚类相似内容，用本地 LLM 合成结构化知识，旧 episode 被淘汰。

### 4.2 在 Agent 里集成记忆

```python
from consolidation_memory import MemoryClient

mem = MemoryClient()

# 每次对话结束后，存入记忆
mem.store("用户偏好 Python 后端开发", content_type="preference", tags=["tech"])

# 新会话开始时，检索相关记忆注入上下文
result = mem.recall("用户的技术偏好")
for ep in result.episodes:
    print(ep["content"])
```

### 4.3 工作记忆/短期记忆用 Redis 分层

```python
def get_working_memory(session_id: str, last_n: int = 5) -> list:
    """工作记忆：最近 N 轮对话"""
    raw = r.lrange(f"session:{session_id}:messages", -last_n, -1)
    return [json.loads(m) for m in raw]

def get_short_term_summary(session_id: str) -> str:
    """短期记忆：压缩后的摘要（存 Redis Hash）"""
    return r.hget(f"session:{session_id}:summary", "text") or ""
```

压缩策略：当消息数超过 20 条时，用 LLM 把最早 10 条压缩成一段摘要，存入 Hash，原始消息删除。


## 阶段五：Prompt 迭代——用自动化评测代替“感觉”

**目标**：针对任务规划、工具选择、结果合成三个环节分别优化 Prompt，并能量化提升。

### 5.1 用 evx 做 Prompt 评测（来自搜索结果）

`evx` 是一个 CLI 工具，专门做 Prompt 优化：你定义测试用例和评分标准，它自动评估当前 Prompt，让 Agent 提出改进版本，只有通过率更高才接受。

```bash
pip install evx

# 创建评测项目
ev create myagent

# 编辑 evals/myagent/eval.md，定义评分标准
# 编辑 evals/myagent/system_prompt.j2，写你的 Prompt
# 添加 evals/myagent/cases/case1.json，写测试用例

# 运行优化：3 轮迭代
ev run myagent -i 3
```

评分标准示例（写进 `eval.md`）：
```markdown
# 规划完整性
任务规划必须包含所有必要步骤，不能遗漏。

# 工具选择正确性
选择的工具必须与任务匹配，不能选错。

# 输出格式
最终输出必须是自然语言总结，不能包含原始 JSON。
```

### 5.2 三个环节的 Prompt 核心约束

**任务规划**：强制“只规划，不执行”，步骤用动词开头。
```
只输出编号步骤列表，每步是动词开头的原子操作。完成后输出 PLAN_COMPLETE。
```

**工具选择**：调用前输出一行理由。
```
在调用工具前，输出：SELECTING: <tool_name> | REASON: <一句话理由>
```

**结果合成**：用格式契约锁死输出。
```
FINAL OUTPUT CONTRACT:
- 必须包含：①一句话结论 ②关键数字 ③数据来源
- 禁止包含：原始 JSON、工具日志、中间推理
```


## 阶段六：串起来——完整可跑的最小系统

把以上所有阶段组装成一个 `main.py`，核心流程：

```python
# 伪代码结构
async def handle_request(user_input: str, session_id: str):
    # 1. 加载会话状态（Redis）
    state = load_session(session_id)
    
    # 2. 检索长期记忆，注入上下文
    memories = mem.recall(user_input)
    
    # 3. 调用 LangGraph Agent（带 MCP 工具）
    result = app.invoke({
        "messages": [HumanMessage(content=user_input)],
        "memories": memories
    })
    
    # 4. 保存状态（乐观锁）
    update_session_safely(session_id, lambda s: {...})
    
    # 5. 写入记忆
    mem.store(f"用户问了：{user_input}", content_type="episode")
    
    return result
```

---

## 小白实操建议

**第 1 天**：只做阶段一，让单 Agent 跑通。不要碰 Redis、MCP、记忆，先看到“Agent 调用工具并回答”的结果。

**第 2 天**：加 Redis（阶段二），写一个简单的并发测试脚本，确认状态不丢。

**第 3 天**：加 MCP（阶段三）。如果不想搭 Higress，可以用 `@modelcontextprotocol/server-*` 系列的现成服务器，或者干脆用 `hippocampus-memory-mcp` 这个包，它同时提供了记忆和 MCP 服务器。

**第 4 天**：加记忆（阶段四）。先用 `consolidation-memory`，它开箱即用，不需要自己写向量检索代码。

**第 5 天**：用 `evx` 跑 Prompt 评测（阶段五），看看“规划完整性”和“工具选择正确性”的通过率能提到多少。

**第 6-7 天**：串联（阶段六），写测试脚本，用 20 个并发请求验证系统稳定性。