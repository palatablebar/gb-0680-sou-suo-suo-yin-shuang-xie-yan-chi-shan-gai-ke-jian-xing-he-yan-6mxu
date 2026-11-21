from app import store
def _backfill_status(row, decision):
    if decision == "退回复核":
        return "挂起待重核"
    return row.get("status", "") or "通过"
def build_backfill(run):
    """组装回补清单：原对账结论保留在 original_status，现行判定作用于 backfill_status。"""
    chain = store.judgment_chain(run["run_id"])
    if not chain:
        return {"rows": [], "metrics": {"records": 0, "corrections": 0, "suspended": 0},
                "current_seq": None, "judgments": [], "current_judgment": None}
    current = next((j for j in chain if j["seq"] == run.get("current_seq")), chain[-1])
    base_rows = run["stages"][5]["rows"]
    rows = []
    for row in base_rows:
        rows.append({
            "doc_id": row.get("doc_id", ""),
            "source_version": row.get("source_version", ""),
            "index_version": row.get("index_version", ""),
            "gate": row.get("gate", ""),
            "original_status": row.get("status", "") or "通过",
            "backfill_status": _backfill_status(row, current["decision"]),
            "current_judgment": current["decision"],
            "judgment_seq": current["seq"],
            "judgment_id": current["judgment_id"],
            "_sources": row.get("_sources", []),
        })
    suspended = sum(1 for r in rows if r["backfill_status"] == "挂起待重核")
    metrics = {
        "records": len(rows),
        "corrections": sum(1 for j in chain if j["idempotency_key"] is not None),
        "suspended": suspended,
        "current_seq": current["seq"],
        "chain_depth": len(chain),
    }
    return {"rows": rows, "metrics": metrics, "judgments": chain,
            "current_seq": current["seq"], "current_judgment": current}
