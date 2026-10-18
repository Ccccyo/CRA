"""行业对标：把客户的比率放到化工同行（SEC XBRL，公开数据）的分布里看。

同行数据由 scripts/build_public_knowledge.py 生成到 data/knowledge/chemicals_sec_ratios.csv。
只作参考，不参与打分。
"""
import csv
import statistics
from typing import Dict, List, Optional, Tuple

from cra.config import get_settings

RATIOS = ["流动比率", "资产负债率(%)", "总负债/权益", "利息保障倍数", "营业利润率(%)", "资产回报率(%)", "经营现金流/总负债(%)"]
LOWER_IS_BETTER = {"资产负债率(%)", "总负债/权益"}


def ratios_of(v: Dict[str, float]) -> Dict[str, Optional[float]]:
    def div(a, b, scale=1.0):
        return round(a / b * scale, 2) if a is not None and b else None

    tl = v.get("total_liabilities")
    if tl is None and v.get("total_assets") is not None and v.get("total_equity") is not None:
        tl = v["total_assets"] - v["total_equity"]  # 报表没列总负债时由代码推算
    return {
        "流动比率": div(v.get("total_current_assets"), v.get("total_current_liabilities")),
        "资产负债率(%)": div(tl, v.get("total_assets"), 100),
        "总负债/权益": div(tl, v.get("total_equity")),
        "利息保障倍数": div(v.get("operating_income"), v.get("interest_expense")),
        "营业利润率(%)": div(v.get("operating_income"), v.get("net_sales"), 100),
        "资产回报率(%)": div(v.get("net_income"), v.get("total_assets"), 100),
        "经营现金流/总负债(%)": div(v.get("operating_cash_flow"), tl, 100),
    }


def load_peers() -> List[Dict[str, str]]:
    path = get_settings().data_dir / "knowledge" / "chemicals_sec_ratios.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def quartiles(peers: List[Dict[str, str]]) -> Dict[str, Tuple[float, float, float, int]]:
    out = {}
    for r in RATIOS:
        xs = sorted(float(p[r]) for p in peers if p.get(r))
        if len(xs) >= 4:
            q = statistics.quantiles(xs, n=4)
            out[r] = (round(q[0], 2), round(q[1], 2), round(q[2], 2), len(xs))
    return out


def position(x: float, q: Tuple[float, float, float, int]) -> str:
    if x < q[0]:
        return "低于 P25"
    if x < q[1]:
        return "P25–中位数"
    if x <= q[2]:
        return "中位数–P75"
    return "高于 P75"


def compare_text(v: Dict[str, float], label: str = "") -> str:
    """给模型和分析师看的对标说明，一行一个比率。"""
    qs = quartiles(load_peers())
    if not qs:
        return "没有行业对标数据（先运行 scripts/build_public_knowledge.py）。"
    lines = []
    for name, x in ratios_of(v).items():
        if x is None or name not in qs:
            continue
        q = qs[name]
        lines.append(f"{name} {x:,.2f}：{position(x, q)}（化工同行 P25 {q[0]}、中位数 {q[1]}、P75 {q[2]}，"
                     f"{'越低越好' if name in LOWER_IS_BETTER else '越高越好'}）")
    head = f"{label}与 {len(load_peers())} 家美国上市化工公司（SEC XBRL，FY2025）对比；同行为大型生产商，2025 年行业下行、多家计提减值。"
    return head + "\n" + "\n".join(lines)
