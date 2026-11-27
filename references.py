import hashlib
import io
import re

from PIL import Image, ImageOps

from snapshots import TABS
from vision import Vision

GROUPS = ("men", "women")
REFERENCE_FILENAME = re.compile(r"[a-f0-9]{64}\.jpg")
REFERENCE_PREVIEW_PREFIX = "/api/reference/"
REFERENCE_PREVIEW_HEADERS = {"Cache-Control": "private, no-store", "Cross-Origin-Resource-Policy": "same-origin"}


def prepare_reference(form, files, models):
    brand, group = form.get("brand", "WHO"), form.get("group", "women")
    if brand not in TABS or group not in GROUPS:
        raise ValueError("브랜드와 남성·여성 그룹을 선택하세요.")
    if "image" not in files:
        raise ValueError("기준 이미지를 선택하세요.")
    try:
        with Image.open(files["image"].stream) as upload:
            picture = ImageOps.exif_transpose(upload).convert("RGB")
    except Image.DecompressionBombError as error:
        raise ValueError("이미지 해상도를 낮춘 뒤 다시 올려 주세요.") from error
    picture.thumbnail((1200, 1200))
    classifier = Vision(models)
    if not classifier.can_compare:
        raise ValueError("얼굴 분석 모델을 사용할 수 없습니다.")
    buffer = io.BytesIO()
    picture.save(buffer, format="JPEG", quality=94)
    content = buffer.getvalue()
    with Image.open(io.BytesIO(content)) as normalized:
        embedding = classifier.reference_embedding(normalized)
    return ({"brand": brand, "reference_group": group, "storage_path": reference_path(content),
             "embedding": embedding}, content)


def reference_path(content):
    return f"reference/{hashlib.sha256(content).hexdigest()}.jpg"


def legacy_entry(path):
    return {"brand": "WHO", "reference_group": "women", "storage_path": path}


def legacy_embedding(entry, content, models):
    with Image.open(io.BytesIO(content)) as image:
        return dict(entry, embedding=Vision(models).reference_embedding(image.convert("RGB")))


def reference_counts(entries):
    counts = {brand: {group: set() for group in GROUPS} for brand in TABS}
    for entry in entries:
        counts[entry["brand"]][entry["reference_group"]].add(entry["storage_path"])
    return {brand: {group: len(paths) for group, paths in groups.items()}
            for brand, groups in counts.items()}


def reference_filenames(entries, brand, group):
    names = set()
    for entry in entries:
        if entry["brand"] == brand and entry["reference_group"] == group:
            path = entry["storage_path"]
            if path.startswith("reference/"):
                filename = path.removeprefix("reference/")
                if REFERENCE_FILENAME.fullmatch(filename):
                    names.add(filename)
    return sorted(names)


def changed_brands(entries, previous):
    if previous is None:
        return set(TABS)

    def paths(rows, brand):
        return {r["storage_path"] for r in rows if r["brand"] == brand}

    return {brand for brand in TABS if paths(entries, brand) != paths(previous, brand)}
