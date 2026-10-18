from cra.validate.citations import check_answer, numbers_in, parse_number, source_text_on_page
from cra.validate.financial_checks import check_balance, check_magnitude, check_subtotals, run_all


def test_parse_number_handles_parentheses():
    assert parse_number("(1,234.5)") == -1234.5


def test_numbers_in_ignores_years_and_citation_ids():
    assert numbers_in("2024 年收入 1,234,000 [S1]") == {1234000.0}


def test_numbers_in_keeps_amounts_that_look_like_years():
    # 回归：1,900 带千分位，是金额不是年份（曾导致 CASE-001 无形资产误报）
    assert numbers_in("Goodwill and intangible assets  1,900  2,000") == {1900.0, 2000.0}


def test_check_answer_flags_unsupported_number_and_unknown_id():
    sources = {"S1": "营业收入 1,000,000 元"}
    report = check_answer("收入为 1,000,000 元 [S1]。利润为 500,000 元 [S9]。", sources)
    assert report.unknown_ids == ["S9"]
    assert report.unsupported_numbers == [500000.0]


def test_check_answer_checks_numbers_per_sentence():
    # 回归：15 只出现在 S2 里，不能被引用 S1 的句子借用
    sources = {"S1": "营业收入 1,240,000", "S2": "付款习惯 15 to 20 arrear"}
    report = check_answer("营业收入 1,240,000 [S1]。预计明年增长 15% [S1]。逾期 15 到 20 天 [S2]。", sources)
    assert report.unsupported_numbers == [15.0]


def test_check_answer_flags_uncited_sentence():
    report = check_answer("这家公司的杠杆偏高，需要关注。", {"S1": "x"})
    assert report.uncited_sentences


def test_source_text_on_page_ignores_whitespace():
    assert source_text_on_page("资产总计  5,678", "……\n资产总计 5,678\n……")
    assert not source_text_on_page("资产总计 9,999", "资产总计 5,678")


def test_balance_check():
    ok = {"2024-12-31": {"total_assets": 100.0, "total_liabilities": 60.0, "total_equity": 40.0}}
    bad = {"2024-12-31": {"total_assets": 100.0, "total_liabilities": 60.0, "total_equity": 30.0}}
    assert check_balance(ok) == []
    assert check_balance(bad)[0].level == "error"


def test_subtotal_and_magnitude_checks():
    values = {
        "2023-12-31": {"net_sales": 5_000_000.0, "total_current_assets": 10.0, "total_assets": 100.0},
        "2024-12-31": {"net_sales": 5_200.0, "total_current_assets": 120.0, "total_assets": 100.0},
    }
    assert any(f.field == "total_current_assets" for f in check_subtotals(values))
    assert any(f.field == "net_sales" and "单位" in f.message for f in check_magnitude(values))
    assert run_all(values)


def test_citation_after_full_stop_belongs_to_previous_sentence():
    # 回归 BC-0003：deepseek-reasoner 把引用写在句号后面，曾被误判为缺出处、数字对错材料
    sources = {"S1": "营业收入 1,240,000,000", "S2": "失败评分 70", "S3": "平均逾期 17 天，15 to 20 arrear"}
    text = "营业收入1,240,000,000 MXN。[S1] 失败评分70。[S2] 平均逾期17天，处于15 to 20 arrear。[S3]"
    assert check_answer(text, sources).ok


def test_whole_statement_jump_is_one_flag():
    # 回归：把别家公司的 10-K 传进案件，曾经出现 20 条「检查单位」；现在合并成一条
    prior = {f: 1e6 for f in ["net_sales", "total_assets", "total_equity", "cash", "inventories", "capex"]}
    new = {f: v * 150 for f, v in prior.items()}
    flags = check_magnitude({"2024-12-31": prior, "2025-12-31": new})
    assert len(flags) == 1 and flags[0].field == "整份报表" and "不是这个客户" in flags[0].message


def test_interest_and_capex_stored_positive_and_check_level():
    import json
    from cra.extract.extractor import check_level, extract_from_pages
    from cra.ingest.pdf_reader import Page
    from cra.llm import FakeLLM

    page = Page(1, "Interest expense, net (79) (79)\\nCapital expenditures (173) (184)")
    out = {"company": "X", "statement_scope": "consolidated", "values": [
        {"field": "interest_expense", "period_end": "2025-12-31", "value": -79, "unit_multiplier": 1e6, "page": 1,
         "source_text": "Interest expense, net (79) (79)", "confidence": "high"},
        {"field": "capex", "period_end": "2025-12-31", "value": -173, "unit_multiplier": 1e6, "page": 1,
         "source_text": "Capital expenditures (173) (184)", "confidence": "high"}]}
    rep = extract_from_pages([page], FakeLLM(json.dumps(out)))
    assert [v.value for v in rep.result.values] == [79, 173]  # 括号照抄成负数的，代码纠正为正数
    assert check_level(rep.result.values[0], rep.pages, []) == "高"
    fake = rep.result.values[0].model_copy(update={"source_text": "编造的原文 79"})
    assert check_level(fake, rep.pages, []) == "低"


def test_company_in_text_and_precheck():
    from pathlib import Path
    from cra.extract.extractor import company_in_text, precheck_company

    assert company_in_text("CELANESE CORPORATION AND SUBSIDIARIES", ["Celanese Corp"]) == "Celanese Corp"
    assert company_in_text("CELANESE CORPORATION", ["Northstar International Chemicals"]) is None  # 通用词不算
    assert company_in_text("浙江华澜新材料有限公司 合并资产负债表", ["华澜新材料"]) == "华澜新材料"
    found, _ = precheck_company(Path("data/filings/ce_10k_2025.pdf"), ["Northstar International Chemicals"])
    assert found is False  # 回归：把 Celanese 的 10-K 传进 Northstar 案件，调用 DeepSeek 之前就拦住


def test_total_liabilities_derived_when_not_on_statement():
    import json
    from cra.extract.extractor import check_level, extract_from_pages, is_derived
    from cra.ingest.pdf_reader import Page
    from cra.llm import FakeLLM

    page = Page(1, "Total assets 21,695\\nTotal equity 4,472")
    vals = [{"field": f, "period_end": "2025-12-31", "value": v, "unit_multiplier": 1e6, "page": 1,
             "source_text": t, "confidence": "high"} for f, v, t in
            [("total_assets", 21695, "Total assets 21,695"), ("total_equity", 4472, "Total equity 4,472")]]
    rep = extract_from_pages([page], FakeLLM(json.dumps({"company": "X", "values": vals})))
    tl = next(v for v in rep.result.values if v.field == "total_liabilities")
    assert is_derived(tl) and tl.value == 17_223e6 and check_level(tl, rep.pages, []) == "推算"
    assert not [f for f in rep.verify_flags if f.field == "total_liabilities"]  # 推算值不当成「原文找不到」


def test_prior_year_mismatch_is_error():
    from cra.validate.financial_checks import check_prior_year
    # 回归：人民币报表（别家公司）进了美元案件，2024 年收入 6.28 亿 vs SAP 12.5 亿
    stmt = {"2024-12-31": {"net_sales": 628e6, "total_assets": 820e6, "total_equity": 400e6}}
    sap = {"2024-12-31": {"net_sales": 1250e6, "total_assets": 1375e6, "total_equity": 756e6}}
    flags = check_prior_year(stmt, sap)
    assert len(flags) == 1 and flags[0].level == "error"
    restated = {"2024-12-31": {"net_sales": 1320e6, "total_assets": 1450e6, "total_equity": 756e6}}
    assert check_prior_year(restated, sap)[0].level == "warn"  # 小幅重述只提示
