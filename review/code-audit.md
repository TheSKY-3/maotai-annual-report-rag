# 代码审计：贵州茅台年报 RAG 问答系统

- 审计对象：D:/eng_project/rag项目，HEAD 3f84645，94 个跟踪文件 / 约 9.96 MB
- 审计方式：**只读**。全程未修改任何源码与既有文件；未调用任何付费接口（没有运行 evaluate_answers.py / evaluate_faithfulness.py / build_contextual_chunks.py）；未打印 .env 或任何 Key；未执行 git 写操作。
- 运行环境：Windows + PowerShell，.venv Python 3.12（torch 2.15.0 / numpy 2.5.4 / transformers 4.51.3 / openai 1.109.1 / fastapi 0.162.0 / starlette 1.10.0）
- 证据口径：每条结论标注 **【已验证】**（读过代码并/或在本机跑过探针，附输出）或 **【推测】**（静态推断，未实测，附录 B 汇总）。所有探针都是本地 CPU 计算，**不联网、不调用大模型**。

被审计的问答链路（后文按此顺序引用）：

    Streamlit(src/app.py) ─┐
    FastAPI(src/api.py) ───┼─→ ask.answer_auto / answer_auto_stream ─→ hybrid.HybridRetriever.search ─→ store.VectorStore
    MCP(src/mcp_server.py) ┘        │                                        └→ hybrid.get_page（页码直达）
                                    └→ OpenAI 兼容端点（Ollama qwen3:4b / DeepSeek deepseek-flash）

---

## 0. 结论摘要

| 编号 | 严重度 | 结论（一句话） | 位置 |
|---|---|---|---|
| C1 | **CRITICAL** | 答案里的 [来源: 2024年报 P888] 从来没有任何代码校验；接地校验前反而先把出处整段删掉，sources 又来自检索片段而非答案 —— 项目第一卖点「答案必附原文页码」事实上无法核验 | ask.py:164, 171-197, 270-290 |
| C2 | **CRITICAL** | 同一条问题有 4 份生成实现（直通/流式、agent/流式 agent），空正文重试、拒答重试的采纳条件、接地校验的证据池、工具异常文案各不相同 —— 界面上看到的答案与 evaluate_answers.py 打分的答案**不是同一条路径** | ask.py:220-253 vs 535-661；459-509 vs 574-630 |
| M1 | MAJOR | 数字接地校验有 4 个洞：一位数被跳过（5% vs 6% 不报）、纯子串匹配（12 是 1234 的前缀）、上下文里混入 LLM 生成的语境句、「（计算得出）」的数字永不接地 | ask.py:163-197, 58-74；build_contextual_chunks.py:176 |
| M2 | MAJOR | 接地修正只做一轮、不做复验，修正结果非空就直接替换；修正提示本身允许「计算得出」，与主提示「必须与片段完全一致」冲突 | ask.py:200-217, 58-74 vs 27-40 |
| M3 | MAJOR | REFUSE_RE 会把「先给数字、后补一句未披露」的正常答案判成拒答，进而触发 10 块重试并**无条件覆盖**首轮答案 | ask.py:78-81, 240-252 |
| M4 | MAJOR | Agent 路径：接地校验用整个池、对外只给前 12 块；page_direct 缺年份时静默返回空；compare_years 被 locked_year 折叠成一年；get_page_reader() 绕过注入的 page_fn | ask.py:509, 623-624, 385-400, 525, 577 |
| M5 | MAJOR | 年份识别只认「20xx 年」：2022-2024年→只留 2024、2022到2025年间→只留 2025、2024财年→不过滤；页码直达在无年份时按年份升序只答 2022 | hybrid.py:19-32, 64-91 |
| M6 | MAJOR | 拼进提示的上下文没有长度上限（4 年对比约 9,060 字），本地 num_ctx 8192 可能装不下 | ask.py:139-143；ollama.Modelfile:4 |
| M7 | MAJOR | 检索器懒加载无锁，且 _search 在 _retriever 之后才赋值（存在返回 None 的窗口）；API 侧 OpenAI 客户端**不设超时**（MCP 侧却设了 120s） | pipeline.py:22-44, 47-50；api.py:103-116 vs mcp_server.py:69-85 |
| M8 | MAJOR | /ask 公网可达时（compose 默认映射全部网卡）CORS 通配 + 无鉴权 + 服务端 Key 可被任意网页驱动；流式生成器断连不 close、无 finally | api.py:37-42, 130-144, 162-178；ask.py:555-569 |
| M9 | MAJOR | 容器入口以未入库的 data/index/baseline.npz 为门禁，而随仓库分发的是 contextual.npz → 全新 clone 的一条命令启动会去巨潮下载 PDF 并重建（硬编码 URL） | docker/start.sh:6-12；download_data.py:15-20 |
| M10 | MAJOR | 仓库里**没有任何自动化测试**；CI 只守检索 hit@5，答案解析、路由、引用、流式、API 契约全无覆盖；CI 的 torch 未锁版本；finish_reason 全仓 0 处检查 | scripts/ci_retrieval_gate.py:32；eval-gate.yml:31, 65 |
| M11 | MAJOR | VectorStore.search 返回的 score 是**另一个片段**的相似度（索引写错），实测最大误差 0.388、排序值不单调、自查询 top1 报 0.48 而非 1.0 | store.py:57 |
| m1–m18 | MINOR | 展示层、配置/依赖、可观测性、实验资产共 18 条，见第 4 节 | — |

**先说优点**（不影响下面挑刺）：按页切块保证出处精确到页、RRF 融合、拒答重试、页码直达工具、检索闸门进 CI，这些在同类「demo 级 RAG」里属于做得比较认真的；问题集中在**校验缺失**与**多份实现之间漂移**，而不是架构选型。

---

## 1. 方法与证据口径

