"""案件数据：把「SAP 导出的往年财报」和「AI 从 PDF 抽取的新年度」合到一起，并整理成写意见用的案件事实。

数据边界：SAP 数据只来自宏导出的文件（data/sap_mock/ 模拟），这里只读不写。
"""
import csv
import json
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from cra.config import get_settings
from cra.lookup.matcher import Entity
from cra.validate.financial_checks import Values


@dataclass
class Case:
    case_id: str
    title: str
    bp: str
    credit_segment: str
    requested_cl_usd: float
    statement_currency: str
    customer: Dict[str, str]
    expected_entity_id: str
    business: str
    new_year: Dict[str, object]
    inputs: Dict[str, str]
    mock: bool = True
    package: Optional[str] = None  # 导入的交接包（相对项目目录）；往年财报和汇率只从这里来

    @property
    def new_period(self) -> str:
        return str(self.new_year["period_end"])


def _data_dir() -> Path:
    return get_settings().data_dir


def load_cases() -> List[Case]:
    fields = set(Case.__dataclass_fields__)
    return [Case(**{k: v for k, v in json.loads(p.read_text(encoding="utf-8")).items() if k in fields})
            for p in sorted((_data_dir() / "cases").glob("CASE-*.json"))]


def next_case_id() -> str:
    n = max([int(c.case_id.split("-")[1]) for c in load_cases()] + [0]) + 1
    return f"CASE-{n:03d}"


def case_for_entity(entity_id: str) -> Optional[Case]:
    """这家公司是否已经有案件（按案件里预期的 DUNS）。"""
    return next((c for c in load_cases() if c.expected_entity_id == entity_id), None)


def load_sap_export(bp: str) -> Values:
    """模拟宏导出的往年财报（FS.xlsx）：{期间: {科目: 数值}}。"""
    out: Values = {}
    with (_data_dir() / "sap_mock" / "fs_export.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["bp"] == bp:
                out.setdefault(row["period_end"], {})[row["field"]] = float(row["value"])
    return out


def load_fx() -> Dict[Tuple[str, str], float]:
    """模拟 TCURR：{(币种, 日期): 1 美元兑多少该币种}。"""
    with (_data_dir() / "sap_mock" / "tcurr.csv").open(encoding="utf-8") as f:
        return {(r["from_currency"], r["valid_date"]): float(r["units_per_usd"]) for r in csv.DictReader(f)}


def to_usd(values: Values, currency: str, fx: Dict[Tuple[str, str], float]) -> Tuple[Values, List[str]]:
    """按期末汇率换算成美元；缺汇率的期间原样跳过并返回提示。"""
    out: Values = {}
    missing = []
    for period, fields in values.items():
        rate = fx.get((currency, period))
        if rate is None:
            missing.append(f"{period} 缺少 {currency} 汇率")
            continue
        out[period] = {k: v / rate for k, v in fields.items()}
    return out, missing


def merge_years(sap_values: Values, new_values: Values) -> Values:
    merged = {p: dict(v) for p, v in sap_values.items()}
    for p, v in new_values.items():
        merged.setdefault(p, {}).update(v)
    return dict(sorted(merged.items(), reverse=True))


def ratios(v: Dict[str, float]) -> Dict[str, Optional[float]]:
    def div(a: str, b: str, scale: float = 1.0) -> Optional[float]:
        return v[a] / v[b] * scale if v.get(a) is not None and v.get(b) else None

    return {
        "营业利润率(%)": div("operating_income", "net_sales", 100),
        "利息保障倍数": div("operating_income", "interest_expense"),
        "资产负债率(%)": div("total_liabilities", "total_assets", 100),
        "流动比率": div("total_current_assets", "total_current_liabilities"),
    }


def _fmt(x: Optional[float], digits: int = 0) -> str:
    return "无数据" if x is None else f"{x:,.{digits}f}"


def build_case_facts(case: Case, values: Values, entity: Optional[Entity]) -> "OrderedDict[str, str]":
    """写意见用的编号材料。顺序固定：S1 财报摘要、S2 D&B 信息、S3 付款与商务信息。"""
    lines = []
    for period, v in sorted(values.items(), reverse=True):
        r = ratios(v)
        tangible_net_worth = None
        if v.get("total_equity") is not None:
            tangible_net_worth = (
                v["total_equity"]
                - (v.get("intangible_assets") or 0)
                - (v.get("goodwill") or 0)
            )
        lines.append(
            f"{period}（{case.statement_currency}）：营业收入 {_fmt(v.get('net_sales'))}；"
            f"营业利润 {_fmt(v.get('operating_income'))}；净利润 {_fmt(v.get('net_income'))}；"
            f"利息费用 {_fmt(v.get('interest_expense'))}；总资产 {_fmt(v.get('total_assets'))}；"
            f"总负债 {_fmt(v.get('total_liabilities'))}；所有者权益 {_fmt(v.get('total_equity'))}；"
            f"无形资产 {_fmt(v.get('intangible_assets'))}；商誉 {_fmt(v.get('goodwill'))}；"
            f"有形净资产 {_fmt(tangible_net_worth)}；"
            f"经营现金流 {_fmt(v.get('operating_cash_flow'))}；"
            f"营业利润率 {_fmt(r['营业利润率(%)'], 1)}%；利息保障倍数 {_fmt(r['利息保障倍数'], 1)}；"
            f"资产负债率 {_fmt(r['资产负债率(%)'], 1)}%；流动比率 {_fmt(r['流动比率'], 2)}"
        )
    if entity:
        dnb = (f"D&B（模拟）编号 {entity.entity_id}，名称 {entity.name}，所在地 {entity.city} {entity.country}；"
               f"Paydex {entity.paydex or '未提供'}；失败评分 {entity.failure_score or '未提供'}；"
               f"评级 {entity.rating or '未提供'}")
    else:
        dnb = "尚未确认 D&B 匹配公司"
    i = case.inputs
    commercial = (f"客户：{case.customer.get('name', '未提供')}；所在地："
                  f"{case.customer.get('city', '')} {case.customer.get('state', '')} {case.customer.get('country', '')}。"
                  f"业务：{case.business}。付款习惯（SAP 应收）：{i['pay_habits_from_ar']}，{i['pay_habits_detail']}。"
                  f"客户战略重要性（商务部门）：{i['customer_distinction_from_sales']}。公司类型线索：{i['company_type_hint']}。"
                  f"国家风险：{i['sovereign_hint']}。申请额度 {case.requested_cl_usd:,.0f} 美元。")
    return OrderedDict([("财报摘要", "\n".join(lines)), ("D&B 信息（模拟）", dnb), ("付款与商务信息", commercial)])


# ---------- 离线示例（没有 API key 时演示用，模型输出为预先录制） ----------

def sample_path(kind: str, case_id: str) -> Path:
    return _data_dir() / "samples" / f"{kind}_{case_id}.json"


def load_sample(kind: str, case_id: str) -> Optional[dict]:
    p = sample_path(kind, case_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
