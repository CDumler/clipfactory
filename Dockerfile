FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg fonts-dejavu-core curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /srv/app/

RUN mkdir -p /data/raw /data/out /data/tmp /secrets /config

EXPOSE 8080
CMD ["uvicorn", "app.main:api", "--host", "0.0.0.0", "--port", "8080"]
