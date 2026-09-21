FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DB_PATH=/data/snapshots.db
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY scripts scripts
RUN useradd --create-home --uid 10001 appuser && mkdir /data && chown appuser /data
USER appuser
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
# One worker keeps the in-memory cache and rate limiter consistent; threads handle concurrency.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "8", "--access-logfile", "-", "app:create_app()"]
