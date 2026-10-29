FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install --no-install-recommends -y git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-powercontext.txt /tmp/requirements-powercontext.txt
RUN pip install --no-cache-dir -r /tmp/requirements-powercontext.txt \
    && groupadd --gid 10001 powercontext \
    && useradd --uid 10001 --gid powercontext --home-dir /app --create-home --shell /usr/sbin/nologin powercontext \
    && mkdir -p /data \
    && chown powercontext:powercontext /data

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POWERCONTEXT_HOME=/data \
    POWERCONTEXT_SERVER_HTTP_HOST=0.0.0.0 \
    POWERCONTEXT_SERVER_HTTP_PORT=8000 \
    POWERCONTEXT_SERVER_ACCESS_MODE=enforced

USER powercontext
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)"]
ENTRYPOINT ["powercontext"]
CMD ["server", "run", "--no-env-file"]
