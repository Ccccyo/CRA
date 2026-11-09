"""国家代码 → 中文名：检索时中英文都带上（OECD 表是英文国名，用户和案件多用中文）。"""
COUNTRY_ZH = {
    "US": "美国", "CN": "中国", "MX": "墨西哥", "FR": "法国", "DE": "德国", "GB": "英国", "CA": "加拿大",
    "AR": "阿根廷", "CL": "智利", "BR": "巴西", "IN": "印度", "VN": "越南", "TR": "土耳其", "JP": "日本", "KR": "韩国",
    "FI": "芬兰",
}

ISO3 = {"US": "USA", "CN": "CHN", "MX": "MEX", "FR": "FRA", "DE": "DEU", "GB": "GBR", "CA": "CAN", "AR": "ARG",
        "CL": "CHL", "BR": "BRA", "IN": "IND", "VN": "VNM", "TR": "TUR", "JP": "JPN", "KR": "KOR"}


def oecd_risk_text(country: str, mapping=None) -> str:
    """返回 OECD 原始国家风险分类线索，不把它自动换算成公司的内部评级。"""
    import csv

    from cra.config import get_settings

    code = (mapping or {}).get(country.upper(), country.upper())
    path = get_settings().data_dir / "knowledge" / "country_risk_oecd.csv"
    iso3 = ISO3.get(code)
    if not path.exists() or not iso3:
        return f"{country}：OECD 国家风险分类表里没有收录，材料不足"
    with path.open(encoding="utf-8") as f:
        row = next((r for r in csv.DictReader(f) if r["iso3"] == iso3), None)
    if not row:
        return f"{country}：OECD 国家风险分类表里没有收录，材料不足"
    via = f"（属地 {country} 按替换表换成 {code}）" if code != country.upper() else ""
    if row["current"] == "-":
        cat = "未分类（不是类别 0，不能据此推定为最低风险）"
    else:
        cat = f"第 {row['current']} 类（0 最低、7 最高）"
    return (f"{COUNTRY_ZH.get(code, '')}{row['country']}{via}：OECD 国家风险分类 {cat}"
            "（2026-06-26 快照）；该分类用于官方支持出口信贷，"
            "仅作国家风险线索，内部 Sovereign Rating 须按已批准政策另行判断")
