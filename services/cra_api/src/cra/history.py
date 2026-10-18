"""历史 CRA 申报记录（模拟）：以往每次评审的分数、风险等级、建议额度和批准结果，供分析师参考。

真实场景来自 SAP CRA 历史记录或 SCASE 审批单；AI 只读，用于对比「这次和上次相比变了什么」。
"""
import csv
from typing import Dict, List, Optional

from cra.config import get_settings


def load_history(bp: Optional[str] = None) -> List[Dict[str, str]]:
    path = get_settings().data_dir / "history" / "cra_submissions_mock.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows = [r for r in rows if bp is None or r["bp"] == bp]
    return sorted(rows, key=lambda r: r["review_date"], reverse=True)


def history_text(bp: str) -> str:
    """给模型看的历史摘要（一行一次评审）。"""
    rows = load_history(bp)
    if not rows:
        return "该客户没有历史 CRA 申报记录（新客户）。"
    return "\n".join(
        f"{r['review_date']} {r['review_type']}（财报期 {r['statement_period']}）：财务分 {r['financial_score']}，"
        f"定性分 {r['qualitative_score']}，总分 {r['overall_score']}，风险等级 {r['risk_class']}；"
        f"D&B 评级 {r['dnb_rating'] or '无'}，付款习惯 {r['pay_habits']}；额度方法 {r['method']}；"
        f"申请 {float(r['requested_cl_usd']):,.0f} 美元，建议 {float(r['suggested_cl_usd']):,.0f}，"
        f"批准 {float(r['approved_cl_usd']):,.0f}（{r['approver']}）。备注：{r['comment']}"
        for r in rows)


def history_by_duns(duns: str) -> List[Dict[str, str]]:
    return [r for r in load_history() if duns and r["duns"] == duns]


HISTORY_SOURCE = "历史申报/cra_submissions_mock.csv"


def history_chunks():
    """历史申报进检索库：一次申报一段（一条记录本身就是完整的语义单元，不再切分）。

    段落开头写客户、国家、日期、类型，方便按客户名或「逾期」「例外审批」这类词检索到相似案例。
    """
    from cra.ingest.chunker import Chunk

    chunks = []
    for r in load_history():
        section = f"{r['bp']} {r['customer']} > {r['review_date']} {r['review_type']}"
        text = (f"{section}\n"
                f"历史案例：{r['customer']}（BP {r['bp']}，国家 {r['country']}，币种 {r['currency']}，"
                f"DUNS {r['duns'] or '未确认'}）。{r['review_date']} {r['review_type']}，财报期 {r['statement_period']}。"
                f"D&B 评级 {r['dnb_rating'] or '无'}；付款习惯 {r['pay_habits']}；公司类型 {r['company_type']}；"
                f"国家风险档位 {r['sovereign_rating']}；战略重要性 {r['customer_distinction']}。"
                f"财务分 {r['financial_score']}，定性分 {r['qualitative_score']}，总分 {r['overall_score']}，"
                f"风险等级 {r['risk_class']}；额度方法 {r['method']}。"
                f"申请 {float(r['requested_cl_usd']):,.0f} 美元，建议 {float(r['suggested_cl_usd']):,.0f}，"
                f"批准 {float(r['approved_cl_usd']):,.0f}；审批：{r['approver']}"
                f"{'；例外审批' if r['exception'] == 'Y' else ''}。备注：{r['comment']}")
        chunks.append(Chunk(id=f"history#{r['bp']}-{r['review_date']}", text=text,
                            source=HISTORY_SOURCE, section=section))
    return chunks
