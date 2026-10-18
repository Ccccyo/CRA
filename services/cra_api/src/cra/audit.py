"""签名与审计记录（SQLite：data/audit/audit.db）。

关键确认（如「本年度数字已核对」）不是点个勾选框，而是用 UID + 密码签名：
- 谁签的（UID、姓名、角色）、什么时候、签的是什么（声明文字）
- 签的是哪一版内容：内容指纹（SHA-256）+ 当时的数字快照；之后数字再变，签名自动失效，需要重签
- 密码只存加盐哈希（PBKDF2），不存明文；登录失败也记一条

演示用户在 data/auth/demo_users.csv（演示密码明文写在那里，仅供本地演示）。
真实环境应接公司统一登录（SSO / AD）：UID 来自登录，签名时再输一次密码确认身份。
"""
import csv
import hashlib
import json
import secrets
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from cra.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    uid TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
    salt TEXT NOT NULL, pin_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS signoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL, step TEXT NOT NULL,
    uid TEXT NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL, signed_at TEXT NOT NULL,
    meaning TEXT NOT NULL, content_hash TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, case_id TEXT, uid TEXT, event TEXT NOT NULL, detail TEXT
);
"""
ITERATIONS = 200_000


@dataclass
class User:
    uid: str
    name: str
    role: str


@dataclass
class Signoff:
    id: int
    case_id: str
    step: str
    uid: str
    name: str
    role: str
    signed_at: str
    meaning: str
    content_hash: str
    payload: Dict


def db_path() -> Path:
    return get_settings().data_dir / "audit" / "audit.db"


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def _hash(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), ITERATIONS).hex()


def add_user(uid: str, name: str, role: str, pin: str) -> None:
    salt = secrets.token_hex(16)
    with closing(_connect()) as con, con:
        con.execute("INSERT OR REPLACE INTO users (uid, name, role, salt, pin_hash, active) VALUES (?, ?, ?, ?, ?, 1)",
                    (uid.strip().upper(), name, role, salt, _hash(pin, salt)))


def seed_demo_users() -> int:
    """从 data/auth/demo_users.csv 导入演示用户（已存在的不覆盖）。返回新增人数。"""
    path = get_settings().data_dir / "auth" / "demo_users.csv"
    if not path.exists():
        return 0
    with closing(_connect()) as con:
        existing = {r[0] for r in con.execute("SELECT uid FROM users")}
    added = 0
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["uid"].upper() not in existing:
                add_user(r["uid"], r["name"], r["role"], r["pin"])
                added += 1
    return added


def log_event(event: str, case_id: Optional[str] = None, uid: Optional[str] = None, detail: str = "") -> None:
    with closing(_connect()) as con, con:
        con.execute("INSERT INTO events (ts, case_id, uid, event, detail) VALUES (?, ?, ?, ?, ?)",
                    (datetime.now().isoformat(timespec="seconds"), case_id, uid, event, detail))


def verify(uid: str, pin: str, case_id: Optional[str] = None) -> Optional[User]:
    """核对 UID + 密码；失败记一条事件（不记录输入的密码）。"""
    uid = (uid or "").strip().upper()
    with closing(_connect()) as con:
        row = con.execute("SELECT uid, name, role, salt, pin_hash, active FROM users WHERE uid = ?", (uid,)).fetchone()
    if row and row[5] and secrets.compare_digest(_hash(pin or "", row[3]), row[4]):
        return User(row[0], row[1], row[2])
    log_event("签名失败", case_id, uid or None, "UID 不存在或密码错误")
    return None


def content_hash(content: Dict) -> str:
    """签名内容的指纹：内容任何变化，指纹就变。"""
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def sign(case_id: str, step: str, user: User, meaning: str, content: Dict) -> Signoff:
    signed_at = datetime.now().isoformat(timespec="seconds")
    h = content_hash(content)
    with closing(_connect()) as con, con:
        cur = con.execute(
            "INSERT INTO signoffs (case_id, step, uid, name, role, signed_at, meaning, content_hash, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (case_id, step, user.uid, user.name, user.role, signed_at, meaning, h,
             json.dumps(content, ensure_ascii=False, default=str)))
        sid = cur.lastrowid
    log_event("签名", case_id, user.uid, f"{step}：{meaning}")
    return Signoff(sid, case_id, step, user.uid, user.name, user.role, signed_at, meaning, h, content)


def signoffs(case_id: str, step: Optional[str] = None) -> List[Signoff]:
    """某案件的签名记录，最新在前。"""
    sql = "SELECT id, case_id, step, uid, name, role, signed_at, meaning, content_hash, payload FROM signoffs WHERE case_id = ?"
    args = [case_id]
    if step:
        sql += " AND step = ?"
        args.append(step)
    with closing(_connect()) as con:
        rows = con.execute(sql + " ORDER BY id DESC", args).fetchall()
    return [Signoff(*r[:9], json.loads(r[9])) for r in rows]


def valid_signoff(case_id: str, step: str, content: Dict) -> Optional[Signoff]:
    """最近一次签名是否仍然有效（签的就是现在这版内容）。"""
    latest = next(iter(signoffs(case_id, step)), None)
    return latest if latest and latest.content_hash == content_hash(content) else None
