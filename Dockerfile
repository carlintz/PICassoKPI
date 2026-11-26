FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PICASSO_DATA=/data PORT=8765
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt setup-models.sh ./
RUN pip install --no-cache-dir -r requirements.txt \
    && PICASSO_DATA=/opt/picasso ./setup-models.sh \
    && useradd --system --uid 10001 picasso && mkdir /data && chown picasso /data
ENV PICASSO_MODELS=/opt/picasso/models
COPY app.py collect.py vision.py references.py reviews.py snapshots.py serve.py cloud_app.py cloud_store.py ./
COPY static ./static
COPY seed ./seed
USER picasso
EXPOSE 8765
CMD ["python", "serve.py"]
