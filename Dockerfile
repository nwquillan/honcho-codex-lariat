FROM python:3.14-slim

ARG LARIAT_UID=10001
ARG LARIAT_GID=10001

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN groupadd --gid "${LARIAT_GID}" lariat \
    && useradd --uid "${LARIAT_UID}" --gid "${LARIAT_GID}" --home-dir /var/lib/lariat --create-home lariat \
    && pip install --no-cache-dir . \
    && mkdir -p /var/lib/lariat/codex /var/empty/lariat \
    && chown -R lariat:lariat /var/lib/lariat /var/empty/lariat

ENV HOME=/var/lib/lariat \
    CODEX_HOME=/var/lib/lariat/codex \
    LARIAT_HOST=0.0.0.0 \
    LARIAT_PORT=5055 \
    LARIAT_WORKDIR=/var/empty/lariat \
    PYTHONUNBUFFERED=1

VOLUME ["/var/lib/lariat/codex"]
USER lariat:lariat

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=8 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5055/readyz', timeout=2).read()"

CMD ["lariat", "serve"]
