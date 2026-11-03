"""答案层评测：对 50 题黄金集逐题跑完整 RAG（检索+生成），打分。

指标（确定性，可复现）：
  答对率        有答案题：回答文本包含 gold_answer 中的关键数字/短语
  引用正确率    回答中引用的出处与 gold source 页码一致
  拒答正确率    无答案题：模型是否明确表示"无法回答"（不编造）
  拒答误伤率    有答案题：模型错误拒答的比例

用法:
  RAG_BASE_URL=http://localhost:11434/v1 RAG_MODEL=qwen3:4b \
      .venv/Scripts/python.exe scripts/evaluate_answers.py --name baseline
输出: results/{name}_answers.json
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from src.ask import REFUSE_RE, answer_auto, answer_question  # noqa: E402
from src.config import GOLD_FILE, RESULTS_DIR  # noqa: E402
from src.pipeline import get_search  # noqa: E402
from src.config import OLLAMA_BASE_URL  # noqa: E402

import os  # noqa: E402


def gold_numbers(text: str) -> list[str]:
    """提取 gold_answer 中的数字串（去千分位逗号后比较）。"""
    out = []
    for m in re.findall(r"[0-9][0-9,，]*(?:\.[0-9]+)?", text):
        out.append(m.replace(",", "").replace("，", ""))
    return [n for n in out if len(n) >= 2]  # 忽略个位数噪声


def cited_pages(answer: str) -> set[tuple[int, int]]:
    return {(int(y), int(p))
            for y, p in re.findall(r"(\d{4})年报\s*P(\d+)", answer)}


def gold_pages_of(source: str) -> set[tuple[int, int]]:
    return {(int(y), int(p))
            for y, p in re.findall(r"(\d{4})年报\s*P(\d+)", source)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="baseline")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题（调试用）")
    parser.add_argument("--router", action="store_true",
                        help="走 answer_auto 路由（跨年/跨页题进 agent 工具循环）")
    args = parser.parse_args()

    from openai import OpenAI
    model = os.environ.get("RAG_MODEL", "qwen3:4b")
    base_url = os.environ.get("RAG_BASE_URL", OLLAMA_BASE_URL)
    client = OpenAI(base_url=base_url,
                    api_key=os.environ.get("RAG_API_KEY", "ollama"))
    search_fn = get_search()

    questions = [json.loads(l) for l in
                 open(GOLD_FILE, encoding="utf-8") if l.strip()]
    if args.limit:
        questions = questions[: args.limit]

    rows = []
    for q in questions:
        meta = None
        try:
            if args.router:
                meta = answer_auto(client, search_fn, q["question"], model)
                text, hits = meta["answer"], meta["hits"]
            else:
                text, hits = answer_question(client, search_fn,
                                             q["question"], model)
        except Exception as e:  # noqa: BLE001
            text, hits = f"__ERROR__: {e}", []
        refused = bool(REFUSE_RE.search(text))
        row = {"id": q["id"], "type": q["type"], "question": q["question"],
               "answer": text, "refused": refused}
        if meta is not None:
            # 落盘路由信息与证据块 id，忠实度评测（judge）据此重建原文依据，
            # 不必重跑检索，避免两次检索差异造成的判卷漂移
            row["route"] = meta["route"]
            row["standalone_question"] = meta["standalone_question"]
            row["hit_ids"] = [h["id"] for h in hits]
        if q["type"] == "no_answer":
            row["refusal_correct"] = refused
        else:
            gnums = gold_numbers(q["gold_answer"])
            answer_flat = text.replace(",", "").replace("，", "")
            hit_nums = [n for n in gnums if n in answer_flat]
            row["num_hit"] = len(hit_nums)
            row["num_total"] = len(gnums)
            row["answer_correct"] = bool(gnums) and len(hit_nums) == len(gnums)
            row["cite_correct"] = bool(cited_pages(text) & gold_pages_of(q["source"]))
            row["false_refusal"] = refused
        rows.append(row)
        print(f"[{q['id']:02d} {q['type']:10s}] "
              + ("拒答" if refused else "答"), flush=True)

    def agg(rs):
        n = len(rs)
        if n == 0:
            return {"n": 0}
        out = {"n": n}
        if rs[0]["type"] == "no_answer":
            out["refusal_correct率"] = round(
                sum(r["refusal_correct"] for r in rs) / n, 4)
        else:
            out["answer_correct率"] = round(
                sum(r["answer_correct"] for r in rs) / n, 4)
            out["cite_correct率"] = round(
                sum(r["cite_correct"] for r in rs) / n, 4)
            out["false_refusal率"] = round(
                sum(r["false_refusal"] for r in rs) / n, 4)
        return out

    has = [r for r in rows if r["type"] != "no_answer"]
    na = [r for r in rows if r["type"] == "no_answer"]
    by_type = defaultdict(list)
    for r in has:
        by_type[r["type"]].append(r)
    summary = {"model": model,
               "有答案题": agg(has), "无答案题": agg(na),
               **{t: agg(rs) for t, rs in sorted(by_type.items())}}
    if args.router:
        n_agent = sum(r.get("route") == "agent" for r in rows)
        summary["路由"] = {"agent题数": n_agent,
                          "direct题数": len(rows) - n_agent}

    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{args.name}_answers.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n答案评测 -> {out}")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
