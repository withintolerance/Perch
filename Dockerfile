FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/nvr
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY static ./static

ENV PYTHONUNBUFFERED=1 TZ=UTC NVR_CONFIG=/config/cameras.yaml
EXPOSE 8090
CMD ["python", "-m", "app"]
