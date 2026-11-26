import csv
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPAO = "https://www.spao.com"
WHO = "https://whoau.com"
TABS = {"SPAO": {"우먼": 2605000005, "맨": 2605000008},
        "WHO": {"WOMEN": 25, "MEN": 24}}
SEED_ID = "20261124-000000-seed"
INVALID_COMPARISON = "집계 기록보다 이전의 완료된 공식몰 수집 기록을 선택하세요."


def seed_snapshot():
    with (ROOT / "seed/whoau-women.csv").open(encoding="utf-8-sig") as f:
        comparisons = {row["product_id"]: row["visual_assessment"] for row in csv.DictReader(f)}
    with (ROOT / "seed/whoau-codes.csv").open(encoding="utf-8") as f:
        codes = {row["product_id"]: row["code"] for row in csv.DictReader(f)}
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
    return {"id": SEED_ID, "at": "2026-11-24", "source": "검증 샘플",
                "reference_hash": "seed", "products": rows}


def summarize(products):
    total = len(products)
    similarities = [r["similarity"] for r in products if r["thumbnail_type"] == "model"]
    model = len(similarities)
    product = sum(r["thumbnail_type"] == "product_only" for r in products)
    return {"total": total, "model": model, "product": product, "match": similarities.count("similar"),
            "unknown": total - model - product,
            "unclear": similarities.count("unclear"),
            "code": sum(bool(r["code"]) for r in products),
            "compared": sum(value in ("similar", "other", "unclear") for value in similarities),
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


def weekly_public(end_at, window_start, baseline_at=None, products=()):
    if not baseline_at:
        return {"summary": None}
    start_at = max(datetime.fromisoformat(window_start), datetime.fromisoformat(baseline_at))
    return {"start_at": start_at.isoformat(), "end_at": end_at, "baseline_at": baseline_at,
            "summary": summarize(products),
            "brands": {brand: summarize([p for p in products if p["brand"] == brand]) for brand in TABS}}


def local_weekly(run_id, runs, comparison_id=None):
    if run_id == SEED_ID:
        if comparison_id:
            raise ValueError(INVALID_COMPARISON)
        return {"summary": None}
    anchor = next(run for run in runs if run["id"] == run_id)
    through = datetime.fromisoformat(anchor["at"])
    start = through - timedelta(days=7)
    history = sorted((datetime.fromisoformat(run["at"]), run["id"], run["products"])
                     for run in runs if run["id"] != SEED_ID and
                     (datetime.fromisoformat(run["at"]), run["id"]) <= (through, run_id))
    if comparison_id:
        baseline = next((batch for batch in history[:-1] if batch[1] == comparison_id), None)
        if baseline is None:
            raise ValueError(INVALID_COMPARISON)
        start = baseline[0]
        history = [baseline, history[-1]]
    before = [batch for i, batch in enumerate(history) if i == 0 or batch[0] < start]
    if len(history) < 2:
        return {"summary": None}
    recent = history[len(before):]
    known, uncoded = set(), set()
    for _, _, rows in before:
        for row in rows:
            code = row["code"].strip().upper()
            if code:
                known.add((row["brand"], code))
            else:
                uncoded.add((row["brand"], row["product_id"]))
    candidates = {}
    for _, _, rows in reversed(recent):
        for row in sorted(rows, key=lambda r: (r["thumbnail_type"] == "unknown", str(r["product_id"]))):
            code = row["code"].strip().upper()
            if not code:
                continue
            key = (row["brand"], code)
            if (row["brand"], row["product_id"]) in uncoded:
                known.add(key)
            candidates.setdefault(key, row)
    products = [row for key, row in candidates.items() if key not in known]
    return weekly_public(anchor["at"], start.isoformat(), before[-1][0].isoformat(), products)
