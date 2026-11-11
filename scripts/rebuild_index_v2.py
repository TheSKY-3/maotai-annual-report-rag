"""轴2重半：峰谷闲时一键重建索引并验收。

切块“/”过滤病根修复已提交（src/chunking.py）；本脚本重算 287 块语境、
重建索引、换入生产、复跑评测对比。半成品 baseline_v2.jsonl / progress_v2.jsonl
已备好（免费重切生成，data/chunks 被 gitignore）。

成本：语境重算 ~¥0.86（闲时）+ 答案评测 ~¥0.58（闲时）；检索评测免费。
安全闸：换入后先跑免费检索评测，hit@5 < 74% 自动回退、不花答案评测的钱。

用法（项目根目录，闲时）：
  .venv/Scripts/python.exe -X utf8 scripts/rebuild_index_v2.py
回退（验收不满意）：
  .venv/Scripts/python.exe -X utf8 scripts/rebuild_index_v2.py --revert
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "scripts"))
PY = sys.executable
CH = PROJECT / "data" / "chunks"
IX = PROJECT / "data" / "index"
HIT_FLOOR = 0.74  # 与 CI 门禁同阈值


def _env() -> dict:
    env = dict(os.environ)
    env["NO_PROXY"] = "localhost,127.0.0.1"  # 本机注册表代理会让 httpx 崩
    env.setdefault("RAG_BASE_URL", "https://api.deepseek.com/v1")
    env.setdefault("RAG_MODEL", "deepseek-flash")
    if not env.get("RAG_API_KEY"):
        for line in (PROJECT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                key = line.split("=", 1)[1].strip()
                env["RAG_API_KEY"] = key
                env["DEEPSEEK_API_KEY"] = key
    return env


def _run(args, env):
    print(">>", " ".join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], cwd=PROJECT, env=env, check=True)


def _swap(src_jsonl, src_npz, backup: bool):
    if backup:
        shutil.copy(CH / "contextual.jsonl", CH / "contextual.jsonl.bak")
        shutil.copy(IX / "contextual.npz", IX / "contextual.npz.bak")
    shutil.copy(src_jsonl, CH / "contextual.jsonl")
    shutil.copy(src_npz, IX / "contextual.npz")


def revert():
    for p in (CH / "contextual.jsonl", IX / "contextual.npz"):
        bak = Path(str(p) + ".bak")
        if bak.exists():
            shutil.copy(bak, p)
            print("已回退", p.name)
        else:
            print("无备份，跳过", p.name)


def _report():
    from build_holdout import clean_answer
    from evaluate_answers import gold_numbers
    gold = {json.loads(l)["id"]: json.loads(l)
            for l in (PROJECT / "data/gold/holdout.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()}

    def stats(name):
        rows = {r["id"]: r for r in json.loads(
            (PROJECT / f"results/{name}_answers.json").read_text(encoding="utf-8"))["rows"]}
        na = [i for i, q in gold.items() if q["type"] == "no_answer"]
        has = [i for i, q in gold.items() if q["type"] != "no_answer"]
        refusal = sum(bool(rows[i].get("refused")) for i in na)
        correct = 0
        for i in has:
            gn = gold_numbers(clean_answer(gold[i]["gold_answer"]))
            flat = rows[i]["answer"].replace(",", "").replace("，", "")
            correct += bool(gn) and all(n in flat for n in gn)
        return refusal, len(na), correct, len(has)

    print("\n=== 验收对比（修正口径）===")
    for name in ("holdout_axis2", "holdout_v2"):
        if (PROJECT / f"results/{name}_answers.json").exists():
            r, rn, c, cn = stats(name)
            print(f"{name:16s} 拒答 {r}/{rn} | 答对 {c}/{cn} = {c/cn:.2%}")
    print("合计目标题 id175/191/219 是否答对，见 results/holdout_v2_answers.json。")
    print("满意则保留 v2；不满意运行：python scripts/rebuild_index_v2.py --revert")


def main():
    if "--revert" in sys.argv:
        revert()
        return
    if not (CH / "baseline_v2.jsonl").exists():
        raise SystemExit("缺 baseline_v2.jsonl，请先免费重切准备（见 review/holdout_outcome.md）")
    env = _env()
    if "--yes" not in sys.argv:
        ans = input("将花费约 ¥1.4（闲时：语境重算¥0.86 + 答案评测¥0.58）重建索引，继续？[y/N] ")
        if ans.strip().lower() != "y":
            print("已取消。")
            return
    # 1. 重算语境 + 建 v2 索引（API）
    _run([PY, "scripts/build_contextual_chunks.py",
          "--input", CH / "baseline_v2.jsonl", "--out", CH / "contextual_v2.jsonl",
          "--index", IX / "contextual_v2.npz", "--progress", CH / "progress_v2.jsonl"], env)
    # 2. 备份生产索引 + 换入 v2
    _swap(CH / "contextual_v2.jsonl", IX / "contextual_v2.npz", backup=True)
    print("已换入 v2（旧索引备份为 .bak）", flush=True)
    # 3. 免费检索评测 + 安全闸
    _run([PY, "scripts/eval_holdout.py"], env)
    hit = json.loads((PROJECT / "results/holdout_retrieval.json").read_text(
        encoding="utf-8"))["summary"]["overall"]["page_hit@k"]
    print(f"\n留出集 hit@5 = {hit:.2%}（门禁 {HIT_FLOOR:.0%}）", flush=True)
    if hit < HIT_FLOOR:
        print("hit@5 低于门禁，自动回退，不跑答案评测。", flush=True)
        revert()
        return
    # 4. 答案评测（API）+ 对比
    _run([PY, "scripts/evaluate_answers.py", "--router",
          "--gold", "data/gold/holdout.jsonl", "--name", "holdout_v2"], env)
    _report()


if __name__ == "__main__":
    main()