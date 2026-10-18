import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from cra_api import main
from cra_api.store import WorkflowStore
from cra.draft.workbench import deterministic_risk_opinion
from cra.excel_mock import read_workbook
from cra.extract.extractor import ExtractionReport
from cra.extract.schema import ExtractedValue, ExtractionResult
from cra.ingest.pdf_reader import Page


def isolated_store(tmp_path: Path, monkeypatch) -> WorkflowStore:
    store = WorkflowStore(tmp_path / "workbench.db")
    monkeypatch.setattr(main, "store", store)
    main.ensure_seed_states()
    return store


def test_entity_review_exposes_materials_and_persists_selection(tmp_path, monkeypatch):
    store = isolated_store(tmp_path, monkeypatch)

    detail = main.case_detail("CASE-002")
    candidates = detail["notes"]["entity_candidates"]
    assert candidates
    assert {"entity_id", "name", "address", "rating", "paydex", "score"} <= set(
        candidates[0]
    )

    task = detail["human_tasks"][0]
    resolved = main.resolve_human_task(
        "CASE-002",
        task["id"],
        main.ResolveTask(
            resolution={"selected_entity_id": candidates[0]["entity_id"]}
        ),
    )
    assert resolved["notes"]["selected_entity"]["entity_id"] == candidates[0]["entity_id"]
    assert store.workflow("CASE-002")["current_stage"] == "financial_evidence"


def test_seed_case_without_financial_values_is_blocked_before_signoff(tmp_path, monkeypatch):
    store = isolated_store(tmp_path, monkeypatch)

    detail = main.case_detail("CASE-004")

    assert detail["workflow"]["status"] == "blocked"
    assert detail["workflow"]["current_stage"] == "financial_evidence"
    assert detail["notes"].get("financial_values") is None
    assert not any(
        task["task_type"] == "financial_signoff"
        for task in detail["human_tasks"]
    )
    assert any(
        bad_case["failure_type"] == "当期财务证据缺失"
        for bad_case in detail["bad_cases"]
    )


def test_case_list_uses_lightweight_database_summary(tmp_path, monkeypatch):
    isolated_store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        main,
        "ensure_seed_states",
        lambda: (_ for _ in ()).throw(AssertionError("GET 不应重复初始化种子数据")),
    )

    rows = main.cases()

    assert rows
    assert set(rows[0]) == {
        "case_id",
        "title",
        "bp",
        "requested_cl_usd",
        "customer",
        "workflow",
    }
    assert rows[0]["workflow"]["current_stage"]
    assert rows[0]["workflow"]["total_stages"] > 0


def test_upload_financial_report_extracts_and_queues_signoff(tmp_path, monkeypatch):
    store = isolated_store(tmp_path, monkeypatch)
    reports = tmp_path / "financial-reports"
    reports.mkdir()
    monkeypatch.setattr(main, "FINANCIAL_REPORTS", reports)
    case = main.find_case("CASE-004")
    extracted = ExtractedValue(
        field="cash",
        period_end=case.new_period,
        value=2900,
        unit_multiplier=1000,
        currency="USD",
        page=4,
        source_text="Cash and cash equivalents 2,900",
        confidence="high",
    )
    report = ExtractionReport(
        result=ExtractionResult(
            company=case.customer["name"],
            statement_scope="consolidated",
            values=[extracted],
        ),
        pages=[Page(number=4, text="Cash and cash equivalents 2,900")],
    )
    monkeypatch.setattr(main, "extract_from_pdf", lambda path, llm: report)
    monkeypatch.setattr("cra.llm.get_llm", lambda: SimpleNamespace())

    result = asyncio.run(
        main.upload_financial_report(
            case.case_id,
            UploadFile(filename="Northstar FY2026.pdf", file=io.BytesIO(b"%PDF-1.7\nmock")),
        )
    )

    assert result["workflow"]["current_stage"] == "financial_signoff"
    assert result["workflow"]["status"] == "awaiting_human"
    assert result["notes"]["financial_values"][0]["field"] == "cash"
    source = result["notes"]["financial_source"]
    assert source["type"] == "report"
    assert source["filename"] == "Northstar FY2026.pdf"
    assert source["pages_used"] == [4]
    assert len(source["checksum"]) == 64
    assert (reports / source["stored_filename"]).is_file()
    assert main.find_case(case.case_id).new_year["pdf"] == "Northstar FY2026.pdf"


