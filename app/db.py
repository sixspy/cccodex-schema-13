import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import Config


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or Config.database()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def get_db(db_path: str | None = None, *, immediate: bool = False):
    """Open a connection; ``immediate=True`` takes the write lock up front.

    换版是「升版 + 审计」的一笔事务，必须先拿到写锁再读草稿状态，
    否则两名审计员会同时读到「草稿」并各自写一遍。
    """
    conn = connect(db_path)
    try:
        if immediate:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_contracts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  table_name TEXT NOT NULL,
  version_no INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('现行','草稿','停用')),
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(table_name, version_no)
);

CREATE TABLE IF NOT EXISTS contract_fields (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract_id INTEGER NOT NULL REFERENCES schema_contracts(id),
  field_name TEXT NOT NULL,
  field_type TEXT NOT NULL,
  UNIQUE(contract_id, field_name)
);

CREATE TABLE IF NOT EXISTS catalog_tables (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  table_name TEXT NOT NULL UNIQUE,
  live_version INTEGER NOT NULL,
  registered_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS upgrade_audits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  audit_ref TEXT NOT NULL UNIQUE,
  contract_id INTEGER NOT NULL REFERENCES schema_contracts(id),
  table_name TEXT NOT NULL,
  from_version INTEGER,
  to_version INTEGER NOT NULL,
  actor TEXT NOT NULL CHECK (length(trim(actor)) > 0),
  fingerprint TEXT NOT NULL,
  snapshot TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_upgrade_audits_contract
  ON upgrade_audits(contract_id);
CREATE INDEX IF NOT EXISTS ix_upgrade_audits_table
  ON upgrade_audits(table_name, id);

CREATE TABLE IF NOT EXISTS apply_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract_id INTEGER NOT NULL,
  table_name TEXT NOT NULL,
  from_version INTEGER,
  to_version INTEGER NOT NULL,
  result TEXT NOT NULL,
  audit_id INTEGER REFERENCES upgrade_audits(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reconcile_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  as_of TEXT NOT NULL,
  matched INTEGER NOT NULL,
  drifted INTEGER NOT NULL,
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# 审计快照只增不改：升版凭证一旦落库就是复盘依据，任何 UPDATE/DELETE 直接拒绝。
GUARDS = """
CREATE TRIGGER IF NOT EXISTS upgrade_audits_immutable_update
BEFORE UPDATE ON upgrade_audits
BEGIN
  SELECT RAISE(ABORT, '审计快照不可变：禁止更新');
END;

CREATE TRIGGER IF NOT EXISTS upgrade_audits_immutable_delete
BEFORE DELETE ON upgrade_audits
BEGIN
  SELECT RAISE(ABORT, '审计快照不可变：禁止删除');
END;

CREATE TRIGGER IF NOT EXISTS apply_runs_require_audit
BEFORE INSERT ON apply_runs
WHEN NEW.audit_id IS NULL
BEGIN
  SELECT RAISE(ABORT, '升版必须携带审计凭证：缺审计即拦');
END;
"""

SEED = [
    ("dw_orders_d", 1, "现行", "初始现行", [("order_id", "INT"), ("amount", "DECIMAL"), ("biz_date", "DATE")]),
    ("dw_orders_d", 2, "草稿", "拟删 amount 改币种", [("order_id", "INT"), ("currency", "STRING"), ("biz_date", "DATE")]),
    ("dw_payments_d", 1, "现行", "支付现行", [("pay_id", "INT"), ("order_id", "INT"), ("paid_at", "TIMESTAMP")]),
]


def _migrate(conn: sqlite3.Connection) -> None:
    """老库补齐 audit_id 列；历史无凭证的升版记录保留为 NULL，交给合规检查亮红。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(apply_runs)").fetchall()}
    if "audit_id" not in cols:
        conn.execute(
            "ALTER TABLE apply_runs ADD COLUMN audit_id INTEGER REFERENCES upgrade_audits(id)"
        )


def init_db(db_path: str | None = None) -> None:
    with get_db(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.executescript(GUARDS)
        if conn.execute("SELECT value FROM meta WHERE key='seeded'").fetchone():
            return
        for table, ver, status, note, fields in SEED:
            cur = conn.execute(
                "INSERT INTO schema_contracts(table_name, version_no, status, note) VALUES (?,?,?,?)",
                (table, ver, status, note),
            )
            cid = cur.lastrowid
            for fname, ftype in fields:
                conn.execute(
                    "INSERT INTO contract_fields(contract_id, field_name, field_type) VALUES (?,?,?)",
                    (cid, fname, ftype),
                )
            if status == "现行":
                conn.execute(
                    "INSERT OR REPLACE INTO catalog_tables(table_name, live_version) VALUES (?,?)",
                    (table, ver),
                )
        conn.execute("INSERT INTO meta(key, value) VALUES ('seeded','1')")
