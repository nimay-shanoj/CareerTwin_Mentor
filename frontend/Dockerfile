# CareerTwin – production image
#
# The build context is the `frontend` directory (see docker-compose.yml),
# because that is where the application lives.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TCP_CHAT_ENABLED=1 \
    TCP_CHAT_HOST=0.0.0.0 \
    TCP_CHAT_PORT=9000

WORKDIR /app

# Dependencies first, so a source-only change does not re-download them.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The application is stateless; the database lives in MySQL.
RUN mkdir -p /app/uploads \
 && useradd --create-home --shell /bin/bash careertwin \
 && chown -R careertwin:careertwin /app
USER careertwin

EXPOSE 5000 9000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/healthz', timeout=4).status == 200 else 1)"

# A single worker on purpose: the connection hub lives in the process that
# owns the sockets, so several workers would not see each other's clients.
# See docs/NETWORK_ARCHITECTURE.md section 4.9.
CMD ["gunicorn", "--worker-class", "gevent", "--workers", "1", \
     "--bind", "0.0.0.0:5000", "--access-logfile", "-", "app:app"]
