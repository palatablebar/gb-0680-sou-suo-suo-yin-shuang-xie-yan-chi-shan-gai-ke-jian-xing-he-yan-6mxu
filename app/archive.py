import csv,io,json
from app import store
from app.corrections import build_backfill
def build_archive(run):
    stream=io.StringIO(newline="");writer=csv.writer(stream)
    writer.writerow(["run_id","input_sha256","rule_version","kind","source_lines","reason","evidence"])
    meta=run["manifest"]
    # 原签发证据：来自不可变流水线载荷，补正不改写这些行
    for row in run["stages"][-1]["rows"]:
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"accepted",json.dumps(row.get("_sources",[])),row.get("status",""),json.dumps(row,ensure_ascii=False)])
    for row in run["stages"][-1].get("cumulative_quarantine",[]):
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"quarantine",json.dumps(row.get("_sources",[])),row.get("reason",""),json.dumps(row,ensure_ascii=False)])
    backfill=build_backfill(run)
    # 回补清单：原对账结论与现行判定并排，补正判定随原对账结论一并回补
    for row in backfill["rows"]:
        evidence={"doc_id":row["doc_id"],"original_status":row["original_status"],
                  "backfill_status":row["backfill_status"],"current_judgment":row["current_judgment"],
                  "judgment_seq":row["judgment_seq"],"judgment_id":row["judgment_id"]}
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"backfill",json.dumps(row.get("_sources",[])),row["original_status"],json.dumps(evidence,ensure_ascii=False)])
    # 判定台账：原签发版、当前版及去向（superseded_by）全留痕
    current_seq=backfill["current_seq"]
    for j in backfill["judgments"]:
        if j["superseded_by"]:
            destination="已被 "+j["superseded_by"]+" 替代"
        elif j["seq"]==current_seq:
            destination="现行唯一正式判定"
        else:
            destination="原签发版（已留存）"
        evidence={"judgment_id":j["judgment_id"],"seq":j["seq"],"decision":j["decision"],
                  "reason":j["reason"],"basis_version":j["basis_version"],
                  "evidence_sha256":j["evidence_sha256"],"idempotency_key":j["idempotency_key"],
                  "superseded_by":j["superseded_by"],"issued_at":j["issued_at"],"destination":destination}
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"judgment",j["judgment_id"],destination,json.dumps(evidence,ensure_ascii=False)])
    return "﻿"+stream.getvalue()
