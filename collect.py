import csv
import io
import json
import os
import re
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urljoin, urlsplit
from urllib.request import Request, urlopen

from PIL import Image, ImageOps

from snapshots import SPAO, WHO, TABS

WHO_DETAIL_IMAGE_HOSTS = {"item-imghub.eland.co.kr", "item.elandrs.com"}
WHO_CODE = re.compile(r"\bWH[A-Z]{3}[0-9][A-Z0-9]{3}[A-Z]\b")


def fetch(url):
    target = urlsplit(url)
    url = target._replace(path=quote(target.path, safe="/%:@!$&'()*+,;="),
                          query=quote(target.query, safe="%=&+/:?@!$'()*;,")).geturl()
    request = Request(url, headers={"User-Agent": "PICassoKPI/1.0"})
    for attempt in range(2):
        try:
            with urlopen(request, timeout=18) as response:
                body = response.read(20_000_001)
            break
        except HTTPError as error:
            if attempt or error.code not in (502, 503, 504):
                raise
            time.sleep(1)
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


class WhoDetailImages(HTMLParser):
    """Read the lazy images displayed when the detail panel expands."""

    def __init__(self, url):
        super().__init__()
        self.url = url
        self.depth = 0
        self.images = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div":
            if self.depth or attrs.get("id") in {"prdDetailContent", "prdDetailContentLazy"}:
                self.depth += 1
        if tag == "img" and self.depth:
            for field in ("ec-data-src", "data-src", "src"):
                if not attrs.get(field):
                    continue
                url = urljoin(self.url, attrs[field])
                target = urlsplit(url)
                cafe24_detail = (target.hostname == "cafe24.poxo.com"
                                 and re.match(r"/ec\d+/whoaukr/.+/_/web/upload/NNEditor/", target.path))
                if target.scheme == "https" and (target.hostname in WHO_DETAIL_IMAGE_HOSTS or cafe24_detail):
                    if url not in self.images:
                        self.images.append(url)
                    break

    def handle_endtag(self, tag):
        if tag == "div" and self.depth:
            self.depth -= 1


def who_image_code(url, budget):
    deadline = time.monotonic() + budget if budget else float("inf")

    def remaining(limit):
        return min(limit, max(0.1, deadline - time.monotonic()))

    detail = WhoDetailImages(url)
    detail.feed(fetch(url).decode())
    images = sorted(detail.images,
                    key=lambda url: not any(marker in url for marker in ("-EXTRA-", "_detail_page1.")))
    errors = []
    inspected = 0
    ocr_env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "code.png"
        for url in images[:6]:
            if time.monotonic() >= deadline:
                raise TimeoutError("detail OCR time limit reached")
            try:
                content = fetch(url)
                with Image.open(io.BytesIO(content)) as picture:
                    image = picture.convert("L")
                    rescan = picture.width > 1000 or picture.height > 12000
            except (OSError, ValueError) as error:
                errors.append(error)
                continue
            inspected += 1
            if image.height > 4000:
                image = image.crop((0, 0, image.width, 3500))
            image.save(path)
            layout = subprocess.run(["tesseract", str(path), "stdout", "tsv"],
                                    capture_output=True, text=True, timeout=remaining(20), check=True, env=ocr_env)
            words = list(csv.DictReader(io.StringIO(layout.stdout), delimiter="\t"))
            codes = set(WHO_CODE.findall(" ".join(w["text"] or "" for w in words).upper()))
            if len(codes) == 1:
                return codes.pop()
            # Read the small, often light-gray style code directly under the title.
            lines = [w for w in words if w["level"] == "4" and 18 <= int(w["height"]) <= image.width * .12]
            for line in sorted(lines, key=lambda word: int(word["height"]), reverse=True)[:4]:
                bottom = int(line["top"]) + int(line["height"])
                for top_scale, end_scale, width_scale in ((.008, .065, .42), (0, .085, .62)):
                    top = bottom + round(image.width * top_scale)
                    end = min(image.height, bottom + round(image.width * end_scale))
                    if end <= top:
                        continue
                    band = ImageOps.autocontrast(image.crop((0, top, round(image.width * width_scale), end)))
                    band.resize((band.width * 2, band.height * 2), Image.Resampling.LANCZOS).save(path)
                    run = subprocess.run(["tesseract", str(path), "stdout", "--psm", "7"],
                                         capture_output=True, text=True, timeout=remaining(15), check=True, env=ocr_env)
                    text = run.stdout.upper().strip()
                    compact = re.sub(r"\s+", "", text)
                    if WHO_CODE.fullmatch(compact):
                        return compact
                    codes = set(WHO_CODE.findall(text))
                    if len(codes) == 1:
                        return codes.pop()

            if rescan:
                with Image.open(io.BytesIO(content)) as alternate:
                    alternate.thumbnail((1000, 12000))
                    if alternate.height > 3500:
                        alternate = alternate.crop((0, 0, alternate.width, 3500))
                    alternate.convert("RGB").save(path, format="JPEG")
                scan = subprocess.run(["tesseract", str(path), "stdout"], capture_output=True,
                                      text=True, timeout=remaining(15), check=True, env=ocr_env)
                codes = set(WHO_CODE.findall(scan.stdout.upper()))
                if len(codes) == 1:
                    return codes.pop()

    if errors and not inspected:
        raise errors[0]
    return ""


