"""模拟 D&B eRAM：Merge 把 DUNS 绑定到 Account，并模拟 Refresh 回填 FS_ 字段。

真实流程：PUT /REST/account?merge=true → 返回 200 只代表请求被接收 → eRAM 异步向 D&B 拉数据 → FS_ 字段落库。
这里同步完成并持久化到 data/eram_mock/accounts.json，方便在界面里看到「Merge 到哪个 Account、拉回了什么」。
FS_ 财务字段用该客户最近一年 SAP 导出数据代替 D&B Global Financials（演示用，数字为虚构）。
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from cra.casefile import Case, load_sap_export
from cra.config import get_settings
from cra.lookup.matcher import Entity

US_PROFILE = "US Fin Stress and Score with Global Financials"
GLOBAL_PROFILE = "Global eRAM and Global Financials"


def _store() -> Path:
    return get_settings().data_dir / "eram_mock" / "accounts.json"


def _load() -> Dict[str, dict]:
    p = _store()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save(accounts: Dict[str, dict]) -> None:
    p = _store()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")


def account_number(case: Case) -> str:
    return f"ER{case.bp}"


def product_profile(country: str) -> str:
    return US_PROFILE if country.upper() in {"US", "PR"} else GLOBAL_PROFILE


def get_account(account_nbr: str) -> Optional[dict]:
    return _load().get(account_nbr)


def list_accounts() -> List[dict]:
    return sorted(_load().values(), key=lambda a: a["merged_at"], reverse=True)


def merge_request(account: dict) -> dict:
    """真实 Merge 接口的请求体（演示中只展示，不发出）。"""
    return {"MergeAccountRequest": {
        "MandatoryFields": [{"Name": "ACCOUNT_ACCT_NBR", "Value": account["account_nbr"]},
                            {"Name": "REFRESH_CREDIT_LIMIT_FLAG", "Value": "Y"}],
        "OptionalFields": [{"Name": "ACCOUNT_DUNS_NBR", "Value": account["duns"]},
                           {"Name": "ACCOUNT_CTRY_CODE_CUST", "Value": account["country"]},
                           {"Name": "ACCOUNT_CUST_NME", "Value": account["name"]},
                           {"Name": "ACCOUNT_CRCY_CODE", "Value": account["currency"]},
                           {"Name": "ADDNL_API_PARAM_PROFILE_NAME", "Value": account["profile"]}],
    }}


def refresh(account_nbr: str) -> Optional[dict]:
    """模拟手动 Refresh：重新从 D&B 拉数据（演示中数据不变，只更新时间和日志）。"""
    accounts = _load()
    acct = accounts.get(account_nbr)
    if not acct:
        return None
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    acct["refreshed_at"] = now
    acct["log"] = (acct["log"] + [f"{now} 手动 Refresh 完成（模拟）"])[-10:]
    _save(accounts)
    return acct


def _fs_fields(case: Case, entity: Entity) -> Dict[str, object]:
    from cra.handoff import case_sap

    sap = load_sap_export(case.bp) or case_sap(case)  # 模拟 D&B 财报：用该客户最近一年数据代替
    latest = sap[max(sap)] if sap else {}
    fields = {
        "FS_STMT_DATE": max(sap) if sap else None, "FS_CURRENCY": case.statement_currency,
        "FS_SALES": latest.get("net_sales"), "FS_NET_INCOME": latest.get("net_income"),
        "FS_TOTAL_ASSETS": latest.get("total_assets"), "FS_TOTAL_LIAB": latest.get("total_liabilities"),
        "FS_NET_WORTH": latest.get("total_equity"),
        "PAYDEX": entity.paydex or None, "FAILURE_SCORE": entity.failure_score or None, "DNB_RATING": entity.rating or None,
    }
    return fields


def merge(case: Case, entity: Entity) -> dict:
    """绑定 DUNS 并模拟 Refresh。返回账户记录（含本次操作日志）。"""
    accounts = _load()
    nbr = account_number(case)
    old = accounts.get(nbr, {})
    log: List[str] = list(old.get("log", []))
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if old.get("duns") and old["duns"] != entity.entity_id:
        log.append(f"{now} 换绑：DUNS {old['duns']} → {entity.entity_id}")
    log.append(f"{now} PUT account?merge=true → 200 OK（请求已接收）")
    log.append(f"{now} Refresh 完成：已从 D&B 拉回 FS_ 字段（模拟）")
    accounts[nbr] = {
        "account_nbr": nbr, "bp": case.bp, "duns": entity.entity_id, "name": entity.name,
        "country": entity.country, "currency": case.statement_currency,
        "profile": product_profile(entity.country), "merged_at": now, "refreshed_at": now,
        "fields": _fs_fields(case, entity), "log": log[-10:],
    }
    _save(accounts)
    return accounts[nbr]


def verify(account: Optional[dict], expected_duns: str) -> List[tuple]:
    """Merge 返回 200 不等于成功：反查账户，逐项核验。返回 [(检查项, 是否通过, 说明)]。"""
    if not account:
        return [("账户存在", False, "eRAM 中查不到该账户")]
    f = account["fields"]
    fs_filled = [k for k in ("FS_SALES", "FS_TOTAL_ASSETS", "FS_NET_WORTH") if f.get(k) is not None]
    return [
        ("DUNS 已绑定到账户", account["duns"] == expected_duns, f"账户 DUNS：{account['duns']}"),
        ("FS_ 财务字段已回填", len(fs_filled) == 3,
         "已回填" if len(fs_filled) == 3 else f"仅回填 {fs_filled}，先检查 Product Profile 是否含财务数据"),
        ("Product Profile 正确", account["profile"] == product_profile(account["country"]), account["profile"]),
        ("D&B 评级", bool(f.get("DNB_RATING")), f.get("DNB_RATING") or "D&B 未提供评级，定性分该项按无评级处理"),
    ]


# eRAM 拉回的 D&B 财报字段 → CRA 科目（用于和新财报交叉核对）
FS_TO_FIELD = {"FS_SALES": "net_sales", "FS_NET_INCOME": "net_income", "FS_TOTAL_ASSETS": "total_assets",
               "FS_TOTAL_LIAB": "total_liabilities", "FS_NET_WORTH": "total_equity"}


def dnb_values(account: Optional[dict]) -> Dict[str, float]:
    f = (account or {}).get("fields", {})
    return {field: float(f[k]) for k, field in FS_TO_FIELD.items() if f.get(k) is not None}
