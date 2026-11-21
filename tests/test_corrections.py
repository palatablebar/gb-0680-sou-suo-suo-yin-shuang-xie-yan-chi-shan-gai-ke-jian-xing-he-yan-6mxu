import csv,io
from app.pipeline import execute,load_sample
from app import store
from app.archive import build_archive
from app.corrections import build_backfill
from app.errors import DomainError
import pytest

@pytest.fixture
def signed(client):
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    rid=p["run_id"]
    r=client.post(f"/api/runs/{rid}/review",json={"version":1,"decision":"已复核"})
    assert r.status_code==200
    return rid

def _correct(client,rid,version,decision,reason,key):
    return client.post(f"/api/runs/{rid}/corrections",
                       json={"version":version,"decision":decision,"reason":reason,"idempotency_key":key})

def test_review_seals_original_judgment(client,signed):
    run=store.get(signed)
    assert run["current_seq"]==1 and run["decision"]=="已复核" and run["version"]==2
    chain=store.judgment_chain(signed)
    assert len(chain)==1 and chain[0]["superseded_by"] is None
    assert len(chain[0]["evidence_sha256"])==64

def test_in_place_review_blocked_after_issuance(client,signed):
    r=client.post(f"/api/runs/{signed}/review",json={"version":2,"decision":"退回复核"})
    assert r.status_code==409 and r.get_json()["code"]=="REVIEW_SUPERSEDED"

def test_correction_becomes_unique_current_and_keeps_original(client,signed):
    r=_correct(client,signed,2,"退回复核","门禁口径写错，延迟未超预算","CORR-001")
    assert r.status_code==200
    run=store.get(signed)
    assert run["current_seq"]==2 and run["decision"]=="退回复核" and run["version"]==3
    chain=store.judgment_chain(signed)
    assert [j["seq"] for j in chain]==[1,2]
    assert chain[0]["superseded_by"]==chain[1]["judgment_id"]
    assert chain[1]["superseded_by"] is None and chain[1]["idempotency_key"]=="CORR-001"

def test_idempotent_retry_does_not_add_judgment(client,signed):
    assert _correct(client,signed,2,"退回复核","同一理由","CORR-002").status_code==200
    assert _correct(client,signed,2,"退回复核","同一理由","CORR-002").status_code==200
    assert len(store.judgment_chain(signed))==2
    assert store.get(signed)["version"]==3

def test_same_key_different_content_rejected(client,signed):
    assert _correct(client,signed,2,"退回复核","理由甲","CORR-003").status_code==200
    r=_correct(client,signed,3,"已复核","理由乙","CORR-003")
    assert r.status_code==422 and r.get_json()["code"]=="IDEMPOTENCY_CONFLICT"
    assert len(store.judgment_chain(signed))==2

def test_late_stale_correction_cannot_override_new(client,signed):
    assert _correct(client,signed,2,"退回复核","第一次更正","CORR-004").status_code==200
    r=_correct(client,signed,2,"已复核","迟到的旧版补正","CORR-005")
    assert r.status_code==409 and r.get_json()["code"]=="STALE_CORRECTION"
    assert store.get(signed)["current_seq"]==2

def test_correction_requires_reason_and_key(client,signed):
    r=client.post(f"/api/runs/{signed}/corrections",json={"version":2,"decision":"已复核","reason":"","idempotency_key":"K"})
    assert r.status_code==422 and r.get_json()["code"]=="BAD_REASON"
    r=client.post(f"/api/runs/{signed}/corrections",json={"version":2,"decision":"已复核","reason":"r","idempotency_key":""})
    assert r.status_code==422 and r.get_json()["code"]=="BAD_IDEMPOTENCY"

def test_correction_before_review_rejected(client):
    p=client.post("/api/runs",json={"sample":"clean.csv"}).get_json()
    r=_correct(client,p["run_id"],1,"已复核","无原判定","CORR-X")
    assert r.status_code==422 and r.get_json()["code"]=="NO_BASE_JUDGMENT"

def test_backfill_carries_original_conclusion_and_current_judgment(client,signed):
    _correct(client,signed,2,"退回复核","改为退回","CORR-006")
    view=build_backfill(store.get(signed))
    assert view["current_seq"]==2 and view["metrics"]["corrections"]==1
    assert view["rows"], "回补清单不能为空"
    for row in view["rows"]:
        assert row["original_status"] in {"通过","差异"}
        assert row["backfill_status"]=="挂起待重核"
        assert row["current_judgment"]=="退回复核" and row["judgment_seq"]==2

def test_archive_keeps_original_evidence_and_appends_ledger(client,signed):
    before=build_archive(store.get(signed))
    _correct(client,signed,2,"已复核","纠正口径","CORR-007")
    after=build_archive(store.get(signed))
    def accepted_part(text):
        rows=list(csv.reader(io.StringIO(text.lstrip("﻿"))))
        return [r for r in rows[1:] if r[3]=="accepted"]
    assert accepted_part(before)==accepted_part(after), "原签发证据行不得被补正改写"
    rows=list(csv.reader(io.StringIO(after.lstrip("﻿"))))[1:]
    kinds={r[3] for r in rows}
    assert {"accepted","quarantine","backfill","judgment"} <= kinds
    judgments=[r for r in rows if r[3]=="judgment"]
    assert any("现行唯一正式判定" in r[5] for r in judgments)
    assert any("已被" in r[5] for r in judgments)
    backfills=[r for r in rows if r[3]=="backfill"]
    assert len(backfills)==len(build_backfill(store.get(signed))["rows"])

def test_judgments_and_backfill_api(client,signed):
    _correct(client,signed,2,"退回复核","api更正","CORR-008")
    j=client.get(f"/api/runs/{signed}/judgments").get_json()
    assert j["current_seq"]==2 and len(j["judgments"])==2
    b=client.get(f"/api/backfills/{signed}").get_json()
    assert b["current_judgment"]["idempotency_key"]=="CORR-008"

def test_pages_render_with_chain(client,signed):
    _correct(client,signed,2,"退回复核","页面更正","CORR-009")
    for slug in ("backfills","parity","latency"):
        html=client.get("/"+slug).data.decode()
        assert "判定链" in html and "CORR-009" in html and "现行唯一正式判定" in html

def test_store_level_chain_directly():
    run=execute(load_sample("dirty.csv"));rid=run["run_id"]
    store.review(rid,1,"已复核")
    store.correct(rid,2,"退回复核","直接补正","DIRECT-1")
    with pytest.raises(DomainError) as e:
        store.correct(rid,2,"已复核","迟到补正","DIRECT-2")
    assert e.value.code=="STALE_CORRECTION"
    assert store.get(rid)["current_seq"]==2

def test_legacy_run_without_chain_cannot_be_corrected(tmp_path,monkeypatch):
    import sqlite3
    monkeypatch.setattr(store,"DATA_DIR",tmp_path)
    db=sqlite3.connect(tmp_path/"records.db")
    db.execute("CREATE TABLE runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,decision TEXT NOT NULL DEFAULT '待复核')")
    db.execute("INSERT INTO runs VALUES(?,?,?,?)",("legacy1",'{"run_id":"legacy1"}',2,"已复核"))
    db.commit();db.close()
    assert store.get("legacy1")["current_seq"] is None
    with pytest.raises(DomainError) as e:
        store.correct("legacy1",2,"退回复核","旧批次补正","OLD-1")
    assert e.value.code=="NO_BASE_JUDGMENT"
    assert store.judgment_chain("legacy1")==[]
