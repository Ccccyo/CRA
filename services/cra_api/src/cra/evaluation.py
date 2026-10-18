"""评测：跑考卷、算分、自动判断错在哪个环节，并把错题写进 eval/badcases.csv。

错误归因按流程从前往后找第一个出错的环节：
- 答疑：检索没召回 → 排序没进前 5 → 生成（误拒答 / 答案不全 / 该拒答没拒答）→ 出处校验
- 认公司：国家映射 → 没进候选 → 排序 → 硬选 / 误拒
- 财报抽取：未找到 → 数值错（单位 / 正负号 / 其他）
"""
import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from cra.config import get_settings

BADCASE_FIELDS = ["case_id", "module", "eval_id", "round", "input", "expected", "actual",
                  "stage", "category", "severity", "fix", "status"]


@dataclass
class ItemResult:
    eval_id: str
    passed: bool
    input: str
    expected: str
    actual: str
    stage: str = ""
    category: str = ""
    severity: str = ""
    metrics: Dict[str, float] = field(default_factory=dict)


def load_jsonl(path: Path, split: Optional[str] = None) -> List[dict]:
    items = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [it for it in items if split in (None, "all") or it.get("split") == split]


# ---------- 答疑 ----------

def eval_qa_item(item: dict, retriever, llm=None) -> ItemResult:
    from cra.qa.answer import answer_question

    q = item["question"]
    cands = retriever.candidates(q, 20)
    top5 = retriever.search(q, 5)
    gold = item.get("gold_source")
    in20 = bool(gold) and any(h.chunk.source == gold for h in cands)
    in5 = bool(gold) and any(h.chunk.source == gold for h in top5)
    metrics = {"recall@20": float(in20), "hit@5": float(in5)} if item["answerable"] else {}
    expected = ("应回答，包含：" + "、".join(item["must_include"])) if item["answerable"] else "应拒答"
    base = dict(eval_id=item["id"], input=q, expected=expected, metrics=metrics)

    if llm is None:  # 只测检索
        if not item["answerable"]:
            return ItemResult(passed=True, actual="（未调用模型）", **base)
        if not in20:
            return ItemResult(passed=False, actual="正确文档不在前 20", stage="检索", category="检索未召回",
                              severity="P1", **base)
        if not in5:
            return ItemResult(passed=False, actual="正确文档在前 20 但不在前 5", stage="排序",
                              category="排序靠后", severity="P1", **base)
        return ItemResult(passed=True, actual="检索命中", **base)

    res = answer_question(q, retriever, llm)
    actual = res.answer
    if not item["answerable"]:
        if res.found:
            return ItemResult(passed=False, actual=actual, stage="生成", category="该拒答未拒答",
                              severity="P0", **base)
        return ItemResult(passed=True, actual=actual, **base)

    if not in20:
        stage, cat, sev = "检索", "检索未召回", "P1"
    elif not in5:
        stage, cat, sev = "排序", "排序靠后", "P1"
    elif not res.found:
        stage, cat, sev = "生成", "误拒答", "P1"
    elif any(k not in actual for k in item["must_include"]):
        stage, cat, sev = "生成", "答案不全或答错", "P1"
    elif res.citation_report and res.citation_report.unsupported_numbers:
        stage, cat, sev = "出处校验", "数字无出处", "P0"
    elif res.citation_report and not res.citation_report.ok:
        stage, cat, sev = "出处校验", "引用缺失或编号错误", "P1"
    else:
        return ItemResult(passed=True, actual=actual, **base)
    return ItemResult(passed=False, actual=actual, stage=stage, category=cat, severity=sev, **base)


# ---------- 认公司 ----------

def eval_entity_item(item: dict, matcher) -> ItemResult:
    res = matcher.search(item["name"], item["country"], item.get("state", ""), item.get("city", ""))
    expected_id = item.get("expected_id")
    ids = [c.entity.entity_id for c in res.candidates]
    top = res.best
    actual = (f"{top.entity.entity_id} {top.entity.name}（{top.score}）" if top else "无候选") + \
             ("，判定无可信匹配" if res.no_good_match else "")
    base = dict(eval_id=item["id"], input=f"{item['name']} / {item['country']} {item.get('state', '')}",
                expected=expected_id or "无可信匹配", actual=actual)

    if expected_id is None:
        ok = res.no_good_match
        return ItemResult(passed=ok, metrics={"correct_reject": float(ok)},
                          **({} if ok else dict(stage="排序/阈值", category="硬选", severity="P0")), **base)

    metrics = {"top1": float(bool(ids) and ids[0] == expected_id), "top3": float(expected_id in ids[:3])}
    expected_country = next((e.country for e in matcher.entities if e.entity_id == expected_id), "")
    if res.effective_country != expected_country:
        stage, cat, sev = "国家映射", "映射遗漏", "P1"
    elif expected_id not in ids:
        stage, cat, sev = "召回", "没进候选", "P1"
    elif ids[0] != expected_id:
        stage, cat, sev = "排序", "选错同名/近似公司", "P0" if not res.no_good_match else "P1"
    elif res.no_good_match:
        stage, cat, sev = "阈值", "误拒", "P1"
    else:
        return ItemResult(passed=True, metrics=metrics, **base)
    return ItemResult(passed=False, metrics=metrics, stage=stage, category=cat, severity=sev, **base)


