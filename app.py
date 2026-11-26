import csv
import hmac
import io
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from threading import Lock, Thread
from uuid import uuid4
from zoneinfo import ZoneInfo

from flask import Flask, Response, abort, jsonify, request, send_from_directory
from PIL import Image, ImageOps

from collect import TABS, collect, fetch
from references import changed_brands, legacy_embedding, legacy_entry, prepare_reference, reference_counts, reference_path
from snapshots import public, seed_snapshot
from vision import Vision

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("PICASSO_DATA", ROOT / ".local")).resolve()
DATA.mkdir(parents=True, exist_ok=True)
REFERENCE = DATA / "reference.jpg"
REFERENCES = DATA / "references.json"
MODELS = Path(os.environ.get("PICASSO_MODELS", DATA / "models")).resolve()
WEB_USER = os.environ.get("PICASSO_WEB_USER", "")
WEB_PASSWORD = os.environ.get("PICASSO_WEB_PASSWORD", "")
if bool(WEB_USER) != bool(WEB_PASSWORD):
    raise RuntimeError("Set both PICASSO_WEB_USER and PICASSO_WEB_PASSWORD.")
SEOUL = ZoneInfo("Asia/Seoul")
app = Flask(__name__, static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
lock = Lock()
job = {"state": "idle"}


@app.before_request
def protect_workspace():
    if WEB_USER:
        credentials = request.authorization
        if not credentials or credentials.type != "basic" or not (
            hmac.compare_digest((credentials.username or "").encode(), WEB_USER.encode())
            and hmac.compare_digest((credentials.password or "").encode(), WEB_PASSWORD.encode())
        ):
            return Response("Authentication required", 401,
                            {"WWW-Authenticate": 'Basic realm="PICasso KPI", charset="UTF-8"'})
    if request.method in {"POST", "PATCH", "PUT", "DELETE"} and request.headers.get("X-PICasso-Request") != "1":
        return jsonify(error="대시보드에서 요청을 다시 실행하세요."), 403


def load(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def save(path, value):
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def now():
    return datetime.now(SEOUL)


def files():
    return sorted(DATA.glob("run-*.json"), reverse=True)


def snapshot(run_id=None):
    runs = files()
    if run_id:
        runs = [path for path in runs if path.stem == "run-" + run_id]
    return load(runs[0]) if runs else None


def seed():
    path = DATA / "run-20261124-000000-seed.json"
    baseline = seed_snapshot()
    if not path.exists():
        save(path, baseline)
        return
    run = load(path)
    codes = {r["product_id"]: r["code"] for r in baseline["products"]
             if r["brand"] == "WHO" and r["code"]}
    changed = False
    for row in run["products"]:
        if row["brand"] == "WHO" and not row["code"] and row["product_id"] in codes:
            row.update(code=codes[row["product_id"]], code_status="found")
            changed = True
    if changed:
        save(path, run)


def options():
    return load(DATA / "schedule.json", {"enabled": False, "time": "09:00", "last": ""})


def progress(done, total):
    job.update(done=done, total=total)


def reference_library(embeddings=False):
    entries = load(REFERENCES, [])
    if REFERENCE.exists():
        content = REFERENCE.read_bytes()
        entry = legacy_entry(reference_path(content))
        if embeddings:
            entry = legacy_embedding(entry, content, MODELS)
        entries.append(entry)
    return entries


def work():
    run_id = now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
    image_dir = DATA / "images" / run_id
    image_dir.mkdir(parents=True)
    try:
        prior = snapshot()
        previous = {(r["brand"], r["product_id"]): r for r in prior["products"]} if prior else {}
        references = reference_library(embeddings=True)
        previous_references = prior.get("reference_sets") if prior else None
        classifier = Vision(MODELS, references, changed_brands(references, previous_references))
        rows = collect(classifier, previous, image_dir, progress)
        reference_sets = [{k: v for k, v in entry.items() if k != "embedding"} for entry in references]
        save(DATA / f"run-{run_id}.json", {"id": run_id, "at": now().isoformat(),
             "source": "공식몰 수집", "reference_sets": reference_sets, "products": rows})
        job.update(state="complete", message="수집 완료", id=run_id)
    except Exception as error:
        job.update(state="failed", message=str(error))


def launch():
    if job["state"] == "running":
        return False
    job.clear()
    job.update(state="running", done=0, total=0, message="상품 목록 수집 중")
    Thread(target=work, daemon=True).start()
    return True


def scheduler():
    while True:
        with lock:
            rule = options()
            date = now().date().isoformat()
            if rule["enabled"] and rule["last"] != date and now().strftime("%H:%M") >= rule["time"]:
                if launch():
                    save(DATA / "schedule.json", dict(rule, last=date))
        time.sleep(15)


@app.get("/")
def home():
    return app.send_static_file("index.html")


@app.get("/api/state")
def state():
    runs = [public(load(path)) for path in files()]
    return jsonify(runs=runs, job=dict(job), schedule=options(),
                   references=reference_counts(reference_library()), vision=Vision(MODELS).can_compare)


@app.get("/api/runs/<run_id>")
def results(run_id):
    run = snapshot(run_id)
    if not run:
        return jsonify(error="수집 기록이 없습니다."), 404
    return jsonify(**public(run), products=run["products"])


@app.post("/api/collect")
def collect_now():
    with lock:
        return (jsonify(ok=True), 202) if launch() else (jsonify(error="수집 중입니다."), 409)


@app.post("/api/schedule")
def schedule():
    data = request.get_json()
    try:
        datetime.strptime(data["time"], "%H:%M")
        if len(data["time"]) != 5 or not isinstance(data["enabled"], bool):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return jsonify(error="유효한 집계 시각을 입력하세요."), 400
    with lock:
        save(DATA / "schedule.json", dict(options(), enabled=data["enabled"], time=data["time"]))
    return jsonify(ok=True)


@app.post("/api/reference")
def reference():
    with lock:
        if job["state"] == "running":
            return jsonify(error="수집이 끝난 뒤 기준 이미지를 변경하세요."), 409
        try:
            entry, content = prepare_reference(request.form, request.files, MODELS)
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except OSError:
            return jsonify(error="사용할 수 없는 이미지입니다."), 400
        if any(all(existing[k] == entry[k] for k in ("brand", "reference_group", "storage_path"))
               for existing in reference_library()):
            return jsonify(ok=True, added=False)
        destination = DATA / entry["storage_path"]
        destination.parent.mkdir(exist_ok=True)
        destination.write_bytes(content)
        save(REFERENCES, [*load(REFERENCES, []), entry])
    return jsonify(ok=True, added=True)


@app.patch("/api/runs/<run_id>/product")
def review(run_id):
    data = request.get_json()
    kind, similar = data.get("thumbnail_type"), data.get("similarity")
    if kind not in ("model", "product_only", "unknown") or similar not in (
        "similar", "other", "unclear", "unscanned", "not_applicable"
    ):
        return jsonify(error="분류 값을 확인하세요."), 400
    with lock:
        run = snapshot(run_id)
        if not run:
            return jsonify(error="수집 기록이 없습니다."), 404
        item = next((r for r in run["products"] if r["brand"] == data.get("brand")
                     and r["product_id"] == data.get("product_id")), None)
        if item is None:
            return jsonify(error="상품이 없습니다."), 404
        item.update(thumbnail_type=kind, similarity=similar if kind == "model" else "not_applicable",
                    code=str(data.get("code", ""))[:40].strip(), reviewed=True)
        if kind != "model":
            item["score"] = None
        item["code_status"] = "found" if item["code"] else "missing"
        save(DATA / f"run-{run_id}.json", run)
    return jsonify(ok=True)


@app.get("/api/runs/<run_id>/csv")
def export(run_id):
    run = snapshot(run_id)
    if not run:
        return jsonify(error="수집 기록이 없습니다."), 404
    buffer = io.StringIO()
    fields = ["brand", "tabs", "product_id", "name", "code", "price", "thumbnail_type",
              "similarity", "score", "thumbnail_url", "detail_url", "error"]
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in run["products"]:
        writer.writerow(dict(row, tabs="|".join(row["tabs"])))
    return Response("\ufeff" + buffer.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="picasso-{run_id}.csv"'})


@app.get("/seed-images/<brand>/<product_id>")
def seed_image(brand, product_id):
    if brand not in TABS or not product_id.isascii() or not product_id.isdecimal():
        abort(404)
    directory = DATA / "seed-images"
    filename = f"{brand}-{product_id}.jpg"
    target = directory / filename
    if not target.is_file():
        run = snapshot("20261124-000000-seed")
        row = next((r for r in run["products"] if r["brand"] == brand
                    and r["product_id"] == product_id), None) if run else None
        if not row or not row["thumbnail_url"]:
            abort(404)
        try:
            with Image.open(io.BytesIO(fetch(row["thumbnail_url"]))) as source:
                picture = ImageOps.exif_transpose(source).convert("RGB")
            picture.thumbnail((360, 480))
        except (OSError, ValueError):
            abort(502)
        directory.mkdir(exist_ok=True)
        temporary = directory / f".{uuid4().hex}.jpg"
        picture.save(temporary, quality=88)
        temporary.replace(target)
    return send_from_directory(directory, filename)


@app.get("/images/<run_id>/<filename>")
def image(run_id, filename):
    if not re.fullmatch(r"\d{8}-\d{6}-[a-f0-9]{6}", run_id):
        abort(404)
    return send_from_directory(DATA / "images" / run_id, filename)


seed()

if __name__ == "__main__":
    Thread(target=scheduler, daemon=True).start()
    app.run(host=os.environ.get("PICASSO_HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8765")), threaded=True)
