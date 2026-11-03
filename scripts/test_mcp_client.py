"""MCP 客户端实测：起 stdio 子进程，真实调一次 ask_annual_report（③验收）。

用法（项目根目录）:
  .venv/Scripts/python.exe scripts/test_mcp_client.py ["自定义问题"]

等价于任意 MCP 客户端（Claude Desktop 等）接入后的调用路径：
stdio 拉起 src/mcp_server.py -> initialize -> list_tools -> call_tool。
"""
import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT = Path(__file__).resolve().parent.parent
PYTHON = PROJECT / ".venv" / "Scripts" / "python.exe"
SERVER = PROJECT / "src" / "mcp_server.py"


async def main() -> None:
    question = (sys.argv[1] if len(sys.argv) > 1
                else "2025年营业总收入比2022年增长了多少？")
    params = StdioServerParameters(
        command=str(PYTHON), args=[str(SERVER)],
        env=None)  # None=客户端默认环境（会过滤密钥），server 从 .env 兜底
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("tools:", [t.name for t in tools.tools])
            result = await session.call_tool(
                "ask_annual_report", {"question": question})
            answer = result.content[0].text
            # Windows 控制台默认 GBK 会把 UTF-8 显示成乱码：落盘 UTF-8 文件
            # 供核对（MCP 协议本身按 UTF-8 传输，真实客户端不受控制台影响）
            out = PROJECT / "results" / "mcp_test_answer.txt"
            out.write_text(f"Q: {question}\n\n{answer}\n",
                           encoding="utf-8")
            print(f"answer written -> {out} ({len(answer)} chars)")


if __name__ == "__main__":
    asyncio.run(main())