# ---------- 财报抽取 ----------

KEY_TOLERANCE_FIELDS = {"net_sales", "total_assets", "total_liabilities", "total_equity",
                        "net_income", "interest_expense"}


SIGN_FREE_FIELDS = {"capex", "interest_expense"}


def compare_extraction(gold: Dict[str, Dict[str, float]], got: Dict[str, Dict[str, float]],
                       eval_id: str) -> List[ItemResult]:
    """逐个数字比对。每个（期间, 科目）是一道题。"""
    results = []
    for period, fields in gold.items():
        for f, expected in fields.items():
            tol = 0.005 if f in KEY_TOLERANCE_FIELDS else 0.01
            actual = got.get(period, {}).get(f)
            if f in SIGN_FREE_FIELDS and actual is not None:  # 现金流量表上是括号，XBRL 是正数，只比大小
                expected, actual = abs(expected), abs(actual)
            base = dict(eval_id=f"{eval_id}:{period}:{f}", input=f"{period} {f}",
                        expected=f"{expected:,.0f}", actual="未找到" if actual is None else f"{actual:,.0f}")
            if actual is None:
                results.append(ItemResult(passed=False, stage="抽取", category="漏抽", severity="P1", **base))
                continue
            err = abs(actual - expected) / abs(expected) if expected else abs(actual)
            if err <= tol:
                results.append(ItemResult(passed=True, **base))
                continue
            ratio = abs(actual / expected) if expected else 0
            if actual == -expected:
                cat = "正负号错"
            elif any(abs(ratio - m) / m < 0.01 for m in (1e3, 1e4, 1e6, 1e-3, 1e-4, 1e-6)):
                cat = "单位错"
            else:
                cat = "数值错（科目对应/年份错位/报表口径）"
            sev = "P0" if f in KEY_TOLERANCE_FIELDS else "P1"
            results.append(ItemResult(passed=False, stage="抽取", category=cat, severity=sev, **base))
    return results


# ---------- 汇总与记录 ----------

def summarize(results: List[ItemResult]) -> Dict[str, float]:
    summary = {"n": len(results), "pass_rate": round(sum(r.passed for r in results) / max(len(results), 1), 3)}
    keys = {k for r in results for k in r.metrics}
    for k in sorted(keys):
        vals = [r.metrics[k] for r in results if k in r.metrics]
        summary[k] = round(sum(vals) / len(vals), 3)
    summary["P0"] = sum(1 for r in results if not r.passed and r.severity == "P0")
    return summary


def write_report(suite: str, round_id: str, split: str, results: List[ItemResult]) -> Path:
    reports = get_settings().reports_dir
    reports.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    detail = reports / f"{suite}_{round_id}_{split}_{stamp}.csv"
    with detail.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["eval_id", "passed", "input", "expected", "actual",
                                          "stage", "category", "severity", "metrics"])
        w.writeheader()
        for r in results:
            row = asdict(r)
            row["metrics"] = json.dumps(row["metrics"], ensure_ascii=False)
            w.writerow(row)

    summary_path = reports / "summary.csv"
    is_new = not summary_path.exists()
    with summary_path.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(["time", "round", "suite", "split", "summary"])
        w.writerow([stamp, round_id, suite, split, json.dumps(summarize(results), ensure_ascii=False)])
    return detail


def append_badcases(module: str, round_id: str, results: List[ItemResult]) -> int:
    """把本轮错题追加到 badcases.csv（同一轮同一题不重复记录）。"""
    path = get_settings().eval_dir / "badcases.csv"
    existing = []
    if path.exists():
        with path.open(encoding="utf-8-sig") as f:
            existing = list(csv.DictReader(f))
    seen = {(r["eval_id"], r["round"]) for r in existing}
    new_rows = []
    for r in results:
        if r.passed or (r.eval_id, round_id) in seen:
            continue
        new_rows.append({
            "case_id": f"BC-{len(existing) + len(new_rows) + 1:04d}", "module": module, "eval_id": r.eval_id,
            "round": round_id, "input": r.input, "expected": r.expected, "actual": r.actual[:300],
            "stage": r.stage, "category": r.category, "severity": r.severity, "fix": "", "status": "open",
        })
    if not new_rows:
        return 0
    if not path.exists() or path.stat().st_size == 0:
        with path.open("w", newline="", encoding="utf-8-sig") as f:  # 带 BOM，Excel 打开中文不乱码
            csv.DictWriter(f, fieldnames=BADCASE_FIELDS).writeheader()
    with path.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=BADCASE_FIELDS).writerows(new_rows)
    return len(new_rows)


def run_suite(items: List[dict], fn: Callable[[dict], ItemResult]) -> List[ItemResult]:
    return [fn(it) for it in items]
