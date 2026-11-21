import sqlite3,json,hashlib
from datetime import datetime,timezone
from app.config import DATA_DIR
from app.errors import DomainError
DECISIONS={"已复核","退回复核"}
def connect():
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DATA_DIR/"records.db",timeout=20)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,decision TEXT NOT NULL DEFAULT '待复核')")
    _migrate(db)
    return db
def _migrate(db):
    cols={r[1] for r in db.execute("PRAGMA table_info(runs)")}
    if "current_seq" not in cols:
        db.execute("ALTER TABLE runs ADD COLUMN current_seq INTEGER")
    db.execute("""CREATE TABLE IF NOT EXISTS judgments(
        judgment_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        decision TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        basis_version INTEGER NOT NULL,
        evidence_sha256 TEXT NOT NULL,
        idempotency_key TEXT,
        superseded_by TEXT,
        issued_at TEXT NOT NULL,
        UNIQUE(run_id,seq))""")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_judgment_idem ON judgments(run_id,idempotency_key) WHERE idempotency_key IS NOT NULL")
def save(run_id,payload):
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,payload) VALUES (?,?)",(run_id,json.dumps(payload,ensure_ascii=False)))
    return get(run_id)
def get(run_id):
    with connect() as db: row=db.execute("SELECT payload,version,decision,current_seq FROM runs WHERE id=?",(run_id,)).fetchone()
    if not row: raise DomainError("NOT_FOUND","批次不存在",404)
    return {**json.loads(row[0]),"version":row[1],"decision":row[2],"current_seq":row[3]}
def latest():
    with connect() as db: row=db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    return get(row[0]) if row else None
def history():
    with connect() as db: return [{"run_id":i,"version":v,"decision":d} for i,v,d in db.execute("SELECT id,version,decision FROM runs ORDER BY rowid DESC")]
def _now(): return datetime.now(timezone.utc).isoformat()
def _evidence_hash(run_id,payload):
    body=json.dumps({"run_id":run_id,"payload":payload},ensure_ascii=False,sort_keys=True,default=str)
    return hashlib.sha256(body.encode()).hexdigest()
def _load(db,run_id):
    row=db.execute("SELECT payload,version,decision,current_seq FROM runs WHERE id=?",(run_id,)).fetchone()
    if not row: raise DomainError("NOT_FOUND","批次不存在",404)
    return row
def review(run_id,version,decision):
    if decision not in DECISIONS: raise DomainError("BAD_DECISION","复核状态无效")
    if not isinstance(version,int): raise DomainError("BAD_VERSION","请提供版本号")
    with connect() as db:
        payload,current_version,_old_decision,current_seq=_load(db,run_id)
        if current_seq is not None:
            raise DomainError("REVIEW_SUPERSEDED","该批次已有签发判定，更正必须走补正，不得原地改写",409)
        if version!=current_version:
            raise DomainError("STALE_VERSION","版本已更新，请刷新",409)
        seq=1;judgment_id=f"J{run_id[:12]}-v{seq}"
        db.execute("""INSERT INTO judgments(judgment_id,run_id,seq,decision,reason,basis_version,evidence_sha256,idempotency_key,superseded_by,issued_at)
            VALUES(?,?,?,?,'',?,?,NULL,NULL,?)""",
            (judgment_id,run_id,seq,decision,current_version,_evidence_hash(run_id,payload),_now()))
        db.execute("UPDATE runs SET version=version+1,decision=?,current_seq=? WHERE id=?",(decision,seq,run_id))
    return get(run_id)
def correct(run_id,basis_version,decision,reason,idempotency_key):
    if decision not in DECISIONS: raise DomainError("BAD_DECISION","补正判定无效")
    if not isinstance(basis_version,int): raise DomainError("BAD_VERSION","请提供版本号")
    if not isinstance(reason,str) or not reason.strip(): raise DomainError("BAD_REASON","补正必须写明更正理由")
    if not isinstance(idempotency_key,str) or not idempotency_key.strip(): raise DomainError("BAD_IDEMPOTENCY","补正必须携带补正单号用于重试幂等")
    reason=reason.strip();idempotency_key=idempotency_key.strip()
    with connect() as db:
        payload,current_version,_old_decision,current_seq=_load(db,run_id)
        if current_seq is None:
            raise DomainError("NO_BASE_JUDGMENT","批次尚未签发原判定，请先复核签发再补正")
        prior=db.execute("SELECT judgment_id,seq,decision,reason,evidence_sha256,issued_at FROM judgments WHERE run_id=? AND idempotency_key=?",(run_id,idempotency_key)).fetchone()
        if prior:
            if prior[2]!=decision or prior[3]!=reason:
                raise DomainError("IDEMPOTENCY_CONFLICT","同一补正单号已用于不同内容的补正，请更换补正单号")
            return get(run_id)
        if basis_version!=current_version:
            raise DomainError("STALE_CORRECTION","补正基于的版本已被更新，迟到旧补正不得覆盖新版",409)
        current_row=db.execute("SELECT judgment_id FROM judgments WHERE run_id=? AND seq=?",(run_id,current_seq)).fetchone()
        seq=current_seq+1;judgment_id=f"J{run_id[:12]}-v{seq}"
        try:
            db.execute("""INSERT INTO judgments(judgment_id,run_id,seq,decision,reason,basis_version,evidence_sha256,idempotency_key,superseded_by,issued_at)
                VALUES(?,?,?,?,?,?,?,?,NULL,?)""",
                (judgment_id,run_id,seq,decision,reason,basis_version,_evidence_hash(run_id,payload),idempotency_key,_now()))
        except sqlite3.IntegrityError:
            raise DomainError("CONCURRENT_CORRECTION","已有补正正在写入，请刷新后基于最新版重试",409)
        cursor=db.execute("UPDATE runs SET version=version+1,decision=?,current_seq=? WHERE id=? AND current_seq=?",(decision,seq,run_id,current_seq))
        if cursor.rowcount!=1:
            raise DomainError("CONCURRENT_CORRECTION","当前判定已被并发补正更新，迟到旧补正不得覆盖新版",409)
        db.execute("UPDATE judgments SET superseded_by=? WHERE judgment_id=?",(judgment_id,current_row[0]))
    return get(run_id)
def judgment_chain(run_id):
    with connect() as db:
        _load(db,run_id)
        rows=db.execute("""SELECT judgment_id,seq,decision,reason,basis_version,evidence_sha256,idempotency_key,superseded_by,issued_at
            FROM judgments WHERE run_id=? ORDER BY seq""",(run_id,)).fetchall()
    keys=("judgment_id","seq","decision","reason","basis_version","evidence_sha256","idempotency_key","superseded_by","issued_at")
    return [dict(zip(keys,r)) for r in rows]
