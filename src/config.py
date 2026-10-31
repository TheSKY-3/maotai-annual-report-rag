"""项目全局配置。"""
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

EXTRACTED_DIR = PROJECT / "data" / "extracted"
CHUNKS_FILE = PROJECT / "data" / "chunks" / "baseline.jsonl"
INDEX_FILE = PROJECT / "data" / "index" / "baseline.npz"
GOLD_FILE = PROJECT / "data" / "gold" / "questions.jsonl"
RESULTS_DIR = PROJECT / "results"

YEARS = [2022, 2023, 2024, 2025]

# Embedding（bge 官方建议：查询侧加指令前缀，文档侧不加）
EMBED_MODEL = "BAAI/bge-small-zh-v1.5"
# 本地已下载的模型目录优先（scripts/fast_download.py 或 curl 从 hf-mirror 获取）
_EMBED_LOCAL = PROJECT / "data" / "models" / "bge-small-zh-v1.5"
EMBED_PATH = (
    str(_EMBED_LOCAL) if (_EMBED_LOCAL / "model.safetensors").exists()
    else EMBED_MODEL
)
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

# 生成模型（OpenAI 兼容端点：Ollama 或 llama-cpp-python 均可）
# 环境变量可覆盖: RAG_BASE_URL / RAG_MODEL
OLLAMA_BASE_URL = "http://localhost:11434/v1"
GEN_MODEL = "qwen3:4b"  # 以实际部署的模型为准

COLLECTION = "moutai"
TOP_K = 5
