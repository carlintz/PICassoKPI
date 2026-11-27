"""Server-only Supabase access for the Vercel collector."""

import json
import os
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

BASELINE_ID = "00000000-0000-4000-8000-000000000001"
BUCKET = "picasso-private"


class StoreError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(f"Supabase request failed ({status})")


def request(path, *, method="GET", query=None, body=None, headers=None, binary=False):
    secret = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    base = os.environ["SUPABASE_URL"].rstrip("/")
    url = base + path + ("?" + urlencode(query) if query else "")
    request_headers = {"apikey": secret, "Authorization": f"Bearer {secret}"}
    data = None
    if body is not None:
        if isinstance(body, bytes):
            data = body
        else:
            data = json.dumps(body, ensure_ascii=False).encode()
            request_headers["Content-Type"] = "application/json"
    request_headers.update(headers or {})
    try:
        with urlopen(Request(url, data=data, headers=request_headers, method=method),
                     timeout=15) as response:
            payload = response.read()
            return payload if binary else json.loads(payload) if payload else None
    except HTTPError as error:
        raise StoreError(error.code) from None


def rest(table, **options):
    return request(f"/rest/v1/{table}", **options)


def first(table, **filters):
    rows = rest(table, query={**filters, "limit": 1})
    return rows[0] if rows else None


def setting(name, default=None):
    row = first("picasso_settings", name=f"eq.{name}", select="value")
    return row["value"] if row else default


def save_setting(name, value):
    rest("picasso_settings", method="POST", body={"name": name, "value": value},
         headers={"Prefer": "resolution=merge-duplicates,return=minimal"})


def product_rows(run_id, keys=None, select="result"):
    filters = {"run_id": f"eq.{run_id}", "select": select, "order": "brand,product_id"}
    if keys:
        filters["product_id"] = "in.(" + ",".join(sorted({str(int(pid)) for _, pid in keys})) + ")"
    return all_rows("picasso_products", filters)


def reference_rows(embeddings=False, brand=None, group=None):
    columns = "brand,reference_group,storage_path"
    filters = {"select": columns + (",embedding" if embeddings else ""), "order": columns}
    if brand is not None:
        filters["brand"] = f"eq.{brand}"
    if group is not None:
        filters["reference_group"] = f"eq.{group}"
    return all_rows("picasso_references", filters)


def all_rows(table, filters):
    rows = []
    while True:
        batch = rest(table, query={**filters, "limit": 500, "offset": len(rows)})
        rows.extend(batch)
        if len(batch) < 500:
            return rows


def put_image(path, data):
    request(f"/storage/v1/object/{BUCKET}/{quote(path)}", method="POST", body=data,
            headers={"Content-Type": "image/jpeg", "x-upsert": "true"})


def get_image(path):
    return request(f"/storage/v1/object/authenticated/{BUCKET}/{quote(path)}", binary=True)
