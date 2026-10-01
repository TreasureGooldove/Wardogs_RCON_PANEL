FROM public.ecr.aws/docker/library/python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PANEL_DB_PATH=/var/lib/wardogs/panel.sqlite3

WORKDIR /srv/backend

COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN python -m pip install --no-cache-dir . "uvicorn[standard]>=0.30,<1" \
    && mkdir -p /var/lib/wardogs \
    && chown 10001:10001 /var/lib/wardogs

COPY frontend/dist /srv/frontend/dist

# Source extraction may happen under umask 077; the unprivileged runtime
# must be able to traverse application directories and read bundled files.
RUN chmod -R a+rX /srv/backend/app /srv/frontend/dist

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/', timeout=3).close()" || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
