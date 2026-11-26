import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPAO = "https://www.spao.com"
WHO = "https://whoau.com"
TABS = {"SPAO": {"우먼": 2605000005, "맨": 2605000008},
        "WHO": {"WOMEN": 25, "MEN": 24}}


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
    return {"id": "20261124-000000-seed", "at": "2026-11-24", "source": "검증 샘플",
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
