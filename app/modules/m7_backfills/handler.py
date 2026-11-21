from flask import Blueprint,request,jsonify
from app.errors import DomainError
from app.pipeline import stage_preview
from app import store
from app.corrections import build_backfill
bp=Blueprint("backfills",__name__)
@bp.route("/api/backfills",methods=["GET","POST"])
def preview():
    payload=request.get_json(silent=True) if request.method=="POST" else {}
    if payload is None or not isinstance(payload,dict): raise DomainError("BAD_BODY","请求需要对象")
    return jsonify(stage_preview(6,payload))
@bp.route("/api/backfills/<run_id>")
def backfill_detail(run_id):
    return jsonify(build_backfill(store.get(run_id)))
