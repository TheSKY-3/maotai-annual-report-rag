<div align="center">

# 茅台财报 RAG 问答系统

**把贵州茅台 2022–2025 年 4 份年报（556 页）变成一个"能提问的资料库"**

自然语言提问 · 答案必附原文页码出处 · 查不到就明说
全流程可本地运行，检索与嵌入 CPU 即可

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Runs](https://img.shields.io/badge/Runs-100%25_Local-success)
![Embedding](https://img.shields.io/badge/Embedding-BGE--small--zh--v1.5-orange)
![LLM](https://img.shields.io/badge/LLM-Qwen3--4B_%7C_DeepSeek-orange)
![UI](https://img.shields.io/badge/UI-Streamlit-FF4B4B?logo=streamlit&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-compose_up_--build-2496ED?logo=docker&logoColor=white)
![API](https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi&logoColor=white)
![eval-gate](https://github.com/TheSKY-3/maotai-annual-report-rag/actions/workflows/eval-gate.yml/badge.svg)

<img src="docs/screenshots/chat_citations.png" width="720" alt="问答界面"/>

*问一句，答一句，出处跟着答案走*

</div>

---

## 目录

- [这是什么](#这是什么)
- [功能一览](#功能一览)
- [系统架构](#系统架构)
- [快速开始：Docker / 源码 / API](#快速开始)
- [效果如何：可复现评测](#效果如何可复现评测)
- [仓库结构](#仓库结构)
- [技术报告](#技术报告)
- [边界与免责声明](#边界与免责声明)

---

## 这是什么

给投资者、研究者、也给自己用：**上市公司年报动辄五六百页，想找一个数
要翻半天目录**。本项目把 4 份茅台年报整体读进一个可检索的资料库，你用
一句话提问，它负责翻页找数，并把**原文页码**放在答案旁边让你随时核对。

三个设计底线：

1. **每个答案必须带出处**——大模型直接答财报容易"编"，这里强制它先查
   资料再回答，答案旁挂出所引用的页码和原文片段；
2. **查不到就明说**——年报里根本没有的信息，系统明确拒答（评测中
   无答案题拒答正确率 100%，一次都没有编造）；
3. **全部可以本地跑**——嵌入、检索、生成三环节都能离线运行，不依赖
   任何云服务（接云端大模型是可选项）。

## 功能一览

### 💬 对话问答，出处跟着答案走

直接用自然语言提问；每条答案下方自动挂出**出处档案卡**（年份、页码），
点开即可看到所引用的原文片段，十秒内完成核对。

<div align="center">
<img src="docs/screenshots/source_card.png" width="720" alt="出处档案卡"/>
</div>

回答之后系统还会推荐 3 个**接着问**的问题（追问），点击即问，不用想
下一句怎么措辞。另外，问题里直接写"P6"或"第6页"会触发**页码直达**：
跳过语义检索，取该页原文回答。

### 🧠 追问有记忆

问过"2024年末国内经销商有多少家？"之后，直接追问**"那净利润呢？"**——
系统自动把追问补全成"2024年净利润是多少？"，继承上一问的年份与主体，
不用每次都问完整句子：

<div align="center">
<img src="docs/screenshots/chat_memory.png" width="720" alt="多轮追问记忆"/>
</div>

### 🤖 复杂题自动进 Agent 模式（Function Calling）

跨年对比、多指标组合这类复杂题，一次检索装不下所有证据。系统按问题特征
自动路由到 **Function Calling 工具循环**：大模型自己决定调哪个工具——
`search_reports` 按年检索、`page_direct` 整页直读、`compare_years` 跨年
分别取证——分多轮取齐证据后再作答；简单题仍走原有直通管线，不白花
延迟与费用。答案下方的**工具调用轨迹**可展开，Agent 每一步调了什么
工具、取回多少片段完全透明：

<div align="center">
<img src="docs/screenshots/agent_trace.png" width="720" alt="Agent 工具调用轨迹"/>
</div>

### ⚡ 边生成边显示（流式输出）

答案像打字机一样逐字蹦出，不用盯着转圈等全文：回答时上方实时显示当前阶段
（正在检索 → 开始生成 → 数字接地校验），答案流出过程直接可见，完成后出处
卡片自动挂出。下图是**生成中途**的真实截图——答案刚写到第 2 条的一半：

<div align="center">
<img src="docs/screenshots/streaming.png" width="720" alt="流式输出进行中"/>
</div>

底层是 SSE 流式协议：FastAPI 暴露 `POST /ask/stream` 端点，事件流含
`status`（阶段进展）/`delta`（答案增量）/`replace`（整体替换）/`done`
（最终元信息）四类——拒答重试或数字接地修正会替换已流出内容，流式显示
与答案质量校验互不牺牲。

### 🔑 自带 Key 即用，也能完全离线

侧边栏一键切换生成模型：粘贴你自己的 **DeepSeek API Key**（只保存在
当前浏览器会话，不上传任何第三方）即刻云端问答，无需任何本地模型；
选**本地 Ollama**（Qwen3-4B）则完全离线、零成本：

<div align="center">
<img src="docs/screenshots/settings_key.png" width="300" alt="侧边栏模型设置"/>
</div>

### 🔀 双年报对比模式

同一问题在两份年报里并排回答，看变化不用来回切换文件：

<div align="center">
<img src="docs/screenshots/compare_mode.png" width="720" alt="对比模式"/>
</div>

### 🔒 查不到就明说

问年报里没有的事，系统不编造、不硬答，明确告知查不到并列出检索依据：

<div align="center">
<img src="docs/screenshots/refusal.png" width="720" alt="拒答示意"/>
</div>

### ⚖️ 财务口径硬约束（不靠提示词求模型自觉）

- **默认合并报表**：问题没明确问"母公司"时，直接在证据层剔除母公司报表块，
  模型没得选。实测软提示压不住模型对"母公司表更权威"的偏好，硬过滤才确定性
  修好口径混淆题。
- **合计行保真**：切块去页脚只删"页码/总页数"那一行，保留表格空单元格"/"与
  合计行；问"合计/总额/共计"不再因合计行被误删而假拒答。
- **折旧/摊销核对资产类别**：固定资产、使用权资产、投资性房地产等各有
  "累计折旧/本期增加/计提"，强制核对表头资产类别，防止跨类别取数。

## 系统架构

*先查资料，再回答：离线建库做一次，在线问答每次提问。下图为当前架构（GitHub
直接渲染 mermaid，只画稳定结构；带实时数字的交互看板在本地 `docs/dashboard.html`，
可再生、不入库）。*

```mermaid
flowchart TB
  subgraph OFF["离线建库（一次性 · 产物落盘）"]
    A["年报 PDF（4 份）"] --> B["切块"]
    B --> C["语境句生成（E5）"]
    C --> D["向量索引 + BM25"]
  end
  subgraph ON["在线问答（每次提问）"]
    Q["用户提问"] --> R["零成本路由"]
    R --> H["混合检索（向量+BM25）+ 年份过滤"]
    H --> S["口径硬过滤（合并 vs 母公司）"]
    S --> T["结构化答案 + 相邻页 P+1 续接"]
    T --> N["数字接地校验"]
    N --> P["页级引用出处"]
  end
  D --> H
```

> 生产管线**无精排阶段**：E5 语境句已取代精排（见 `src/pipeline.py` 模块说明）。

## 快速开始

### 方式一：Docker 一键起（网页 + API）

```bash
docker compose up --build
```

- 网页界面：http://localhost:8501 （首次启动自动下载年报并建索引，约 3-5 分钟）
- API 文档：http://localhost:8000/docs
- （可选）云端生成：在项目根目录建 `.env` 写入 `DEEPSEEK_API_KEY=sk-…`，
  或启动后直接在网页**侧边栏粘贴自己的 Key**。不配 Key 也可配合
  本地 Ollama 使用（需自行安装并映射模型目录进容器）。

### 方式二：源码运行

```bash
# 0. 环境：Windows / Linux / Python 3.12（检索与嵌入 CPU 即可）
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt

# 注：语境增强索引已随仓库分发（data/chunks/contextual.jsonl +
#     data/index/contextual.npz，共 5.5MB）——克隆后即可直接问答与复现
#     评测；想从头重建再执行下面第 1-2 步（需下载约 30MB 年报 PDF）

# 1. 下载 4 份年报（巨潮资讯网公开文件，约 30MB）
python scripts/download_data.py

# 2. 提取文本 + 建索引
python scripts/extract_text.py
python scripts/build_index.py

# 3. 启动问答界面（侧边栏粘贴 DeepSeek Key 即云端问答；
#    或选本地 Ollama qwen3:4b 完全离线）
python -m streamlit run src/app.py

# 4. 语境增强索引（可选，推荐：hit@5 76% 修正口径，需 DeepSeek Key，约 3 元）
python scripts/build_contextual_chunks.py

# 5. 复现评测
python scripts/ci_retrieval_gate.py                    # 检索层·生产管线（应显示 hit@5 86%，低于 84% 报错）
python scripts/evaluate_retrieval.py                   # 检索层·E1 基线对照（历史实验口径 30%）
python scripts/evaluate_answers.py --name final_local  # 答案层（本地模型）
```

### 也可以做成 API 服务

```bash
uvicorn src.api:app --port 8000     # Swagger 交互文档: /docs
```

```bash
# 单轮问答
curl -X POST localhost:8000/ask -H "Content-Type: application/json" \
  -d '{"question": "2024年末国内经销商有多少家？"}'

# 多轮追问：回传 history，"那净利润呢？"自动补全为"2024年净利润是多少？"
curl -X POST localhost:8000/ask -H "Content-Type: application/json" \
  -d '{"question": "那净利润呢？", "history": [{"role": "user", "content": "2024年末国内经销商有多少家？"}, {"role": "assistant", "content": "2024年末国内经销商有2,143家。"}]}'
```

返回 `answer`（含出处标注）、`sources`（页码级出处数组）、`refused`
（是否拒答）与 `standalone_question`（多轮改写结果）；请求带 `"api_key"`
字段即可用你自己的 DeepSeek Key。

```bash
# 流式问答（SSE）：status/delta/replace/done 事件边生成边推送，参数与 /ask 相同
curl -N -X POST localhost:8000/ask/stream -H "Content-Type: application/json" \
  -d '{"question": "2024年末国内经销商有多少家？"}'
```

#### 健康检查与可观测性

`GET /ready` 是给容器/负载均衡用的就绪探针（判断服务能不能接流量）。
它不加载嵌入模型，只检查索引文件、年份覆盖、坏行与模型配置，返回：

```json
{
  "status": "ready",
  "loaded": false,
  "index": {"kind": "contextual", "chunks": 1778, "years": [2022, 2023, 2024, 2025],
            "missing_years": [], "chunks_file_info": {"exists": true}},
  "embedding": {"model": "BAAI/bge-small-zh-v1.5", "local_files": true},
  "model": "deepseek-flash", "backend": "cloud", "api_key_configured": true,
  "reasons": []
}
```

- `ready`：索引完整；`degraded`：能回答但退到基线索引或年份不全；
  `not_ready`：缺文件/零块，返回 HTTP 503，`reasons` 里写清缺什么。
- `/health` 是轻量存活检查，保留 `index`/`index_status` 摘要。
- `/ask` 响应新增 `request_id`、`usage`（prompt/completion tokens 与调用次数）、
  `cost_cny`（按 DeepSeek 高峰/非高峰单价估算）、`latency_ms`；流式 `done`
  事件的 `meta` 含同样字段，响应头带 `X-Request-ID`。
- 每次问答只输出一条 JSON 日志到 stderr（`event=ask`），含 request_id、
  耗时、token、成本、路由、拒答/降级与出处数量，可直接接 Loki/CloudWatch：

```json
{"event":"ask","status":200,"request_id":"…","latency_ms":6710,
 "prompt_tokens":1868,"completion_tokens":66,"calls":1,
 "cost_cny":0.002271,"cost_peak":false,"route":"direct",
 "structured":true,"refused":false,"sources":5,"citations":1}
```

成本是估算值（不是账单）：按 DeepSeek 官方单价与当前时段计算，便于在
压测/评测时设预算上限；真实账单以服务商为准。

### 也可以做成 MCP 工具，接进 Claude Desktop / Cursor

本项目还是一个 **MCP（Model Context Protocol）server**：任何 MCP 客户端
都能把"茅台年报问答"当成一个标准工具直接调用，配置即插即用：

```json
{
  "mcpServers": {
    "maotai-annual-report": {
      "command": "<项目路径>/.venv/Scripts/python.exe",
      "args": ["<项目路径>/src/mcp_server.py"]
    }
  }
}
```

本地一行即可实测（起 stdio 子进程 → 握手 → 列工具 → 真实调用一次）：

```bash
.venv/Scripts/python.exe scripts/test_mcp_client.py "2025年比2022年净利润增长了多少？"
```

真实调用记录见 `results/mcp_test_answer.txt`：跨年对比题返回带两个年份
页码出处的计算结果。server 启动时预热检索模型（数秒，之后进程内复用），
并从项目 `.env` 兜底读取 DeepSeek Key（MCP 客户端拉起子进程时默认过滤
密钥类环境变量，值不打印、不入日志）。

## 效果如何：可复现评测

**先给可外推的诚实数字（150 题冻结留出集，从未参与调参）**：检索出处页命中
hit@5 **77.3%**、答案层修正口径 **91.9%**、无答案题拒答 **100%**。下文先讲在
50 题调参集上如何一步步改进（每步有对照实验），最后用留出集验收并披露调参溢价；
逐题归因见[留出集验证](#留出集验证2026-11-11)。

自建 50 题评测集（30 事实题 / 10 跨页题 / 5 跨年对比 / 5 无答案题），
检索是否命中出处页由程序判定，全部数字可复现（脚本在 `scripts/`，
原始结果在 `results/`）。每一步改进都有对照实验；**每次推送 GitHub
都会自动免费复跑检索层评测**（`eval-gate` 工作流，hit@5 低于 84% 直接
失败亮红灯），防止改动悄悄打漏水位：

<div align="center">
<a href="https://github.com/TheSKY-3/maotai-annual-report-rag/actions/runs/42233681733"><img src="docs/screenshots/ci_gate.png" width="720" alt="eval-gate 的 retrieval job 在 GitHub Actions 上通过"/></a>
</div>

### 检索层：出处页命中率 hit@5 从 30% 提升到 86%（旧口径；修正口径 34%→86%）

| 实验 | 改动 | hit@5 | 增益 |
|---|---|---|---|
| 基线 | 500字窗口切块 + 纯向量检索 | 30% | — |
| E1 | 更小块/结构化切块 | 22~26% | ❌ 更细反而更差 |
| E2 | 混合检索（BM25+向量 RRF）+ 年份识别过滤 | 56% | 年份正则 +16pp，性价比最高 |
| E3 | bge-reranker 精排 | 62% | +6pp，代价约 2 秒/次 |
| E5 | 语境增强索引：大模型给每块写"说明书" | **66%** | +4pp，取代精排，建库约 3 元 |
| E7 | 表格感知解析：pdfplumber 键值句线性化 | 58% | ❌ 不敌语境句（详见报告） |
| B1 | min-max 融合（CAND_DEPTH=50, α=0.5）+ 年份过滤 | **86%** | +10pp（对 E5 修正口径 76%） |

> **口径说明（2026-11-06）**：金标出处里“2024年报 P9、P16”这类写法原先只解析出第一页，
> 修正后同一批实验的 hit@5 整体上移（基线 30%→**34%**，E5 66%→**76%**，跨页题 40%→90%）。
> 上表保留旧口径以对应历史报告；修正后的全量数值、逐题对账与复现脚本见
> [review/phase_a_metrics.md](review/phase_a_metrics.md)。CI 阈值已同步调到 84%。
> B1 的 α=0.5 是在同一套 50 题 gold 上扫出来的，86% 不是留出集数字。
> **150 题冻结留出集复测：检索 hit@5 77.3%（有答案题 84.4%），86% 含约 9pp
> 调参溢价**；答案层与逐题归因见下文[留出集验证](#留出集验证2026-11-11)与
> [review/holdout_outcome.md](review/holdout_outcome.md)。B2/B3/B4 见
> [review/phase_b_outcome.md](review/phase_b_outcome.md)。

<div align="center">
<img src="report/figures/fig_experiments.png" width="720" alt="检索策略对比"/>
</div>

### B 组：出处、指标工具与可观测性（2026-11-06）

出处逐页归属、21 指标×4 年 curated 字典、重复评测置信区间、/ready 与成本日志等
工程加固；完整数字、逐题对账与残余风险见
[review/phase_b_outcome.md](review/phase_b_outcome.md)。

### 答案层：拒答自动重试 + 数字接地校验

| 配置 | 完整答案率 | 误拒答率 | 无答案题拒答正确率 |
|---|---|---|---|
| 本地 Qwen3-4B | 33.3% → **37.8%** | 22.2% → **8.9%** | 100% |
| 云端 DeepSeek-Flash | 35.6% → **42.2%** | 35.6% → **13.3%** | 100% |

<div align="center">
<img src="report/figures/fig_e6_retry.png" width="720" alt="E6 拒答重试对比"/>
</div>

两个值得一看的发现：**误拒答**（明明查到了却说"无法回答"）是大模型在
财报问答里的头号失分，一次"扩充检索再问一遍"的重试就能砍掉三分之二；
而对答错题目的归因显示，**六成错题的答案其实已经在检索资料里**——瓶颈
在模型抄写而非检索，数字接地校验因此被用来定位可疑数字。

### 答案层 2.0：复杂题路由到 Agent 工具循环

一次检索的 top-5 装不下"两个年份""两个指标"的全部证据——这正是跨年题
（40%）与跨页多指标题（20%）失分的根因。把这类题路由到 Function Calling
工具循环、分轮取齐证据后再答，同一套 50 题复测（云端 DeepSeek-Flash）：

| 题型 | 直通管线 | Agentic 路由 | 增益 |
|---|---|---|---|
| 跨年对比（5 题） | 40% | **80%** | +40pp |
| 跨页多指标（10 题） | 20% | **50%** | +30pp |
| 事实题（30 题） | 50% | 56.7% | +6.7pp |
| **整体完整答案率（45 有答案题）** | 42.2% | **57.8%** | **+15.6pp** |
| 无答案题拒答正确率 | 100% | 100% | 持平 |

直通题行为一行未改（`answer_question` 向后兼容），检索层 hit@5 66% 零回退；
路由是零成本正则（≥2 个年份 / 对比词 / 多问号 / "分别是"句式），50 题里
23 题进 agent、27 题保持直通。结果文件：`results/agentic_cloud_answers.json`。

**这个提升是统计显著的，不是运气**：45 题配对样本中 7 次不一致全部是
新管线获胜（0 次反例），McNemar 精确检验 **p=0.016**；提升量 +15.6pp 的
bootstrap 95% 置信区间 **[+6.7pp, +26.7pp]**，不含 0。复现命令：

```bash
python scripts/stat_significance.py \
  --baseline results/final_e8_cloud_answers.json \
  --treatment results/agentic_cloud_answers.json
```

### 生成质量：忠实度评测（LLM-as-judge）

数字比对能判"标准答案里的数有没有出现"，判不了"答案里的每个论断是否
真有原文依据"。参考 RAGAS 的 faithfulness 口径，用 DeepSeek-Flash 当
判卷员，对照**作答时实际看到的检索原文**给每份答案打忠实度与相关性分
（1-5 分）并摘录无依据论点（脚本 `scripts/evaluate_faithfulness.py`）：

- 41 份非拒答答案：**忠实度均分 5.0 / 相关性均分 5.0，无依据论点 0 条**
- 判卷员经过对抗校验：喂编造数字的答案判 3 分并精确摘录该论点，喂
  只答一半的答案相关性判 3 分——不是盖橡皮章
- 全 5.0 与 E8 的归因互相印证：数字接地校验在源头修掉了"抄错数"，
  剩余失分是检索未送达与评测匹配口径，而非编造

结果文件：`results/agentic_cloud_faithfulness.json`。

### 口径与表格修复（2026-11-11，轴1/轴2）

- **轴1 默认合并口径**：证据层硬剔母公司报表块，确定性修好口径混淆题（id169），
  拒答 100% 与跨页/跨年零回退。
- **轴2 合计行保真**：修切块误删表格行病根 + 重建索引，合计 3 题（id175/191/219）
  由假拒答转正常作答。
- **折旧资产类别核对**：修 id190 误取使用权资产折旧，单题稳定 2/2 答对。

逐题明细与复跑命令见 [review/holdout_outcome.md](review/holdout_outcome.md)。

### 坐标级引用（2026-11-12，轴4）

- 引用从「页码」升级到「页面坐标」：答案里的每个指标数字定位到年报页面上的框，
  渲染整行横带高亮图（行首标签与数字同框），界面出处面板直接展示，把「信我」
  变成「你自己看」。
- 同页多处出现时按被引片段的行首标签消歧；无信号整组高亮并标记歧义，不猜。
- 130 条非拒答答案的 369 个指标数字：**90.2% 定位成功**（333/369）；22 处歧义整组
  高亮；36 处不猜不框（答案单位与页面声明无法互证、或数字不在出处页）。
- 纯附加层：不改检索与生成，留出 hit@5 77.33% 与 CI 门禁 86% 不变。

复现：`scripts/make_citation_crops.py`（零 API）；逐数字明细
`results/citation_crops.json`。

### 工程闭环（2026-11-12，阶段1/2）

- **阶段1 本地 Linux 部署**：WSL2 Ubuntu-24.04 原生 venv 一键起服务
  （`scripts/start_local_linux.sh`，与 docker/start.sh 同构），/health、/ready、
  /ask、网页四项真跑验收；不上公网。留痕见
  [docs/deploy_local_linux.md](docs/deploy_local_linux.md)。
- **阶段2 前后端分离前端**：`web/` 为 React + Vite + TypeScript 手搓界面，
  消费 FastAPI 的 SSE 流式问答与 `/cite_crop` 坐标高亮接口；出处卡片展开即见
  整行红框图。`npm run build` 后 FastAPI 单端口交付（根路径服务前端、API 优先）。
  前端 14 条 vitest、后端新增 6 条 pytest，全绿。

### 留出集验证（2026-11-11）

86% 是在同一套 50 题上扫参得到的，会不会"考前知道考题"？为此造了
**150 题从未参与调参的冻结留出集**（90 事实 / 30 跨页 / 15 跨年 / 15 无答案）：
调参集用过的 32 个出处页全部排除，答案数字按原文 verbatim，程序校验
（数字须在所标页原文出现 + 查重 + 页级隔离）一次通过，SHA256 冻结
（`data/gold/holdout_manifest.json`），此后不在其上调整任何参数。

**检索层**（纯本地复跑生产管线，`scripts/eval_holdout.py`）：

| 题型 | 调参 50 题 | 留出 150 题 |
|---|---|---|
| 整体 hit@5 | 86.0% | **77.3%** |
| 事实题 | 90.0% | 83.3% |
| 跨页题 | 90.0% | 90.0% |
| 跨年题 | 80.0% | 80.0% |

86% 含约 9pp 调参溢价，可外推水平约 77%（有答案题 84.4%）；跨页/跨年
零回退，说明结构化检索改进是真实的。

> **口径桥接**：上文答案层各表（42.2%/57.8% 等）为**调参 50 题·旧口径**（把出处
> 引文里的数字也当必答数字）；本节 91.9% 为**留出 150 题·修正口径**（先剥出处再比）。
> 集合与口径都不同，差值不能直接归因于某一项改进；同口径对照见"调参集同口径 66.7%"。

**答案层**（150 题 DeepSeek-Flash 全跑通，0 错误）：

- **无答案题拒答正确率 100%（15/15）**，3 倍样本复现调参集 100%，零编造。
- 完整答案率：原始打分 67.4%，修正打分口径后 **91.9%**（124/135，重建索引 +
  折旧修复后最新复跑 `holdout_v3`；调参集同口径 66.7%）。修正的是 `gold_numbers`
  把出处引文里的表格数字/页码误当必答数字的偏差——对两套题同样重打，双口径并列，
  未单方面美化。
- 修正口径下仍错的 11 题：6 题检索未送达、2 题正文 vs 附注真歧义（决定不强修）、
  3 题生成端/跑间波动。**"合计"行假拒答（3 题）与折旧指标混淆（id190）已修复**；
  瓶颈仍在检索送达，不是生成端编造。逐题归因见
  [review/holdout_outcome.md](review/holdout_outcome.md)。

逐题明细 `results/holdout_retrieval.json`、`results/holdout_v2_answers.json`（重建后）、
`results/holdout_v3_answers.json`（折旧修复后）、`results/holdout_answer_rescore.json`；
完整报告与复现命令见
[review/holdout_outcome.md](review/holdout_outcome.md)。

## 仓库结构

```
src/          核心代码：切块/索引/混合检索/语境增强/问答（直通+Agent 路由）/
              Streamlit 界面/FastAPI 服务/MCP server
scripts/      下载、建索引、评测（答案层/忠实度）、画图等一次性脚本
docker/       容器入口脚本（Dockerfile 与 docker-compose.yml 在根目录）
data/gold/    50 题黄金评测集 + 150 题冻结留出集（问题+标准答案+出处页码）
results/      全部评测结果 JSON（每个数字都出自这里）
report/       通俗中文技术报告（LaTeX 源码 + 图表）
docs/         界面截图
```

## 技术报告

完整的项目报告（含实验设计、badcase 逐例分析、经验教训）见
`report/report.tex`，用 [tectonic](https://tectonic-typesetting.github.io/) 编译：

```bash
tectonic -o report/_build report/report.tex
```

## 边界与免责声明

<details>
<summary>点击展开</summary>

本项目数据来自[巨潮资讯网](http://www.cninfo.com.cn)依法公开披露的上市公司
年度报告，**仅用于学习和技术研究，不构成任何投资建议**。本项目与贵州茅台酒
股份有限公司无任何关联；相关商标归其权利人所有。请勿将本仓库中的语料处理
方式用于未经授权的商业场景。

</details>

## License

[MIT](LICENSE)
