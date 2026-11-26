import re

from snapshots import TABS


def review_changes(data, bulk=False):
    if not isinstance(data, dict):
        raise ValueError("분류 값을 확인하세요.")
    kind, similarity = data.get("thumbnail_type"), data.get("similarity")
    if kind not in ("model", "product_only", "unknown") or similarity not in (
        "similar", "other", "unclear", "unscanned", "not_applicable"
    ):
        raise ValueError("분류 값을 확인하세요.")
    products = data.get("products") if bulk else [data]
    if not isinstance(products, list) or not products:
        raise ValueError("분류를 변경할 상품을 선택하세요.")
    keys = set()
    for item in products:
        if not isinstance(item, dict):
            raise ValueError("선택한 상품 정보를 확인하세요.")
        brand, pid = item.get("brand"), str(item.get("product_id", ""))
        if not isinstance(brand, str) or brand not in TABS or not re.fullmatch(r"[0-9]+", pid):
            raise ValueError("선택한 상품 정보를 확인하세요.")
        keys.add((brand, pid))
    changes = {"thumbnail_type": kind, "similarity": similarity if kind == "model" else "not_applicable",
               "reviewed": True}
    if kind != "model":
        changes["score"] = None
    if not bulk:
        code = str(data.get("code", ""))[:40].strip()
        changes.update(code=code, code_status="found" if code else "missing")
    return keys, changes


def apply_review(rows, keys, changes):
    selected = [row for row in rows if (row["brand"], str(row["product_id"])) in keys]
    if len(selected) != len(keys):
        return False
    for row in selected:
        row.update(changes)
    return True
