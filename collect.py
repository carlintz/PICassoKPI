import csv
import io
import json
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from PIL import Image, ImageOps

SPAO = "https://www.spao.com"
WHO = "https://whoau.com"
TABS = {"SPAO": {"우먼": 2605000005, "맨": 2605000008},
        "WHO": {"WOMEN": 25, "MEN": 24}}
WHO_CODE = re.compile(r"\bWH[A-Z0-9]{7,12}\b")


def fetch(url):
    with urlopen(Request(url, headers={"User-Agent": "PICassoKPI/1.0"}), timeout=18) as response:
        body = response.read(20_000_001)
    if len(body) > 20_000_000:
        raise ValueError("Response exceeded 20 MB")
    return body


def spao_list(tab, category):
    url = f"{SPAO}/api/item/new?size=100&dispCategoryNo={category}"
    payload = json.loads(fetch(url))
    if str(payload["resultCode"]) != "200":
        raise ValueError("SPAO list did not return a successful result")
    for item in payload["data"]:
        name = item["itemName"]
        code = re.search(r"_(SP[A-Z0-9]{6,})\s*$", name)
        images = item.get("image") or []
        image = images[0]["imagePath"] if images else ""
        path = "upload" + image if image.startswith("/") else image
        yield dict(brand="SPAO", tabs=[tab], product_id=item["itemNo"], name=name,
                   price=item.get("sellprice"), code=code[1] if code else "",
                   thumbnail_url=f"https://spao-item.elandrs.com/{path}" if path else "",
                   list_url=f"{SPAO}/u/new", detail_url="")


def who_page(category, page):
    url = f"{WHO}/product/list.html?cate_no={category}&page={page}"
    source = fetch(url).decode()
    listing, paging = source.split("xans-product-listnormal", 1)[1].split("xans-product-normalpaging", 1)
    last = re.search(r'href="[^"]*[?&]page=(\d+)" class="last"', paging)
    parts = re.split(r'<li id="anchorBoxId_(\d+)"', listing)
    return url, int(last[1]) if last else 1, list(zip(parts[1::2], parts[2::2]))


def who_list(tab, category):
    url, last, cards = who_page(category, 1)
    for page in range(1, last + 1):
        if page > 1:
            url, _, cards = who_page(category, page)
        if not cards and last > 1:
            raise ValueError(f"WHO.A.U {tab}: empty page {page}")
        for product_id, card in cards:
            name = re.search(r'<div class="name">\s*<a href="([^"]+)"[^>]*><span[^>]*>([^<]+)', card)
            image = re.search(r'<img class="origin" src="([^"]+)"', card)
            price = re.search(r'class="m_item"><span[^>]*>([\d,]+)', card)
            yield dict(brand="WHO", tabs=[tab], product_id=product_id,
                       name=unescape(name[2]) if name else "",
                       price=int(price[1].replace(",", "")) if price else None, code="",
                       thumbnail_url=urljoin(url, unescape(image[1])) if image else "",
                       list_url=url, detail_url=urljoin(url, unescape(name[1])) if name else
                       f"{WHO}/product/detail.html?product_no={product_id}")


def who_code(url):
    source = fetch(url).decode()
    block = source.split('id="prdDetailContent"', 1)[1].split("</div>", 1)[0]
    images = [unescape(u) for u in re.findall(r'(?:ec-data-src|src)="([^"]+)"', block)
              if urlsplit(u).hostname == "item-imghub.eland.co.kr"]
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "code.png"
        for url in images[:2]:
            with Image.open(io.BytesIO(fetch(url))) as picture:
                image = ImageOps.autocontrast(picture.convert("L"))
            if image.height > 4000:
                image = image.crop((0, 0, image.width, int(image.height * .28)))
            image.save(path)
            layout = subprocess.run(["tesseract", str(path), "stdout", "tsv"],
                                    capture_output=True, text=True, timeout=15, check=True)
            words = list(csv.DictReader(io.StringIO(layout.stdout), delimiter="\t"))
            codes = set(WHO_CODE.findall(" ".join(w["text"] or "" for w in words).upper()))
            if len(codes) == 1:
                return codes.pop()
            # Try the lighter line immediately below the largest title lines.
            lines = [w for w in words if w["level"] == "4"]
            for line in sorted(lines, key=lambda w: int(w["height"]), reverse=True)[:3]:
                bottom = int(line["top"]) + int(line["height"])
                band = image.crop((0, bottom, round(image.width * .7),
                                   min(image.height, bottom + round(image.width * .14))))
                if not band.height:
                    continue
                ImageOps.autocontrast(band).save(path)
                run = subprocess.run(["tesseract", str(path), "stdout", "--psm", "7"],
                                     capture_output=True, text=True, timeout=12, check=True)
                codes = set(WHO_CODE.findall(run.stdout.upper()))
                if len(codes) == 1:
                    return codes.pop()
    return ""


def collect(classifier, previous, directory, update, workers=6):
    products = {}
    def read_tab(brand, tab, category):
        loader = spao_list if brand == "SPAO" else who_list
        return list(loader(tab, category))

    with ThreadPoolExecutor(max_workers=4) as pool:
        tabs = [pool.submit(read_tab, brand, tab, category)
                for brand, mapping in TABS.items() for tab, category in mapping.items()]
        for tab in tabs:
            for item in tab.result():
                key = (item["brand"], item["product_id"])
                existing = products.setdefault(key, item)
                for label in item["tabs"]:
                    if label not in existing["tabs"]:
                        existing["tabs"].append(label)
    if not products:
        raise ValueError("No products received")

    def process(row):
        key = (row["brand"], row["product_id"])
        old = previous.get(key, {})
        row.update(thumbnail_type="unknown", similarity="unscanned", score=None,
                   code_status="missing", error="", image_file="")
        messages = []
        if row["brand"] == "WHO" and old.get("code"):
            row["code"] = old["code"]
        try:
            with Image.open(io.BytesIO(fetch(row["thumbnail_url"]))) as im:
                picture = ImageOps.exif_transpose(im).convert("RGB")
            picture.thumbnail((900, 1200))
            filename = f"{row['brand']}-{int(row['product_id'])}.jpg"
            picture.save(directory / filename, quality=90)
            row["image_file"] = filename
            row.update(classifier.classify(picture, "WOMEN" in row["tabs"]))
            if old.get("thumbnail_url") == row["thumbnail_url"] and old.get("reviewed"):
                row["thumbnail_type"] = old["thumbnail_type"]
                row["reviewed"] = True
                if not classifier.reference_changed:
                    row["similarity"] = old["similarity"]
                if row["thumbnail_type"] != "model":
                    row.update(similarity="not_applicable", score=None)
        except Exception as error:
            messages.append(f"썸네일: {error}")
        if row["brand"] == "WHO" and not row["code"]:
            try:
                row["code"] = who_code(row["detail_url"])
            except Exception as error:
                messages.append(f"품번: {error}")
        row["code_status"] = "found" if row["code"] else "missing"
        row["error"] = " · ".join(messages)
        return row

    with ThreadPoolExecutor(max_workers=workers) as pool:
        result = []
        for row in pool.map(process, products.values()):
            result.append(row)
            if len(result) % 10 == 0 or len(result) == len(products):
                update(len(result), len(products))
    return result
