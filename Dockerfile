# 茅台财报 RAG 问答系统 —— 一个容器同时提供网页界面与 REST API
#   docker compose up --build
#   网页: http://localhost:8501   API: http://localhost:8000/docs
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_ETAG_TIMEOUT=30 \
    HF_HUB_DOWNLOAD_TIMEOUT=60

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# 先装 CPU 版 torch，再装其余依赖（requirements 里的 torch 已满足，不会重复下载）
COPY requirements.txt .
RUN pip install torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu \
 && pip install -r requirements.txt

COPY . .

# 构建期预下载嵌入模型（约 100MB），容器启动即离线可用
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5')" \
 && python -c "import jieba; jieba.initialize()"

EXPOSE 8000 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=600s --retries=5 \
  CMD curl -sf http://localhost:8000/health || exit 1

CMD ["bash", "docker/start.sh"]
