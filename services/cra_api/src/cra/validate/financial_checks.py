"""财报规则检查：不靠大模型，用会计恒等式和常识拦住抄错的数字。

输入统一为 {期间: {科目: 数值}}，数值已乘过单位（如「千元」已换算成元）。
"""
from typing import Dict, List, Optional

from cra.validate import Flag

Values = Dict[str, Dict[str, float]]

BALANCE_TOLERANCE = 0.005  # 允许 0.5% 的舍入误差
MAGNITUDE_JUMP = 20.0  # 同一科目相邻年份差 20 倍以上，多半是单位错
WHOLE_STATEMENT_JUMPS = 5  # 5 个以上科目同时跳变，合并成一条「整份报表对不上」

# (较小项, 较大项)：较小项不应超过较大项
SUBTOTAL_RULES = [
    ("total_current_assets", "total_assets"),
    ("total_current_liabilities", "total_liabilities"),
    ("intangible_assets", "total_assets"),
    ("goodwill", "total_assets"),
    ("cash", "total_current_assets"),
    ("accounts_receivable", "total_current_assets"),
    ("inventories", "total_current_assets"),
]
MUST_BE_POSITIVE = ["net_sales", "total_assets", "total_current_assets"]


def check_balance(values: Values) -> List[Flag]:
    flags = []
    for period, v in values.items():
        if not {"total_assets", "total_liabilities", "total_equity"} <= v.keys():
            continue
        assets, rhs = v["total_assets"], v["total_liabilities"] + v["total_equity"]
        if assets and abs(assets - rhs) / abs(assets) > BALANCE_TOLERANCE:
            flags.append(Flag("error", "total_assets",
                              f"{period}：资产 {assets:,.0f} ≠ 负债 + 权益 {rhs:,.0f}"))
    return flags


def check_subtotals(values: Values) -> List[Flag]:
    flags = []
    for period, v in values.items():
        for small, big in SUBTOTAL_RULES:
            if small in v and big in v and v[small] > v[big] * (1 + BALANCE_TOLERANCE):
                flags.append(Flag("error", small, f"{period}：{small} {v[small]:,.0f} 大于 {big} {v[big]:,.0f}"))
    return flags


def check_signs(values: Values) -> List[Flag]:
    return [
        Flag("warn", f, f"{period}：{f} 不应为负数或零（{v[f]:,.0f}），检查括号负数或科目对应")
        for period, v in values.items() for f in MUST_BE_POSITIVE if f in v and v[f] <= 0
    ]


def check_magnitude(values: Values, reference: Optional[Values] = None) -> List[Flag]:
    """相邻年份（或与参考数据，如 D&B 财报）比较，数量级跳变提示单位可能错了。"""
    flags = []
    periods = sorted(values)
    pairs = [(values[a], values[b], f"{a}→{b}") for a, b in zip(periods, periods[1:])]
    if reference:
        pairs += [(reference[p], values[p], f"{p} 与参考数据") for p in periods if p in reference]
    for left, right, label in pairs:
        jumps = {}
        for f in left.keys() & right.keys():
            a, b = abs(left[f]), abs(right[f])
            if a and b and max(a, b) / min(a, b) >= MAGNITUDE_JUMP:
                jumps[f] = max(a, b) / min(a, b)
        if len(jumps) >= WHOLE_STATEMENT_JUMPS:  # 大多数科目一起跳：不是个别抄错，是整份报表对不上
            ratio = sorted(jumps.values())[len(jumps) // 2]
            flags.append(Flag("warn", "整份报表", f"{label}：{len(jumps)} 个科目都和往年差几十倍以上（中位数 {ratio:.0f} 倍）。"
                                                 f"很可能上传的不是这个客户的财报，或者整份报表单位错了（千 / 百万），请核对公司名称和表头单位"))
        else:
            flags += [Flag("warn", f, f"{label}：{f} 和往年差 {r:.0f} 倍，可能单位错了（千 / 百万）或抄错了科目")
                      for f, r in jumps.items()]
    return flags


def check_reference(values: Dict[str, float], reference: Dict[str, float], label: str) -> List[Flag]:
    """和外部参考数据（如 D&B 拉回的财报）比数量级：期间可以不同，只看是不是同一个量级的公司。"""
    jumps = {f: max(abs(values[f]), abs(reference[f])) / min(abs(values[f]), abs(reference[f]))
             for f in values.keys() & reference.keys() if values[f] and reference[f]}
    jumps = {f: r for f, r in jumps.items() if r >= MAGNITUDE_JUMP}
    if len(jumps) >= 3:
        return [Flag("warn", "D&B 对比", f"和 {label} 相比，{len(jumps)} 个科目差几十倍以上："
                                          f"上传的财报和认定的 D&B 公司可能不是同一家，或单位错了")]
    return [Flag("warn", f, f"和 {label} 相比差 {r:.0f} 倍，核对单位或科目") for f, r in jumps.items()]


PRIOR_YEAR_FIELDS = ["net_sales", "total_assets", "total_equity", "net_income", "total_current_assets"]


def check_prior_year(statement: Values, sap: Values) -> List[Flag]:
    """新财报上的上年数 vs SAP 里同一年的数：同一家公司、同一币种，应该基本一致（重述会有小差异）。

    坏例子：别家公司（人民币报表）的财报传进美元案件，2024 年营业收入报表上是 6.28 亿、SAP 里是 12.5 亿。
    """
    flags = []
    for period in sorted(statement.keys() & sap.keys()):
        diffs = {f: abs(statement[period][f] - sap[period][f]) / abs(sap[period][f])
                 for f in PRIOR_YEAR_FIELDS if statement[period].get(f) and sap[period].get(f)}
        big = {f: d for f, d in diffs.items() if d > 0.05}
        if len(big) < 2:
            continue
        worst = sorted(big.values())[len(big) // 2]
        detail = "、".join(f"{f} 差 {d:.0%}" for f, d in big.items())
        if worst > 0.3:
            flags.append(Flag("error", "上年数对不上", f"报表上的 {period} 数字和 SAP 里同一年对不上（{detail}）："
                                                       "很可能不是同一家公司，或币种 / 单位不同"))
        else:
            flags.append(Flag("warn", "上年数对不上", f"报表上的 {period} 数字和 SAP 里同一年有差异（{detail}）："
                                                      "可能是报表重述，请在意见里说明"))
    return flags


def run_all(values: Values, reference: Optional[Values] = None) -> List[Flag]:
    return check_balance(values) + check_subtotals(values) + check_signs(values) + check_magnitude(values, reference)
