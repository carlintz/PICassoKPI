"""Run the dashboard and its scheduler in a single web process."""

import os
from threading import Thread

from waitress import serve

from app import app, scheduler


if __name__ == "__main__":
    Thread(target=scheduler, daemon=True).start()
    serve(app, host=os.environ.get("PICASSO_HOST", "0.0.0.0"),
          port=int(os.environ.get("PORT", "8765")), threads=8,
          max_request_body_size=app.config["MAX_CONTENT_LENGTH"])
