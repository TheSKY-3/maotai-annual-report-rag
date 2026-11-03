"""忠实度评测：LLM-as-judge 对已保存的答案打忠实度与相关性分（②升级）。

判卷口径（参考 RAGAS faithfulness 的定义，落成中文可执行规则）：
  忠实度 1-5   回答中的论断（数字/事实）是否都能在检索片段中找到原文依据；
               标注"（计算得出）"的计算结果视为有依据；编造扣分。
  相关性 1-5   回答是否切题、是否覆盖问题的主要问点（多指标题逐项核对）。
  无依据论点   逐条列出，供 badcase 归因（对应"模型抄错"这一最大失分源）。

只判非拒答答案（拒答无论断可判，单独计数）；证据直接取答案评测落盘的
hit_ids 对应的原文全文，不重跑检索——判卷依据与当时作答所见一致。

用法:
  RAG_BASE_URL=https://api.deepseek.com/v1 RAG_MODEL=deepseek-flash \
      RAG_API_KEY=sk-xxx .venv/Scripts/python.exe \
      scripts/evaluate_faithfulness.py --name agentic_cloud
输出: results/{name}_faithfulness.json
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from src.config import (CONTEXTUAL_CHUNKS_FILE, CHUNKS_FILE,  # noqa: E402
                        RESULTS_DIR)

JUDGE_PROMPT = """你是严格的年报问答评测员。给定【问题】【年报原文片段】【系统回答】，按以下口径打分：

一、忠实度（1-5 分）：回答里的每个论断（数字、事实）是否都能在片段中找到原文依据。
- 5 分：全部论断有原文依据；
- 4 分：个别措辞引申但数字全对；
- 3 分：有一处数字或事实无依据；
- 1-2 分：多处无依据（编造）。
- 由片段数字计算得出且已标注"（计算得出）"的，视为有依据；未标注的计算结果算无依据。
- 找不到依据的论断逐条原文摘录到 unsupported_claims（没有则空数组）。

二、相关性（1-5 分）：回答是否切题并覆盖问题的主要问点。
- 5 分：完整回答了所有问点；
- 3 分：只答了部分问点（如两问只答了一问）；
- 1 分：答非所问。

只输出一个 JSON 对象，不要任何其他文字：
{{"faithfulness": <1-5 整数>, "relevance": <1-5 整数>, "unsupported_claims": ["..."], "reason": "<30字内说明>"}}

【问题】
{question}

【年报原文片段】
{context}

【系统回答】
{answer}

评测 JSON："""


def load_chunks() -> dict[str, dict]:
    """id -> chunk，与生产检索同源（contextual 优先）。"""
    path = (CONTEXTUAL_CHUNKS_FILE if CONTEXTUAL_CHUNKS_FILE.exists()
            else CHUNKS_FILE)
    chunks = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                c = json.loads(line)
                chunks[c["id"]] = c
    return chunks


def build_context(chunks: dict, hit_ids: list[str]) -> str:
    parts = []
    for i, cid in enumerate(hit_ids):
        c = chunks.get(cid)
        if c:
            parts.append(f"[片段{i + 1} | 来源: {c['year']}年报 "
                         f"P{c['page']}]\n{c['text']}")
    return "\n\n".join(parts)


def parse_json(text: str) -> dict | None:
    """判卷输出健壮解析：剥 ``` 围栏、截取首个平衡大括号对象。"""
    text = re.sub(r"```(?:json)?", "", text).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def judge(client, model: str, question: str, context: str, answer: str,
          row_id) -> dict:
    def call(prompt: str):
        # 判卷无需工具，但同样显式关思考：省 token 且避免思考吃配额正文为空
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0, max_tokens=900,
            extra_body={"thinking": {"type": "disabled"}})
        return resp.choices[0].message.content or ""

    prompt = JUDGE_PROMPT.format(question=question, context=context,
                                 answer=answer)
    out = call(prompt)
    obj = parse_json(out)
    if obj is None:  # 空正文/围栏怪异：翻倍配额重试一次
        out = call(prompt + "\n（再次提醒：只输出 JSON 对象本身）")
        obj = parse_json(out)
    if obj is None:
        return {"id": row_id, "parse_error": out[:200]}
    def clamp(v):
        try:
            return max(1, min(5, int(v)))
        except (TypeError, ValueError):
            return None
    return {"id": row_id,
            "faithfulness": clamp(obj.get("faithfulness")),
            "relevance": clamp(obj.get("relevance")),
            "unsupported_claims": obj.get("unsupported_claims") or [],
            "reason": str(obj.get("reason") or "")[:100]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True,
                        help="results/{name}_answers.json 的 name")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    import os

    from openai import OpenAI

    model = os.environ.get("RAG_MODEL", "deepseek-flash")
    base_url = os.environ.get("RAG_BASE_URL",
                              "https://api.deepseek.com/v1")
    client = OpenAI(base_url=base_url,
                    api_key=os.environ.get("RAG_API_KEY", "missing"))

    in_path = RESULTS_DIR / f"{args.name}_answers.json"
    data = json.loads(in_path.read_text(encoding="utf-8"))
    rows = data["rows"]
    if args.limit:
        rows = rows[: args.limit]
    missing_ids = [r["id"] for r in rows if not r.get("hit_ids")
                   or r.get("refused")]
    chunks = load_chunks()

    judged = []
    for r in rows:
        if r.get("refused"):
            continue  # 拒答无论断可判，单独计数
        context = build_context(chunks, r.get("hit_ids") or [])
        if not context:
            judged.append({"id": r["id"], "parse_error": "no_context"})
            continue
        res = judge(client, model, r["question"], context, r["answer"],
                    r["id"])
        res["type"] = r.get("type")
        res["route"] = r.get("route")
        judged.append(res)
        print(f"[{r['id']:02d} {res.get('faithfulness')}/"
              f"{res.get('relevance')}] {r['question'][:24]}", flush=True)

    ok = [j for j in judged if j.get("faithfulness") is not None]
    errs = [j for j in judged if j.get("faithfulness") is None]
    refusals = [r for r in rows if r.get("refused")]

    def agg(js):
        n = len(js)
        if n == 0:
            return {"n": 0}
        return {"n": n,
                "faithfulness均分": round(sum(j["faithfulness"] for j in js) / n, 3),
                "relevance均分": round(sum(j["relevance"] for j in js) / n, 3),
                "完全忠实占比": round(
                    sum(not j["unsupported_claims"] for j in js) / n, 4)}

    by_route = defaultdict(list)
    for j in ok:
        by_route[j.get("route") or "direct"].append(j)

    summary = {"model": model, "judge_source": str(in_path.name),
               "判答卷数": len(ok), "拒答跳过": len(refusals),
               "解析失败": len(errs),
               "总体": agg(ok),
               **{f"route={k}": agg(v) for k, v in sorted(by_route.items())}}

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"{args.name}_faithfulness.json"
    out_path.write_text(json.dumps({"summary": summary, "rows": judged},
                                   ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"\n忠实度评测 -> {out_path}")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
