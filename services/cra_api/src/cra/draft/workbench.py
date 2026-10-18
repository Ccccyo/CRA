"""Generate a reviewable risk opinion for the case workbench.

The original narrative agent remains the preferred path. A deterministic draft is
kept as a resilience fallback so a model outage never turns the analyst's review
screen into an empty state.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from cra.casefile import Case, build_case_facts, ratios
from cra.draft.narrative import (
    QUALITATIVE_OPTIONS,
    draft,
    own_history_only,
    policy_queries,
)
from cra.handoff import case_sap
from cra.lookup.matcher import Entity


def _selected_entity(notes: Dict[str, Any]) -> Optional[Entity]:
    raw = notes.get("selected_entity") or {}
    if not raw:
        return None
    fields = Entity.__dataclass_fields__
    return Entity(**{key: str(raw.get(key, "") or "") for key in fields})


def _values(case: Case, notes: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    values = {period: dict(items) for period, items in case_sap(case).items()}
    current: Dict[str, float] = {}
    for item in notes.get("financial_values", []):
        value = item.get("value")
        if value is None:
            continue
        multiplier = float(item.get("unit_multiplier") or 1)
        current[str(item.get("field"))] = float(value) * multiplier
    if current:
        values[case.new_period] = current
    return values


def _source_type(origin: str) -> Tuple[str, str]:
    if origin == "本案材料":
        return "case_evidence", "当前案件"
    if origin.startswith("公开资料"):
        return "public_reference", "公开"
    if origin.startswith("历史申报"):
        return "case_history", "高级分析师"
    return "policy", "信用分析师"


def _visible_sources(sources: Dict[str, str], origins: Dict[str, str]) -> List[Dict[str, str]]:
    result: List[Dict[str, str]] = []
    for source_id, text in sources.items():
        origin = origins.get(source_id, "本案材料")
        source_type, access_scope = _source_type(origin)
        first_line = text.splitlines()[0].strip() if text else source_id
        citation = f"本案 · {first_line}" if origin == "本案材料" else origin
        result.append(
            {
                "id": source_id,
                "citation": citation,
                "text": text[:900],
                "source_type": source_type,
                "access_scope": access_scope,
            }
        )
    return result


def _fmt(value: Optional[float], digits: int = 1) -> str:
    return "材料不足" if value is None else f"{value:,.{digits}f}"


def _fallback_qualitative(case: Case) -> Dict[str, Dict[str, str]]:
    inputs = case.inputs
    pay = inputs.get("pay_habits_from_ar", "")
    if pay not in QUALITATIVE_OPTIONS["pay_habits"]:
        pay = "No history"
    hint = inputs.get("company_type_hint", "").lower()
    company_type = (
        "Public listed"
        if "上市" in hint or "listed" in hint
        else "Private - audited"
        if "审计" in hint or "audited" in hint
        else "Private - unaudited"
    )
    country = case.customer.get("country", "")
    sovereign = "grade A or above" if country in {"US", "CA", "DE", "FR", "GB", "JP", "KR", "CN"} else "grade B"
    distinction = inputs.get("customer_distinction_from_sales", "")
    if distinction not in QUALITATIVE_OPTIONS["customer_distinction"]:
        distinction = "Standard"
    return {
        "pay_habits": {"choice": pay, "reason": f"SAP 应收记录：{inputs.get('pay_habits_detail') or '未提供'} [S3]"},
        "company_type": {"choice": company_type, "reason": f"公司类型线索：{inputs.get('company_type_hint') or '未提供'} [S3]"},
        "sovereign_rating": {"choice": sovereign, "reason": f"客户所在国家为 {country or '未提供'}，由分析师根据当期制度复核 [S3]"},
        "customer_distinction": {"choice": distinction, "reason": f"商务部门标注为 {distinction} [S3]"},
    }


def deterministic_risk_opinion(
    case: Case,
    notes: Dict[str, Any],
    retriever: Any,
) -> Dict[str, Any]:
    """Create a source-backed draft without an LLM when the model is unavailable."""
    entity = _selected_entity(notes)
    values = _values(case, notes)
    facts = build_case_facts(case, values, entity)
    sources: Dict[str, str] = {}
    origins: Dict[str, str] = {}
    for name, text in facts.items():
        source_id = f"S{len(sources) + 1}"
        sources[source_id] = f"{name}\n{text}"
        origins[source_id] = "本案材料"
    seen = set()
    for query in policy_queries(case, entity, values.get(case.new_period, {})):
        for hit in retriever.search(query, k=1, keep=own_history_only(case.bp)):
            if hit.chunk.id in seen:
                continue
            seen.add(hit.chunk.id)
            source_id = f"S{len(sources) + 1}"
            sources[source_id] = hit.chunk.text
            origins[source_id] = hit.chunk.citation
            if len(sources) >= 8:
                break
        if len(sources) >= 8:
            break

    current = values.get(case.new_period, {})
    metrics = ratios(current) if current else {}
    interest_cover = metrics.get("利息保障倍数")
    leverage = metrics.get("资产负债率(%)")
    margin = metrics.get("营业利润率(%)")
    current_ratio = metrics.get("流动比率")
    tangible_net_worth = None
    if current.get("total_equity") is not None:
        tangible_net_worth = (
            current["total_equity"]
            - (current.get("intangible_assets") or 0)
            - (current.get("goodwill") or 0)
        )
    pay_detail = case.inputs.get("pay_habits_detail") or "材料不足"
    sovereign_hint = case.inputs.get("sovereign_hint") or ""
    sovereign = (
        f"客户所在国家为 {case.customer.get('country') or '未提供'}；{sovereign_hint}"
        if sovereign_hint
        else f"客户所在国家为 {case.customer.get('country') or '未提供'}"
    )
    demo_warning = (notes.get("financial_source") or {}).get("warning")

    company = case.customer.get("name", "未命名客户")
    location = " ".join(filter(None, [case.customer.get("city"), case.customer.get("state"), case.customer.get("country")]))
    entity_text = f"，D&B 编号 {entity.entity_id}，评级 {entity.rating or '未提供'}" if entity else "，D&B 主体待确认"
    description = f"{company}位于{location or '未提供地区'}，业务为{case.business}{entity_text}。[S2][S3]"

    financial_risk = (
        f"流动比率 {_fmt(current_ratio, 2)}，利息保障倍数 {_fmt(interest_cover, 1)}，"
        f"资产负债率 {_fmt(leverage, 1)}%，营业利润率 {_fmt(margin, 1)}%，"
        f"有形净资产 {_fmt(tangible_net_worth, 0)} {case.statement_currency}。[S1]"
        if current
        else "偿债能力、杠杆和盈利能力缺少可签署的当期财务材料。[S1]"
    )
    warning_line = f"\n3. {demo_warning}" if demo_warning else ""
    comments = (
        f"【初步意见】有条件支持：申请额度 {case.requested_cl_usd:,.0f} 美元，"
        "需在核对当期财务证据、D&B 主体和适用制度后由有权人审批。[S3]\n"
        f"【主要风险】1. {financial_risk}\n"
        f"2. 国家风险线索为“{sovereign}”，需按当期分类和公司制度复核。[S3]{warning_line}\n"
        f"【缓释因素】付款记录：{pay_detail}。[S3] 经营现金流为 {_fmt(current.get('operating_cash_flow'), 0)} {case.statement_currency}。[S1]\n"
        "【建议条件】确认原始报表、评分规则版本和最终额度；如申请额度高于 CRA 建议额度，记录偏离理由并按制度升级审批。"
    )
    return {
        "draft": {
            "company_description": description,
            "stance": "有条件支持",
            "risk_comments": comments,
            "qualitative": _fallback_qualitative(case),
            "draft_mode": "deterministic_fallback",
            "edited_by": "Agent",
        },
        "sources": _visible_sources(sources, origins),
    }


def generate_risk_opinion(
    case: Case,
    notes: Dict[str, Any],
    retriever: Any,
    llm: Any = None,
) -> Dict[str, Any]:
    """Use the original narrative agent, with an auditable fallback on failure."""
    fallback = deterministic_risk_opinion(case, notes, retriever)
    if llm is None:
        return fallback
    try:
        entity = _selected_entity(notes)
        values = _values(case, notes)
        case_facts = build_case_facts(case, values, entity)
        result = draft(
            case_facts,
            retriever,
            llm,
            queries=policy_queries(case, entity, values.get(case.new_period, {})),
            keep=own_history_only(case.bp),
        )
        if not result.ok:
            fallback["draft"]["generation_warning"] = "模型草稿未通过引用或用语校验，已使用规则型草稿。"
            return fallback
        output = result.output
        return {
            "draft": {
                "company_description": output.company_description,
                "stance": output.stance or "有条件支持",
                "risk_comments": output.risk_comments,
                "qualitative": {
                    key: {
                        "choice": getattr(output.qualitative, key).choice,
                        "reason": getattr(output.qualitative, key).reason,
                    }
                    for key in QUALITATIVE_OPTIONS
                },
                "draft_mode": "llm",
                "edited_by": "Agent",
            },
            "sources": _visible_sources(result.sources, result.origins),
        }
    except Exception as exc:  # the review flow must remain usable during model outages
        fallback["draft"]["generation_warning"] = f"模型起草暂时不可用，已使用规则型草稿：{type(exc).__name__}"
        return fallback
