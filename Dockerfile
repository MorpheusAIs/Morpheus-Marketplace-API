# Stage 1: Build stage
FROM python:3.11-slim AS builder

WORKDIR /app

# Install poetry
RUN pip install --upgrade pip && \
    pip install poetry==1.8.2 # Use a specific version for reproducibility

# Copy only files needed for dependency installation
COPY pyproject.toml poetry.lock* ./

# Install dependencies
# --no-root: Don't install the project itself yet
# --only main: Exclude development dependencies (replaces deprecated --no-dev)
RUN poetry config virtualenvs.create false && \
    # Check if lock file is out of sync and regenerate if needed \
    (poetry check --lock || poetry lock) && \
    poetry install --no-root --only main --no-interaction --no-ansi

# Stage 2: Final stage
FROM python:3.11-slim

WORKDIR /app

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
COPY alembic.ini .

# Create logs directory and initial models.json before changing ownership
RUN mkdir /app/logs && \
    echo '{"models": []}' > /app/models.json

# Change ownership to non-root user
RUN chown -R app:app /app

# Switch to non-root user
USER app

# Expose the port the app runs on
EXPOSE 8000

# Run only the application using gunicorn
# Migrations should be run separately (e.g., manually or via a dedicated job)
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "-w", "4", "-b", "0.0.0.0:8000", "src.main:app"] 