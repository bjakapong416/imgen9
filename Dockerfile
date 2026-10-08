FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # rembg looks for its ONNX models here; mount a volume to keep them between containers.
    U2NET_HOME=/models

# libgomp: OpenMP runtime used by onnxruntime / numba.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Optionally bake the model into the image (~180 MB) so the first request is fast:
#   docker build --build-arg PRELOAD_MODEL=1 -t imgen9 .
ARG PRELOAD_MODEL=0
ARG REMBG_MODEL=isnet-general-use
ENV REMBG_MODEL=${REMBG_MODEL}
RUN if [ "$PRELOAD_MODEL" = "1" ]; then python -c "from rembg import new_session; new_session('${REMBG_MODEL}')"; fi

COPY main.py .
COPY core ./core
COPY static ./static
COPY tools ./tools
COPY renders/samples ./renders/samples

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
