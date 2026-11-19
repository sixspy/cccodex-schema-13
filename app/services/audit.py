"""升版审计凭证：独立审计快照表 upgrade_audits（全局唯一形态）。

不落外部文件、不双写第二套凭证。每次换版在同一事务里写一行快照，
快照字段做规范化 JSON 后取 SHA-256 指纹，凭证号 = AUD-<指纹前 12 位>。
复盘（审计队列 / 时间线 / 合规复验）一律读这一张表。
"""

import hashlib
import json
from datetime import datetime, timezone

from app.db import get_db
from app.services.errors import ServiceError

AUDIT_FORM = "独立审计快照表 upgrade_audits"
AUDIT_TABLE = "upgrade_audits"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def canonical_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint_of(snapshot: str) -> str:
    return hashlib.sha256(snapshot.encode("utf-8")).hexdigest()


def build_credential(
    contract_id: int,
    table_name: str,
    version_no: int,
    note: str,
    fields: dict[str, str],
    from_version: int | None,
    actor: str,
    applied_at: str,
) -> tuple[str, str, str]:
    """返回 (snapshot_json, fingerprint, audit_ref)。"""
    payload = {
        "contract_id": contract_id,
        "table_name": table_name,
        "version_no": version_no,
        "note": note,
        "fields": sorted([[name, ftype] for name, ftype in fields.items()]),
        "from_version": from_version,
        "to_version": version_no,
        "actor": actor,
        "applied_at": applied_at,
    }
    snapshot = canonical_json(payload)
    fingerprint = fingerprint_of(snapshot)
    return snapshot, fingerprint, "AUD-" + fingerprint[:12].upper()


def audit_public(row) -> dict:
    data = dict(row)
    snapshot = data.pop("snapshot", None)
    fingerprint = data.get("fingerprint") or ""
    fields = []
    if snapshot:
        try:
            fields = json.loads(snapshot).get("fields", [])
        except (TypeError, ValueError):
            fields = []
    return {
        "id": data["id"],
        "audit_ref": data["audit_ref"],
        "form": AUDIT_FORM,
        "storage": f"{AUDIT_TABLE}#{data['id']}",
        "contract_id": data["contract_id"],
        "table_name": data["table_name"],
        "from_version": data["from_version"],
        "to_version": data["to_version"],
        "actor": data["actor"],
        "fingerprint": fingerprint,
        "fingerprint_short": fingerprint[:12],
        "field_count": len(fields),
        "created_at": data["created_at"],
    }


def list_audits(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM upgrade_audits ORDER BY id DESC"
        ).fetchall()
        return [audit_public(r) for r in rows]


def build_timeline(table_name: str, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        contracts = conn.execute(
            "SELECT * FROM schema_contracts WHERE table_name=? ORDER BY version_no",
            (table_name,),
        ).fetchall()
        if not contracts:
            raise ServiceError("表不存在于契约目录", 404)
        runs = conn.execute(
            "SELECT r.*, a.audit_ref, a.fingerprint, a.actor AS audit_actor, "
            "a.created_at AS audit_at "
            "FROM apply_runs r LEFT JOIN upgrade_audits a ON a.id=r.audit_id "
            "WHERE r.table_name=? ORDER BY r.id",
            (table_name,),
        ).fetchall()
        events: list[dict] = []
        seq = 0
        for c in contracts:
            seq += 1
            events.append(
                {
                    "seq": seq,
                    "at": c["created_at"],
                    "kind": "契约登记",
                    "title": f"v{c['version_no']} 登记（当前{c['status']}）",
                    "detail": c["note"] or "无备注",
                    "audit_ref": None,
                    "fingerprint": None,
                    "storage": None,
                    "missing_audit": False,
                }
            )
        for r in runs:
            seq += 1
            has_audit = r["audit_id"] is not None
            events.append(
                {
                    "seq": seq,
                    "at": r["audit_at"] or r["created_at"],
                    "kind": "换版应用",
                    "title": f"v{r['from_version'] if r['from_version'] else '—'} → v{r['to_version']}",
                    "detail": f"操作人 {r['audit_actor']}" if has_audit else "缺审计凭证",
                    "audit_ref": r["audit_ref"],
                    "fingerprint": r["fingerprint"],
                    "storage": f"{AUDIT_TABLE}#{r['audit_id']}" if has_audit else None,
                    "missing_audit": not has_audit,
                }
            )
        events.sort(key=lambda e: (e["at"] or "", e["seq"]))
        return {
            "table_name": table_name,
            "audit_form": AUDIT_FORM,
            "audit_count": sum(1 for e in events if e["audit_ref"]),
            "events": events,
        }
