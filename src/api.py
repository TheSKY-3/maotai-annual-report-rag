"""茅台财报 RAG 问答 API（FastAPI）。

启动（项目根目录）:
  uvicorn src.api:app --host 0.0.0.0 --port 8000
  # 或 python -m uvicorn src.api:app --port 8000

接口文档（Swagger UI）: http://localhost:8000/docs

模型选择（与界面同一套检索管线）:
  1. 请求里带 api_key → 用云端 DeepSeek；
  2. 否则按环境变量：RAG_BASE_URL（自定义 OpenAI 兼容端点）→
     DEEPSEEK_API_KEY（云端 DeepSeek）→ 都没有则本机 Ollama。
"""
from __future__ import annotations

import json
import os
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import OpenAI
from pydantic import BaseModel, Field

from src.config import (DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, GEN_MODEL,
                        OLLAMA_BASE_URL, YEARS)
from src.pipeline import get_search
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


def _get_search():
    """惰性加载检索器（BGE + 索引，首次约几秒，之后复用）。"""
    global _search_fn
    if _search_fn is None:
        _search_fn = get_search()
    return _search_fn


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


@app.get("/")
def root() -> dict:
    return {"service": "茅台财报 RAG API", "docs": "/docs", "ask": "POST /ask"}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "years": YEARS,
            "model": os.environ.get("RAG_MODEL", DEEPSEEK_MODEL)}


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    if req.year is not None and req.year not in YEARS:
        raise HTTPException(400, f"year 仅支持 {YEARS}")
    client, model = _resolve(req.api_key, req.model)
    try:
        meta = answer_auto(
            client, _get_search(), req.question, model,
            search_kwargs={"year": req.year} if req.year is not None else None,
            history=[m.model_dump() for m in req.history],
            force_agent=req.agent,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"生成失败：{e}") from e
    return AskResponse(model=model, **meta)


@app.post("/ask/stream")
def ask_stream(req: AskRequest) -> StreamingResponse:
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

    def sse():
        try:
            for ev in answer_auto_stream(
                    client, _get_search(), req.question, model,
                    search_kwargs={"year": req.year}
                    if req.year is not None else None,
                    history=[m.model_dump() for m in req.history],
                    force_agent=req.agent):
                yield (f"event: {ev['type']}\n"
                       f"data: {json.dumps(ev, ensure_ascii=False)}\n\n")
        except Exception as e:  # noqa: BLE001
            yield (f"event: error\n"
                   f"data: {json.dumps({'message': str(e)}, ensure_ascii=False)}\n\n")

    return StreamingResponse(
        sse(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
