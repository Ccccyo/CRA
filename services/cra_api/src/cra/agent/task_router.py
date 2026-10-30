"""Observable, bounded plans for case questions.

The business workflow remains a deterministic state machine. This planner may
adapt read-only evidence gathering, but cannot authorize or execute approvals.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from cra.external.gleif_mcp import requested_lei


POLICY_TERMS = re.compile(r"政策|制度|审批|权限|复审|升级|授信规则|policy", re.I)
FORMAL_AUTHORITY_TERMS = re.compile(r"审批权限|谁能批|谁能批准|批准额度|正式授信政策|授信制度")
PROPOSAL_TERMS = re.compile(r"拟议|风控方法|评估流程|风险评估|证据规范|需要哪些资料|怎么核验|如何核验|如何分析")
LIVE_AR_TERMS = re.compile(r"当前敞口|实时敞口|未清应收|最新逾期余额|实时逾期|现在欠款")
FORBIDDEN_ACTION_TERMS = re.compile(
    r"帮我批准|直接批准|替我审批|跳过.{0,6}签核|给我管理员权限|授予.{0,6}权限"
)
REVIEW_DATE = re.compile(
    r"(?P<year>20\d{2})[-年/](?P<month>\d{1,2})[-月/](?P<day>\d{1,2})日?"
)


@dataclass(frozen=True)
class CaseTaskFrame:
    case_id: str
    workflow_status: str
    stage: str
    entity_confirmed: bool
    financial_evidence_present: bool
    risk_draft_present: bool


@dataclass(frozen=True)
class ReadPlan:
    intent: str
    steps: tuple[str, ...]
    libraries: frozenset[str]
    external_lei: str = ""
    blocker: str = ""


def task_frame(case_id: str, workflow: Mapping[str, Any], notes: Mapping[str, Any]) -> CaseTaskFrame:
    """Rebuild working memory from persisted case state on every request."""
    return CaseTaskFrame(
        case_id=case_id,
        workflow_status=str(workflow.get("status") or "unknown"),
        stage=str(workflow.get("current_stage") or "unknown"),
        entity_confirmed=bool(notes.get("selected_entity")),
        financial_evidence_present=bool(notes.get("financial_values")),
        risk_draft_present=bool(notes.get("risk_draft")),
    )


def explicit_review_date(question: str) -> str:
    """Find an explicit review date, not a nearby financial-statement date."""
    for match in REVIEW_DATE.finditer(question):
        before = question[max(0, match.start() - 8):match.start()]
        after = question[match.end():match.end() + 5]
        if not (
            re.search(r"(?:评审|复审)(?:日期?|时间)?[：:，、在于\s]{0,4}$", before)
            or re.match(r"(?:当日|进行)?(?:评审|复审)", after)
        ):
            continue
        try:
            return date(
                int(match.group("year")), int(match.group("month")),
                int(match.group("day")),
            ).isoformat()
        except ValueError:
            continue
    return ""


def review_date_for_question(question: str) -> str:
    return explicit_review_date(question) or datetime.now(
        ZoneInfo("Asia/Shanghai")
    ).date().isoformat()


def plan_case_question(question: str, frame: CaseTaskFrame) -> ReadPlan:
    """Select at most two read sources, then evidence-check; no open-ended loop."""
    if FORBIDDEN_ACTION_TERMS.search(question):
        return ReadPlan(
            intent="approval_or_permission_action", steps=(), libraries=frozenset(),
            blocker="不能代替有权人员批准授信、跳过人工签核或改变权限；可以整理证据供人工评审。",
        )
    if LIVE_AR_TERMS.search(question):
        return ReadPlan(
            intent="live_ar_request", steps=(), libraries=frozenset(),
            blocker="当前 SAP/AR 实时敞口接口未接入；不能用历史或静态 RAG 代替。",
        )
    if (
        POLICY_TERMS.search(question)
        and re.search(r"去年|当时|历史版本|旧版|过去", question)
        and not explicit_review_date(question)
    ):
        return ReadPlan(
            intent="policy_date_clarification", steps=(), libraries=frozenset(),
            blocker="请提供要核对的评审日期；历史制度须按当时有效版本判断。",
        )
    lei = requested_lei(question)
    if lei:
        return ReadPlan(
            intent="external_entity_reference",
            steps=("gleif_exact_lei", "case_evidence", "answer_validation"),
            libraries=frozenset({"public_reference"}),
            external_lei=lei,
        )
    if FORMAL_AUTHORITY_TERMS.search(question):
        return ReadPlan(
            intent="policy_explanation",
            steps=("policy_retrieval", "case_evidence", "answer_validation"),
            libraries=frozenset({"policy"}),
        )
    if PROPOSAL_TERMS.search(question):
        return ReadPlan(
            intent="risk_method_explanation",
            steps=("proposed_method_retrieval", "case_evidence", "answer_validation"),
            libraries=frozenset({"proposed_rule", "evidence_spec"}),
        )
    if POLICY_TERMS.search(question):
        return ReadPlan(
            intent="policy_explanation",
            steps=("policy_retrieval", "case_evidence", "answer_validation"),
            libraries=frozenset({"policy"}),
        )
    return ReadPlan(
        intent="case_analysis",
        steps=("case_evidence", "authorized_reference_retrieval", "answer_validation"),
        libraries=frozenset({"policy", "public_reference", "case_history", "proposed_rule", "evidence_spec"}),
    )
