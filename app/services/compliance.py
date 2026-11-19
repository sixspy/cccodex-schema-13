from app.db import get_db
from app.services.audit import AUDIT_FORM, AUDIT_TABLE, fingerprint_of


def run_compliance_check(db_path: str | None = None) -> dict:
    """合规复验：逐条升版记录回读 upgrade_audits 快照并重算指纹。

    合规判断只认这一套凭证；没有审计行、指纹对不上、凭证与升版不同源都判不合规。
    """
    with get_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT r.*, a.audit_ref, a.fingerprint, a.snapshot, a.actor AS audit_actor,
                   a.table_name AS audit_table, a.from_version AS audit_from,
                   a.to_version AS audit_to, a.created_at AS audit_at,
                   c.version_no AS contract_version
            FROM apply_runs r
            LEFT JOIN upgrade_audits a ON a.id = r.audit_id
            LEFT JOIN schema_contracts c ON c.id = r.contract_id
            ORDER BY r.id DESC
            """
        ).fetchall()
        items: list[dict] = []
        for r in rows:
            problems: list[str] = []
            if r["audit_id"] is None:
                problems.append("缺审计凭证")
            else:
                if not (r["audit_actor"] or "").strip():
                    problems.append("凭证缺操作人")
                recomputed = fingerprint_of(r["snapshot"] or "")
                if recomputed != r["fingerprint"]:
                    problems.append("凭证指纹被篡改")
                if (
                    r["audit_table"] != r["table_name"]
                    or r["audit_from"] != r["from_version"]
                    or r["audit_to"] != r["to_version"]
                ):
                    problems.append("凭证与升版记录不同源")
                if r["contract_version"] != r["to_version"]:
                    problems.append("契约版本与凭证不一致")
            items.append(
                {
                    "apply_run_id": r["id"],
                    "table_name": r["table_name"],
                    "from_version": r["from_version"],
                    "to_version": r["to_version"],
                    "audit_ref": r["audit_ref"],
                    "fingerprint_short": (r["fingerprint"] or "")[:12],
                    "storage": f"{AUDIT_TABLE}#{r['audit_id']}" if r["audit_id"] else None,
                    "actor": r["audit_actor"],
                    "created_at": r["audit_at"] or r["created_at"],
                    "verified": not problems,
                    "problems": problems,
                }
            )

    total = len(items)
    verified = sum(1 for i in items if i["verified"])
    missing = sum(1 for i in items if "缺审计凭证" in i["problems"])
    tampered = sum(1 for i in items if "凭证指纹被篡改" in i["problems"])
    if total == 0:
        status, progress = "无升版", 0
    elif verified == total:
        status, progress = "合规", 100
    else:
        status, progress = "不合规", round(verified * 100 / total)

    return {
        "form": AUDIT_FORM,
        "status": status,
        "progress": progress,
        "total": total,
        "verified": verified,
        "missing_audit": missing,
        "tampered": tampered,
        "items": items,
    }
