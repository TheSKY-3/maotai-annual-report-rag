"""项目全局配置。"""
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

EXTRACTED_DIR = PROJECT / "data" / "extracted"
CHUNKS_FILE = PROJECT / "data" / "chunks" / "baseline.jsonl"
INDEX_FILE = PROJECT / "data" / "index" / "baseline.npz"
# E5 上下文增强索引（语境句 + 原文，scripts/build_contextual_chunks.py 生成）
CONTEXTUAL_CHUNKS_FILE = PROJECT / "data" / "chunks" / "contextual.jsonl"
CONTEXTUAL_INDEX_FILE = PROJECT / "data" / "index" / "contextual.npz"
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
# 注意用 127.0.0.1 而非 localhost：开 Clash TUN 时 localhost 的 DNS 会被
# fake-ip 劫持，请求绕道代理后返回 502
OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
GEN_MODEL = "qwen3:4b"  # 以实际部署的模型为准

# 云端 DeepSeek（可选）：界面侧边栏粘贴自己的 Key 即用，或设环境变量
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-flash"

COLLECTION = "moutai"
TOP_K = 5
# E6 拒答自动重试：首轮拒答后扩充检索再问一次的块数
RETRY_TOP_K = 10
