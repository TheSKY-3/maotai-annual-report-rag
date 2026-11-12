"""轴4 批量核验：对已落盘答案生成坐标级高亮图与清单（零 API、零网络）。

用法:
  .venv/Scripts/python.exe -X utf8 scripts/make_citation_crops.py \
      --answers results/holdout_v3_answers.json --ids 175,191,219
产物: docs/citations/*.png（gitignore）+ results/citation_crops.json
"""
import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from src import cite_locate  # noqa: E402
from src.config import CONTEXTUAL_CHUNKS_FILE, RESULTS_DIR  # noqa: E402


def load_chunk_texts() -> dict[str, str]:
    lines = CONTEXTUAL_CHUNKS_FILE.read_text(encoding="utf-8").splitlines()
    return {r["id"]: r["text"] for r in map(json.loads, lines) if r.get("id")}


def build_manifest(rows: list[dict], texts: dict[str, str], out_dir):
    entries: list[dict] = []
    n_answers = 0
    for r in rows:
        if r.get("refused") or not r.get("answer"):
            continue
        n_answers += 1
        evidence = " ".join(texts.get(i, "") for i in r.get("hit_ids", []))
        for e in cite_locate.verify_citations(
                r["answer"], evidence, out_dir, f"id{r['id']}"):
            e["id"] = r["id"]
            e["question"] = r.get("question", "")
            entries.append(e)
    located = [e for e in entries if e["located"]]
    summary = {
        "answers": n_answers,
        "numbers": len(entries),
        "located": len(located),
        "ambiguous": sum(1 for e in located if e["ambiguous"]),
        "locate_rate": round(len(located) / len(entries), 4) if entries else None,
    }
    return summary, entries


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--answers", default="results/holdout_v3_answers.json")
    ap.add_argument("--ids", default="", help="逗号分隔题号，空=全部")
    ap.add_argument("--out", default="docs/citations")
    args = ap.parse_args()

    rows = json.loads((PROJECT / args.answers).read_text(encoding="utf-8"))["rows"]
    if args.ids:
        wanted = {int(x) for x in args.ids.split(",")}
        rows = [r for r in rows if r["id"] in wanted]

    summary, entries = build_manifest(rows, load_chunk_texts(), PROJECT / args.out)
    (RESULTS_DIR / "citation_crops.json").write_text(
        json.dumps({"summary": summary, "entries": entries},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    for e in entries:
        if not e["located"]:
            print(f"  未定位 id{e['id']} {e['number']}")
        elif e["ambiguous"]:
            print(f"  歧义整组高亮 id{e['id']} {e['number']}")


if __name__ == "__main__":
    main()