def who_product_id(url):
    target = urlsplit(url)
    match = re.match(r"/product/[^/]+/([0-9]+)(?:/|$)", target.path)
    if match:
        return match[1]
    if target.path == "/product/detail.html":
        values = parse_qs(target.query).get("product_no", [])
        if len(values) == 1 and re.fullmatch(r"[0-9]+", values[0]):
            return values[0]
    return None


def who_mobile_tag_code(url):
    target = urlsplit(url)
    product_id = who_product_id(url)
    if (not product_id or target.scheme != "https"
            or target.hostname not in {"whoau.com", "www.whoau.com", "m.whoau.com"}):
        return ""
    mobile = target._replace(netloc="m.whoau.com", fragment="").geturl()
    try:
        source = fetch(mobile).decode()
    except (OSError, ValueError):
        return ""

    # Match the requested product's official page before using its own tag list.
    canonical = re.search(r'<link\b[^>]*\brel="canonical"[^>]*\bhref="([^"]+)"', source)
    if not canonical or who_product_id(unescape(canonical[1])) != product_id:
        return ""
    tags = re.search(r'<div\b[^>]*\bclass="[^"]*\bxans-product-hashtag\b[^"]*"[^>]*>'
                     r'.*?<ul\b[^>]*>(.*?)</ul>', source, re.DOTALL)
    if not tags:
        return ""
    codes = {text for keyword, text in re.findall(
        r'<a\b[^>]*\bhref="/product/search\.html\?keyword=([^"]+)"[^>]*>\s*#([^<>]+)</a>', tags[1])
        if keyword == text and WHO_CODE.fullmatch(text)}
    return codes.pop() if len(codes) == 1 else ""


def who_code(url, budget=0):
    try:
        code = who_image_code(url, budget)
    except (OSError, ValueError, subprocess.SubprocessError):
        code = who_mobile_tag_code(url)
        if code:
            return code
        raise
    return code or who_mobile_tag_code(url)


class ProductListError(Exception):
    pass


def list_products():
    products = {}

    def read_tab(brand, tab, category):
        loader = spao_list if brand == "SPAO" else who_list
        label = f"{'스파오' if brand == 'SPAO' else '후아유'} {tab}"
        try:
            return list(loader(tab, category))
        except HTTPError as error:
            raise ProductListError(f"{label} 공식몰 응답 오류 (HTTP {error.code})") from error
        except (TimeoutError, URLError, OSError) as error:
            detail = "응답 시간 초과" if isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError) else "연결 오류"
            raise ProductListError(f"{label} 공식몰 {detail}") from error
        except (KeyError, IndexError, ValueError, TypeError) as error:
            raise ProductListError(f"{label} 공식몰 상품 목록 형식 오류") from error

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
        raise ProductListError("공식몰에서 상품을 가져오지 못했습니다.")

    return list(products.values())


def process_product(row, classifier, old, directory, ocr_budget=0):
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
        row.update(classifier.classify(picture, row["brand"]))
        if old.get("thumbnail_url") == row["thumbnail_url"] and old.get("reviewed"):
            row["thumbnail_type"] = old["thumbnail_type"]
            row["reviewed"] = True
            if row["brand"] not in classifier.changed_brands and old["similarity"] != "not_applicable":
                row["similarity"] = old["similarity"]
            if row["thumbnail_type"] != "model":
                row.update(similarity="not_applicable", score=None)
            elif row["similarity"] == "not_applicable":
                row["similarity"] = "unscanned"
    except Exception as error:
        messages.append(f"썸네일: {error}")
    if row["brand"] == "WHO" and not row["code"]:
        try:
            row["code"] = who_code(row["detail_url"], ocr_budget)
        except Exception as error:
            messages.append(f"스타일코드: {error}")
    row["code_status"] = "found" if row["code"] else "missing"
    row["error"] = " · ".join(messages)
    return row


def collect(classifier, previous, directory, update, workers=6):
    products = list_products()

    def process(row):
        return process_product(row, classifier, previous.get((row["brand"], row["product_id"]), {}), directory)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        result = []
        for row in pool.map(process, products):
            result.append(row)
            if len(result) % 10 == 0 or len(result) == len(products):
                update(len(result), len(products))
    return result
