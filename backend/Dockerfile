FROM nvidia/cuda:12.8.0-cudnn-runtime-ubuntu22.04

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3-pip \
    libsndfile1 \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.11 /usr/bin/python \
    && ln -sf /usr/bin/python3.11 /usr/bin/python3 \
    && python -m pip install --upgrade pip

# 离线安装 PyTorch（从本地 wheel 文件，CUDA 12.8）
COPY docker-wheels/ /wheels/
RUN python -m pip install --no-cache-dir --find-links=/wheels/ /wheels/torch*.whl /wheels/torchaudio*.whl

# 安装其他依赖（从本地 wheels 优先）
COPY requirements-prod.txt ./
RUN python -m pip install --no-cache-dir -r requirements-prod.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
