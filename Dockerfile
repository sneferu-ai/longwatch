# syntax=docker/dockerfile:1
# LongWatch — one image: FastAPI + the built SPA + ffmpeg + ONNX Runtime (CPU).
#
#   stage spa    : build the Vite/React single-page application
#   stage deps   : install the Python dependencies (and the package) into /venv
#   stage models : fetch the digest-pinned detector + face model and the class map
#   stage runtime: python:3.12-bookworm + ffmpeg, non-root, VOLUME /data
#
# No secrets and no .env files are copied; DATABASE_URL is read from the
# environment only, and the Ed25519 signing key is generated into the data
# volume on first start.

# ---------------------------------------------------------------- spa stage
FROM node:22-bookworm-slim AS spa
WORKDIR /spa
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
ENV LONGWATCH_SPA_OUT=/out
RUN npm run build

# --------------------------------------------------------------- deps stage
FROM python:3.12-bookworm AS deps
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /src
COPY pyproject.toml README.md ./
COPY longwatch/ ./longwatch/
RUN python -m venv /venv \
 && /venv/bin/pip install --no-cache-dir --upgrade pip \
 && /venv/bin/pip install --no-cache-dir .

# ------------------------------------------------------------- models stage
# The detector weights are AGPL-3.0 and are never vendored into this MIT
# repository: they are fetched here, at image-build time, from their canonical
# public sources, with a digest pin when one is available.  Both sources and
# pins live in models/pins.json (run scripts/pin_models.sh once on a networked
# machine to fill the digests in and commit it); a build-arg overrides the
# file.  A supplied digest that mismatches fails the build.  A detector fetch
# that cannot run at all (no network) falls back to the in-repo contract-valid
# fixture — a plain file copy, so the fallback itself needs no network, no pip
# and no generator toolchain — and records MODEL_PROVENANCE=fixture; a fetch
# without a pin records fetched_unpinned.  Every surface says which, so no
# surface may claim the real weights were fetched and verified when they were
# not.
FROM python:3.12-bookworm AS models
# Canonical public sources, baked as defaults so the no-build-args image build
# fetches the real artifacts rather than the fixture: the Ultralytics YOLOv8n
# COCO ONNX export and the OpenCV Zoo YuNet face detector.  models/pins.json
# carries the same URLs plus the digest pins (empty until scripts/pin_models.sh
# runs once on a networked machine and the file is committed); a build-arg
# overrides either.  Each artifact has its own `none` sentinel that disables
# only that artifact's fetch; the two fetches are independent, so a detector
# fetch that fails can never cost the build its face model, and a fixture-only
# build passes `none` for both.
ARG LONGWATCH_MODEL_URL="https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.onnx"
ARG LONGWATCH_MODEL_SHA256=""
ARG LONGWATCH_FACE_MODEL_URL="https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
ARG LONGWATCH_FACE_MODEL_SHA256=""
WORKDIR /models
COPY models/coco80.json models/pins.json models/resolve_pin.py ./
# The in-repo contract-valid fixture ([1,3,640,640] float32 in, [1,4+80,M] out,
# all-zero detections) is committed at tests/fixtures/model_fixture.onnx; the
# fallback below is a file copy, so it works on a build host with no network
# and no Python package index at all.
COPY tests/fixtures/model_fixture.onnx ./model_fixture.onnx
RUN set -eu; \
    model_urls="$(python3 resolve_pin.py yolov8n.onnx url "$LONGWATCH_MODEL_URL")"; \
    model_sha="$(python3 resolve_pin.py yolov8n.onnx sha256 "$LONGWATCH_MODEL_SHA256")"; \
    face_urls="$(python3 resolve_pin.py yunet.onnx url "$LONGWATCH_FACE_MODEL_URL")"; \
    face_sha="$(python3 resolve_pin.py yunet.onnx sha256 "$LONGWATCH_FACE_MODEL_SHA256")"; \
    download() { \
      url="$1"; path="$2"; \
      if command -v curl >/dev/null 2>&1; then \
        curl -fsSL --retry 3 --max-time 600 -o "$path" "$url"; \
      else \
        python3 -c 'import socket, sys, urllib.request; socket.setdefaulttimeout(600); \
urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' "$url" "$path"; \
      fi; \
    }; \
    fetch() { \
      urls="$1"; path="$2"; want="$3"; \
      if [ -z "$urls" ]; then echo "no URL supplied for $path"; return 1; fi; \
      if [ "$urls" = "none" ]; then echo "fetch disabled for $path by build-arg"; return 1; fi; \
      for url in $urls; do \
        echo "fetching $path from $url"; \
        if ! download "$url" "$path"; then \
          echo "fetch failed: $url" >&2; rm -f "$path"; continue; \
        fi; \
        if [ ! -s "$path" ]; then \
          echo "empty download: $url" >&2; rm -f "$path"; continue; \
        fi; \
        got="$(sha256sum "$path" | cut -d' ' -f1)"; \
        if [ -n "$want" ]; then \
          if [ "$got" != "$want" ]; then \
            echo "DIGEST MISMATCH for $path: expected $want got $got" >&2; return 2; \
          fi; \
          echo "PINNED $path sha256=$got"; \
        else \
          echo "UNPINNED $path sha256=$got (models/pins.json carries no digest yet; run scripts/pin_models.sh)"; \
        fi; \
        return 0; \
      done; \
      return 1; \
    }; \
    provenance="fixture"; \
    set +e; \
    fetch "$model_urls" yolov8n.onnx "$model_sha"; \
    status=$?; \
    set -e; \
    if [ "$status" = "2" ]; then exit 2; fi; \
    if [ "$status" = "0" ]; then \
      if [ -n "$model_sha" ]; then provenance="fetched"; else provenance="fetched_unpinned"; fi; \
    else \
      echo "detector fetch unavailable — the in-repo contract fixture will be used"; \
    fi; \
    set +e; \
    fetch "$face_urls" yunet.onnx "$face_sha"; \
    face_status=$?; \
    set -e; \
    if [ "$face_status" = "2" ]; then exit 2; fi; \
    if [ "$face_status" != "0" ]; then \
      echo "face model unavailable — export-time pixelation will fail closed (as specified)"; \
    fi; \
    if [ ! -s yolov8n.onnx ]; then \
      cp model_fixture.onnx yolov8n.onnx; \
    fi; \
    if [ ! -s yunet.onnx ]; then touch yunet.onnx.absent; fi; \
    printf '%s\n' "$provenance" > MODEL_PROVENANCE; \
    sha256sum yolov8n.onnx | cut -d' ' -f1 > MODEL_SHA256; \
    if [ -s yunet.onnx ]; then \
      sha256sum yunet.onnx | cut -d' ' -f1 > FACE_MODEL_SHA256; \
    else \
      : > FACE_MODEL_SHA256; \
    fi; \
    cat MODEL_PROVENANCE MODEL_SHA256

