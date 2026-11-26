import csv
import json
from pathlib import Path
from shutil import copyfile

from snapshots import public, seed_snapshot

output = Path("public")
(output / "static").mkdir(parents=True, exist_ok=True)
for name in ("app.js", "style.css"):
    copyfile(Path("static") / name, output / "static" / name)
copyfile("static/index.html", output / "index.html")

run = seed_snapshot()
summary = public(run)
demo = {"state": {"runs": [summary], "job": {"state": "idle"},
                  "schedule": {"enabled": False, "time": "09:00"},
                  "references": {}, "vision": False},
        "run": dict(summary, products=run["products"])}
(output / "demo.json").write_text(json.dumps(demo, ensure_ascii=False), encoding="utf-8")
with (output / "demo.csv").open("w", encoding="utf-8-sig", newline="") as file:
    writer = csv.DictWriter(file, fieldnames=[
        "brand", "tabs", "product_id", "name", "code", "thumbnail_type",
        "similarity", "thumbnail_url", "list_url"
    ], extrasaction="ignore")
    writer.writeheader()
    writer.writerows(dict(row, tabs="|".join(row["tabs"])) for row in run["products"])
