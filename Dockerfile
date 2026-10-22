# Stage 1: Build stage
FROM python:3.11-slim@sha256:cce12316475d7d56d605f76e1d811d0dc4e7227f2d4afd29951521264f504ff8 AS builder

WORKDIR /app

# Install system dependencies for building Python packages
RUN set -e; \
    ok=; for i in 1 2 3 4 5; do \
      if apt-get update -o APT::Update::Error-Mode=any; then ok=1; break; fi; \
      rm -rf /var/lib/apt/lists/*; sleep 10; \
    done; \
    [ -n "$ok" ]; \
    apt-get install -y --no-install-recommends \
      gcc \
      g++ \
      python3-dev; \
    rm -rf /var/lib/apt/lists/*

# Install poetry
RUN pip install --upgrade pip && \
    pip install poetry==1.8.2 # Use a specific version for reproducibility

# Copy only files needed for dependency installation
COPY pyproject.toml poetry.lock* ./

# Install dependencies from the committed lock.
# --no-root: Don't install the project itself yet
# --only main: Exclude development dependencies (replaces deprecated --no-dev)
# Do not re-lock here. A stale lock used to run `poetry lock` in the image,
# which resolved SQLAlchemy 2.1 without greenlet and the task exited on import.
RUN poetry config virtualenvs.create false && \
    poetry check --lock && \
    poetry install --no-root --only main --no-interaction --no-ansi

# Stage 2: Final stage
FROM python:3.11-slim@sha256:cce12316475d7d56d605f76e1d811d0dc4e7227f2d4afd29951521264f504ff8

WORKDIR /app

# Install runtime dependencies (including postgresql-client for pg_isready)
# and the current ca-certificates.
# - apt-get update fails on any index error (retried for transient mirror errors)
# - ca-certificates must be 20250419+deb13u1 or later
# - the emSign roots are disabled in /etc/ca-certificates.conf and the bundle is regenerated
RUN set -e; \
    ok=; for i in 1 2 3 4 5; do \
      if apt-get update -o APT::Update::Error-Mode=any; then ok=1; break; fi; \
      rm -rf /var/lib/apt/lists/*; sleep 10; \
    done; \
    [ -n "$ok" ]; \
    apt-get install -y --no-install-recommends \
      postgresql-client \
      curl \
      ca-certificates; \
    rm -rf /var/lib/apt/lists/*; \
    dpkg --compare-versions "$(dpkg-query -W -f='${Version}' ca-certificates)" ge 20250419+deb13u1; \
    sed -i -E 's|^(mozilla/emSign_[^/]*\.crt)$|!\1|' /etc/ca-certificates.conf; \
    for c in mozilla/emSign_ECC_Root_CA_-_G3.crt mozilla/Comodo_AAA_Services_root.crt; do \
      grep -qxF "!$c" /etc/ca-certificates.conf || echo "!$c" >> /etc/ca-certificates.conf; \
    done; \
    update-ca-certificates --fresh

# Point Python TLS clients (httpx, requests, botocore) at the system bundle
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    AWS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# Build arguments for version information
ARG BUILD_VERSION="0.0.0-dev"
ARG BUILD_COMMIT="unknown"
ARG BUILD_TIME=""

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV BUILD_VERSION=${BUILD_VERSION}
ENV BUILD_COMMIT=${BUILD_COMMIT}
ENV BUILD_TIME=${BUILD_TIME}

# Create a non-root user
RUN addgroup --system app && adduser --system --group app

# Copy installed dependencies from builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY ./src ./src
COPY ./alembic ./alembic
COPY ./models ./models
COPY alembic.ini .

# Check the CA configuration (bundle subjects, env, certifi version, client resolution)
COPY scripts/check_ca_bundle.py /tmp/check_ca_bundle.py
RUN python /tmp/check_ca_bundle.py && rm -f /tmp/check_ca_bundle.py

# Create logs directory and initial models.json before changing ownership
RUN mkdir /app/logs && \
    echo '{"models": []}' > /app/models.json

# Change ownership to non-root user
RUN chown -R app:app /app

# Switch to non-root user
USER app

# Expose the port the app runs on
EXPOSE 8000

# Run single uvicorn worker per container
# Migrations should be run separately (e.g., manually or via a dedicated job)
# ECS/Fargate will scale by running multiple tasks, each with its own uvicorn instance
# This provides better isolation, simpler architecture, and improved ALB stickiness
CMD ["uvicorn", "src.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--timeout-keep-alive", "75", \
     "--timeout-graceful-shutdown", "30", \
     "--limit-concurrency", "1000", \
     "--backlog", "2048", \
     "--no-access-log"] 