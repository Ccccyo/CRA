from types import SimpleNamespace

from cra import countries


def _settings(data_dir):
    return SimpleNamespace(data_dir=data_dir)


def test_oecd_unclassified_is_not_treated_as_lowest_risk(tmp_path, monkeypatch):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "country_risk_oecd.csv").write_text(
        "iso3,country,previous,current,notes,cra_sovereign_choice\n"
        "USA,United States,-,-,,grade A or above\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("cra.config.get_settings", lambda: _settings(tmp_path))

    text = countries.oecd_risk_text("US")

    assert "未分类（不是类别 0" in text
    assert "不能据此推定为最低风险" in text
    assert "内部 Sovereign Rating 须按已批准政策另行判断" in text
    assert "grade A or above" not in text


def test_oecd_classification_keeps_original_category_without_internal_mapping(tmp_path, monkeypatch):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "country_risk_oecd.csv").write_text(
        "iso3,country,previous,current,notes,cra_sovereign_choice\n"
        "MEX,Mexico,3,3,,grade B\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("cra.config.get_settings", lambda: _settings(tmp_path))

    text = countries.oecd_risk_text("MX")

    assert "第 3 类（0 最低、7 最高）" in text
    assert "内部 Sovereign Rating 须按已批准政策另行判断" in text
    assert "grade B" not in text