- 静态：逐行读 src/*.py、scripts/*.py、Dockerfile、docker-compose.yml、.github/workflows/eval-gate.yml、requirements.txt、.gitignore / .dockerignore、ollama.Modelfile、.streamlit/config.toml。
- 动态（本地、免费）：用 .venv 直接 import 生产模块，跑 4 组探针（附录 A 有原始输出）：
    1. VectorStore.search 报分 vs 真余弦；
    2. ungrounded_numbers / REFUSE_RE / _CITE_RE 边界；
    3. detect_years / detect_page / needs_agent 覆盖盲区；
    4. get_search()（真实索引 + 真实 BGE 模型）对 5 条典型问题的 top5 命中，验证年份过滤与页码直达。
- 数据：data/chunks/contextual.jsonl（1761 块，平均 453 字）、data/index/contextual.npz、data/gold/questions.jsonl（50 题：fact 30 / cross_page 10 / cross_year 5 / no_answer 5）、results/ci_retrieval_retrieval.json。
- 未做：没有启动 Ollama / DeepSeek，没有跑 Docker，没有做真实 HTTP 压测。凡涉及真实模型行为的推论都标【推测】。

---

## 2. CRITICAL

### C1 答案里的页码引用从未被校验，而接地校验前恰好把出处删掉了

**证据**

- 出处模板要求模型自己写 [来源: 2024年报 P8]（ask.py:27-40；build_context 只提供「片段N | 来源: 2024年报 P8」这样的上下文，ask.py:139-143）。
- 接地校验的第一步就是删掉它们：_CITE_RE = re.compile(r"\[来源[^\]]*\]")（ask.py:164）→ _flat_digits(_CITE_RE.sub("", answer))（ask.py:177）。**【已验证】** 探针输出：_CITE_RE.sub('', '[来源: 2024年报 P888] 营业收入 1,234 亿元') 的结果是 ' 营业收入 1,234 亿元'。
- 全仓没有任何代码把答案里的（年份, 页码）解析出来与 hits 比对（grep「来源」只在 ask.py / app.py 出现，且都是模板或美化）。
- 对外暴露的 sources 是**检索片段**而不是**答案引用**：_meta 的 sources 遍历 hits（ask.py:281-286），hits 来自 search_fn，与答案里写了什么无关。

**后果（用户可见）**：模型凭空写 [来源: 2024年报 P888]（2024 年报只有 143 页）会原样出现在界面与 API 回答里；而 sources 列的是真正检索到的 5 个片段，两者可以完全对不上。读者按 P888 翻不到任何内容，却以为系统校验过。这是「答案必附原文页码出处」这一核心承诺的唯一实现缺口，且成本极低就能补。

**最小修法**（不动数据、不重建索引）：

    PAGE_CITE_RE = re.compile(r"(\d{4})\s*年报\s*[Pp]\s*(\d{1,3})")

    def check_citations(text, hits):
        cited = {(int(y), int(p)) for y, p in PAGE_CITE_RE.findall(text)}
        valid = {(h["year"], h["page"]) for h in hits}
        return sorted(cited - valid), sorted(cited & valid)   # (伪造的, 可核验的)

在 _meta 里加 citations_unverified 字段，界面把不可核验的出处标红或直接剥掉；评测里加 cite_precision 指标。**不要**做全文逐句核对（成本高、收益低），只核对页码是否落在检索片段集合内。

### C2 同一条问题有 4 份生成实现，界面上看到的答案与评测/API 的答案不是同一条路径

**证据（逐条对照，全部【已验证】静态）**

| 行为 | 非流式直通 _answer_core | 流式直通 answer_auto_stream | 非流式 agent _agent_loop | 流式 agent（内联副本） |
|---|---|---|---|---|
| 位置 | ask.py:220-253 | ask.py:632-661 | ask.py:459-509 | ask.py:574-630 |
| 首轮空正文重试 | **无**（_complete:146-152 只调一次） | **有**，max_tokens=1800（:639-641） | 共享 _agent_call 的重试（:449-455） | 同左 |
| 拒答重试的采纳条件 | if retry_text: → **无条件替换**（:246-252），哪怕重试仍是拒答也替换 | if retry_text and not REFUSE_RE.search(retry_text)（:650）→ 重试仍是拒答就**保留首轮** | 不适用 | 不适用 |
| 接地校验的证据池 | 首轮用 hits，重试用 more（:246-253） | 同左，字段名 hits_used（:656） | _grounding_fix(..., pool) **整个池**（:509） | 先 capped = pool[:12] 再校验（:623-624） |
| 工具异常时的表现 | — | — | 把「工具执行失败：{err}」当工具结果回给模型（:491-503） | 捕获后**只把 hits 置空**（:606-607），模型看到的是「（未检索到相关片段）」（:617） |

**后果**：scripts/evaluate_answers.py 走 answer_auto / answer_question（非流式），而用户点按钮走 answer_auto_stream。同一问题、同一模型、同一检索结果，两条路径可能给出不同答案：遇到拒答重试时分叉最明显（一边替换、一边保留），空正文时流式会救回来而评测记一次空回答。**所以 README/报告里的 answer 指标不能直接代表用户看到的答案**，而这个偏差方向不可预测。工具异常那条更隐蔽：流式下 agent 静默降级成「没检索到」，非流式下模型会看到错误并可能换工具重试。

**修法**：把差异点抽成两个纯函数——should_replace(retry_text, first_text) 与 grounding_pool(route, pool, hits)——四条路径都调它们；再加一个「假 client」对等测试（第 7 节 test_parity.py），断言同输入下两条路径的最终 answer 与 refused 一致。

---

## 3. MAJOR

### M1 数字接地校验的四个洞（子串 / 一位数 / 语境句 / 计算值）

**证据**（ask.py:163-197，探针输出见附录 A）

1. **一位数直接跳过**：if len(n) < 2: continue（:183）→ 「毛利率 5%」对「毛利率 6%」返回 []（漏报）；「净利润 3 亿元」对「3.5 亿元」返回 []。财报里个位数（5%、3 亿、6 家）很常见，这条规则等于放弃校验所有一位数。
2. **纯子串匹配**：any(x in hay for x in alts)（:195）→ 「营业收入 12 亿元」对上下文「1,234 亿元」返回 []（"12" 是 "1234" 的前缀）；「营业收入 120 亿元」对「1,200,000,000 元」返回 []（"1200000" 是 "1200000000" 的前缀）。**编造的数量级只要与原值前缀相同就查不出来。**
3. **生成语境句进了比对池**：生产语料 text 的格式是「语境句 + 换行 + 原文」（build_contextual_chunks.py:176），而 build_context(hits)（ask.py:139-143）与 ungrounded_numbers 的 hay 都用这个 text。**【已验证】** 读 data/chunks/contextual.jsonl：第一块 text 以「该片段为年报封面，标示公司代码」开头，后接原文；1761 块全部带这种前缀。→ 语境句是模型写的，里面出现的数字（如「2022 年」「第 8 页」）都会被当作「片段里有这个数」，等于用幻觉证明幻觉。
4. **「（计算得出）」永远不接地**：GROUNDING_PROMPT_TEMPLATE 第 2 条允许模型标注「（计算得出）」（ask.py:58-74），但 ungrounded_numbers 只做数字比对、完全不看这个标记；反过来，被标「计算得出」的**正确**增长率的数字往往不在片段里 → 会被当作幻觉丢进 _grounding_fix，而修正提示里又允许保留计算值 → 来回拉扯（见 M2）。

**修法**：① 去掉 len(n) < 2，或用「数字前后不能紧邻数字/小数点」的边界匹配、按数值集合比较而不是子串；② 加载语料时把 context 字段与原文分开（contextual.jsonl 已经单独存了 context，不需要重建索引），接地 hay 只用原文；③ 把「（计算得出）」变成结构化标记并在校验里显式跳过，同时要求标注算式的两个因子都在片段里（可先只做前者）。

### M2 接地修正只做一轮、不复验，改完不检查

**证据**：_grounding_fix（ask.py:200-217）把所有「未接地」的数字塞进一个提示，让模型重写一次；fixed = content or ""，return fixed if fixed else text —— **只要非空就采纳，不再跑 ungrounded_numbers**。且只剥一个前导「修正后的回答[:：]」，模型在正文里夹带解释、或者改写后引入了**新的**幻觉数字，都不会被发现。

**后果**：修正这一步本身是「用大模型修大模型」，没有校验就没有收敛保证；一旦模型把正确的数字改成上下文里另一个数字（子串匹配下很容易），错误反而被固化——而且它以 replace 事件整段替换已流出的内容（app.py:502-504），用户会看到答案突然变脸。

**修法**：修完再跑一次 ungrounded_numbers；仍有未接地的，退回原文并把差异写进 meta（例如 grounding: {before: [...], after: [...]}）；最多一轮，不要递归。

### M3 拒答判定误判，且重试会无条件覆盖首轮答案

**证据**

- REFUSE_RE（ask.py:78-81）含「未披露|没有披露|未提供|无法提供|无法确定|无法给出|未找到」，全部按「子串出现即拒答」判定。**【已验证】** 探针：
    - 「2024年报披露了分红方案，但未披露2025年一季度数据，因此无法给出该项。」→ True（误判）
    - 「该指标在2022年报中未提供，2023年报披露为 1234 万元。」→ True（误判，答案其实给了数）
    - 正常答案「2024年营业总收入为 1,741.44 亿元，同比增长 15.66%。」→ False（正确）
- 触发后：_answer_core:240-252 用 RETRY_TOP_K=10 再检索再问一次，if retry_text: 就 return _meta(..., retry_text, ...) —— **首轮答案被整段丢掉，不比较哪个更好**。首轮很可能已经答对了主要指标，只是顺带说了一句「未披露」。
- _meta.refused = bool(REFUSE_RE.search(text))（ask.py:280）→ 正常答案在 API 里被标成 refused: true。
- scripts/evaluate_answers.py:24 直接 import 同一个 REFUSE_RE 算拒答率/正确性 → **评测指标与线上判定同源同错**，这种误判不会在评测里暴露，反而会被「优化」。

**修法**：把「拒答」限定为「整段回答都不含任何数字且长度短于 N 字」，或改成「首句/首 30 字内出现拒答词」；重试采纳改成比较两者：retry_text 更短且有更多未接地数字时保留首轮。再加 5 正 5 负的回归用例（第 7 节）。

### M4 Agent 路径：证据截断、工具静默返空、注入被绕过

**证据**

1. **接地池 vs 对外池不一致**：非流式 return _grounding_fix(client, model, final_text, pool), pool[:12], steps（ask.py:509）—— 校验用整个池，_meta.hits 只留 12 条；流式反过来，先 capped = pool[:12] 再 _grounding_fix(..., capped)（:623-624）。→ 答案可以引用池里第 13 条之后的页码，而 sources 里没有它（与 C1 叠加后完全无法核验）；流式则会把「其实在池里、只是被截掉」的数字误判为幻觉。
2. **page_direct 静默返空**：if year is None or not 1 <= page <= 999: return []（ask.py:392-394）。模型调用 page_direct(page=6) 忘了给 year 时，工具返回空列表，模型只能猜或拒答，而 steps 里只记录 n_hits: 0，看不出是参数错。
3. **compare_years 被锁年份折叠**：locked_year（ask.py:524 来自 search_kwargs["year"]）在 _exec_tool 里覆盖一切模型给的年份（:371-411，:399-400）→ 用户点了「2024 年报」却问跨年对比时，compare_years(2022,2024) 只返回 2024，模型没有任何提示它被截断。同样的机制让 page_direct 的 year 参数形同虚设。
4. **注入被绕过**：answer_auto 与流式副本都硬写 page_fn = get_page_reader()（:525、:577），而 search_fn 是参数 —— 于是 agent 路径无法用假检索器单测，实验脚本只要不重新装配全局 src.pipeline 就会读到生产索引；mcp_server.py:106-108 预热、api.py:44-52、app.py:260-263 三处单例也因此绑死。
5. **AGENT_MAX_ROUNDS=4 的收口是对的**：耗尽后用不带工具的 call(messages) 逼最终答案（:507-508），这点【已验证】没问题；但收口调用仍受 max_tokens=900 限制（:448），且全仓不检查 finish_reason（见 M10），被截断的答案与完整答案无法区分。

**修法**：page_direct 缺 year 时返回一条明确的工具错误（例如「需要 year 参数，如 2024」）让模型自我修正；locked_year 下若模型请求别的年份，返回工具错误而不是静默改写；把 page_fn 提为 answer_auto 的可选参数（默认 get_page_reader()），并在 pool[:12] 之前把截断记录进 meta.truncated_evidence。

### M5 年份过滤与页码直达：范围题只留最后一年

**证据**：YEAR_RE 只认「(20xx)\s*年」（hybrid.py:19），detect_years 只做 findall（:24-26）；search 里 if self.use_year_filter and year is None 且 len(known) in (1, 2) 才过滤（:67-70）。

**【已验证】** 探针：

| 输入 | detect_years | 生产 top5 命中（年, 页） |
|---|---|---|
| 2024年茅台营业总收入是多少？ | [2024] | 全部 2024（正确） |
| 2022-2024年的营收分别是多少？ | [2024] | (2024,6) (2024,6) (2024,5) (2024,34) (2024,70) ← 2022/2023 被排除 |
| 2022到2025年间营业总收入的复合增长率是多少？ | [2025] | (2025,61) (2025,12) (2025,63) (2025,10) (2025,141) |
| 2024财年的营收是多少？ | [] | (2024,6) (2025,6) (2024,70) (2023,5) (2023,8) ← 混进 2025/2023 |
| P6 的主要财务数据是什么？ | [] | (2022,6)×4 (2023,6)，direct=True ← 只答 2022 |

- 「2024财年」「24年」识别不到（要求「年」紧跟数字）；「2022-2024年」只匹配到「2024年」。
- 2 年集合（如「2023和2025年」）会启用过滤 → **中间的 2024 被排除**；3 年以上反而不过滤（in (1,2)）。
- 页码直达：if not self.use_year_filter 时跳过直达（:74）→ 关了年份过滤就没有直达；direct.sort(key=lambda c: c["year"])（:87）后 direct[:top_k]（:91）→ 同一页有 10 块（2024 p43、2025 p42 实测最多 10 块）时，前 5 块全来自最早的年份。
- detect_page 误报：「P2P业务收入」→ 2（hybrid.py:20），needs_agent 因此为 True（ask.py:427-439）→ 为一个普通问题白跑一轮 agent。

**缓解事实**：跨年题若被路由到 agent，模型调 compare_years(2022,2024) 时传显式年份，hybrid.search(year=2022) 会覆盖自动识别 → 能救回来。但这依赖模型自己选对工具；直通路径、evaluate_answers.py 默认路径、任何直接调 get_search() 的新脚本都拿不到另两年。「2022-2024」那个问题在 needs_agent 里是 True（「分别是」命中 _MULTI_METRIC_RE），所以生产上还有救；「2022到2025…复合增长率」同样 True（「增长」命中 _COMPARE_RE）。但「财报里只写了 2025 一年，模型据此算出一个增长率」的风险仍在。

**修法**：detect_years 至少覆盖「年度|财年|年报|-|–|~|至|到」并显式建模区间（区间展开成逐年的集合）；len(known) in (1,2) 改成「任意年份集合都过滤，2 年以上时同时保留相邻年」；页码直达不要排序后截断，改成按与问题年份的匹配度排序、或 year 没给时返回各年同页；PAGE_RE 加负向断言（P 后面不能紧跟字母）。

### M6 拼进提示的上下文没有长度上限

**证据**：build_context（ask.py:139-143）逐块拼接，无截断；compare_years 每年最多 5 块、4 年 20 块（ask.py:399-400）；_agent_loop 最多 4 轮，每轮结果都进 messages。实测：1761 块平均 453 字、p95 549 字 → 5 块约 2,265 字、10 块约 4,530 字、20 块约 9,060 字（中文粗算约 1.3 万 token）。本地 ollama.Modelfile:4 是 num_ctx 8192。**【推测】** Ollama 超出 num_ctx 时会从头截断提示（保留尾部），系统提示与问题可能被丢掉，表现是「答非所问」或「忽略格式要求」；未在本机实测。

**修法**：给 build_context 加预算参数（按字符或 token，例如 6,000 字上限，超了按块均分或只保留首块全文 + 其余摘要）；compare_years 每年最多 2 块；num_ctx 提到 16384，并给 _complete 显式设 max_tokens 上限（现在完全没设）。

### M7 检索器懒加载无锁；API 侧客户端没有超时

**证据**

- pipeline._get_retriever（pipeline.py:22-44）：先判 if _retriever is not None 就返回，没有锁；_retriever = HybridRetriever(...)（L35/L41）之后才 _search = _retriever.search（L43）。get_search()（L47-50）只 _get_retriever(); return _search → 恰好落在两者之间的线程拿到 None，后续 search_fn(...) 直接 TypeError → 500。两个线程同时首请求则会加载两份模型（内存翻倍、启动时间翻倍）。
- 同样的模式复制在 api.py:44-52 与 mcp_server.py:57-66（后者在 main() 里预热躲过冷启动，API 没有）。
- api._resolve（api.py:103-116）构造 OpenAI(...) **不带 timeout / max_retries**（openai SDK 默认 600 秒 × 3 次重试）；mcp_server._resolve 却写了 timeout=120（:77-85）。/ask 是同步 def → 跑在 AnyIO 线程池（默认 40 线程）→ 上游挂起 10 分钟就能把线程池占满，之后所有请求（包括 /health）排队。
- **【推测】** jieba.lcut 首次调用会懒加载词典（全局单例、非线程安全），API 首请求并发时可能拿到半初始化结果或抛错；HybridRetriever 本身对 store（只读 numpy）与 bm25（只读）是并发安全的。

**修法**：threading.Lock() + 双检（或 functools.lru_cache）；把 _search 的赋值提到 _retriever 之前；HybridRetriever 构造后调一次 jieba.initialize() 预热；_resolve 统一加 timeout=120, max_retries=1；/ask 改成 async def + anyio.to_thread.run_sync 并给线程池设上限。

### M8 服务暴露面：CORS 通配、无鉴权、流式不关流

**证据**

- api.py:37-42：allow_origins=["*"]、allow_methods=["*"]、allow_headers=["*"]，无 allow_credentials（这点没错，否则浏览器会拒绝）。docker-compose.yml:13-17 把 8000/8501 映射到宿主机所有网卡，并把 DEEPSEEK_API_KEY 注入容器。→ 同一局域网的任何页面都能 fetch('http://<host>:8000/ask')，用**服务端的 Key** 提问并计费；AskRequest.api_key（api.py:67-68）还允许调用方自带 Key 明文透传（不落盘，但会进日志/代理）。
- 没有鉴权、没有限流、没有语义复杂度限制（只有 200 字上限，api.py:60-62）；agent 路径可被构造成 4 轮 × 20 块的最贵请求，成本上限不可控。
- 流式：api.py:162-174 的 sse() 只有 except、**没有 finally**；ask.py:555-569 的 _stream_call 直接 for chunk in resp，不 close、不 try/finally。**【已验证】** 本机 starlette 1.10.0 + uvicorn 0.54（spec_version 2.3）走 responses.py:277-285 的 task-group 分支：客户端断连时 stream_response 被 cancel，iterate_in_threadpool（concurrency.py:51-58）在线程里的 next() 仍在飞 → 生成器是否被 close() 不确定。**【推测】** 上游 openai.Stream 因此可能在断连后继续读到下一次产出才被 GC 关闭，期间继续计费；未实测（需要接真实计费端点，违反本次约束）。
- /health（api.py:124-127）恒返回 {status: ok}，model 字段来自常量 → 索引损坏/模型缺失/上下文索引回退都照样 healthy，而 Dockerfile:30-31 的 HEALTHCHECK 就依赖它。

**修法**：allow_origins 改成可配置白名单（默认只允许 localhost:8501）；加一个 RAG_API_TOKEN 环境变量做 Bearer 校验（默认关闭但文档写清）；给 /ask 加简单令牌桶限流；_stream_call 改成 with client.chat.completions.create(..., stream=True) as resp，sse() 外层 try/finally: gen.close()；/health 真去 ping 一次检索并检查索引文件与模型是否加载成功。

### M9 容器首次启动的门禁检查错了产物

**证据**：docker/start.sh:6 判 [ ! -f data/index/baseline.npz ] → 不存在就跑 download_data.py / extract_text.py / build_index.py（:7-11）。但：

- .gitignore 只放行 data/index/contextual.npz 与 data/chunks/contextual.jsonl，**baseline.npz 从不入库**；data/raw_pdfs/ 与 data/models/ 又被 .dockerignore 排除 → 全新 clone 的镜像里 baseline.npz 必然缺失。
- 于是 README:149 承诺的一条命令 docker compose up --build 实际会：重建 baseline（build_index.py 还会覆盖同目录的 baseline.jsonl）、并把启动时间从秒级拉到 3–5 分钟；在无外网/巨潮改版/限流时直接 set -e 退出，容器反复重启（restart: unless-stopped）。
- 更矛盾的是：生产管线读的是 contextual.npz（pipeline.py:33-37），而 contextual.npz 正是随仓库分发的那一个 → **门禁检查的产物与真正被使用的产物不是同一个**。
- download_data.py:15-20 是硬编码巨潮 finalpage 路径 + 纯 http，无重试、无校验（只查 %PDF 头）。脚本注释自己也承认「接口失效请手动重新获取」。

**修法**：门禁改成「两者都没有才重建」；更稳的是给 data/index/contextual.npz 一个校验和，启动时校验失败才重建；把重建做成显式的 docker compose run --rm maotai-rag python scripts/build_index.py，而不是启动路径的副作用。

### M10 没有任何自动化测试；CI 只守召回；finish_reason 从不检查

**证据**

- 全仓无 tests/、无 conftest.py、无 pytest.ini / pyproject.toml；唯一的「测试」是 scripts/test_mcp_client.py（手动跑、要起 MCP 服务）。requirements.txt 里也没有 pytest。
- CI（.github/workflows/eval-gate.yml）只做两件事：检索闸门（scripts/ci_retrieval_gate.py:32 只看 page_hit@5，阈值 0.64 硬编码在 :23），以及 workflow_dispatch 且手动勾选 run_answers 才跑的付费答案评测（:51-86）。→ 答案解析逻辑、路由（needs_agent）、流式事件、/ask 契约、/health、MCP 工具全部无覆盖。
- 闸门本身有盲区：只看 overall，而 overall 含 no_answer 类（gold 里 5 题且都带 source 页）→ 0.66 里有多少来自「拒答题也命中了 gold 页」无法区分；cross_page 0.40 / no_answer 0.40（CI 结果 JSON 实测）这种短板不会被闸门拦住。
- torch 版本三处不一致：requirements.txt:8 锁 2.15.0，Dockerfile:19 从 CPU 源装同版本（2.15.0+cpu 满足 ==2.15.0，这点**是对的**，不会二次下载），CI 两处（eval-gate.yml:31, 65）却是 pip install torch --index-url .../cpu **不锁版本** → 未来 torch 破坏性升级会先炸 CI 而不是本地，或反过来本地能跑 CI 挂了。
- finish_reason：grep 全仓 0 命中。而 _agent_call（ask.py:448）与流式重试（:641）都用 max_tokens=900；被 length 截断的答案会和完整答案一样进后处理，refused 也可能因此误判（截断处正好丢了结尾的出处）。

**修法**：见第 7 节的可执行清单（10 个测试文件，不需要联网，也不会花钱）；CI 加一个 pytest job + 锁 torch==2.15.0；把 finish_reason == length 记进 meta 并在界面提示「回答被截断」。

### M11 VectorStore.search 返回的 score 是另一个片段的分数

**证据**：store.py:49-60

    top_local = np.argsort(-scores)[:top_k]    # 局部下标
    top = idx[top_local]                      # 全局下标
    for j, i in enumerate(top):               # j 是 0..top_k-1
        ... Hit(..., score=float(scores[j]))  # 应该用 scores[top_local[j]]

**【已验证】** 用真实索引 + 真实查询探针（附录 A）：

| 场景 | 报出的 score | 真实余弦 |
|---|---|---|
| year=None | 0.5596, 0.5378, 0.2992, 0.3728, 0.3472（**不单调**） | 0.7000, 0.6990, 0.6869, 0.6863, 0.6788 |
| year=2024 | 0.5996, 0.4529, 0.3275, 0.4531, 0.3449（**不单调**） | 0.7000, 0.6788, 0.6660, 0.6650, 0.6607 |
| 自查询（top1 应 = 1.0） | 0.4822, 0.5209, 0.5363, 0.8369, 1.0（**递增**） | 1.0, 0.9519, 0.9268, 0.8821, 0.8780 |

最大绝对误差 0.388（year=None）/ 0.338（year=2024）。**排序本身是对的**（argsort 独立算），所以检索指标不受影响。

**影响面（重要，别夸大）**

- 生产路径 use_bm25=True 会用 RRF 分数覆盖它（hybrid.py:126-135）→ 当前不影响线上排序，界面也不显示分数（app.py:340-350 没渲染 score）。
- **use_bm25=False 路径（hybrid.py:99-101）直接返回 store.search 的 Hit** → 实验里的「纯向量」消融拿到的是错分数。
- API 的 sources[].score（api.py:76-80 → ask.py:283）对外暴露错值。
- 任何将来基于 score 做阈值过滤/重排/置信度展示的改动都会直接踩雷。

**修法**：score=float(scores[top_local[j]])（一行），并补一个「返回的 score 单调不增且等于余弦」的测试。
---

## 4. MINOR（18 条，按主题分组）

### 4.1 答案与展示层

- **m1 E5 语境句被当作「原文」展示**：render_sources 直接显示 h["text"][:600]（app.py:340-350），而生产语料的 text 以 LLM 写的语境句开头（build_contextual_chunks.py:176；contextual.jsonl 1761 块全部如此，第一块即「该片段为年报封面，标示公司代码…」）→ 用户以为自己读到的是年报原文，实际先读到一句模型总结。context 字段本来就单存了，界面应分开渲染并标注「AI 语境提示」。
- **m2 引用样式正则比剥离正则严格**：app.py:291 的 _CITE_TAG_RE 要求**半角冒号 + 空格**，而 ask.py:164 的 _CITE_RE 匹配任意「[来源…]」→ 模型写成 [来源:2024年报P8] 或全角冒号时，剥离成功（接地校验通过）但界面不出样式（显示成纯文本）。同一个概念两套正则，早晚漂移。
- **m3 AskResponse 丢掉 meta.hits**：_meta 返回全文 hits（ask.py:287），AskResponse（api.py:89-100）没有该字段，pydantic 默认 extra=ignore 静默丢弃 → API 用户只能拿到 200 字 snippet，无法核对原文（与 C1 叠加更糟）。要么显式 extra=forbid 暴露不一致，要么加一个 /ask/full。
- **m4 refused 完全由正则决定**（ask.py:280），没有和 sources / citations 交叉验证：「回答里一个数字都没有 + 有出处」才是可信的拒答特征。
- **m5 追问建议是一笔额外的付费调用**：app.py:300-337 gen_followups 在每次回答后再打一次云端；异常被吞成空列表（合理），但成本模型里要算进去（云端模式每题最多 1 改写 + 1 回答 + 1 重试 + 1 修正 + 1 追问 = 5 次）。

### 4.2 配置与依赖

- **m6 后端解析有 4 份实现，语义已漂移**：api.py:103-116、app.py:266-274、mcp_server.py:69-85、ask.py:664-689（main）。最要命的是 **Streamlit 完全忽略 RAG_BASE_URL**（app.py:266-274 只看 backend 选择 + DEEPSEEK_API_KEY / RAG_API_KEY）→ 用户按 README 设 RAG_BASE_URL 指向 vLLM/Ollama，API 与 MCP 生效、**网页界面不生效**；且缺 Key 时用字面量 "missing" 当 Key 发出去（:274），错误信息是 Incorrect API key provided: missing，很难查。ask.py:664-667 又把 RAG_BASE_URL 当 Ollama 端点、固定 api_key="ollama" → 同一个变量四种含义。
- **m7 死常量与未生效的最佳实践**：config.py:38 COLLECTION 无人引用；config.py:25 QUERY_INSTRUCTION（bge 官方建议的查询前缀）只被 src/retriever.py:25（E0 基线）使用，**生产 hybrid.py:93-94 直接 encode([query]) 不加前缀** → 基线 vs 混合检索的 A/B 严格说混入了查询预处理这个变量。不过实测影响可忽略：E1_baseline 与 E2_pure（纯向量）的 hit@5 完全相同（0.30 / MRR 0.1773 / prec 0.06），说明前缀对 hit@5 没有可见影响；而 E1_baseline 0.30 → E2_hybrid_year 0.56（+26pp）的提升主要来自 BM25 + 年份过滤，不是前缀。修法：给 hybrid 也加前缀统一口径，或在实验报告里注明这是两变量。
- **m8 依赖清单**：requirements.txt 里 matplotlib==3.11.2 只被 scripts/make_figures.py（报告插图）使用，却进了运行时镜像（明文增加约 30 MB + 字体依赖）；没锁 transformers / pydantic / httpx（三者都被直接或间接 import）；装了但代码里不用的 python-dotenv、sse-starlette（mcp_server.py:38-50 手写了 dotenv 解析器代替前者）；缺 pytest。建议拆 requirements.txt / requirements-dev.txt。
- **m9 模型名与路径三处不一致**：config.py:32 GEN_MODEL="qwen3:4b"、ollama.Modelfile:1 FROM D:/eng_project/rag项目/data/models/Qwen3-4B-Q4_K_M.gguf（硬编码本机绝对路径，别人 clone 后无法 ollama create）、Dockerfile:25 用 HF id。文档里说「以实际部署的模型为准」，但代码里没有一处能确认实际模型。
- **m10 .dockerignore 漏了构建上下文里的产物**：排除了 .env / .venv / data/models / data/raw_pdfs（这些是对的），但没排除 data/extracted/（1.28 MB / 556 文件）、results/、docs/、review/、report/、.handoff/ → COPY . .（Dockerfile:22）把审计报告和实验输出都打进镜像，且任何一次 review/ 变更都会让镜像层缓存失效。

### 4.3 可观测性与运维

- **m11 全仓没有 logging**：只有 print（pipeline.py:39-40、app.py:336、ask.py:685 等），容器里无法按级别过滤、无法关联请求 id；agent 工具异常在流式路径被静默吞掉（ask.py:606-607 只把 hits 置空），线上出现「没检索到」时无法区分「检索空」与「工具崩了」。
- **m12 静默降级只 print 一行**：pipeline.py:33-42，上下文索引缺失时退回基线（实测 hit@5 从 0.66 掉到约 0.62），除了那行日志没有任何信号；CI 的闸门能拦住 0.62，但前提是「有人看 CI」。建议降级时在 meta 或 /health 里加 index: contextual|baseline。
- **m13 /health 恒 ok**（见 M8），Dockerfile:30-31 的 HEALTHCHECK 因此形同虚设；建议真做一次轻量检索（1 条 query、top_k=1）。
- **m14 数据下载无重试、无缓存校验**：download_data.py:29-36，http 明文、无 Range 续传、无 sha256；断流后直接写文件，set -e 的容器会反复重来。

### 4.4 实验资产与评测口径

- **m15 RerankedRetriever 生产不用但仍会误导**：hybrid.py:139-156，硬编码 top_k=20、就地改写 candidate 的 score、类型标注 year: int 与基类的 int | set 不符；只有 scripts/run_experiments.py 用。留着做消融没问题，但建议加「实验专用，勿接入生产」并在类 docstring 里写清（E3 已证明无增益，pipeline.py:8-11）。
- **m16 BM25 每查询全量打分**：hybrid.py:108-114 每次重建 pos 映射 + get_scores 全语料 + sorted 全量。1761 块下是毫秒级（可接受），但 10 倍语料后 get_scores 是同步 CPU 密集操作，会占满 AnyIO 线程池；pos 应该在构造时建一次。
- **m17 答案评测口径偏乐观**：evaluate_answers.py:94-100：answer_correct 用 gold 数字纯子串（gold="12" 会被 1234 满足）；cite_correct 只要 cited_pages 与 gold_pages 有交集就算对（多页题蒙对一页即可）；except 时 text = "__ERROR__: ..."（:80-81）被算成「答错」而不是「调用失败」→ 上游偶发 5xx 会拉低答对率、污染版本对比。
- **m18 温度 0.1 与「可复现」表述冲突**：_complete:146-152、_agent_call:442-448、rewrite_followup:122 全是 0.1 而非 0；同一问题两次运行答案可能不同，benchmark 对比需要固定 seed 或最佳-of-N。

---

## 5. 四条生成路径的一致性矩阵（C2/M4 的汇总视图）

| 能力 | 直通（非流式） | 直通（流式） | Agent（非流式） | Agent（流式） |
|---|---|---|---|---|
| 多轮改写 | answer_auto:522 统一调用 | 同左 | 同左 | 同左 |
| 首轮空正文重试 | 无 | 有，max_tokens=1800（:641） | 有（共享 _agent_call） | 有（共享） |
| 拒答重试 | 有，无条件替换 | 有，带 not REFUSE_RE 条件 | — | — |
| 接地校验证据 | hits / more | 同左 | 整池 pool | **pool[:12]** |
| 对外 sources | ≤10 | ≤10 | **≤12** | **≤12** |
| 工具异常 | — | — | 错误文本回给模型 | **静默为空** |
| finish_reason 检查 | 无 | 无 | 无 | 无 |
| meta 字段 | 同构（_meta） | 同左 | 同左 | 同左 |

---

## 6. 评测与 CI 到底覆盖了什么

| 层 | 有覆盖？ | 证据 |
|---|---|---|
| 检索 hit@5 / MRR / page_prec | 有，CI 闸门（阈值 0.64） | ci_retrieval_gate.py:23, 32 |
| 答案解析（数字/引用） | 仅手动、付费的 workflow_dispatch | eval-gate.yml:51-86 |
| 路由（needs_agent） | 无 | 无测试 |
| Agent 工具循环 | 无 | 无测试 |
| 接地校验 / 拒答判定 | 无 | 无测试 |
| 流式事件协议 | 无 | 无测试 |
| API 契约（/ask、/ask/stream、/health） | 无 | 无测试 |
| MCP server | 无 | 只有 scripts/test_mcp_client.py 手动脚本 |
| 引用页码真实性 | 无 | 无代码（C1） |
| 依赖可复现 | 部分 | CI 的 torch 未锁版本 |

---

## 7. 最小可行测试清单（不需要联网、不花钱）

原则：用「假 client / 假 encoder」把 LLM 与模型挡掉，全部是纯函数或本地数据测试。放进 tests/，pytest -q 秒级跑完；**标 ✗ 的用例在当前代码上应当是失败的**，正好当修复契约。

    tests/test_numbers.py            # ask.ungrounded_numbers
      - test_identity:            ungrounded_numbers("营业收入 1,234 亿元", "营业收入 1,234 亿元") == []
      - test_unit_conversion:     ungrounded_numbers("归母净利润 862.28 亿元", "净利润 86,228,000,000 元") == []
      - ✗ test_partial_substring: ungrounded_numbers("营业收入 12 亿元", "营业收入 1,234 亿元") == ["12"]
      - ✗ test_single_digit:      ungrounded_numbers("毛利率 5%", "毛利率 6%") == ["5"]
      - ✗ test_number_scale:      ungrounded_numbers("营业收入 120 亿元", "1,200,000,000 元") == ["120"]
      - test_no_false_positive:   ungrounded_numbers("2024年报第 8 页", "第 8 页") == []

    tests/test_refuse.py             # ask.REFUSE_RE / 重试采纳
      - test_pure_refusal:        5 条纯拒答句 == True
      - ✗ test_partial_refusal:   "…但未披露2025年一季度数据…" == False
      - ✗ test_negation_with_num: "该指标在2022年报中未提供，2023年报披露为 1234 万元" == False
      - ✗ test_replace_decision:  should_replace("未披露该数据", "营业收入 1,234 亿元…") is False

    tests/test_years.py              # hybrid.detect_years / detect_page；ask.needs_agent
      - ✗ test_range:             detect_years("2022-2024年的营收") == [2022, 2023, 2024]
      - ✗ test_range_cn:          detect_years("2022到2025年间") == [2022, 2023, 2024, 2025]
      - ✗ test_fiscal_year:       detect_years("2024财年营收") == [2024]
      - ✗ test_no_false_page:     detect_page("P2P业务收入") is None
      - test_page_ok:             detect_page("第 6 页讲了什么") == 6
      - test_route_compare:       needs_agent("2022和2024年营收对比") is True

    tests/test_chunking.py           # chunking.chunk_fixed
      - ✗ test_slash_content_kept: 含 "基本每股收益（元/股）17.5" 的页保留该行
      - test_footer_dropped:       含 "13 / 143" 独占一行的页丢弃该行
      - test_page_attribution:     每块 chunk.id 的页码 == 来源页号

    tests/test_store.py              # store.VectorStore.search
      - ✗ test_score_matches_cosine: 返回的 score 约等于 embs[i] @ q，且单调不增
      - test_topk_order:            top_k 截断后仍按相似度降序

    tests/test_parity.py             # 假 client，最值钱的一个
      - ✗ test_stream_equals:       同一假 client 下 answer_auto 的 answer 等于拼接 answer_auto_stream 的 delta/replace
      - ✗ test_empty_first_retry:   首轮返回空字符串时两条路径都触发重试
      - ✗ test_retry_refusal_kept:  重试仍是拒答时两条路径行为一致
      - ✗ test_agent_tool_error:    工具抛异常时两条路径都记录到 meta.steps

    tests/test_meta.py               # ask._meta
      - ✗ test_citation_pages:      答案里出现 P888 时 meta.citations_unverified == [(2024, 888)]
      - test_sources_from_hits:     meta.sources 的 (year, page) 全部在 hits 里

    tests/test_api.py                # fastapi TestClient，用 monkeypatch 挡掉 LLM/检索
      - test_health_shape / test_ask_contract / test_year_400
      - ✗ test_stream_sse_events:   status → delta* → done；异常时 event: error
      - ✗ test_no_key_leak:         502 响应正文不含 DEEPSEEK_API_KEY

CI 侧（.github/workflows/eval-gate.yml）加一个 unit job：pip install pytest（锁 torch==2.15.0）+ pytest -q tests/，与现有检索闸门并行；这不增加任何付费调用。

---

## 8. 不要做的重构

下面这些是「看起来像优化、实际上会变差」的方向，审计后特意写明，避免下一轮接手时踩：

1. **不要引入向量数据库（Chroma / FAISS / Milvus / pgvector / Qdrant）。** 语料 1761 块 × 512 维，np.dot 全量内积在本机是毫秒级；换成向量库要引入持久化、并发写入、索引重建、额外进程与容器，召回率不会提升（当前瓶颈是年份过滤与引用校验，不是速度）。真要扩到 10 万块再谈 ANN，而且优先考虑 faiss-cpu 而非外部服务。
2. **不要引入 LangChain / LlamaIndex / 任何 prompt 编排框架。** 现在 5 个文件、约 700 行把「检索 → 拼上下文 → 调模型 → 校验 → 元信息」写得可以逐行读；换成框架会把 prompt、重试、JSON 解析藏进黑盒，_meta 这种统一返回结构也保不住，评测不可复现。
3. **不要把「4 份生成实现」合并成一个大重构。** 正确做法是先把差异抽成 2 个纯函数 + test_parity.py 锁住行为（第 7 节），再谈抽象；先重写再补测试，等于把 C2 的偏差藏得更深。
4. **不要换 embedding 模型或加精排来「提升效果」。** pipeline.py:8-11 记录了 E3（精排）与 E5（语境句）的对比，精排已被语境句取代；当前 hit@5 的短板在 cross_page 0.40 / no_answer 0.40，那是切块与判定问题，换模型治不了。
5. **不要为了「去掉 numpy」手写纯 Python 点积，也不要把 BM25 换成 Elasticsearch。** 前者会慢一到两个数量级；后者为 1761 块引入一个 Java 服务，运维成本与本项目体量完全不匹配。
6. **不要动 data/ 下的产物路径与 contextual.jsonl 的 text 格式。** 已分发索引 contextual.npz 与 chunks 文件是成对的，改名或改前缀会让现有镜像与 CI 同时失效；要修 M1-③（语境句污染接地校验）应**在加载时**用 context 字段把前缀分离，而不是重建索引（重建还要再花约 5 元且会改变已报告的 66%）。
7. **不要删除 src/retriever.py、RerankedRetriever、scripts/run_experiments.py。** 它们是实验可复现性的一部分（E0/E3 基线）；只需加注释说明「非生产路径」，不要顺手清理。
8. **不要把 .env 纳入版本控制、不要为了「方便」git add -f data/models。** 模型 3.55 GB、.env 含 Key；当前 .gitignore 与 .dockerignore 在这两点上是正确的（.env 从未进过历史，git log --all -- .env 为空；模型不进 Git，无需 LFS）。
9. **不要引入 pydantic-settings / typer / pytest-asyncio 之类的「现代化」依赖来重写配置与 CLI。** 配置只有 41 行、CLI 只有一个 main()；要解决的是 m6 的「四份解析实现」，抽一个 resolve_backend() 共用即可，不需要新框架。

---

## 附录 A 复现输出（本机 CPU，无网络、无付费调用）

**A.1 生产检索 top5（真实 get_search() + 真实 BGE 模型 + 真实索引）**

    2024年茅台营业总收入是多少？              -> [(2024,56),(2024,141),(2024,21),(2024,141),(2024,8)]
    2022-2024年的营收分别是多少？              -> [(2024,6),(2024,6),(2024,5),(2024,34),(2024,70)]
    2022到2025年间营业总收入的复合增长率是多少？ -> [(2025,61),(2025,12),(2025,63),(2025,10),(2025,141)]
    2024财年的营收是多少？                     -> [(2024,6),(2025,6),(2024,70),(2023,5),(2023,8)]
    P6 的主要财务数据是什么？                   -> [(2022,6),(2022,6),(2022,6),(2022,6),(2023,6)]  direct=True×5

**A.2 VectorStore.search 报分 vs 真余弦**

    year=None   reported 0.559553, 0.537823, 0.299183, 0.372838, 0.347153   不单调
                true     0.700033, 0.698969, 0.686851, 0.686286, 0.678844   单调
                max abs err: 0.387668
    year=2024   reported 0.599590, 0.452875, 0.327494, 0.453112, 0.344923   不单调
                true     0.700033, 0.678844, 0.665992, 0.664961, 0.660701   单调
                max abs err: 0.338498
    自查询       reported 0.482162, 0.520884, 0.536300, 0.836855, 1.000000  递增！
                true     1.000000, 0.951942, 0.926750, 0.882081, 0.878043

**A.3 ungrounded_numbers（答案, 上下文）**

    ("营业收入 12 亿元，同比增长 5%",  "…营业收入 1,234 亿元，同比增长 6%")  -> []   ← 子串 + 一位数
    ("归母净利润 862.28 亿元",         "净利润 86,228,000,000 元")            -> []   ← 单位换算正确工作
    ("营业收入 120 亿元",              "营业收入 1,200,000,000 元")          -> []   ← 120 被 1,200,000,000 接地
    ("[来源: 2024年报 P888] 营业收入 1,234 亿元", "营业收入 1,234 亿元")     -> []   ← 伪造出处不被检查
    ("毛利率 5%",                      "毛利率 6%")                          -> []   ← 一位数被跳过
    ("净利润 3 亿元",                  "净利润 3.5 亿元")                     -> []

**A.4 REFUSE_RE**

    True   "根据年报内容无法回答该问题"                                  （正确）
    True   "2024年报披露了分红方案，但未披露2025年一季度数据…"          （误判）
    True   "该指标在2022年报中未提供，2023年报披露为 1234 万元。"       （误判）
    False  "2024年营业总收入为 1,741.44 亿元，同比增长 15.66%。"        （正确）

**A.5 detect_years / detect_page / needs_agent**

    detect_years('2024年营收') -> [2024]        ('2024 年' -> [2024])        ('2024财年营收' -> [])
    detect_years('2022-2024年的营收') -> [2024]  ('2022到2025年间年化增长' -> [2025])
    detect_years('2024年报营收') -> [2024]        ('24年营收' -> [])
    detect_page('P6 的主要财务数据') -> 6         ('第 6 页讲了什么' -> 6)
    detect_page('P2P业务收入') -> 2               ('EPS 6 元' -> None)
    needs_agent('P2P业务收入') -> True            needs_agent('EPS 6 元') -> False

**A.6 切块阶段丢弃含斜杠的行（chunking.py:46-49）**

    data/extracted 四份年报共 30,258 行；含 "/" 的行 757
      其中 556 行是页脚 "13 / 143"（正好 556 页）→ 这些应该丢
      另外 201 行是正文 → 被一并丢弃，其中 21 行含 "元/股"（基本/稀释每股收益、每股净资产、
      每股经营现金流），另有表格合计行（如 "合计 / / / / / / 754.12 /"）、单位行（"毫克/升"、
      "7000t/d 污水处理厂 18.385 …"）、公司网址等
    data/chunks/contextual.jsonl（1761 块）中 "元/股" 出现次数：0

→ 这是一个【已验证】的数据层缺陷：if "/" not in line（chunking.py:48）本意是去页脚，实际把**所有含斜杠的正文行**都删了。改成只匹配「整行是 数字 / 数字」的页脚即可（556 行页脚全部命中的前提下，正文零损失）。注意 gold 第 25 题正好问「2025年度茅台的分红预案是每股派多少现金红利」，能靠「每 10 股派…」的相邻句兜住，但任何直接问「每股收益」的问题都检索不到——这是**静默的指标族缺失**。按影响面明确但量小，定 MAJOR，未进 CRITICAL。

---

## 附录 B 已验证 / 推测 / 未验证 对照

| 结论 | 状态 | 依据 |
|---|---|---|
| C1 引用无校验、_CITE_RE 先删出处 | 已验证 | 读码 + _CITE_RE.sub 探针 |
| C2 四份实现的 5 处差异 | 已验证（静态） | 逐行对照，见第 5 节 |
| M1 四个洞 | 已验证 | 7 个 ungrounded_numbers 用例 + contextual.jsonl 前缀 |
| M2 修正不复验 | 已验证（静态） | _grounding_fix:200-217 |
| M3 拒答误判、重试覆盖 | 已验证 | REFUSE_RE 探针 + 读码 |
| M4 截断 / 静默空 / 折叠 / 注入 | 已验证（静态） | ask.py:509, 623-624, 392-394, 399-400, 525, 577 |
| M5 年份盲区、P6 只答 2022 | 已验证 | 生产检索探针（A.1、A.5） |
| M6 上下文无上限 | 代码+块长统计已验证；**Ollama 截断行为推测** | num_ctx 8192 vs 9,060 字 |
| M7 竞态窗口、无超时 | 代码已验证；**并发触发推测** | pipeline.py:43, 49；api.py:107 vs mcp_server.py:80 |
| M8 CORS / 鉴权 / 成本 | 代码已验证；**SSE 断连后的真实关闭时机推测** | starlette responses.py:277-285 + concurrency.py:51-58 |
| M9 门禁错产物 | 已验证 | .gitignore + start.sh:6 + Dockerfile:22 |
| M10 无测试、CI 盲区、torch 漂移、finish_reason 0 处 | 已验证 | 文件系统 + grep |
| M11 score 错位 | 已验证 | A.2，最大误差 0.388 |
| A.6 斜杠行丢弃 | 已验证 | 30,258 / 757 / 556 / 201 / 21 统计 + 语料计数 0 |
| 容器 docker compose up 未实测 | 未验证 | 本次不启动 Docker |
| 真实模型下的拒答误判率 / 重试前后质量 | 未验证 | 需付费调用，本次禁止 |

**已确认无问题（也一并记录，避免重复排查）**：.env 从未进过 Git 历史（含全部分支）；模型文件不在 Git 里，无需 LFS；.dockerignore 排除了 .env / .venv / data/models / data/raw_pdfs；Dockerfile 先装 CPU 版 torch 再装 requirements，2.15.0+cpu 满足 ==2.15.0，不会二次下载；data/extracted 的文件是 page_001.txt 形式的零填充命名，sorted() 顺序正确；data/index/contextual.npz 与 data/chunks/contextual.jsonl 的块数一致（1761）。

---

*审计结束。全文只读，未改动任何既有文件；本文件是本任务唯一写入。*


