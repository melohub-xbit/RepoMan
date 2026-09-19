# One container, App Runner's whole deployment story. "A URL in minutes" is the track description.
FROM python:3.13-slim

# git is not optional: intake shallow-clones every submission. ca-certificates for HTTPS clones
# and the deploy probe.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so a code change does not reinstall the world.
COPY pyproject.toml README.md ./
COPY repoman ./repoman
RUN pip install --no-cache-dir .

# Submissions are hostile input and this process reads them. It does not need to be root.
RUN useradd --create-home --uid 10001 repoman \
 && mkdir -p /tmp/repoman && chown -R repoman /tmp/repoman /app
USER repoman

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    REPOMAN_CACHE_DIR=/tmp/repoman \
    PORT=8080

EXPOSE 8080
CMD ["sh", "-c", "uvicorn repoman.web.app:app --host 0.0.0.0 --port ${PORT}"]
