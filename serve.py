"""Start the deployment's HTTP service."""

import os
from threading import Thread

from waitress import serve


if __name__ == "__main__":
    cloud = bool(os.environ.get("PICASSO_WORKER_KEY") and os.environ.get("SUPABASE_URL"))
    if cloud:
        from cloud_app import app
    else:
        from app import app, scheduler

        Thread(target=scheduler, daemon=True).start()
    serve(app, host=os.environ.get("PICASSO_HOST", "0.0.0.0"),
          port=int(os.environ.get("PORT", "8765")), threads=4 if cloud else 8,
          max_request_body_size=app.config["MAX_CONTENT_LENGTH"])
