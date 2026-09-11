FROM python:3.12-slim AS base

# Install Infisical CLI for runtime secret injection
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl gnupg && \
    curl -1sLf 'https://dl.cloudsmith.io/public/infisical/infisical-cli/setup.deb.sh' | bash - && \
    apt-get install -y infisical && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

# Secret injection wrapper: Infisical pulls secrets into env before starting uvicorn.
# Set INFISICAL_TOKEN and INFISICAL_PROJECT_ID in the runtime environment (not baked
# into the image). When Infisical vars are absent the wrapper skips injection and
# falls back to whatever env vars are already set (local dev).
ENTRYPOINT ["/bin/sh", "-c", "\
  if [ -n \"${INFISICAL_TOKEN:-}\" ] && [ -n \"${INFISICAL_PROJECT_ID:-}\" ]; then \
    echo '[review-surface] Injecting secrets from Infisical...' && \
    infisical run \
      --projectId \"${INFISICAL_PROJECT_ID}\" \
      --env \"${INFISICAL_ENV:-prod}\" \
      --token \"${INFISICAL_TOKEN}\" \
      --bash -c 'exec uvicorn main:app --host 0.0.0.0 --port ${REVIEW_PORT:-8080} --log-level ${REVIEW_LOG_LEVEL:-info}'; \
  else \
    echo '[review-surface] No Infisical creds; starting with local env vars.' && \
    exec uvicorn main:app --host 0.0.0.0 --port ${REVIEW_PORT:-8080} --log-level ${REVIEW_LOG_LEVEL:-info}; \
  fi"]