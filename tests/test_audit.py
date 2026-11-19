import sqlite3
import threading

import pytest

from app.services import (
    apply_contract,
    build_timeline,
    list_audits,
    list_contracts,
    run_compliance_check,
)
from app.services.errors import ServiceError


def _draft(db_path):
    return next(c for c in list_contracts(db_path) if c["status"] == "草稿")


def test_apply_writes_audit_snapshot(client_db):
    draft = _draft(client_db)
    result = apply_contract(draft["id"], "审计员甲", client_db)
    audit = result["audit"]
    assert audit["audit_ref"].startswith("AUD-")
    assert audit["storage"] == f"upgrade_audits#{audit['id']}"
    assert audit["actor"] == "审计员甲"
    assert audit["table_name"] == draft["table_name"]
    assert audit["to_version"] == draft["version_no"]
    assert len(audit["fingerprint"]) == 64

    audits = list_audits(client_db)
    assert len(audits) == 1
    assert audits[0]["audit_ref"] == audit["audit_ref"]


def test_apply_fails_without_actor(client_db):
    draft = _draft(client_db)
    with pytest.raises(ServiceError) as exc:
        apply_contract(draft["id"], "  ", client_db)
    assert exc.value.status_code == 401
    assert list_audits(client_db) == []
    assert _draft(client_db)["id"] == draft["id"]


def test_audit_failure_rolls_back_upgrade(client_db, monkeypatch):
    draft = _draft(client_db)
    import app.services.contracts as contracts_mod

    # 审计快照构建失败 → 升版必须整笔回滚
    monkeypatch.setattr(
        contracts_mod,
        "build_credential",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("审计快照构建失败")),
    )
    with pytest.raises(RuntimeError):
        apply_contract(draft["id"], "审计员甲", client_db)

    still_draft = [c for c in list_contracts(client_db) if c["id"] == draft["id"]][0]
    assert still_draft["status"] == "草稿"
    assert list_audits(client_db) == []


def test_concurrent_apply_single_audit(client_db):
    draft = _draft(client_db)
    results: list[str] = []
    errors: list[Exception] = []

    def worker(actor: str):
        try:
            apply_contract(draft["id"], actor, client_db)
            results.append(actor)
        except ServiceError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"审计员{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 1
    assert len(errors) == 1 and errors[0].status_code == 409
    assert len(list_audits(client_db)) == 1
    compliance = run_compliance_check(client_db)
    assert compliance["total"] == 1
    assert compliance["missing_audit"] == 0
    assert compliance["status"] == "合规"


def test_audit_rows_are_immutable(client_db):
    draft = _draft(client_db)
    apply_contract(draft["id"], "审计员甲", client_db)
    conn = sqlite3.connect(client_db)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE upgrade_audits SET actor='伪造'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM upgrade_audits")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO apply_runs(contract_id, table_name, to_version, result, audit_id) "
                "VALUES (1, 'x', 1, '已应用', NULL)"
            )
    finally:
        conn.close()


def test_compliance_flags_missing_audit(client_db):
    draft = _draft(client_db)
    apply_contract(draft["id"], "审计员甲", client_db)
    # 模拟历史遗留升版行（审计列为空）：入库已被触发器拦，这里直接抹掉凭证列来复验读侧
    conn = sqlite3.connect(client_db)
    try:
        conn.execute("UPDATE apply_runs SET audit_id=NULL")
        conn.commit()
    finally:
        conn.close()

    result = run_compliance_check(client_db)
    assert result["status"] == "不合规"
    assert result["missing_audit"] == 1
    flagged = [i for i in result["items"] if not i["verified"]]
    assert flagged and "缺审计凭证" in flagged[0]["problems"]


def test_timeline_links_audit(client_db):
    draft = _draft(client_db)
    audit = apply_contract(draft["id"], "审计员甲", client_db)["audit"]
    timeline = build_timeline(draft["table_name"], client_db)
    apply_events = [e for e in timeline["events"] if e["kind"] == "换版应用"]
    assert len(apply_events) == 1
    event = apply_events[0]
    assert event["audit_ref"] == audit["audit_ref"]
    assert event["fingerprint"] == audit["fingerprint"]
    assert event["storage"] == audit["storage"]
    assert event["missing_audit"] is False


async def test_apply_response_has_audit(authed):
    items = (await (await authed.get("/api/contracts")).json())["items"]
    draft = next(c for c in items if c["status"] == "草稿")
    j = await (await authed.post(f"/api/contracts/{draft['id']}/apply", json={})).json()
    assert j["audit"]["audit_ref"].startswith("AUD-")
    audits = (await (await authed.get("/api/audits")).json())["items"]
    assert len(audits) == 1
    comp = await (await authed.get("/api/compliance")).json()
    assert comp["status"] == "合规"
    assert comp["progress"] == 100
    tl = await (await authed.get(
        f"/api/timeline?table_name={draft['table_name']}"
    )).json()
    assert tl["audit_count"] == 1