def test_upload_financial_report_rejects_non_pdf_content(tmp_path, monkeypatch):
    isolated_store(tmp_path, monkeypatch)

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            main.upload_financial_report(
                "CASE-004",
                UploadFile(filename="report.pdf", file=io.BytesIO(b"not-a-pdf")),
            )
        )

    assert error.value.status_code == 422
    assert "有效的 PDF" in error.value.detail


def test_financial_evidence_is_downloadable_as_traceable_csv(tmp_path, monkeypatch):
    store = isolated_store(tmp_path, monkeypatch)
    store.set_notes(
        "CASE-001",
        {
            "financial_values": [
                {
                    "field": "cash",
                    "period_end": "2025-12-31",
                    "value": 2900,
                    "unit_multiplier": 1000,
                    "currency": "USD",
                    "page": 41,
                    "confidence": "high",
                    "source_text": "Cash and cash equivalents 2,900",
                }
            ]
        },
    )

    response = main.download_financial_evidence("CASE-001")
    body = response.body.decode("utf-8-sig")
    assert "字段代码,字段名称" in body
    assert "cash,货币资金" in body
    assert "Cash and cash equivalents 2,900" in body


def test_rag_result_includes_visible_source_and_access_metadata(tmp_path, monkeypatch):
    store = isolated_store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        main,
        "generate_risk_opinion",
        lambda case, notes, retriever, llm: deterministic_risk_opinion(
            case, notes, retriever
        ),
    )
    store.update_workflow(
        "CASE-001",
        status="running",
        current_stage="retrieve_context",
        current_task="检索当前规则、行业与历史案件",
    )

    detail = main.run_agent("CASE-001", main.AgentAction(), "A1001")
    sources = detail["notes"]["risk_sources"]
    draft = detail["notes"]["risk_draft"]
    assert sources
    assert all(source.get("source_type") and source.get("access_scope") for source in sources)
    assert draft["company_description"] and draft["risk_comments"]
    assert set(draft["qualitative"]) == {
        "pay_habits",
        "company_type",
        "sovereign_rating",
        "customer_distinction",
    }
    assert detail["workflow"]["current_stage"] == "risk_signoff"


def test_imported_case_generates_a_downloadable_workbook(tmp_path, monkeypatch):
    store = isolated_store(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "EXPORTS", tmp_path / "exports")
    main.EXPORTS.mkdir()
    store.update_workflow(
        "CASE-005",
        status="running",
        current_stage="write_workbook",
        current_task="生成 CRA Excel 新版本",
    )
    store.set_notes(
        "CASE-005",
        {
            "risk_draft": {
                "company_description": "Northstar 是特种化学品生产商。[S1]",
                "risk_comments": "【初步意见】有条件支持。[S1]",
                "qualitative": {
                    "pay_habits": {"choice": "Prompt", "reason": "按期付款 [S1]"},
                    "company_type": {"choice": "Public listed", "reason": "上市公司 [S1]"},
                    "sovereign_rating": {"choice": "grade A or above", "reason": "美国 [S1]"},
                    "customer_distinction": {"choice": "Strategic", "reason": "战略客户 [S1]"},
                },
            },
            "selected_entity": {"entity_id": "900000301", "rating": "5A1"},
        },
    )

    result = main.run_agent("CASE-005", main.AgentAction())
    export_path = Path(result["notes"]["export_path"])
    assert export_path.is_file()
    assert result["workflow"]["current_stage"] == "final_signoff"
    assert result["notes"]["export_file_name"] == "CASE-005_CRA_review.xlsx"
    workbook = read_workbook(export_path)
    assert workbook["desc"].startswith("Northstar")
    assert "有条件支持" in workbook["risk"]
    assert workbook["qual"]["company_type"] == "Public listed"
    assert workbook["rating"] == "5A1"
    assert workbook["duns"] == "900000301"


