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
                  "reference": False, "vision": False},
        "run": dict(summary, products=run["products"])}
(output / "demo.json").write_text(json.dumps(demo, ensure_ascii=False), encoding="utf-8")
copyfile("seed/thumbnails.csv", output / "demo.csv")
