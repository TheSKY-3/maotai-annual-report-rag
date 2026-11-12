"""茅台财报 RAG 问答 API（FastAPI）。

启动（项目根目录）:
  uvicorn src.api:app --host 0.0.0.0 --port 8000
  # 或 python -m uvicorn src.api:app --port 8000

接口文档（Swagger UI）: http://localhost:8000/docs

模型选择（与界面同一套检索管线）:
  1. 请求里带 api_key → 用云端 DeepSeek；
  2. 否则按环境变量：RAG_BASE_URL（自定义 OpenAI 兼容端点）→
     DEEPSEEK_API_KEY（云端 DeepSeek）→ 都没有则本机 Ollama。

可观测性:
  GET  /ready  索引与模型配置探测（缺文件/退基线时给出原因）
  POST /ask    响应含 usage/cost_cny/latency_ms/request_id；
               每次请求输出一条 JSON 日志（event=ask）。
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from openai import OpenAI
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from src import cite_locate, cost, usage
from src.config import (DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, GEN_MODEL,
                        OLLAMA_BASE_URL, PROJECT, YEARS)
from src.pipeline import get_search, index_status
from src.ask import answer_auto, answer_auto_stream

app = FastAPI(
    title="茅台财报 RAG API",
    version="1.0.0",
    description="把贵州茅台 2022–2025 年报（556 页）变成可提问的资料库："
                "答案必附原文页码出处，查不到就明说。",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_search_fn = None

# 单条 JSON 日志；用独立 handler 避免 uvicorn 的前缀破坏 JSON 行。
_LOGGER = logging.getLogger("rag.api")
if not _LOGGER.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _LOGGER.addHandler(_handler)
_LOGGER.setLevel(logging.INFO)
_LOGGER.propagate = False


def _get_search():
    """惰性加载检索器（BGE + 索引，首次约几秒，之后复用）。"""
    global _search_fn
    if _search_fn is None:
        _search_fn = get_search()
    return _search_fn


def _default_backend() -> tuple[str, str, bool]:
    """返回 (backend, model, key_configured)，不泄露 Key 本身。"""
    if os.environ.get("RAG_BASE_URL"):
        return ("custom", os.environ.get("RAG_MODEL", GEN_MODEL),
                bool(os.environ.get("RAG_API_KEY")))
    if os.environ.get("DEEPSEEK_API_KEY"):
        return ("cloud", os.environ.get("RAG_MODEL", DEEPSEEK_MODEL), True)
    return ("ollama", os.environ.get("RAG_MODEL", GEN_MODEL), False)


def _request_usage(bucket: dict | None) -> dict:
    return usage.copy(bucket)


def _cost(bucket: dict) -> tuple[float, bool]:
    peak = cost.is_peak()
    return (cost.estimate_cny(bucket["prompt_tokens"],
                              bucket["completion_tokens"], peak=peak),
            peak)


def _log_ask(*, request_id: str, status: int | str, started: float,
             model: str, meta: dict | None = None,
             bucket: dict | None = None, error: str | None = None,
             year: int | None = None, agent: bool = False,
             question_chars: int = 0) -> None:
    """输出一条 JSON 请求日志；任何分支都只调用一次。"""
    u = _request_usage(bucket)
    cost_cny, peak = _cost(u)
    payload = {
        "event": "ask",
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "request_id": request_id,
        "status": status,
        "model": model,
        "year": year,
        "agent_requested": agent,
        "question_chars": question_chars,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "prompt_tokens": u["prompt_tokens"],
        "completion_tokens": u["completion_tokens"],
        "calls": u["calls"],
        "cost_cny": cost_cny,
        "cost_peak": peak,
    }
    if meta is not None:
        payload.update({
            "route": meta.get("route"),
            "structured": bool(meta.get("structured")),
            "refused": bool(meta.get("refused")),
            "degraded": bool(meta.get("degraded")),
            "sources": len(meta.get("sources") or []),
            "citations": len(meta.get("citations") or []),
            "violations": len(meta.get("violations") or []),
            "steps": len(meta.get("steps") or []),
        })
    if error:
        payload["error"] = error
    _LOGGER.info(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=200,
                          description="问题，如：2024年末国内经销商有多少家？")
    year: int | None = Field(default=None, description=f"限定年报年份 {YEARS}")
    history: list[Message] = Field(
        default=[], max_length=20,
        description="最近几轮对话（user/assistant 交替）；追问会自动补全")
    api_key: str | None = Field(
        default=None, description="自带 DeepSeek API Key（可选）")
    model: str | None = Field(default=None, description="覆盖模型名（可选）")
    agent: bool = Field(
        default=False,
        description="强制走 Agent 工具循环（默认自动路由：仅跨年对比/"
                    "页码直达题启用）")


class Source(BaseModel):
    year: int
    page: int
    score: float
    snippet: str


class Step(BaseModel):
    tool: str
    args: dict
    n_hits: int


class AskResponse(BaseModel):
    question: str
    standalone_question: str = Field(
        description="多轮改写后的独立问题（多轮记忆生效时与 question 不同）")
    answer: str
    refused: bool = Field(description="是否为拒答（年报中确无该信息）")
    sources: list[Source]
    model: str
    route: str = Field(default="direct",
                       description="回答路径：direct=直通检索，agent=工具调用循环")
    steps: list[Step] = Field(
        default=[], description="agent 路径的工具调用轨迹")
    structured: bool = Field(
        default=False, description="是否走结构化答案（claims+证据 id）")
    citations: list[str] = Field(
        default=[], description="程序渲染并校验过的出处")
    violations: list[str] = Field(
        default=[], description="未通过的校验项（引用越界/数字不在所引页）")
    degraded: bool = Field(
        default=False, description="部分内容未通过自动核对")
    request_id: str = Field(default="", description="请求 id，与 JSON 日志对应")
    usage: dict = Field(
        default_factory=dict, description="本次请求 token 用量")
    cost_cny: float = Field(default=0.0, description="本次请求估算成本（元）")
    latency_ms: int = Field(default=0, description="本次请求耗时（毫秒）")


def _resolve(api_key: str | None, model: str | None) -> tuple[OpenAI, str]:
    env_key = os.environ.get("DEEPSEEK_API_KEY", "")
    env_base = os.environ.get("RAG_BASE_URL", "")
    if api_key:  # 请求自带 Key：强制云端 DeepSeek
        return (OpenAI(base_url=DEEPSEEK_BASE_URL, api_key=api_key),
                model or os.environ.get("RAG_MODEL", DEEPSEEK_MODEL))
    if env_base:
        return (OpenAI(base_url=env_base,
                       api_key=os.environ.get("RAG_API_KEY", "ollama")),
                model or os.environ.get("RAG_MODEL", GEN_MODEL))
    if env_key:
        return (OpenAI(base_url=DEEPSEEK_BASE_URL, api_key=env_key),
                model or os.environ.get("RAG_MODEL", DEEPSEEK_MODEL))
    return OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama"), model or GEN_MODEL


# 阶段3 公网预备护栏：未设环境变量时完全开放（本地开发行为不变）。
# 限流为单进程内存桶；多 worker 部署需换共享存储（Redis 等），此处不做。
RATE_BUCKETS: dict[str, list[float]] = {}


def _guard(request: Request) -> None:
    token = os.environ.get("RAG_API_TOKEN")
    if token and request.headers.get("authorization") != f"Bearer {token}":
        raise HTTPException(401, "缺少或错误的 Bearer token")
    limit = os.environ.get("RAG_RATE_LIMIT")
    if not limit:
        return
    now = time.monotonic()
    ip = request.client.host if request.client else "?"
    bucket = [t for t in RATE_BUCKETS.get(ip, []) if now - t < 60]
    if len(bucket) >= int(limit):
        raise HTTPException(
            429, "请求过频",
            headers={"Retry-After": str(int(60 - (now - bucket[0])) + 1)})
    bucket.append(now)
    RATE_BUCKETS[ip] = bucket


_SPA_DIR = PROJECT / "web" / "dist"


@app.get("/")
def root():
    # 路由按注册顺序匹配：根路径须在此显式服务 SPA，末尾 mount 只接 assets
    if _SPA_DIR.is_dir():
        return FileResponse(_SPA_DIR / "index.html")
    return {"service": "茅台财报 RAG API", "docs": "/docs", "ask": "POST /ask"}


@app.get("/health")
def health() -> dict:
    info = index_status()
    backend, model, key_configured = _default_backend()
    return {"status": "ok", "years": YEARS, "model": model,
            "backend": backend, "api_key_configured": key_configured,
            "index": info["index"]["kind"], "index_status": info["status"],
            "retriever_loaded": info["loaded"]}


@app.get("/ready")
def ready() -> JSONResponse:
    """就绪探测：索引文件/年份覆盖/模型配置；不可用返回 503 并带原因。"""
    info = index_status()
    backend, model, key_configured = _default_backend()
    info.update({"model": model, "backend": backend,
                 "api_key_configured": key_configured})
    status_code = 200 if info["status"] in ("ready", "degraded") else 503
    return JSONResponse(info, status_code=status_code)


@app.get("/cite_crop")
def cite_crop(year: int, page: int, numbers: str, _=Depends(_guard)):
    """轴4 坐标高亮图：该页上被引数字的整行横带 PNG，供前端出处面板展示。"""
    if year not in YEARS:
        raise HTTPException(400, f"year 仅支持 {YEARS}")
    if not 1 <= page <= 600:
        raise HTTPException(400, "page 超出范围")
    nums = [n for n in numbers.split(",") if n.strip()]
    if not nums or len(nums) > 40:
        raise HTTPException(400, "numbers 须为 1-40 个逗号分隔的数字")
    path = cite_locate.page_crop(year, page, nums,
                                 PROJECT / "docs" / "citations" / "ui")
    if path is None:
        raise HTTPException(404, "该页未找到被引数字")
    return FileResponse(path, media_type="image/png")


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest, _=Depends(_guard)) -> AskResponse:
    if req.year is not None and req.year not in YEARS:
        raise HTTPException(400, f"year 仅支持 {YEARS}")
    client, model = _resolve(req.api_key, req.model)
    request_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    bucket = None
    try:
        with usage.collect() as bucket:
            meta = answer_auto(
                client, _get_search(), req.question, model,
                search_kwargs={"year": req.year} if req.year is not None else None,
                history=[m.model_dump() for m in req.history],
                force_agent=req.agent,
            )
    except Exception as e:  # noqa: BLE001
        _log_ask(request_id=request_id, status=502, started=started,
                 model=model, bucket=bucket, error=str(e),
                 year=req.year, agent=req.agent,
                 question_chars=len(req.question))
        raise HTTPException(502, f"生成失败：{e}") from e

    u = _request_usage(bucket)
    cost_cny, _ = _cost(u)
    data = dict(meta)
    data.update({
        "request_id": request_id,
        "usage": u,
        "cost_cny": cost_cny,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    })
    _log_ask(request_id=request_id, status=200, started=started,
             model=model, meta=data, bucket=bucket,
             year=req.year, agent=req.agent,
             question_chars=len(req.question))
    return AskResponse(model=model, **data)


@app.post("/ask/stream")
def ask_stream(req: AskRequest, _=Depends(_guard)) -> StreamingResponse:
    """SSE 流式问答（/ask 的流式版，参数完全相同）。

    事件流（text/event-stream，每事件两行 event:/data:）：
      status  阶段进展 {"label": "检索到 5 个片段，开始生成…"}
      delta   答案增量 {"text": "…"}（直通路径逐 token）
      replace 用完整文本替换已流出内容（拒答重试/数字接地修正时）
      done    结束 {"meta": {…与 /ask 响应同构…}}
      error   出错 {"message": "…"}
    """
    if req.year is not None and req.year not in YEARS:
        raise HTTPException(400, f"year 仅支持 {YEARS}")
    client, model = _resolve(req.api_key, req.model)
    request_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()

    def _next_event(it):
        """取下一个事件；StopIteration 不能跨线程边界，转成 None。"""
        try:
            return next(it)
        except StopIteration:
            return None

    async def sse():
        # 必须是 async 生成器：usage.collect() 的 ContextVar 要在事件循环
        # 的 Context 里设置，随后每次 next() 在线程池执行时会复制该
        # Context。若用同步生成器，每个 next() 都从异步 Context 复制，
        # 生成器里设置的桶在 yield 后就丢了，usage 会恒为 0。
        bucket = None
        logged = False
        try:
            with usage.collect() as bucket:
                it = answer_auto_stream(
                    client, _get_search(), req.question, model,
                    search_kwargs={"year": req.year}
                    if req.year is not None else None,
                    history=[m.model_dump() for m in req.history],
                    force_agent=req.agent)
                while True:
                    ev = await run_in_threadpool(_next_event, it)
                    if ev is None:
                        break
                    if ev.get("type") == "done":
                        meta = dict(ev.get("meta") or {})
                        u = _request_usage(bucket)
                        cost_cny, _ = _cost(u)
                        meta.update({
                            "request_id": request_id,
                            "usage": u,
                            "cost_cny": cost_cny,
                            "latency_ms": int(
                                (time.perf_counter() - started) * 1000),
                        })
                        ev = {**ev, "meta": meta}
                        _log_ask(request_id=request_id, status=200,
                                 started=started, model=model, meta=meta,
                                 bucket=bucket, year=req.year,
                                 agent=req.agent,
                                 question_chars=len(req.question))
                        logged = True
                    yield (f"event: {ev['type']}\n"
                           f"data: {json.dumps(ev, ensure_ascii=False)}\n\n")
        except Exception as e:  # noqa: BLE001
            if not logged:
                _log_ask(request_id=request_id, status="error",
                         started=started, model=model, bucket=bucket,
                         error=str(e), year=req.year, agent=req.agent,
                         question_chars=len(req.question))
                logged = True
            yield (f"event: error\n"
                   f"data: {json.dumps({'message': str(e)}, ensure_ascii=False)}\n\n")
        finally:
            # 客户端在 done 前断开时生成器被关闭，except 接不到 GeneratorExit，
            # 用 finally 补一条 aborted 日志，保证“一请求一日志”。
            if not logged:
                _log_ask(request_id=request_id, status="aborted",
                         started=started, model=model, bucket=bucket,
                         error="client disconnected", year=req.year,
                         agent=req.agent, question_chars=len(req.question))

    return StreamingResponse(
        sse(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "X-Request-ID": request_id})


# 阶段2：单端口交付——前端静态资源挂在所有 API 路由之后（根路径见 root()）。
# 未构建（web/dist 不存在）时保持原 JSON 根响应，开发期不受影响。
if _SPA_DIR.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_SPA_DIR, html=True), name="spa")
