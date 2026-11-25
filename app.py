import csv
import hashlib
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

from collect import SPAO, WHO, TABS, collect, fetch
from vision import Vision

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("PICASSO_DATA", ROOT / ".local")).resolve()
DATA.mkdir(parents=True, exist_ok=True)
REFERENCE = DATA / "reference.jpg"
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
    if path.exists():
        return
    with (ROOT / "seed/whoau-women.csv").open(encoding="utf-8-sig") as f:
        comparisons = {row["product_id"]: row["visual_assessment"] for row in csv.DictReader(f)}
    codes = {"5801": "WHLAH1191U", "5811": "WHTMH1222U",
             "5808": "WHYJH2251U", "5813": "WHMWH1153U"}
    rows = []
    with (ROOT / "seed/thumbnails.csv").open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = row.pop("name")
            brand, pid = row["brand"], row["product_id"]
            code = codes.get(pid, "") if brand == "WHO" else name.rpartition("_")[2]
            rows.append(dict(row, name=name, tabs=row["tabs"].split("|"), price=None,
                             code=code, code_status="found" if code else "missing",
                             similarity=comparisons.get(pid, "unscanned") if brand == "WHO" else "unscanned",
                             score=None, reviewed=True, image_file="", error="",
                             detail_url=f"{WHO}/product/detail.html?product_no={pid}" if brand == "WHO" else SPAO))
    save(path, {"id": "20261124-000000-seed", "at": "2026-11-24", "source": "검증 샘플",
                "reference_hash": "seed", "products": rows})


def summarize(products):
    total = len(products)
    model = sum(r["thumbnail_type"] == "model" for r in products)
    product = sum(r["thumbnail_type"] == "product_only" for r in products)
    match = sum(r["thumbnail_type"] == "model" and r["similarity"] == "similar" for r in products)
    return {"total": total, "model": model, "product": product, "match": match,
            "unknown": total - model - product,
            "unclear": sum(r["similarity"] == "unclear" for r in products),
            "code": sum(bool(r["code"]) for r in products),
            "compared": sum(r["similarity"] in ("similar", "other", "unclear") for r in products),
            "errors": sum(bool(r["error"]) for r in products)}


def public(run):
    products = run["products"]
    groups = []
    for brand, tabs in TABS.items():
        for tab in [None, *tabs]:
            rows = [r for r in products if r["brand"] == brand and (tab is None or tab in r["tabs"])]
            groups.append({"brand": brand, "tab": tab, **summarize(rows)})
    return dict(id=run["id"], at=run["at"], source=run["source"],
                groups=groups, summary=summarize(products))


def options():
    return load(DATA / "schedule.json", {"enabled": False, "time": "09:00", "last": ""})


def progress(done, total):
    job.update(done=done, total=total)


def work():
    run_id = now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
    image_dir = DATA / "images" / run_id
    image_dir.mkdir(parents=True)
    try:
        prior = snapshot()
        previous = {(r["brand"], r["product_id"]): r for r in prior["products"]} if prior else {}
        digest = hashlib.sha256(REFERENCE.read_bytes()).hexdigest() if REFERENCE.exists() else ""
        classifier = Vision(MODELS, REFERENCE, not prior or digest != prior["reference_hash"])
        rows = collect(classifier, previous, image_dir, progress)
        save(DATA / f"run-{run_id}.json", {"id": run_id, "at": now().isoformat(),
             "source": "공식몰 수집", "reference_hash": digest, "products": rows})
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
    return jsonify(runs=runs, job=dict(job), schedule=options(), reference=REFERENCE.exists(),
                   vision=(MODELS / "face_detection_yunet_2023mar.onnx").exists())


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
        temp = DATA / ".reference-upload.jpg"
        try:
            with Image.open(request.files["image"].stream) as uploaded:
                picture = ImageOps.exif_transpose(uploaded).convert("RGB")
            picture.thumbnail((1200, 1200))
            picture.save(temp, quality=94)
            if Vision(MODELS, temp).vector is None:
                raise ValueError("얼굴 하나가 선명한 이미지와 설치된 분석 모델이 필요합니다.")
            temp.replace(REFERENCE)
        except (OSError, KeyError, ValueError) as error:
            temp.unlink(missing_ok=True)
            return jsonify(error=str(error)), 400
    return jsonify(ok=True)


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
        item.update(thumbnail_type=kind, similarity=similar if kind == "model" and item["brand"] == "WHO"
                    and "WOMEN" in item["tabs"] else "not_applicable",
                    code=str(data.get("code", ""))[:40].strip(), reviewed=True)
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
