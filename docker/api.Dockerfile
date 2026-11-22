# syntax=docker/dockerfile:1.7

# `misaki[ja]` (pyopenjtalk) is published as an sdist only, so the wheel build needs a
# toolchain that must not ship in the runtime image.
FROM python:3.12-slim AS builder

ARG INSTALL_EXTRAS=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /uvx /bin/

WORKDIR /app

# Dependências primeiro (cache de camada).
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project ${INSTALL_EXTRAS}

COPY backend/ ./
RUN uv sync --frozen --no-dev --no-install-project ${INSTALL_EXTRAS}

# `pyopenjtalk` (misaki[ja]) downloads its open_jtalk dictionary on first use. Bake it in
# so the runtime never reaches the network. No-op when the tts extra is not installed.
RUN if /app/.venv/bin/python -c "import pyopenjtalk" >/dev/null 2>&1; then \
        /app/.venv/bin/python -c \
            "import pyopenjtalk; pyopenjtalk.g2p('こんにちは')" >/dev/null; \
    fi

# German voice for Piper (Kokoro has no German voice). Uses Piper's own downloader against
# the official rhasspy/piper-voices repository, so the runtime image stays offline and
# `app/providers/tts/piper.py` only has to read /app/voices. No-op without the tts extra.
ARG PIPER_VOICE="de_DE-thorsten-medium"
RUN if /app/.venv/bin/python -c "import piper" >/dev/null 2>&1; then \
        /app/.venv/bin/python -m piper.download_voices \
            --download-dir /app/voices "${PIPER_VOICE}" \
        && test -s "/app/voices/${PIPER_VOICE}.onnx" \
        && test -s "/app/voices/${PIPER_VOICE}.onnx.json" \
        && ls -l /app/voices; \
    fi


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# espeak-ng: phonemizer do Kokoro e WAV do smoke test (`scripts/audio_smoke.py`).
# libgomp1: runtime OpenMP do torch CPU.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libsndfile1 \
        libgomp1 \
        espeak-ng \
        ffmpeg \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app /app

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data /home/app/.cache/huggingface \
    && chown -R app:app /app /data /home/app/.cache

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
