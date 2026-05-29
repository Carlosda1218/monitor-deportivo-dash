# CombatIQ - Dockerfile para Railway
# Garantiza que libxcb1 + dependencias del sistema esten siempre presentes.
# Evita el problema de nixpacks.toml que Railway puede ignorar segun el builder.

FROM python:3.12-slim-bookworm

# Dependencias del sistema requeridas por:
# - opencv-python (transitive de ultralytics) → libxcb1, libgl1, libsm6, libxext6, libxrender1
# - render OpenGL ES de opencv/mediapipe   → libgles2 (libGLESv2.so.2), libegl1 (libEGL.so.1)
# - mediapipe → libgomp1
# - video decoding → ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxcb1 \
    libgl1 \
    libgles2 \
    libegl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Setup app
WORKDIR /app

# Copiar requirements primero para aprovechar cache Docker
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copiar codigo
COPY . .

# Railway provee PORT en runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# IMPORTANTE: --preload obligatorio para Dash con --workers 1
# Usamos gthread (sync con threads) en lugar de gevent porque gevent
# rompe la conexion SSL de httpx que usa el SDK de Anthropic (causaba
# ConnectionError silencioso al generar lecturas IA).
CMD gunicorn app:server \
    --bind 0.0.0.0:$PORT \
    --workers 1 \
    --threads 8 \
    --timeout 300 \
    --preload \
    --log-level info
