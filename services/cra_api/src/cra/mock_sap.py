"""模拟 Run Macro：演示中代替 VBA + SAP CRA，算出分数、风险等级和建议额度。

⚠ 规则全部是演示用的假设（与 data/policy/cra_scoring_rules_mock.md 的权重一致，分档阈值为编造），
不是真实 SAP CRA 模型。真实流程中这些结果由 SAP 计算并回填。分数越高风险越高（1 最好，6 最差）。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from cra.draft.narrative import QUALITATIVE_OPTIONS

QUAL_SCORES = {"pay_habits": [1, 2, 4, 5, 3], "company_type": [1, 2, 3, 6],
               "sovereign_rating": [1, 3, 6], "customer_distinction": [2, 3, 4, 6]}
QUAL_WEIGHTS = {"pay_habits": 0.10, "company_type": 0.10, "sovereign_rating": 0.20,
                "customer_distinction": 0.20, "corporate_rating": 0.40}
RISK_CLASSES = [(1.5, "00 Minimal"), (2.5, "01 Low"), (3.5, "02 Low to Moderate"), (4.5, "03 Moderate"),
                (5.5, "04 High"), (99, "05 Very High")]
CL_PCT = {"00": 0.10, "01": 0.075, "02": 0.05, "03": 0.025, "04": 0.01, "05": 0.0}


def _band(x: Optional[float], cutoffs: List[float], higher_is_better: bool = True) -> int:
    """按阈值分 1–6 档：higher_is_better 时 x ≥ cutoffs[0] 得 1 分，依次递增。"""
    if x is None:
        return 4
    for i, c in enumerate(cutoffs, 1):
        if (x >= c) if higher_is_better else (x <= c):
            return i
    return 6


def _div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return a / b if a is not None and b else None


def rating_score(rating: Optional[str]) -> int:
    """D&B 评级（如 3A2）最后一位是综合信用（1 最好 4 最差）→ 分数；无评级记 4。"""
    if rating and rating[-1].isdigit():
        return {1: 1, 2: 2, 3: 4, 4: 5}.get(int(rating[-1]), 4)
    return 4


@dataclass
class MacroResult:
    financial: float
    qualitative: float
    overall: float
    risk_class: str
    suggested_cl: float
    approval: str
    breakdown: List[Dict[str, object]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def as_cells(self) -> Dict[str, object]:
        return {"financial": self.financial, "qualitative": self.qualitative, "overall": self.overall,
                "risk_class": self.risk_class, "suggested_cl": round(self.suggested_cl), "approval": self.approval}


def approval_level(requested: float, suggested: float) -> str:
    levels = ["区域信用主管", "区域信用经理", "全球信用总监"]
    i = 0 if requested <= 250_000 else (1 if requested <= 1_000_000 else 2)
    if requested > suggested:
        i = min(i + 1, 2)
        return f"{levels[i]}（申请额度高于建议额度，提高一级）"
    return levels[i]


def run_macro(v: Dict[str, float], qual: Dict[str, Optional[str]], rating: Optional[str], method: str,
              requested_usd: float, fx_rate: float = 1.0) -> MacroResult:
    """v：本年度数字（报表币种）；fx_rate：1 美元兑多少报表币种。"""
    notes: List[str] = []
    rows: List[Tuple[str, str, Optional[float], int]] = []

    def add(group: str, name: str, value: Optional[float], score: int) -> int:
        rows.append((group, name, value, score))
        return score

    ic = _div(v.get("operating_income"), v.get("interest_expense"))
    ocf_tl = _div(v.get("operating_cash_flow"), v.get("total_liabilities"))
    liq = (add("LIQ 41%", "利息保障倍数", ic, _band(ic, [8, 5, 3, 2, 1])) +
           add("LIQ 41%", "经营现金流 / 总负债 (%)", ocf_tl and ocf_tl * 100, _band(ocf_tl and ocf_tl * 100, [20, 15, 10, 5, 0.01]))) / 2
    de = _div(v.get("total_liabilities"), v.get("total_equity"))
    lvg = add("LVG 19%", "总负债 / 权益", de, _band(de, [0.8, 1.2, 1.6, 2.2, 3.0], higher_is_better=False))
    om = _div(v.get("operating_income"), v.get("net_sales"))
    roa = _div(v.get("net_income"), v.get("total_assets"))
    prof = (add("PROF 37%", "营业利润率 (%)", om and om * 100, _band(om and om * 100, [12, 8, 5, 3, 0.01])) +
            add("PROF 37%", "资产回报率 (%)", roa and roa * 100, _band(roa and roa * 100, [8, 5, 3, 1, 0.01]))) / 2
    ct = qual.get("company_type") or ""
    quality = add("Quality 3%", "财报质量（是否审计）", None, 4 if "unaudited" in ct.lower() else 1)
    financial = round(0.41 * liq + 0.19 * lvg + 0.37 * prof + 0.03 * quality, 2)

    qual_score = 0.0
    for key, opts in QUALITATIVE_OPTIONS.items():
        choice = qual.get(key)
        if choice in opts:
            s = QUAL_SCORES[key][opts.index(choice)]
        else:
            s = 6
            notes.append(f"{key} 未选择，按最差 6 分计")
        qual_score += QUAL_WEIGHTS[key] * add("定性 30%", key, None, s)
    qual_score += QUAL_WEIGHTS["corporate_rating"] * add("定性 30%", f"D&B 评级 {rating or '无'}", None, rating_score(rating))
    qualitative = round(qual_score, 2)

    overall = round(0.7 * financial + 0.3 * qualitative, 2)
    risk_class = next(label for limit, label in RISK_CLASSES if overall < limit)
    equity = v.get("total_equity") or 0.0
    intangibles = (v.get("intangible_assets") or 0.0) + (v.get("goodwill") or 0.0)  # 有形净资产要同时扣掉商誉
    base = equity - intangibles if method.startswith("A") else equity
    if method.startswith("A") and base <= 0:
        notes.append("有形净资产为负，按政策不能用方法 A，已改用 C. % Net Worth")
        base = equity
    suggested = max(base, 0) * CL_PCT[risk_class[:2]] / (fx_rate or 1.0)
    return MacroResult(financial, qualitative, overall, risk_class, suggested,
                       approval_level(requested_usd, suggested),
                       [{"分组": g, "指标": n, "数值": None if x is None else round(x, 2), "得分": s} for g, n, x, s in rows],
                       notes)
