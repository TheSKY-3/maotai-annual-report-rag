"""Phase A 结构化答案评测：50 题黄金集，带成本护栏与可复现清单。

用法：
  .venv/Scripts/python.exe -X utf8 scripts/run_phase_a.py --ids 1,45
  .venv/Scripts/python.exe -X utf8 scripts/run_phase_a.py --budget 5.0
输出：results/phase_a_structured.json（含 manifest / rows / summary）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openai import OpenAI  # noqa: E402

from src import cost, usage  # noqa: E402
from src.ask import answer_auto  # noqa: E402
from src.config import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, GOLD_FILE  # noqa: E402
from src.pipeline import get_search  # noqa: E402


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True,
                              cwd=ROOT).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def load_gold() -> list[dict]:
    return [json.loads(line) for line in
            GOLD_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]


def row_of(q: dict, meta: dict, u: dict, cny: float, elapsed: float) -> dict:
    hits = [{"id": h.get("id"), "year": h.get("year"), "page": h.get("page")}
            for h in meta.get("hits") or []]
    return {
        "id": q["id"], "type": q["type"], "question": q["question"],
        "gold_answer": q["gold_answer"], "source": q["source"],
        "answer": meta.get("answer", ""),
        "standalone": meta.get("standalone_question", ""),
        "refused": bool(meta.get("refused")),
        "route": meta.get("route", "direct"),
        "structured": bool(meta.get("structured")),
        "degraded": bool(meta.get("degraded")),
        "citations": meta.get("citations") or [],
        "violations": meta.get("violations") or [],
        "claims": meta.get("claims") or [],
        "calc_note": meta.get("calc_note") or "",
        "hits": hits,
        "calls": int(u.get("calls") or 0),
        "usage": u,
        "cost_cny": cny,
        "elapsed_sec": round(elapsed, 2),
    }


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    return {
        "n": n,
        "refused": sum(1 for r in rows if r["refused"]),
        "degraded": sum(1 for r in rows if r["degraded"]),
        "with_violations": sum(1 for r in rows if r["violations"]),
        "structured": sum(1 for r in rows if r["structured"]),
        "agent": sum(1 for r in rows if r["route"] == "agent"),
        "citations_total": sum(len(r["citations"]) for r in rows),
        "avg_citations": round(sum(len(r["citations"]) for r in rows) / n, 2) if n else 0,
        "cost_cny": round(sum(r["cost_cny"] for r in rows), 4),
        "calls": sum(r["calls"] for r in rows),
        "usage": {
            "prompt_tokens": sum(r["usage"].get("prompt_tokens", 0) for r in rows),
            "completion_tokens": sum(r["usage"].get("completion_tokens", 0) for r in rows),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="", help="只跑这些 id，逗号分隔")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--budget", type=float, default=5.0, help="人民币上限")
    ap.add_argument("--out", default="results/phase_a_structured.json")
    ap.add_argument("--model", default=os.environ.get("RAG_MODEL", DEEPSEEK_MODEL))
    args = ap.parse_args()

    load_env()
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise SystemExit("缺少 DEEPSEEK_API_KEY（.env 或用户环境变量）")

    gold = load_gold()
    if args.ids:
        want = {int(x) for x in args.ids.split(",") if x.strip()}
        gold = [q for q in gold if q["id"] in want]
    if args.limit:
        gold = gold[:args.limit]

    out_path = ROOT / args.out
    existing: dict = {}
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    rows: list[dict] = [r for r in existing.get("rows", [])
                        if r["id"] not in {q["id"] for q in gold}]

    client = OpenAI(base_url=DEEPSEEK_BASE_URL, api_key=key,
                    timeout=120, max_retries=2)
    search = get_search()

    print(f"[run] {len(gold)} questions, model={args.model}, budget={args.budget} CNY",
          flush=True)
    spent = sum(r["cost_cny"] for r in rows)
    for i, q in enumerate(gold, 1):
        if spent >= args.budget:
            print(f"[stop] budget reached: {spent:.4f} CNY", flush=True)
            break
        usage.reset()
        t0 = time.time()
        try:
            meta = answer_auto(client, search, q["question"], args.model)
            err = ""
        except Exception as e:  # noqa: BLE001
            meta, err = {"answer": "", "refused": False, "hits": [],
                         "violations": [f"运行异常：{e}"]}, str(e)
        u = usage.snapshot()
        cny = cost.estimate_cny(u["prompt_tokens"], u["completion_tokens"])
        row = row_of(q, meta, u, cny, time.time() - t0)
        if err:
            row["violations"] = list(row["violations"]) + [f"异常：{err}"]
        rows.append(row)
        spent += cny
        print(f"[{i}/{len(gold)}] id={q['id']} {q['type']} route={row['route']} "
              f"refused={row['refused']} cites={len(row['citations'])} "
              f"viol={len(row['violations'])} cost={cny:.4f} "
              f"total={spent:.3f} t={row['elapsed_sec']}s", flush=True)

        payload = {
            "manifest": {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "git_sha": git_sha(),
                "model": args.model,
                "structured": os.environ.get("RAG_STRUCTURED", "1") != "0",
                "structured_thinking": "disabled",
                "budget_cny": args.budget,
                "price_note": "deepseek-flash off-peak in $0.15/M out $0.6/M, "
                              "peak in $0.30/M out $1.2/M, USD_CNY=7.1",
                "peak": cost.is_peak(),
            },
            "summary": summarize(rows),
            "rows": rows,
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                            encoding="utf-8")

    print("[done] " + json.dumps(summarize(rows), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
