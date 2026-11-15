FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Brussels \
    PI_DATA_DIR=/data

RUN apt-get update \
 && apt-get install -y --no-install-recommends libimage-exiftool-perl tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# icloudpd is vastgepind: een nieuwe versie kan opties of gedrag wijzigen.
RUN pip install --no-cache-dir icloudpd==1.33.0

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Schrijfbaar voor elke gebruiker, zodat de container als willekeurige uid:gid kan draaien
# (de NAS-mappen hebben eigenaar 1024:100).
RUN mkdir -p /data && chmod 777 /data
VOLUME /data

EXPOSE 8000 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

CMD ["photoimporter", "serve"]
