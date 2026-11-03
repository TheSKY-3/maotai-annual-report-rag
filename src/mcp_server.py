"""茅台年报问答 · MCP server（stdio）。

把"年报问答"作为 MCP 工具暴露：任何 MCP 客户端（Claude Desktop、
Cursor 等）接入后即可直接提问，答案自带年报页码出处，内部与
API/界面同一条管线（直通检索 + 跨年/跨页题自动 agent 工具循环）。

本地实测（项目根目录）:
  .venv/Scripts/python.exe scripts/test_mcp_client.py

客户端配置示例（Claude Desktop 的 claude_desktop_config.json）:
  {
    "mcpServers": {
      "maotai-annual-report": {
        "command": "D:/eng_project/rag项目/.venv/Scripts/python.exe",
        "args": ["D:/eng_project/rag项目/src/mcp_server.py"]
      }
    }
  }

注意：MCP 客户端拉起 stdio 子进程时默认只传"安全"环境变量（会过滤
疑似密钥），DeepSeek Key 因此大概率传不进来——本 server 启动时从
项目 .env 兜底读取（值永不打印、不写入日志）。首次调用需加载检索
模型（数秒），之后进程内复用。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

PROJECT = Path(__file__).resolve().parent.parent
# 客户端以脚本方式拉起本文件（sys.path[0] 是 src/），补上项目根才能 import src.*
sys.path.insert(0, str(PROJECT))


def _load_dotenv() -> None:
    """从项目 .env 补齐缺失的环境变量（不覆盖已有；不打印任何值）。"""
    env_file = PROJECT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


_load_dotenv()

mcp = FastMCP("maotai-annual-report")

_search_fn = None


def _get_search():
    global _search_fn
    if _search_fn is None:
        from src.pipeline import get_search

        _search_fn = get_search()
    return _search_fn


def _resolve():
    """与 src/api.py 同一套后端解析：RAG_BASE_URL → DeepSeek Key → Ollama。"""
    from openai import OpenAI

    from src.config import (DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, GEN_MODEL,
                            OLLAMA_BASE_URL)
    # 显式 120s 超时：网络悬挂时快速报错，不让调用方干等默认的 600s
    if os.environ.get("RAG_BASE_URL"):
        return (OpenAI(base_url=os.environ["RAG_BASE_URL"], timeout=120,
                       api_key=os.environ.get("RAG_API_KEY", "ollama")),
                os.environ.get("RAG_MODEL", GEN_MODEL))
    if os.environ.get("DEEPSEEK_API_KEY"):
        return (OpenAI(base_url=DEEPSEEK_BASE_URL, timeout=120,
                       api_key=os.environ["DEEPSEEK_API_KEY"]),
                os.environ.get("RAG_MODEL", DEEPSEEK_MODEL))
    return (OpenAI(base_url=OLLAMA_BASE_URL, timeout=120,
                   api_key="ollama"), GEN_MODEL)


@mcp.tool()
def ask_annual_report(question: str, year: int | None = None) -> str:
    """向贵州茅台 2022-2025 年报库提问，返回附原文页码出处的答案。

    Args:
        question: 问题；支持跨年对比（如"2025年比2022年净利润增长多少？"）
                  与多指标组合题，系统会自动拆解成多轮检索。
        year: 可选，限定只查某一年年报（2022-2025）。
    """
    from src.ask import answer_auto

    client, model = _resolve()
    meta = answer_auto(
        client, _get_search(), question, model,
        search_kwargs={"year": year} if year is not None else None)
    return meta["answer"]  # 答案自带 [来源: xxxx年报 Pn] 标注


if __name__ == "__main__":
    _get_search()  # 启动即预热：torch/检索模型在主线程载入，避免 Windows 下
    mcp.run()      # 工具线程内首载 DLL 死锁；首问也无需再等模型加载
