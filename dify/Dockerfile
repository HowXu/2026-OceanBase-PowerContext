FROM python:3.11.14-slim-bookworm
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt ./
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN --mount=type=cache,target=/root/.cache/pip pip install --index-url "$PIP_INDEX_URL" -r requirements.txt
COPY station7 ./station7
COPY scripts ./scripts
COPY static ./static
CMD ["python", "-m", "uvicorn", "station7.app:app", "--host", "0.0.0.0", "--port", "8787", "--workers", "1"]
