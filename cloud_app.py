"""HTTP API and short collection batches for the Vercel container service."""

import csv
import hashlib
import hmac
import io
import os
import re
import tempfile

import cv2
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from flask import Flask, Response, jsonify, request, session

import cloud_store as db
from collect import ProductListError, list_products, process_product
from references import changed_brands, legacy_embedding, legacy_entry, prepare_reference, reference_counts
from reviews import apply_review, review_changes
from schedule_rules import FAILURE_STAGES, LOG_PAGE_SIZE, advance_schedule, failure_reason, is_due, parse_schedule, schedule_value
from snapshots import INVALID_COMPARISON, public, seed_snapshot, weekly_public
from vision import Vision

app = Flask(__name__, static_url_path="/static")
app.secret_key = os.environ.get("PICASSO_WORKER_KEY")
app.config.update(MAX_CONTENT_LENGTH=4 * 1024 * 1024, SESSION_COOKIE_SECURE=True,
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict")
MODELS = Path(os.environ.get("PICASSO_MODELS", ".local/models"))
BASELINE = public(seed_snapshot())
cv2.setNumThreads(1)
BATCH_SIZE = 5
LATEST = ("id,created_at,expected_count,status,error,summary,"
          "reference_path,prior_run")
LIST_HOSTS = {"whoau.com", "www.whoau.com", "m.whoau.com"}
IMAGE_HOSTS = {"spao-item.elandrs.com", "cafe24.poxo.com"}


def equals(value, expected):
    return bool(expected) and hmac.compare_digest(value.encode(), expected.encode())


@app.before_request
def authorize():
    if request.path.startswith("/internal/"):
        if not equals(request.headers.get("X-Picasso-Worker", ""), os.environ.get("PICASSO_WORKER_KEY", "")):
            return jsonify(error="찾을 수 없습니다."), 404
    elif request.method in {"POST", "PATCH"}:
        if request.headers.get("X-PICasso-Request") != "1":
            return jsonify(error="대시보드에서 요청을 다시 실행하세요."), 403
        if request.path != "/api/login" and not session.get("operator"):
            return jsonify(error="운영자 로그인이 필요합니다."), 401


@app.errorhandler(db.StoreError)
def storage_error(error):
    app.logger.warning("Supabase status %s", error.status)
    if request.path.startswith("/internal/"):
        return jsonify(error=f"저장소 요청 오류 (HTTP {error.status})"), 502
    return jsonify(error="Supabase 요청을 처리하지 못했습니다."), 502


@app.errorhandler(413)
def too_large(error):
    return jsonify(error="기준 이미지는 한 장당 4MB 미만으로 올려 주세요."), 413


@app.get("/")
def home():
    return app.send_static_file("index.html")


@app.post("/api/login")
def login():
    body = request.get_json(silent=True) or {}
    if not equals(str(body.get("password", "")), os.environ.get("PICASSO_OPERATOR_PASSWORD", "")):
        return jsonify(error="운영자 암호를 확인하세요."), 401
    session["operator"] = True
    return jsonify(ok=True)


def snapshot_id(value):
    if value == BASELINE["id"]:
        return db.BASELINE_ID
    try:
        return str(UUID(value))
    except (TypeError, ValueError):
        return None


def snapshot(run, products):
    initial = run["id"] == db.BASELINE_ID
    return {"id": BASELINE["id"] if initial else run["id"],
            "at": BASELINE["at"] if initial else run["created_at"],
            "source": "검증 샘플 · Supabase" if initial else "공식몰 수집 · Supabase",
            "products": products}


@app.get("/api/state")
def state():
    runs = db.rest("picasso_runs", query={"select": LATEST, "status": "eq.complete",
                                       "order": "created_at.desc", "limit": 15})
    status = db.first("picasso_runs", select=LATEST, order="created_at.desc")
    job = {"state": "idle"}
    if status and status["id"] != db.BASELINE_ID:
        name = status["status"]
        job = {"state": "running" if name in {"queued", "running"} else name, "id": status["id"],
               "message": status["error"] or "상품 목록 수집 중",
               "total": status["expected_count"]}
        if name == "running":
            job["done"] = db.rest("rpc/picasso_progress", method="POST", body={"target_run": status["id"]})
    summaries = [run["summary"] or (dict(BASELINE, source="검증 샘플 · Supabase")
                 if run["id"] == db.BASELINE_ID else public(snapshot(run, []))) for run in runs]
    references = db.reference_rows()
    legacy = db.setting("reference")
    if legacy:
        references.append(legacy_entry(legacy))
    return jsonify(runs=summaries, job=job, references=reference_counts(references),
                   vision=Vision(MODELS).can_compare,
                   schedule=schedule_value(db.setting("schedule")), scheduler_ready=bool(db.setting("scheduler_ready")),
                   operator=bool(session.get("operator")))


@app.get("/api/runs/<run_id>")
def results(run_id):
    ident = snapshot_id(run_id)
    run = db.first("picasso_runs", id=f"eq.{ident}", status="eq.complete", select=LATEST) if ident else None
    if not run:
        return jsonify(error="수집 기록이 없습니다."), 404
    result = snapshot(run, [row["result"] for row in db.product_rows(ident)])
    return jsonify(**public(result), products=result["products"])


@app.get("/api/weekly/runs")
def weekly_runs():
    runs = db.all_rows("picasso_runs", {"select": "id,created_at", "status": "eq.complete",
                                       "id": f"neq.{db.BASELINE_ID}", "order": "created_at.desc,id.desc"})
    return jsonify(runs=[{"id": run["id"], "at": run["created_at"]} for run in runs])


@app.get("/api/runs/<run_id>/weekly")
def weekly(run_id):
    ident = snapshot_id(run_id)
    if not ident:
        return jsonify(error="수집 기록이 없습니다."), 404
    compare = request.args.get("compare")
    comparison = snapshot_id(compare) if compare else None
    if compare and comparison is None:
        return jsonify(error=INVALID_COMPARISON), 400
    result = db.rest("rpc/picasso_weekly_new_products", method="POST",
                     body={"target_run": ident, "comparison_run": comparison})
    if result is None and compare:
        return jsonify(error=INVALID_COMPARISON), 400
    if result is None:
        return jsonify(error="수집 기록이 없습니다."), 404
    return jsonify(weekly_public(**result))


@app.get("/api/runs/<run_id>/csv")
def export(run_id):
    response = results(run_id)
    if isinstance(response, tuple):
        return response
    data = response.get_json()
    output = io.StringIO()
    fields = ["brand", "tabs", "product_id", "name", "code", "price", "thumbnail_type", "similarity",
              "score", "thumbnail_url", "image_file", "detail_url", "error"]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in data["products"]:
        writer.writerow(dict(row, tabs="|".join(row["tabs"])))
    return Response("\ufeff" + output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="picasso-{data["id"]}.csv"'})


@app.post("/api/schedule")
def schedule():
    try:
        rule = parse_schedule(request.get_json(silent=True), datetime.now(timezone.utc))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    db.save_setting("schedule", rule)
    return jsonify(ok=True, schedule=rule)


@app.get("/api/schedule/logs")
def schedule_logs():
    page = max(1, request.args.get("page", default=1, type=int))
    rows = db.rest("picasso_runs", query={"scheduled_at": "not.is.null", "order": "scheduled_at.desc",
                   "select": "id,scheduled_at,created_at,finished_at,status,error,failure_stage,expected_count,errors:summary->summary->errors",
                   "limit": LOG_PAGE_SIZE + 1, "offset": (page - 1) * LOG_PAGE_SIZE})
    return jsonify(logs=rows[:LOG_PAGE_SIZE], more=len(rows) > LOG_PAGE_SIZE)


def active_run():
    return db.first("picasso_runs", status="in.(queued,running)", select=LATEST)


@app.post("/api/reference")
def reference():
    if active_run():
        return jsonify(error="수집이 끝난 뒤 기준 이미지를 변경하세요."), 409
    try:
        entry, content = prepare_reference(request.form, request.files, MODELS)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except OSError:
        return jsonify(error="사용할 수 없는 이미지입니다."), 400
    if (entry["brand"] == "WHO" and entry["reference_group"] == "women"
            and entry["storage_path"] == db.setting("reference")):
        return jsonify(ok=True, added=False)
    db.put_image(entry["storage_path"], content)
    inserted = db.rest("picasso_references", method="POST", body=entry,
                       headers={"Prefer": "resolution=ignore-duplicates,return=representation"})
    return jsonify(ok=True, added=bool(inserted))


@app.patch("/api/runs/<run_id>/product")
@app.patch("/api/runs/<run_id>/products", defaults={"bulk": True})
def review(run_id, bulk=False):
    ident = snapshot_id(run_id)
    if not ident:
        return jsonify(error="수집 기록이 없습니다."), 404
    try:
        keys, changes = review_changes(request.get_json(silent=True), bulk)
    except ValueError as error:
        return jsonify(error=str(error)), 400

    for _ in range(3):
        run = db.first("picasso_runs", id=f"eq.{ident}", status="eq.complete", select="id,created_at,review_version")
        if not run:
            return jsonify(error="수집 기록이 없습니다."), 404
        rows = [p["result"] for p in db.product_rows(ident)]
        products = apply_review(rows, keys, changes)
        if products is None:
            return jsonify(error="이 수집 기록에 없는 상품이 선택되었습니다."), 404
        report = public(snapshot(run, rows))
        saved = db.rest("rpc/picasso_review_products", method="POST",
                        body={"target_run": ident, "expected_version": run["review_version"],
                              "products": products, "changes": {}, "report": report})
        if saved:
            return jsonify(ok=True, updated=len(keys))
    return jsonify(error="수집 기록에 다른 변경이 저장되었습니다. 다시 시도하세요."), 409


@app.get("/images/<run_id>/<filename>")
def image(run_id, filename):
    ident = snapshot_id(run_id)
    match = re.fullmatch(r"(WHO|SPAO)-([0-9]+)\.jpg", filename)
    if not ident or not match:
        return "", 404
    row = db.first("picasso_products", run_id=f"eq.{ident}", brand=f"eq.{match[1]}",
                   product_id=f"eq.{match[2]}", select="result")
    path = row["result"].get("storage_path") if row else None
    if not path or not re.fullmatch(r"thumbnails/[a-f0-9]{64}\.jpg", path):
        return "", 404
    return Response(db.get_image(path), mimetype="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


def collection_references():
    references = db.reference_rows(embeddings=True)
    legacy = db.setting("reference")
    if legacy and not any(row["brand"] == "WHO" and row["storage_path"] == legacy for row in references):
        references.append(legacy_embedding(legacy_entry(legacy), db.get_image(legacy), MODELS))
    return references


@app.post("/internal/schedule/tick")
def tick():
    if not db.setting("scheduler_ready"):
        return jsonify(id=None)
    queued = db.first("picasso_runs", status="eq.queued", scheduled_at="not.is.null", select="id")
    if queued:
        return jsonify(id=queued["id"])
    stored = db.setting("schedule")
    rule = schedule_value(stored)
    current_time = datetime.now(timezone.utc)
    if not is_due(rule, current_time):
        return jsonify(id=None)
    ident = db.rest("rpc/picasso_claim_scheduled", method="POST",
                    body={"expected_rule": stored, "following_rule": advance_schedule(rule, current_time)})
    return jsonify(id=ident)


@app.post("/internal/start")
def start():
    if not session.get("operator"):
        return jsonify(error="운영자 로그인이 필요합니다."), 401
    prior = db.first("picasso_runs", status="eq.complete", select="id", order="created_at.desc")
    references = collection_references()
    try:
        active = db.rest("picasso_runs", method="POST",
                         body={"reference_sets": references,
                               "prior_run": prior["id"] if prior else None},
                         headers={"Prefer": "return=representation"})[0]
    except db.StoreError as error:
        if error.status != 409:
            raise
        active = active_run()
        if not active:
            return jsonify(error="수집 요청을 다시 실행하세요."), 409
    return jsonify(id=active["id"])


@app.post("/internal/plan/<uuid:run_id>")
def plan(run_id):
    filters = {"id": f"eq.{run_id}"}
    run = db.first("picasso_runs", **filters)
    if not run or run["status"] in {"complete", "failed"}:
        return jsonify(batches=0)
    if run["status"] == "queued":
        if run.get("scheduled_at"):
            references = collection_references()
        else:
            references = run["reference_sets"]
        try:
            items = list_products()
        except ProductListError as error:
            return jsonify(error=str(error)), 502
        for row in items:
            row["product_id"] = str(row["product_id"])
        db.rest("picasso_runs", method="PATCH",
                query={**filters, "status": "eq.queued"},
                body={"manifest": items, "expected_count": len(items), "status": "running", "reference_sets": references})
        run = db.first("picasso_runs", **filters)
    return jsonify(batches=(run["expected_count"] + BATCH_SIZE - 1) // BATCH_SIZE)


def approved(url, hosts):
    target = urlsplit(url)
    return target.scheme == "https" and target.hostname in hosts and not target.username


@app.post("/internal/batch/<uuid:run_id>/<int:index>")
def batch(run_id, index):
    ident = str(run_id)
    run = db.first("picasso_runs", id=f"eq.{ident}")
    if not run or run["status"] != "running":
        return jsonify(ok=True)
    items = run["manifest"][index * BATCH_SIZE:(index + 1) * BATCH_SIZE]
    if not items:
        return jsonify(ok=True)
    keys = {(r["brand"], r["product_id"]) for r in items}
    existing = {(r["brand"], r["product_id"]) for r in db.product_rows(ident, keys, "brand,product_id")}
    prior = db.first("picasso_runs", id=f"eq.{run['prior_run']}", select="reference_sets") if run["prior_run"] else None
    previous = {(p["result"]["brand"], p["result"]["product_id"]): p["result"]
                for p in db.product_rows(run["prior_run"], keys)} if prior is not None else {}
    missing = [row for row in items if (row["brand"], row["product_id"]) not in existing]
    if missing:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            references = run["reference_sets"]
            if references is None:
                legacy = run["reference_path"]
                references = [legacy_embedding(legacy_entry(legacy), db.get_image(legacy), MODELS)] if legacy else []
            previous_references = prior["reference_sets"] if prior else None
            classifier = Vision(MODELS, references, changed_brands(references, previous_references))

            def collect_one(row):
                if not approved(row["thumbnail_url"], IMAGE_HOSTS):
                    row["thumbnail_url"] = ""
                if row["brand"] == "WHO" and not approved(row["detail_url"], LIST_HOSTS):
                    row["detail_url"] = ""
                key = (row["brand"], row["product_id"])
                result = process_product(row, classifier, previous.get(key, {}), path, ocr_budget=95)
                if result["image_file"]:
                    data = (path / result["image_file"]).read_bytes()
                    location = f"thumbnails/{hashlib.sha256(data).hexdigest()}.jpg"
                    db.put_image(location, data)
                    result["storage_path"] = location
                db.rest("picasso_products", method="POST",
                        body={"run_id": ident, "brand": key[0], "product_id": key[1], "result": result},
                        headers={"Prefer": "resolution=ignore-duplicates,return=minimal"})

            with ThreadPoolExecutor(max_workers=BATCH_SIZE) as pool:
                list(pool.map(collect_one, missing))
    done = db.rest("rpc/picasso_progress", method="POST", body={"target_run": ident})
    if done == run["expected_count"]:
        summary = public(snapshot(run, [r["result"] for r in db.product_rows(ident)]))
        db.rest("rpc/picasso_complete", method="POST", body={"target_run": ident, "report": summary})
    return jsonify(ok=True)


@app.post("/internal/fail/<uuid:run_id>")
def fail(run_id):
    data = request.get_json(silent=True) or {}
    stage = data.get("stage")
    if not isinstance(stage, str) or stage not in FAILURE_STAGES:
        stage = "dispatch"
    detail = data.get("detail") if isinstance(data.get("detail"), str) else ""
    db.rest("picasso_runs", method="PATCH", query={"id": f"eq.{run_id}", "status": "in.(queued,running)"},
            body={"status": "failed", "failure_stage": stage, "error": failure_reason(stage, detail),
                  "finished_at": datetime.now(timezone.utc).isoformat()})
    return jsonify(ok=True)
