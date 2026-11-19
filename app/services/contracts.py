from app.db import get_db
from app.services.audit import audit_public, build_credential, utc_now
from app.services.errors import ServiceError


def list_contracts(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT c.*, "
            "(SELECT COUNT(*) FROM contract_fields f WHERE f.contract_id=c.id) AS field_count, "
            "(SELECT a.audit_ref FROM upgrade_audits a WHERE a.contract_id=c.id) AS audit_ref "
            "FROM schema_contracts c ORDER BY c.table_name, c.version_no"
        ).fetchall()
        return [dict(r) for r in rows]


def _fields(conn, contract_id: int) -> dict[str, str]:
    rows = conn.execute(
        "SELECT field_name, field_type FROM contract_fields WHERE contract_id=?",
        (contract_id,),
    ).fetchall()
    return {r["field_name"]: r["field_type"] for r in rows}


def run_breaking_check(contract_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        draft = conn.execute(
            "SELECT * FROM schema_contracts WHERE id=?", (contract_id,)
        ).fetchone()
        if not draft:
            raise ServiceError("契约不存在", 404)
        if draft["status"] != "草稿":
            raise ServiceError("仅草稿可做破坏性检查", 409)
        live = conn.execute(
            "SELECT * FROM schema_contracts WHERE table_name=? AND status='现行'",
            (draft["table_name"],),
        ).fetchone()
        if not live:
            raise ServiceError("无现行契约可对照", 409)
        old_f = _fields(conn, live["id"])
        new_f = _fields(conn, draft["id"])
        removed = sorted(set(old_f) - set(new_f))
        type_changed = sorted(
            k for k in set(old_f) & set(new_f) if old_f[k] != new_f[k]
        )
        added = sorted(set(new_f) - set(old_f))
        breaking = bool(removed or type_changed)
        return {
            "contract_id": contract_id,
            "table_name": draft["table_name"],
            "from_version": live["version_no"],
            "to_version": draft["version_no"],
            "removed_fields": removed,
            "type_changed_fields": type_changed,
            "added_fields": added,
            "breaking": breaking,
            "verdict": "破坏性" if breaking else "兼容",
        }


def apply_contract(
    contract_id: int, actor: str, db_path: str | None = None
) -> dict:
    """换版应用：升版与审计同一事务，审计写不出则整笔回滚。

    ``BEGIN IMMEDIATE`` 先占写锁，两名审计员同时点升版时后到者读到已现行，
    直接 409，不会出现「升了版却没有审计」的现行版本。
    """
    if not (actor or "").strip():
        raise ServiceError("缺操作人，无法落审计凭证", 401)

    with get_db(db_path, immediate=True) as conn:
        draft = conn.execute(
            "SELECT * FROM schema_contracts WHERE id=?", (contract_id,)
        ).fetchone()
        if not draft:
            raise ServiceError("契约不存在", 404)
        if draft["status"] != "草稿":
            raise ServiceError("仅草稿可换版应用", 409)
        live = conn.execute(
            "SELECT * FROM schema_contracts WHERE table_name=? AND status='现行'",
            (draft["table_name"],),
        ).fetchone()
        from_ver = live["version_no"] if live else None

        applied_at = utc_now()
        snapshot, fingerprint, audit_ref = build_credential(
            contract_id=contract_id,
            table_name=draft["table_name"],
            version_no=draft["version_no"],
            note=draft["note"],
            fields=_fields(conn, contract_id),
            from_version=from_ver,
            actor=actor,
            applied_at=applied_at,
        )
        try:
            cur = conn.execute(
                "INSERT INTO upgrade_audits(audit_ref, contract_id, table_name, "
                "from_version, to_version, actor, fingerprint, snapshot, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    audit_ref,
                    contract_id,
                    draft["table_name"],
                    from_ver,
                    draft["version_no"],
                    actor,
                    fingerprint,
                    snapshot,
                    applied_at,
                ),
            )
            audit_id = cur.lastrowid
        except Exception as exc:  # 审计落库失败 → 整笔回滚，版本不升
            raise ServiceError(f"审计凭证落库失败，换版已回滚：{exc}", 500) from exc

        if live:
            conn.execute(
                "UPDATE schema_contracts SET status='停用' WHERE id=?",
                (live["id"],),
            )
        conn.execute(
            "UPDATE schema_contracts SET status='现行' WHERE id=?",
            (contract_id,),
        )
        conn.execute(
            "INSERT INTO catalog_tables(table_name, live_version) VALUES (?,?) "
            "ON CONFLICT(table_name) DO UPDATE SET live_version=excluded.live_version, "
            "registered_at=datetime('now')",
            (draft["table_name"], draft["version_no"]),
        )
        conn.execute(
            "INSERT INTO apply_runs(contract_id, table_name, from_version, to_version, "
            "result, audit_id) VALUES (?,?,?,?,?,?)",
            (contract_id, draft["table_name"], from_ver, draft["version_no"], "已应用", audit_id),
        )
        row = conn.execute(
            "SELECT * FROM schema_contracts WHERE id=?", (contract_id,)
        ).fetchone()
        audit_row = conn.execute(
            "SELECT * FROM upgrade_audits WHERE id=?", (audit_id,)
        ).fetchone()
        return {"contract": dict(row), "audit": audit_public(audit_row)}