def test_analyst_can_edit_risk_opinion_and_preserve_versions(tmp_path, monkeypatch):
    store = isolated_store(tmp_path, monkeypatch)
    store.set_notes(
        "CASE-001",
        {
            "risk_draft": {
                "company_description": "原公司介绍",
                "risk_comments": "原风险意见",
                "qualitative": {},
                "updated_at": "2026-10-15T00:00:00+00:00",
            }
        },
    )
    result = main.update_risk_opinion(
        "CASE-001",
        main.RiskOpinionUpdate(
            company_description="分析师修改的公司介绍",
            risk_comments="分析师修改的风险意见 [S1]",
            stance="有条件支持",
            qualitative={
                "pay_habits": {"choice": "Prompt", "reason": "按期付款 [S1]"}
            },
        ),
        "A1001",
    )
    assert result["notes"]["risk_draft"]["draft_mode"] == "analyst_edited"
    assert result["notes"]["risk_draft"]["company_description"] == "分析师修改的公司介绍"
    assert len(result["notes"]["risk_opinion_versions"]) == 1


def test_missing_export_returns_404_instead_of_directory_error(tmp_path, monkeypatch):
    isolated_store(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as error:
        main.export_case("CASE-005")
    assert error.value.status_code == 404


def test_knowledge_catalog_filters_content_by_role():
    analyst = main.visible_knowledge("A1001")
    libraries = {item["id"]: item for item in analyst["libraries"]}
    assert libraries["policy"]["accessible"] is True
    assert libraries["policy"]["documents"]
    assert libraries["policy"]["documents"][0]["chunks"]
    assert libraries["policy"]["documents"][0]["chunks"][0]["text"]
    assert libraries["public_reference"]["accessible"] is True
    assert libraries["case_history"]["accessible"] is False
    assert libraries["case_history"]["document_count"] > 0
    assert libraries["case_history"]["documents"] == []

    senior = main.visible_knowledge("A1002")
    senior_history = next(
        item for item in senior["libraries"] if item["id"] == "case_history"
    )
    assert senior_history["accessible"] is True
    assert senior_history["documents"]
    assert senior_history["documents"][0]["chunks"]


def test_unknown_user_cannot_read_knowledge_catalog():
    with pytest.raises(HTTPException) as error:
        main.visible_knowledge("UNKNOWN")
    assert error.value.status_code == 401


def test_authenticated_database_user_can_read_role_scoped_knowledge():
    result = main.visible_knowledge(
        "user-from-postgres",
        "Li%20Ming",
        "senior_credit_analyst",
    )
    assert result["principal"] == {
        "user_id": "user-from-postgres",
        "name": "Li Ming",
        "role": "senior_credit_analyst",
    }
    history = next(
        item for item in result["libraries"] if item["id"] == "case_history"
    )
    assert history["accessible"] is True


@pytest.mark.parametrize("role", ["admin", "super_admin"])
def test_admin_roles_can_read_every_knowledge_library(role):
    result = main.visible_knowledge(
        f"{role}-from-postgres",
        "CRA%20Admin",
        role,
    )
    assert result["granted_library_count"] == len(result["libraries"])
    assert all(library["accessible"] for library in result["libraries"])
    assert all(library["documents"] for library in result["libraries"])


def test_retriever_excludes_libraries_outside_the_actor_role():
    analyst = main.retriever_for(
        {"user_id": "analyst", "name": "Analyst", "role": "credit_analyst"}
    )
    admin = main.retriever_for(
        {"user_id": "admin", "name": "Admin", "role": "admin"}
    )

    assert not any(
        chunk.citation.startswith("历史申报/")
        for chunk in analyst.keyword.chunks
    )
    assert any(
        chunk.citation.startswith("历史申报/")
        for chunk in admin.keyword.chunks
    )


def test_admin_can_upload_edit_and_delete_chunked_knowledge(tmp_path, monkeypatch):
    store = isolated_store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(cra_api_shared_secret="test-service-secret"),
    )
    client = TestClient(main.app)
    admin_headers = {
        "X-CRA-Service-Secret": "test-service-secret",
        "X-CRA-User": "admin-from-postgres",
        "X-CRA-Name": "CRA%20Admin",
        "X-CRA-Role": "admin",
    }

    uploaded = client.post(
        "/knowledge/documents",
        headers=admin_headers,
        data={"library_id": "policy", "title": "信用政策补充规则"},
        files={
            "file": (
                "credit-policy.md",
                "# 额度规则\n\nUNIQUE-RAG-TERM 需要人工复核。\n\n# 例外\n\n例外需要记录原因。",
                "text/markdown",
            )
        },
    )
    assert uploaded.status_code == 201
    created = uploaded.json()
    assert created["chunk_count"] == 2
    assert created["source_filename"] == "credit-policy.md"
    assert any(
        "UNIQUE-RAG-TERM" in hit.chunk.text
        for hit in main.retriever_for(
            {"user_id": "admin", "name": "Admin", "role": "admin"}
        ).search("UNIQUE-RAG-TERM")
    )

    updated = client.put(
        f"/knowledge/documents/{created['id']}",
        headers=admin_headers,
        json={
            "library_id": "public_reference",
            "title": "公开资料补充说明",
            "content": "# 公开来源\n\nUPDATED-RAG-TERM 仅用作外部参考。",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["library_id"] == "public_reference"
    assert updated.json()["chunk_count"] == 1

    deleted = client.delete(
        f"/knowledge/documents/{created['id']}", headers=admin_headers
    )
    assert deleted.status_code == 200
    assert store.knowledge_document(created["id"]) is None


def test_non_admin_cannot_modify_knowledge(tmp_path, monkeypatch):
    isolated_store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(cra_api_shared_secret="test-service-secret"),
    )
    response = TestClient(main.app).post(
        "/knowledge/documents",
        headers={
            "X-CRA-Service-Secret": "test-service-secret",
            "X-CRA-User": "analyst-from-postgres",
            "X-CRA-Name": "Analyst",
            "X-CRA-Role": "credit_analyst",
        },
        data={"library_id": "policy", "title": "越权文档"},
        files={"file": ("blocked.md", "不应写入", "text/markdown")},
    )

    assert response.status_code == 403


def test_stage_transition_persists_a_history_snapshot(tmp_path):
    store = WorkflowStore(tmp_path / "workbench.db")
    store.upsert_case(
        {
            "case_id": "CASE-HISTORY",
            "title": "History test",
            "bp": "100",
            "credit_segment": "0001",
            "requested_cl_usd": 10,
            "statement_currency": "USD",
            "customer": {"name": "History Co"},
            "new_year": {"period_end": "2026-12-31"},
            "inputs": {},
        }
    )
    store.ensure_case("CASE-HISTORY", stage="resolve_entity")
    store.set_notes(
        "CASE-HISTORY", {"selected_entity": {"entity_id": "DUNS-1"}}
    )

    store.update_workflow(
        "CASE-HISTORY",
        current_stage="financial_evidence",
        current_task="抽取财务证据",
    )

    snapshots = store.stage_snapshots("CASE-HISTORY")
    assert len(snapshots) == 1
    assert snapshots[0]["stage"] == "resolve_entity"
    assert snapshots[0]["payload"]["notes"]["selected_entity"]["entity_id"] == "DUNS-1"


def test_api_requires_internal_service_secret(monkeypatch):
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(cra_api_shared_secret="test-service-secret"),
    )
    client = TestClient(main.app)

    blocked = client.get("/knowledge")
    assert blocked.status_code == 401

    allowed = client.get(
        "/knowledge",
        headers={
            "X-CRA-Service-Secret": "test-service-secret",
            "X-CRA-User": "A1001",
        },
    )
    assert allowed.status_code == 200


def test_api_fails_closed_when_internal_secret_is_missing(monkeypatch):
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(cra_api_shared_secret=""),
    )
    client = TestClient(main.app)

    response = client.get("/knowledge", headers={"X-CRA-User": "A1001"})

    assert response.status_code == 503
    assert response.json()["detail"] == "CRA_API_SHARED_SECRET 尚未配置"
