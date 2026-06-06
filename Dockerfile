# Multi-stage build (S6.2). The builder stage owns pip + build toolchain;
# the runtime stage receives only the populated /opt/venv plus the app
# tree. This keeps the production image free of setuptools/wheel/build
# artifacts and shrinks the layer that gets re-pulled on every restart.

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Create the venv up front so all the install layers land in a known
# location we can copy across stages in one shot.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy only the metadata pip needs to resolve the install plan. Copying
# the full backend dir here would invalidate this layer on every code
# change; keeping it narrow means the heavy pip install layer caches
# across most rebuilds.
COPY backend/pyproject.toml backend/constraints.txt /tmp/build/backend/
COPY backend/app/__init__.py /tmp/build/backend/app/__init__.py

RUN pip install --upgrade pip && \
    pip install -c /tmp/build/backend/constraints.txt /tmp/build/backend && \
    pip uninstall -y nvidia-nccl-cu12


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PROAI_ENVIRONMENT=production \
    PROAI_API_HOST=0.0.0.0 \
    PROAI_API_PORT=8000 \
    PROAI_LOG_JSON=true \
    PROAI_DOCS_ENABLED=false \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN addgroup --system proai && adduser --system --ingroup proai --home /home/proai proai

# Bring the populated venv across from the builder stage. site-packages
# now ships without the toolchain, and the entrypoint just resolves
# `python` through /opt/venv/bin. We chown at copy time rather than via
# `chown -R` so Docker does not have to rewrite every venv file into a
# new layer — that mistake doubled the image footprint in the first
# pass of the multi-stage migration.
COPY --from=builder --chown=proai:proai /opt/venv /opt/venv

COPY --chown=proai:proai backend /app/backend
COPY --chown=proai:proai frontend /app/frontend

RUN mkdir -p /data && chown proai:proai /data

USER proai

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/ready', timeout=3)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