# ------------------------------------------------------------ runtime stage
FROM python:3.12-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/venv/bin:$PATH" \
    LONGWATCH_DATA_DIR=/data \
    LONGWATCH_MODEL_PATH=/opt/longwatch/models/yolov8n.onnx \
    LONGWATCH_CLASS_MAP=/opt/longwatch/models/coco80.json \
    LONGWATCH_FACE_MODEL_PATH=/opt/longwatch/models/yunet.onnx \
    PORT=8000

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 longwatch

WORKDIR /app
COPY --from=deps /venv /venv
COPY longwatch/ ./longwatch/
COPY migrations/ ./migrations/
COPY models/ ./models/
COPY scripts/ ./scripts/
COPY ed25519_verifier.py verify_bundle.py pyproject.toml README.md LICENSE ./
COPY --from=spa /out ./longwatch/static/
COPY --from=models /models/yolov8n.onnx /opt/longwatch/models/yolov8n.onnx
COPY --from=models /models/coco80.json /opt/longwatch/models/coco80.json
COPY --from=models /models/MODEL_PROVENANCE /models/MODEL_SHA256 /models/FACE_MODEL_SHA256 /opt/longwatch/models/
COPY --from=models /models/yunet.onnx* /opt/longwatch/models/

RUN mkdir -p /data/inbox /data/originals /data/proxies /data/excerpts /data/exports \
             /data/keys /data/tmp /data/fixtures \
 && chown -R longwatch:longwatch /data /opt/longwatch \
 && chmod +x /app/scripts/entrypoint.sh

USER longwatch
VOLUME /data
EXPOSE 8000

# the entrypoint executes the product's real initialization and startup:
# wait for the database → migrate → seed builtin patterns → exec serve
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
