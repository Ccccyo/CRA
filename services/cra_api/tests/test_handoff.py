"""AI 交接包：Excel 宏导出的文件是 AI 拿到 SAP 数据的唯一途径。"""
import shutil

import pytest

from cra import casefile
from cra.handoff import SWITCH_MANUAL, SWITCH_YES, case_sap, import_package, read_package, write_package

CASE = {"BP": "1009901", "CustomerName": "Test Resins LLC", "Country": "US", "State": "TX", "City": "Houston",
        "StatementCurrency": "usd", "RequestedCL_USD": 250000, "CustomerDistinction": "Standard"}
SAP = {"2024-12-31": {"net_sales": 1e7, "total_assets": 8e6}, "2023-12-31": {"net_sales": 9e6, "total_assets": 7e6}}
YEARS = [("2025-12-31", SWITCH_MANUAL), ("2024-12-31", SWITCH_YES), ("2023-12-31", SWITCH_YES)]


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    shutil.copytree(casefile._data_dir() / "cases", tmp_path / "data" / "cases")
    monkeypatch.setattr(casefile, "_data_dir", lambda: tmp_path / "data")
    return tmp_path / "data"


def test_round_trip(tmp_path):
    p = write_package(tmp_path / "p.xlsx", CASE, YEARS, SAP, {("USD", "2024-12-31"): 1.0}, {"PayHabits": "Prompt"})
    pkg = read_package(p)
    assert pkg.errors == [] and pkg.manual_years == ["2025-12-31"] and pkg.sap_years == ["2024-12-31", "2023-12-31"]
    assert pkg.sap["2024-12-31"]["net_sales"] == 1e7 and pkg.ar["PayHabits"] == "Prompt"


def test_import_creates_case_and_sap_only_comes_from_package(tmp_path, data_dir):
    p = write_package(tmp_path / "p.xlsx", CASE, YEARS, SAP, {}, {"PayHabits": "Prompt"})
    case, created, errors = import_package(p)
    assert created and not errors and case.bp == "1009901" and case.statement_currency == "USD"
    assert case.new_period == "2025-12-31" and case.inputs["pay_habits_from_ar"] == "Prompt"
    assert case_sap(case) == SAP  # 往年数据来自交接包
    again, created2, _ = import_package(p)  # 同一个 BP 再导入：更新原案件，不重复建
    assert not created2 and again.case_id == case.case_id


def test_bad_package_is_rejected(tmp_path, data_dir):
    years = [("2024-12-31", SWITCH_YES)]  # 没有要补的年份
    _, _, errors = import_package(write_package(tmp_path / "bad.xlsx", CASE, years, {}, {}, {}))
    assert any("No - Enter Manually" in e for e in errors) and any("这一列没有数字" in e for e in errors)


def test_same_file_round_trip_after_ai_fill(tmp_path, data_dir):
    """导入和导出是同一个格式：AI 填完的文件再导入，照样能读。"""
    from openpyxl import load_workbook
    from cra.excel_mock import fill_ai_results, read_workbook
    from cra.extract.schema import ExtractedValue

    p = write_package(tmp_path / "cra.xlsx", CASE, YEARS, SAP, {("USD", "2024-12-31"): 1.0}, {"PayHabits": "Prompt"})
    wb = load_workbook(p)
    v = ExtractedValue(field="net_sales", period_end="2025-12-31", value=11, unit_multiplier=1e6, page=1, source_text="Sales 11")
    fill_ai_results(wb, [v], [], "2025-12-31", {}, "desc", "risk", "3A2", "fake", dnb_duns="900000101")
    wb.save(p)
    data = read_workbook(p)
    assert data["values"]["2025-12-31"]["net_sales"] == 11e6 and data["duns"] == "900000101"
    assert data["values"]["2024-12-31"]["net_sales"] == 1e7  # 宏导出的往年数据还在
    assert read_package(p).errors == []  # 填完的文件仍是合法的 CRA 文件
