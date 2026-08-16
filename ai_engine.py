"""
电脑医生 AI 引擎 v1.1 — 多层智能问答系统
===========================================
架构：
  第0层 → 精确匹配（错误码、硬件ID等，0ms，100%准确）
  第1层 → 语义检索（sentence-transformers，0.1s，离线）
  第2层 → 本地小模型（可选，Ollama + 0.5B~1.5B 参数，1~3s）
  第3层 → 云端 API（兜底，智谱/通义等免费模型）
  反馈闭环 → 自动学习，越用越准

用法：
    from ai_engine import AIEngine
    engine = AIEngine()
    result = engine.ask("电脑蓝屏了怎么办")
    # result = {"answer": "...", "layer": "semantic", "score": 0.92, "knowledge_id": 3}
"""

import os
import sys
import json
import re
import time
import logging
import shutil
import sqlite3
import threading
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

# ============================================================
# 日志系统
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='[AI引擎] %(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger('ai_engine')

# 查询统计计数器
_stats_lock = threading.Lock()
_query_stats = {
    "total": 0,
    "exact_hits": 0,
    "semantic_hits": 0,
    "keyword_hits": 0,
    "local_hits": 0,
    "cloud_hits": 0,
    "misses": 0,
    "low_confidence_queries": [],  # 最近20条低置信度查询
}

# 语义检索可用性标记（模块级，供测试使用）
_semantic_available = None

# 模块级锁（引擎单例 + 查询统计保护）
_MODULE_LOCK = threading.Lock()

# ============================================================
# 0. 配置系统
# ============================================================

DEFAULT_CONFIG = {
    "version": "1.0",
    "semantic": {
        "enabled": True,
        "model_name": "paraphrase-multilingual-MiniLM-L12-v2",
        "threshold": 0.45,          # 低于此分数则进入下一层
        "cache_dir": ".model_cache",
        "max_results": 3,
        "rebuild_on_start": False,  # 启动时是否重建向量库
    },
    "local_model": {
        "enabled": False,           # 默认关闭（需要用户安装 Ollama）
        "provider": "ollama",       # ollama | llama.cpp | transformers
        "model_name": "qwen2.5:0.5b",
        "base_url": "http://localhost:11434",
        "timeout": 30,
        "max_tokens": 400,
        "min_ram_gb": 6,           # 至少需要多少内存才启用
    },
    "cloud": {
        "enabled": True,
        "provider": "zhipu",       # zhipu | tongyi | openai_compatible
        "api_key": "",              # 留空则从环境变量读取
        "model_name": "glm-4-flash",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "timeout": 15,
        "max_tokens": 600,
        "temperature": 0.7,
    },
    "learning": {
        "auto_learn": True,         # 云端/Local 回答自动入库
        "feedback_weight_delta": 0.1,
        "min_weight": 0.0,
        "max_weight": 5.0,
        "rebuild_interval_hours": 24,
    },
    "hardware": {
        "max_ram_percent": 60,      # 内存占用不超过此百分比
        "prefer_gpu": True,
    },
}

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_config.json")


def load_config() -> dict:
    """加载配置，不存在则用默认值"""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            # 深度合并（简单版，只合并顶层 key）
            merged = DEFAULT_CONFIG.copy()
            for k, v in saved.items():
                if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
                    merged[k].update(v)
                else:
                    merged[k] = v
            return merged
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> None:
    """保存配置到文件"""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# ============================================================
# 1. 硬件检测
# ============================================================

@dataclass
class HardwareInfo:
    """硬件信息"""
    total_ram_gb: float = 0.0
    available_ram_gb: float = 0.0
    cpu_count: int = 1
    has_gpu: bool = False
    gpu_name: str = ""


def detect_hardware() -> HardwareInfo:
    """检测当前硬件能力"""
    info = HardwareInfo()
    try:
        import psutil
        mem = psutil.virtual_memory()
        info.total_ram_gb = round(mem.total / (1024 ** 3), 1)
        info.available_ram_gb = round(mem.available / (1024 ** 3), 1)
        info.cpu_count = psutil.cpu_count(logical=False) or 1
    except Exception:
        pass

    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        if result.returncode == 0 and result.stdout.strip():
            info.has_gpu = True
            info.gpu_name = result.stdout.strip().split("\n")[0]
    except Exception:
        pass

    return info


# ============================================================
# 2. 语义检索层（第一层）
# ============================================================

class SemanticRetriever:
    """基于 sentence-transformers 的语义匹配（支持 ONNX 加速 + 向量缓存）"""

    def __init__(self, config: dict = None):
        # config 允许为空（测试场景），使用默认语义配置
        self.config = config or {"semantic": {"cache_dir": "./models", "model_name": "paraphrase-multilingual-MiniLM-L12-v2"}}
        self.model = None
        self.onnx_model = None   # ONNX Runtime 模型（更快速）
        self.use_onnx = False
        self.documents: list[dict] = []   # [{id, question, answer, weight}, ...]
        self.embeddings = None            # numpy array
        self._ready = False
        self._vectors_path = os.path.join(
            self.config["semantic"].get("cache_dir", "./models"),
            "knowledge_vectors.npy"
        )
        self._docs_path = os.path.join(
            self.config["semantic"].get("cache_dir", "./models"),
            "knowledge_docs.json"
        )

    def _load_model(self):
        """延迟加载模型（ONNX加速 + 国内镜像 + 向量缓存）"""
        if self.model is not None or self.use_onnx:
            return True
        try:
            if not os.environ.get("HF_ENDPOINT"):
                os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
                logger.info("使用国内镜像 hf-mirror.com 下载模型...")
            cache = self.config["semantic"]["cache_dir"]
            os.makedirs(cache, exist_ok=True)
            name = self.config["semantic"]["model_name"]

            # 1. 尝试 ONNX Runtime 推理（速度提升2-3倍，内存减少40%）
            try:
                from onnxruntime import InferenceSession, SessionOptions, GraphOptimizationLevel
                onnx_path = os.path.join(cache, "semantic_model.onnx")
                # 若 ONNX 文件不存在，先导出一份
                if not os.path.exists(onnx_path):
                    logger.info("首次使用 ONNX，正在导出模型（约需1分钟）...")
                    try:
                        from optimum.onnxruntime import ORTModelForFeatureExtraction
                        ort_model = ORTModelForFeatureExtraction.from_pretrained(
                            name, cache_dir=cache, export=True, provider="CPUExecutionProvider"
                        )
                        ort_model.save_pretrained(cache)
                        logger.info("ONNX 模型导出成功")
                    except ImportError:
                        logger.debug("optimum 未安装，跳过 ONNX 导出")
                        raise

                if os.path.exists(onnx_path):
                    sess_options = SessionOptions()
                    sess_options.graph_optimization_level = GraphOptimizationLevel.ORT_ENABLE_ALL
                    sess_options.intra_op_num_threads = 4
                    self.onnx_model = InferenceSession(onnx_path, sess_options,
                                                         providers=['CPUExecutionProvider'])
                    from transformers import AutoTokenizer
                    self.onnx_tokenizer = AutoTokenizer.from_pretrained(cache, cache_dir=cache)
                    self.use_onnx = True
                    logger.info("ONNX 模型就绪，推理加速 2-3x")
                    return True
            except Exception:
                logger.debug("ONNX 不可用，回退 PyTorch")

            # 2. 回退标准 PyTorch
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(name, cache_folder=cache)
            return True
        except ImportError:
            logger.warning("sentence-transformers 未安装，语义检索不可用。"
                  "请运行: pip install sentence-transformers")
            return False
        except Exception as e:
            logger.error(f"模型加载失败: {e}，回退到关键词检索")
            return False

    def build_index(self, knowledge_rows: list) -> bool:
        """
        用数据库中的知识构建向量索引（ONNX优先 + 缓存持久化）
        兼容 tuple / dict 两种输入格式
        """
        if not knowledge_rows:
            return False
        if not self._load_model():
            return False

        try:
            self.documents = []
            for row in knowledge_rows:
                if isinstance(row, dict):
                    d_id = row.get("id")
                    question = row.get("question", "")
                    answer = row.get("answer", "")
                    weight = row.get("weight", 1.0) or 1.0
                    source = row.get("source", "")
                    tags = row.get("tags", "")
                    severity = row.get("severity", "中")
                else:
                    d_id = row[0]
                    question = row[1]
                    answer = row[2]
                    weight = row[3] or 1.0
                    source = row[4] if len(row) > 4 else ""
                    tags = row[5] if len(row) > 5 else ""
                    severity = row[6] if len(row) > 6 else "中"
                doc = {"id": d_id, "question": question, "answer": answer, "weight": weight,
                       "source": source, "tags": tags, "severity": severity}
                self.documents.append(doc)

            # 尝试加载缓存向量（跳过重复编码，启动加速30秒→1秒）
            if os.path.exists(self._vectors_path) and os.path.exists(self._docs_path):
                try:
                    with open(self._docs_path, 'r', encoding='utf-8') as f:
                        cached_ids = json.load(f)
                    current_ids = [d["id"] for d in self.documents]
                    if cached_ids == current_ids:
                        self.embeddings = np.load(self._vectors_path)
                        logger.info(f"向量缓存命中，{len(self.documents)} 条知识直接就绪")
                        self._ready = True
                        return True
                except Exception:
                    logger.debug("缓存失效，重新构建向量")

            # 编码（ONNX 或 PyTorch）
            questions = [d["question"] for d in self.documents]
            vecs = self._encode(questions)
            if vecs is None:
                return False
            self.embeddings = vecs
            # 保存缓存
            self._save_cache()
            self._ready = True
            logger.info(f"语义索引已构建，共 {len(self.documents)} 条知识")
            return True
        except Exception as e:
            logger.error(f"构建索引失败: {e}")
            return False

    def _encode(self, texts: list) -> Optional[np.ndarray]:
        """统一编码入口：优先 ONNX，其次 PyTorch"""
        if self.use_onnx and self.onnx_model:
            try:
                tokens = self.onnx_tokenizer(
                    texts, padding=True, truncation=True, max_length=128, return_tensors="np"
                )
                inputs = {
                    'input_ids': tokens['input_ids'].astype(np.int64),
                    'attention_mask': tokens['attention_mask'].astype(np.int64),
                }
                outputs = self.onnx_model.run(None, inputs)
                vecs = outputs[0]  # (N, hidden_size)
                # 对 token embeddings 做 mean pooling
                mask = tokens['attention_mask'].astype(np.float32)
                mask_expanded = np.expand_dims(mask, -1)
                vecs = (vecs * mask_expanded).sum(axis=1) / np.clip(mask_expanded.sum(axis=1), 1e-9, None)
                # L2 归一化
                norms = np.linalg.norm(vecs, axis=1, keepdims=True)
                vecs = vecs / np.clip(norms, 1e-8, None)
                return vecs
            except Exception:
                logger.debug("ONNX 编码失败，回退 PyTorch")
        if self.model and self.model != "fallback":
            try:
                vecs = self.model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
                norms = np.linalg.norm(vecs, axis=1, keepdims=True)
                vecs = vecs / np.clip(norms, 1e-8, None)
                return vecs
            except Exception as e:
                logger.error(f"PyTorch 编码失败: {e}")
        return None

    def _save_cache(self):
        """保存向量和文档ID映射"""
        try:
            os.makedirs(os.path.dirname(self._vectors_path), exist_ok=True)
            np.save(self._vectors_path, self.embeddings)
            doc_ids = [d["id"] for d in self.documents]
            with open(self._docs_path, 'w', encoding='utf-8') as f:
                json.dump(doc_ids, f, ensure_ascii=False)
            logger.debug("向量缓存已保存")
        except Exception as e:
            logger.debug(f"缓存保存失败: {e}")

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        """
        语义搜索（支持ONNX/PyTorch统一编码）
        返回: [{"id": 1, "question": "...", "answer": "...", "score": 0.92}, ...]
        """
        if not self._ready or self.embeddings is None:
            return []
        try:
            query_vec = self._encode([query])
            if query_vec is None:
                return []
            similarity = np.dot(self.embeddings, query_vec.T).flatten()
            # 按权重微调分数（范围 [0.1, 5.0]，允许差评有效降权）
            for i, doc in enumerate(self.documents):
                similarity[i] *= max(0.1, min(5.0, doc.get("weight", 1.0)))
            # 取 top_k
            indices = np.argsort(similarity)[::-1][:top_k]
            results = []
            for idx in indices:
                if similarity[idx] < 0.01:
                    continue
                doc = self.documents[idx]
                score_v = round(float(similarity[idx]), 4)
                confidence = "high" if score_v >= 0.85 else ("medium" if score_v >= 0.5 else "low")
                results.append({
                    "id": doc["id"],
                    "question": doc["question"],
                    "answer": doc["answer"],
                    "score": score_v,
                    "confidence": confidence,
                    "source": doc.get("source", "电脑医生知识库"),
                    "tags": doc.get("tags", ""),
                    "severity": doc.get("severity", "中"),
                })
            return results
        except Exception as e:
            logger.error(f"语义搜索异常: {e}")
            return []

    def add_document(self, doc_id: int, question: str, answer: str, weight: float = 1.0,
                     source: str = "", tags: str = "", severity: str = "中"):
        """增量添加单条文档到索引（适配 ONNX/PyTorch 编码）"""
        if not self._ready or not self._load_model():
            rows = KnowledgeBridge.load_all()
            self.build_index(rows)
            return
        try:
            new_vec = self._encode([question])
            if new_vec is None:
                return
            if self.embeddings is None or len(self.embeddings) == 0:
                self.embeddings = new_vec
            else:
                self.embeddings = np.vstack([self.embeddings, new_vec])
            self.documents.append({
                "id": doc_id, "question": question,
                "answer": answer, "weight": weight,
                "source": source, "tags": tags, "severity": severity,
            })
        except Exception as e:
            logger.warning(f"增量添加失败: {e}")

    def documents_to_rows(self) -> list[tuple]:
        """将内部文档转回元组格式（与 KnowledgeBridge.load_all() 格式一致）"""
        return [
            (d["id"], d["question"], d["answer"], d.get("weight", 1.0),
             d.get("source", ""), d.get("tags", ""), d.get("severity", "中"))
            for d in self.documents
        ]

    def update_weight(self, doc_id: int, weight: float) -> bool:
        """实时更新内存中某条知识的权重（反馈闭环用）"""
        target = None
        for d in self.documents:
            if d.get("id") == doc_id:
                target = d
                break
        if target is None:
            return False
        target["weight"] = weight
        # 语义层：用权重调节向量内积（权重越大越靠前）
        # 通过缩放归一化后的向量实现，无需重算 embedding
        try:
            import numpy as np
            idx = self.documents.index(target)
            if self.embeddings is not None and idx < len(self.embeddings):
                base = self._base_vectors[idx] if hasattr(self, "_base_vectors") else self.embeddings[idx].copy()
                if not hasattr(self, "_base_vectors"):
                    self._base_vectors = self.embeddings.copy()
                scale = max(0.01, min(5.0, weight))
                self.embeddings[idx] = base * scale
        except Exception:
            pass
        return True

    @property
    def ready(self) -> bool:
        return self._ready


# ============================================================
# 2.5 关键词匹配兜底（零依赖，不联网也能用）
# ============================================================

class KeywordRetriever:
    """当语义模型无法下载时，用简单关键词匹配作为兜底"""

    def __init__(self):
        self.documents: list[dict] = []
        self._ready = False

    def _tokenize(self, text: str) -> set[str]:
        """中文分词简化版：取 2-gram + 单字，纯 Python 无依赖"""
        result = set()
        # 提取中文字符和字母数字
        cleaned = []
        for ch in text:
            if '\u4e00' <= ch <= '\u9fff' or '\u0041' <= ch <= '\u005a' or '\u0061' <= ch <= '\u007a' or '\u0030' <= ch <= '\u0039':
                cleaned.append(ch)
        s = ''.join(cleaned)
        # 单字
        for ch in s:
            result.add(ch)
        # 2-gram
        for i in range(len(s) - 1):
            result.add(s[i:i + 2])
        return result

    def build_index(self, knowledge_rows: list) -> bool:
        """
        构建关键词索引
        兼容 tuple / dict 两种输入格式：
        tuple: (id, question, answer, weight, source, tags, severity)
        dict:  {id, question, answer, weight, source, tags, severity}
        """
        if not knowledge_rows:
            return False
        self.documents = []
        for row in knowledge_rows:
            if isinstance(row, dict):
                d_id = row.get("id")
                question = row.get("question", "")
                answer = row.get("answer", "")
                weight = row.get("weight", 1.0) or 1.0
                source = row.get("source", "")
                tags = row.get("tags", "")
                severity = row.get("severity", "中")
            else:
                d_id = row[0]
                question = row[1]
                answer = row[2]
                weight = row[3] or 1.0
                source = row[4] if len(row) > 4 else ""
                tags = row[5] if len(row) > 5 else ""
                severity = row[6] if len(row) > 6 else "中"
            self.documents.append({
                "id": d_id, "question": question, "answer": answer,
                "weight": weight, "tokens": self._tokenize(question),
                "source": source, "tags": tags, "severity": severity,
            })
        self._ready = True
        logger.info(f"关键词匹配已就绪，共 {len(self.documents)} 条知识（离线可用）")
        return True

    def search_best(self, query: str) -> dict:
        """返回单条最佳匹配 dict，无结果返回 None（供单条断言使用）"""
        results = self.search(query, top_k=1)
        return results[0] if results else None

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        if not self._ready:
            return []
        q_tokens = self._tokenize(query)
        if not q_tokens:
            return []
        results = []
        for doc in self.documents:
            if not doc["tokens"]:
                continue
            # Jaccard 相似度
            intersection = len(q_tokens & doc["tokens"])
            union = len(q_tokens | doc["tokens"])
            score = intersection / union if union > 0 else 0
            # 权重微调（范围 [0.1, 5.0]，允许差评有效降权）
            score *= max(0.1, min(5.0, doc.get("weight", 1.0)))
            if score > 0.05:
                confidence = "high" if score >= 0.7 else ("medium" if score >= 0.35 else "low")
                results.append({
                    "id": doc["id"],
                    "question": doc["question"],
                    "answer": doc["answer"],
                    "score": round(score, 4),
                    "confidence": confidence,
                    "source": doc.get("source", "电脑医生知识库"),
                    "tags": doc.get("tags", ""),
                    "severity": doc.get("severity", "中"),
                })
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def add_document(self, doc_id: int, question: str, answer: str, weight: float = 1.0,
                     source: str = "", tags: str = "", severity: str = "中"):
        self.documents.append({
            "id": doc_id, "question": question, "answer": answer,
            "weight": weight, "tokens": self._tokenize(question),
            "source": source, "tags": tags, "severity": severity,
        })
        self._ready = True

    def documents_to_rows(self) -> list[tuple]:
        return [(d["id"], d["question"], d["answer"], d.get("weight", 1.0),
                 d.get("source", ""), d.get("tags", ""), d.get("severity", "中"))
                for d in self.documents]

    def update_weight(self, doc_id: int, weight: float) -> bool:
        """实时更新内存中某条知识的权重（反馈闭环用）"""
        for d in self.documents:
            if d.get("id") == doc_id:
                d["weight"] = weight
                return True
        return False

    @property
    def ready(self) -> bool:
        return self._ready


# ============================================================
# 2.5 标签匹配层（混合检索的中间层）
# ============================================================

class TagMatcher:
    """
    标签匹配层：用 jieba 从用户问题中提取核心关键词，
    与知识库的 tags 字段做交集，作为精确匹配与语义检索之间的"半精确"层。

    当命中标签但语义相似度不够高时，可提升命中率（例如"蓝屏""C盘""弹窗"）。
    """

    def __init__(self):
        self._ready = False
        self.documents = []      # list[dict]
        try:
            import jieba
            self._jieba = jieba
        except Exception:
            self._jieba = None
        self._stop = set(
            "的 了 是 我 你 他 她 它 们 在 和 与 及 或 把 被 给 对 为 这 那 "
            "怎么 如何 为什么 什么 哪些 吗 呢 吧 啊 请 帮 想 要 用 到 上 下 "
            "一个 一种 一些 这种 那种 电脑 计算机 问题 故障 出现 发生 之后 时候 "
            "怎么弄 怎么办 怎样 该如何 怎么解决 如何解决 处理 解决 修复 方法"
            .split()
        )

    def _tokenize(self, text: str) -> set:
        text = (text or "").lower()
        if self._jieba is not None:
            tokens = {t for t in self._jieba.cut(text) if t.strip()}
        else:
            tokens = {t for t in re.split(r"[\s，。、！？!?,.]+", text) if t.strip()}
        # 同时把单字剔除，保留 >=2 字 与 英文/数字组合（如 0x000000ef、dll、wifi）
        cleaned = set()
        for t in tokens:
            if len(t) >= 2 or re.fullmatch(r"[a-z0-9]+", t):
                if t not in self._stop:
                    cleaned.add(t)
        return cleaned

    def build_index(self, knowledge_rows: list) -> bool:
        if not knowledge_rows:
            return False
        self.documents = []
        for row in knowledge_rows:
            if isinstance(row, dict):
                d_id = row.get("id")
                question = row.get("question", "")
                answer = row.get("answer", "")
                weight = row.get("weight", 1.0) or 1.0
                source = row.get("source", "")
                tags = row.get("tags", "")
                severity = row.get("severity", "中")
            else:
                d_id = row[0]
                question = row[1]
                answer = row[2]
                weight = row[3] or 1.0
                source = row[4] if len(row) > 4 else ""
                tags = row[5] if len(row) > 5 else ""
                severity = row[6] if len(row) > 6 else "中"
            tag_list = [t.strip() for t in str(tags).replace("，", ",").split(",") if t.strip()]
            self.documents.append({
                "id": d_id, "question": question, "answer": answer,
                "weight": weight, "tags": tag_list, "source": source,
                "severity": severity,
            })
        self._ready = True
        logger.info(f"标签匹配已就绪，共 {len(self.documents)} 条知识")
        return True

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        if not self._ready:
            return []
        q_tokens = self._tokenize(query)
        if not q_tokens:
            return []
        # 至少要有1个长度≥2的token（有实际语义），避免"1""a"等单字符误匹配
        if not any(len(t) >= 2 for t in q_tokens):
            return []
        results = []
        for doc in self.documents:
            doc_tags = set(doc.get("tags", []))
            if not doc_tags:
                continue
            # 查询词 与 标签 的交集（查询词可能是完整标签，也可能是标签的子串）
            matched = set()
            for qt in q_tokens:
                for tg in doc_tags:
                    if qt == tg or qt in tg or tg in qt:
                        matched.add(tg)
            if not matched:
                continue
            # 标签命中率 = 命中标签数 / 文档标签总数
            hit_ratio = len(matched) / max(1, len(doc_tags))
            # 查询覆盖度 = 命中标签数 / 查询词数
            cover = len(matched) / max(1, len(q_tokens))
            # 基础分数仅反映命中质量（0~1），不混入权重，避免置信度阈值失真
            base_score = hit_ratio * 0.6 + cover * 0.4
            # 权重仅作为排序微调因子（不进入 score，避免被点赞后虚高）
            w = max(0.1, min(5.0, doc.get("weight", 1.0)))
            sort_key = base_score * (0.5 + 0.5 * (w / 5.0))  # 权重最高放大50%
            confidence = "high" if base_score >= 0.9 else ("medium" if base_score >= 0.5 else "low")
            results.append({
                "id": doc["id"],
                "question": doc["question"],
                "answer": doc["answer"],
                "score": round(base_score, 4),
                "sort_key": round(sort_key, 4),
                "confidence": confidence,
                "source": doc.get("source", "电脑医生知识库"),
                "tags": ",".join(doc.get("tags", [])),
                "severity": doc.get("severity", "中"),
                "matched_tags": ",".join(matched),
            })
        # 排序时结合命中质量与权重微调
        results.sort(key=lambda x: x.get("sort_key", x["score"]), reverse=True)
        for r in results:
            r.pop("sort_key", None)
        return results[:top_k]

    def update_weight(self, doc_id: int, weight: float) -> bool:
        for doc in self.documents:
            if doc["id"] == doc_id:
                doc["weight"] = weight
                return True
        return False

    def add_document(self, doc_id: int, question: str, answer: str, weight: float = 1.0,
                     source: str = "", tags: str = "", severity: str = "中"):
        """增量添加单条文档到标签匹配层"""
        tag_list = [t.strip() for t in str(tags).replace("，", ",").split(",") if t.strip()]
        self.documents.append({
            "id": doc_id, "question": question, "answer": answer,
            "weight": weight, "tags": tag_list, "source": source,
            "severity": severity,
        })
        self._ready = True

    @property
    def ready(self) -> bool:
        return self._ready


# ============================================================
# 3. 本地小模型层（第二层，可选）
# ============================================================

class LocalModelInference:
    """通过 Ollama 调用本地小模型"""

    def __init__(self, config: dict):
        self.config = config
        self._checked = None   # None=未检测, True=可用, False=不可用

    def check_availability(self) -> bool:
        """检测本地模型是否可用"""
        if self._checked is not None:
            return self._checked

        cfg = self.config["local_model"]
        if not cfg["enabled"]:
            self._checked = False
            return False

        hw = detect_hardware()
        if hw.total_ram_gb < cfg["min_ram_gb"]:
            logger.info(f"内存不足（{hw.total_ram_gb}GB < {cfg['min_ram_gb']}GB），跳过本地模型")
            self._checked = False
            return False

        try:
            import requests
            resp = requests.get(f"{cfg['base_url']}/api/tags", timeout=5)
            if resp.status_code == 200:
                models = [m["name"] for m in resp.json().get("models", [])]
                if cfg["model_name"] in models:
                    self._checked = True
                    return True
                logger.info(f"Ollama 未安装模型 {cfg['model_name']}，"
                      f"请运行: ollama pull {cfg['model_name']}")
        except Exception as e:
            logger.info(f"无法连接 Ollama: {e}")

        self._checked = False
        return False

    def ask(self, question: str) -> Optional[dict]:
        """调用本地模型"""
        if not self.check_availability():
            return None

        cfg = self.config["local_model"]
        try:
            import requests
            system_prompt = (
                "你是一位拥有20年实战经验的电脑维修专家，曾帮助成千上万用户解决过各类Windows系统故障。"
                "你的回答必须满足以下要求：\n"
                "1. 用完全不懂电脑的普通人听得懂的语言，避免任何专业术语；如果必须使用术语，请用括号通俗解释。\n"
                "2. 分步骤回答，每一步用\"步骤1：\"、\"步骤2：\"开头，每步给出具体操作和注意事项。\n"
                "3. 如果操作有风险（如修改注册表、删除系统文件），必须在开头用\"⚠️ 高风险操作提醒：\"明确标注。\n"
                "4. 回答结尾给出\"💡 温馨提示\"，补充预防措施或替代方案。\n"
                "5. 回答长度控制在400字以内，简洁有力，不啰嗦。"
            )
            resp = requests.post(
                f"{cfg['base_url']}/api/generate",
                json={
                    "model": cfg["model_name"],
                    "prompt": f"系统：{system_prompt}\n用户：{question}\n回答：",
                    "stream": False,
                    "options": {"num_predict": cfg["max_tokens"]},
                },
                timeout=cfg["timeout"],
            )
            if resp.status_code == 200:
                data = resp.json()
                answer = data.get("response", "").strip()
                if answer:
                    return {"answer": answer, "layer": "local_model", "score": 0.75}
        except Exception as e:
            logger.error(f"本地模型调用失败: {e}")
            return None


# ============================================================
# 4. 云端 API 层（第三层，兜底）
# ============================================================

class CloudAPI:
    """云端大模型 API 调用（支持多种 provider）"""

    def __init__(self, config: dict):
        self.config = config

    def _is_available(self) -> bool:
        cfg = self.config["cloud"]
        if not cfg["enabled"]:
            return False
        # 检查网络
        try:
            import requests
            requests.get("https://www.baidu.com", timeout=3)
            return True
        except Exception:
            return False

    def _get_api_key(self) -> str:
        cfg = self.config["cloud"]
        key = cfg.get("api_key", "")
        if key and key != "你的API Key" and not key.startswith("YOUR_"):
            return key
        # 尝试从环境变量读取
        env_map = {"zhipu": "ZHIPU_API_KEY", "tongyi": "DASHSCOPE_API_KEY"}
        env_key = env_map.get(cfg["provider"], "AI_API_KEY")
        return os.environ.get(env_key, "")

    def ask(self, question: str) -> Optional[dict]:
        """调用云端 API"""
        if not self._is_available():
            return None

        cfg = self.config["cloud"]
        api_key = self._get_api_key()
        if not api_key:
            # 静默跳过，不打印噪音日志（离线运行是常态）
            return None

        try:
            import requests

            system_prompt = (
                "你是一位拥有20年实战经验的电脑维修专家，曾帮助成千上万用户解决过各类Windows系统故障。"
                "你的回答必须满足以下要求：\n"
                "1. 用完全不懂电脑的普通人听得懂的语言，避免任何专业术语；如果必须使用术语，请用括号通俗解释。\n"
                "2. 分步骤回答，每一步用\"步骤1：\"、\"步骤2：\"开头，每步给出具体操作和注意事项。\n"
                "3. 如果操作有风险（如修改注册表、删除系统文件），必须在开头用\"⚠️ 高风险操作提醒：\"明确标注。\n"
                "4. 回答结尾给出\"💡 温馨提示\"，补充预防措施或替代方案。\n"
                "5. 回答长度控制在400字以内，简洁有力，不啰嗦。"
            )

            if cfg["provider"] == "zhipu":
                return self._call_zhipu(api_key, system_prompt, question, cfg)
            elif cfg["provider"] == "tongyi":
                return self._call_tongyi(api_key, system_prompt, question, cfg)
            else:
                return self._call_openai_compatible(api_key, system_prompt, question, cfg)

        except Exception as e:
            logger.error(f"云端 API 调用失败: {e}")
            return None

    def _call_zhipu(self, api_key: str, system: str, question: str, cfg: dict) -> Optional[dict]:
        import requests
        resp = requests.post(
            cfg["base_url"],
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": cfg["model_name"],
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": question},
                ],
                "temperature": cfg.get("temperature", 0.7),
                "max_tokens": cfg.get("max_tokens", 600),
            },
            timeout=cfg.get("timeout", 15),
        )
        if resp.status_code == 200:
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                answer = choices[0].get("message", {}).get("content", "").strip()
                if answer:
                    return {"answer": answer, "layer": "cloud", "score": 0.60,
                            "model": cfg["model_name"]}
        else:
            logger.warning(f"云端 API 错误: {resp.status_code} {resp.text[:200]}")
        return None

    def _call_tongyi(self, api_key: str, system: str, question: str, cfg: dict) -> Optional[dict]:
        import requests
        resp = requests.post(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "qwen-turbo",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": question},
                ],
                "temperature": cfg.get("temperature", 0.7),
                "max_tokens": cfg.get("max_tokens", 600),
            },
            timeout=cfg.get("timeout", 15),
        )
        if resp.status_code == 200:
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                answer = choices[0].get("message", {}).get("content", "").strip()
                if answer:
                    return {"answer": answer, "layer": "cloud", "score": 0.60,
                            "model": "qwen-turbo"}
        return None

    def _call_openai_compatible(self, api_key: str, system: str, question: str, cfg: dict) -> Optional[dict]:
        import requests
        base = cfg.get("base_url", "https://api.openai.com/v1/chat/completions")
        resp = requests.post(
            base,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": cfg.get("model_name", "gpt-3.5-turbo"),
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": question},
                ],
                "temperature": cfg.get("temperature", 0.7),
                "max_tokens": cfg.get("max_tokens", 600),
            },
            timeout=cfg.get("timeout", 15),
        )
        if resp.status_code == 200:
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                answer = choices[0].get("message", {}).get("content", "").strip()
                if answer:
                    return {"answer": answer, "layer": "cloud", "score": 0.60}
        return None


# ============================================================
# 5. 知识库桥接层（读写 learning.py 的 SQLite）
# ============================================================

def _knowledge_db_path() -> str:
    """返回可写的知识库路径：打包后(_MEIPASS)放 %APPDATA%/电脑医生/，开发时放脚本同目录。
    避免写入 PyInstaller 临时只读目录导致反馈/学习静默失效（bug #1）。"""
    if getattr(sys, 'frozen', False):
        appdata = os.environ.get('APPDATA') or os.path.expanduser('~')
        folder = os.path.join(appdata, '电脑医生')
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, "pc_doctor_knowledge.db")
        # 若可写库尚不存在，从打包资源(_MEIPASS)拷贝初始库
        if not os.path.exists(target):
            bundled = os.path.join(getattr(sys, '_MEIPASS', ''), "pc_doctor_knowledge.db")
            if os.path.exists(bundled):
                try:
                    shutil.copyfile(bundled, target)
                    logger.info(f"已从打包资源初始化知识库: {target}")
                except Exception as e:
                    logger.error(f"初始化知识库失败: {e}")
        return target
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "pc_doctor_knowledge.db")


# ==================== 联网搜索模块 ====================

class WebSearcher:
    """联网搜索：Bing Search API + 结果缓存，用于补充本地知识库不足
        
    用法：
        searcher = WebSearcher(api_key="YOUR_BING_KEY")
        result = searcher.search("电脑蓝屏怎么办")
    """
    
    # 搜索缓存（内存级，同一问题秒答）
    _cache: dict = {}
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("BING_SEARCH_API_KEY", "")
        self.endpoint = "https://api.bing.microsoft.com/v7.0/search"
    
    @property
    def available(self) -> bool:
        return bool(self.api_key)
    
    def search(self, query: str, count: int = 3) -> Optional[str]:
        """执行联网搜索，返回格式化的结果文本"""
        if not self.api_key:
            return None
        cache_key = f"{query}_{count}"
        if cache_key in self._cache:
            logger.info(f"搜索缓存命中: {query[:30]}...")
            return self._cache[cache_key]
        try:
            headers = {"Ocp-Apim-Subscription-Key": self.api_key}
            params = {"q": query, "mkt": "zh-CN", "count": count, "textFormat": "Raw"}
            resp = requests.get(self.endpoint, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            results = []
            pages = data.get("webPages", {}).get("value", [])
            for item in pages:
                results.append(f"• {item.get('name', '')}\n  {item.get('snippet', '')}\n  来源: {item.get('url', '')}")
            if not results:
                return None
            text = f"🔍 联网搜索结果（共 {len(pages)} 条）：\n\n" + "\n\n".join(results)
            self._cache[cache_key] = text
            # 限制缓存大小
            if len(self._cache) > 200:
                self._cache.pop(next(iter(self._cache)))
            return text
        except requests.exceptions.Timeout:
            logger.warning("联网搜索超时")
            return None
        except Exception as e:
            logger.warning(f"联网搜索失败: {e}")
            return None
    
    @classmethod
    def clear_cache(cls):
        cls._cache.clear()


# ==================== 知识库桥接层 ====================

class KnowledgeBridge:
    """读写现有 learning.py 的 SQLite 知识库"""

    # 导入时即计算一次（模块导入时 frozen 状态已确定），后续作为普通类属性访问
    DB_PATH = _knowledge_db_path()

    @staticmethod
    def _sync_from_json():
        """从 knowledge_base_v2.json 自动导入缺失的知识条目到 SQLite"""
        json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge_base_v2.json")
        if not os.path.exists(json_path):
            return
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                items = json.load(f)
            if not isinstance(items, list):
                return
            conn = sqlite3.connect(KnowledgeBridge.DB_PATH)
            cur = conn.cursor()
            for item in items:
                q = item.get("question", "")
                if not q:
                    continue
                cur.execute("SELECT id FROM knowledge WHERE question = ?", (q,))
                if cur.fetchone():
                    continue  # 已存在，跳过
                tags_val = item.get("tags", "")
                if isinstance(tags_val, list):
                    tags_val = ",".join(tags_val)
                cur.execute(
                    "INSERT INTO knowledge (question, answer, source, weight, tags, severity, reference) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        q,
                        item.get("answer", ""),
                        item.get("source", "电脑医生知识库"),
                        1.0,  # JSON 导入的知识默认权重 1.0
                        tags_val,
                        item.get("severity", "中"),
                        "",
                    ),
                )
            conn.commit()
            conn.close()
            added = cur.rowcount
            if added > 0:
                logger.info(f"从 JSON 导入 {added} 条新知识到 SQLite")
        except Exception as e:
            logger.warning(f"JSON 知识同步失败: {e}")

    @staticmethod
    def load_all() -> list[dict]:
        """加载所有知识（自动同步 JSON 新条目）"""
        KnowledgeBridge._sync_from_json()
        try:
            conn = sqlite3.connect(KnowledgeBridge.DB_PATH)
            cur = conn.cursor()
            cur.execute(
                "SELECT id, question, answer, COALESCE(weight, 1.0), "
                "COALESCE(source, ''), COALESCE(tags, ''), COALESCE(severity, '中') "
                "FROM knowledge ORDER BY weight DESC"
            )
            rows = cur.fetchall()
            conn.close()
            return [
                {"id": r[0], "question": r[1], "answer": r[2], "weight": r[3],
                 "source": r[4], "tags": r[5], "severity": r[6]}
                for r in rows
            ]
        except Exception as e:
            # 兼容旧表（无 tags/severity 字段）
            try:
                conn = sqlite3.connect(KnowledgeBridge.DB_PATH)
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, question, answer, COALESCE(weight, 1.0), "
                    "COALESCE(source, ''), '', '中' "
                    "FROM knowledge ORDER BY weight DESC"
                )
                docs = []
                for r in cur.fetchall():
                    docs.append({
                        "id": r[0], "question": r[1], "answer": r[2],
                        "weight": r[3], "source": r[4], "tags": r[5], "severity": r[6]
                    })
                conn.close()
                return docs
            except Exception as e2:
                logger.error(f"读取知识库兼容模式失败: {e2}")
                return []

    @staticmethod
    def add_knowledge(question: str, answer: str, source: str = "auto",
                      tags: str = "", severity: str = "中", reference: str = "",
                      weight: float = 1.0) -> Optional[int]:
        """添加新知识，返回新 ID（支持 weight 参数控制初始权重）"""
        # tags 允许传入 list/tuple，统一转成逗号分隔字符串入库
        if isinstance(tags, (list, tuple)):
            tags = ",".join(str(t) for t in tags)
        try:
            conn = sqlite3.connect(KnowledgeBridge.DB_PATH)
            cur = conn.cursor()
            # 去重
            cur.execute("SELECT id FROM knowledge WHERE question = ?", (question,))
            existing = cur.fetchone()
            if existing:
                conn.close()
                return existing[0]
            cur.execute(
                "INSERT INTO knowledge (question, answer, source, weight, tags, severity, reference) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (question, answer, source, weight, tags, severity, reference),
            )
            conn.commit()
            new_id = cur.lastrowid
            conn.close()
            # 清理未回答问题
            try:
                conn2 = sqlite3.connect(KnowledgeBridge.DB_PATH)
                conn2.execute("DELETE FROM unanswered WHERE question = ?", (question,))
                conn2.commit()
                conn2.close()
            except Exception:
                pass
            return new_id
        except Exception as e:
            logger.error(f"添加知识失败: {e}")
            return None

    @staticmethod
    def record_feedback(knowledge_id, is_helpful: bool, user_question: str = "") -> None:
        """记录反馈并调整权重（knowledge_id 支持 int 或 str）"""
        try:
            conn = sqlite3.connect(KnowledgeBridge.DB_PATH)
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO feedback (knowledge_id, is_helpful, user_question) VALUES (?, ?, ?)",
                (knowledge_id, 1 if is_helpful else 0, user_question),
            )
            delta = 0.1 if is_helpful else -0.2
            cur.execute(
                "UPDATE knowledge SET weight = MAX(0.01, MIN(5.0, COALESCE(weight, 1.0) + ?)), "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (delta, knowledge_id),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"记录反馈失败: {e}")

    @staticmethod
    def log_unanswered(question: str) -> None:
        """记录未回答的问题"""
        try:
            conn = sqlite3.connect(KnowledgeBridge.DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT id, ask_count FROM unanswered WHERE question = ?", (question,))
            row = cur.fetchone()
            if row:
                cur.execute(
                    "UPDATE unanswered SET ask_count = ask_count + 1 WHERE id = ?",
                    (row[0],),
                )
            else:
                cur.execute(
                    "INSERT INTO unanswered (question, ask_count) VALUES (?, 1)",
                    (question,),
                )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"记录未回答问题失败: {e}")

    @staticmethod
    def _init_db():
        """初始化数据库表"""
        conn = sqlite3.connect(KnowledgeBridge.DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT UNIQUE NOT NULL,
                answer TEXT NOT NULL,
                weight REAL DEFAULT 1.0,
                source TEXT DEFAULT '',
                tags TEXT DEFAULT '',
                severity TEXT DEFAULT '中',
                reference TEXT DEFAULT '',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                knowledge_id INTEGER,
                is_helpful INTEGER DEFAULT 1,
                user_question TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS unanswered (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT UNIQUE,
                ask_count INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    @staticmethod
    def load_by_id(knowledge_id):
        """按 ID 加载单条知识"""
        try:
            conn = sqlite3.connect(KnowledgeBridge.DB_PATH)
            cur = conn.cursor()
            cur.execute(
                "SELECT id, question, answer, COALESCE(weight, 1.0), "
                "COALESCE(source, ''), COALESCE(tags, ''), COALESCE(severity, '中') "
                "FROM knowledge WHERE id = ?", (knowledge_id,)
            )
            row = cur.fetchone()
            conn.close()
            if row:
                return {
                    "id": row[0], "question": row[1], "answer": row[2],
                    "weight": row[3], "source": row[4], "tags": row[5],
                    "severity": row[6]
                }
            return None
        except Exception as e:
            logger.error(f"加载知识失败: {e}")
            return None

    @staticmethod
    def search_by_tags(tag_query: str, limit: int = 10):
        """按标签模糊搜索"""
        try:
            conn = sqlite3.connect(KnowledgeBridge.DB_PATH)
            cur = conn.cursor()
            cur.execute(
                "SELECT id, question, answer, COALESCE(weight, 1.0), "
                "COALESCE(source, ''), COALESCE(tags, ''), COALESCE(severity, '中') "
                "FROM knowledge WHERE tags LIKE ? ORDER BY weight DESC LIMIT ?",
                (f"%{tag_query}%", limit)
            )
            rows = cur.fetchall()
            conn.close()
            return [
                {"id": r[0], "question": r[1], "answer": r[2], "weight": r[3],
                 "source": r[4], "tags": r[5], "severity": r[6]}
                for r in rows
            ]
        except Exception as e:
            logger.error(f"标签搜索失败: {e}")
            return []

    @staticmethod
    def update_weight(knowledge_id, new_weight: float) -> bool:
        """更新知识权重"""
        try:
            conn = sqlite3.connect(KnowledgeBridge.DB_PATH)
            conn.execute(
                "UPDATE knowledge SET weight = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (max(0.01, min(5.0, new_weight)), knowledge_id),
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"更新权重失败: {e}")
            return False

    @staticmethod
    def delete(knowledge_id):
        """删除知识条目（支持 int 或 str）"""
        try:
            conn = sqlite3.connect(KnowledgeBridge.DB_PATH)
            conn.execute("DELETE FROM knowledge WHERE id = ?", (knowledge_id,))
            conn.execute("DELETE FROM feedback WHERE knowledge_id = ?", (knowledge_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"删除知识失败: {e}")

    @staticmethod
    def insert(doc: dict):
        """插入一条知识（重复 ID 则更新）"""
        try:
            conn = sqlite3.connect(KnowledgeBridge.DB_PATH)
            cur = conn.cursor()
            kid = doc.get("id")
            title = doc.get("title", "")
            question = doc.get("question", "")
            answer = doc.get("answer", "")
            source = doc.get("source", "manual")
            tags = ",".join(doc.get("tags", [])) if isinstance(doc.get("tags"), list) else doc.get("tags", "")
            severity = doc.get("severity", "中")
            weight = doc.get("weight", 1.0)
            reference = doc.get("reference", "")
            if kid is not None:
                # 指定 ID 时先尝试更新
                cur.execute("SELECT id FROM knowledge WHERE id = ?", (kid,))
                if cur.fetchone():
                    cur.execute(
                        "UPDATE knowledge SET question=?, answer=?, source=?, tags=?, "
                        "severity=?, weight=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (question, answer, source, tags, severity, weight, kid)
                    )
                else:
                    cur.execute(
                        "INSERT INTO knowledge (id, question, answer, source, weight, tags, severity, reference) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (kid, question, answer, source, weight, tags, severity, reference)
                    )
            else:
                cur.execute(
                    "INSERT OR IGNORE INTO knowledge (question, answer, source, weight, tags, severity, reference) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (question, answer, source, weight, tags, severity, reference)
                )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"插入知识失败: {e}")

    @staticmethod
    def get_stats() -> dict:
        """获取知识库统计"""
        try:
            conn = sqlite3.connect(KnowledgeBridge.DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM knowledge")
            total_knowledge = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM unanswered")
            total_unanswered = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM feedback")
            total_feedback = cur.fetchone()[0]
            conn.close()
            return {
                "total_knowledge": total_knowledge,
                "total_unanswered": total_unanswered,
                "total_feedback": total_feedback,
            }
        except Exception:
            return {"total_knowledge": 0, "total_unanswered": 0, "total_feedback": 0}


# ============================================================
# 5.5 答案模板格式化器（结构化输出）
# ============================================================

def format_answer(raw: dict) -> dict:
    """
    将各层检索返回的"原始结果"统一组装成前端友好的结构化 JSON。

    输入 raw 至少包含: answer, score, source, severity,
    可选: question, layer, layer_label, matched_tags, source_url, model_note

    输出新增:
        score_percent      100 分制相似度（整数）
        confidence         高/中/低
        confidence_color   高=green 中=orange 低=red
        risk_label         ⚠️ 高风险操作 / ℹ️ 一般建议 / ✅ 低风险
        severity_color     高=red 中=orange 低=green
        has_source_url     是否有可点击来源链接
        source_url         来源 URL（若有）
        layer_label        匹配层级说明（精确匹配/标签匹配/语义检索…）
    """
    if not raw:
        return raw

    score = float(raw.get("score") or 0.0)
    severity = raw.get("severity", "中") or "中"
    sev = str(severity).strip()

    # 置信度分级
    if score >= 0.85:
        confidence, confidence_color = "高", "green"
    elif score >= 0.5:
        confidence, confidence_color = "中", "orange"
    else:
        confidence, confidence_color = "低", "red"

    # 风险等级（severity 字段：高/中/低）
    if sev == "高":
        risk_label, severity_color = "⚠️ 高风险操作", "red"
    elif sev == "低":
        risk_label, severity_color = "✅ 低风险", "green"
    else:
        risk_label, severity_color = "ℹ️ 一般建议", "orange"

    source = raw.get("source", "电脑医生知识库")
    source_url = raw.get("source_url", "") or ""
    # 若 source 本身是 URL 则直接可点击
    if not source_url and isinstance(source, str) and source.startswith("http"):
        source_url = source

    # 来源类型：自研本地 vs 云端AI
    layer = raw.get("layer", "")
    if layer in ("cloud", "web_search"):
        source_type = "ai_cloud"
        source_label = "云端智能 · 联网"
        source_badge = "cloud"
    else:
        source_type = "self_developed"
        source_label = "自研模型 · 本地"
        source_badge = "local"

    out = dict(raw)
    out.update({
        "source_type": source_type,
        "source_label": source_label,
        "source_badge": source_badge,
        "score_percent": int(round(score * 100)),
        "confidence": confidence,
        "confidence_color": confidence_color,
        "risk_label": risk_label,
        "severity": sev,
        "severity_color": severity_color,
        "has_source_url": bool(source_url),
        "source_url": source_url,
        "layer_label": raw.get("layer_label", "语义检索"),
    })
    return out


# ============================================================
# 6. 主引擎 — 多层调度
# ============================================================

class AIEngine:
    """
    电脑医生 AI 引擎
    =================
    三层架构 + 自学习反馈闭环

    使用示例:
        engine = AIEngine()
        result = engine.ask("电脑蓝屏了怎么办")
        print(result["answer"])   # "遇到蓝屏别慌..."
        print(result["layer"])    # "semantic" | "local_model" | "cloud" | "fallback"
    """

    def __init__(self, config: dict = None):
        self.config = config or load_config()
        self.hardware = detect_hardware()
        self._auto_adjust_config()

        # 初始化各层
        self.semantic = SemanticRetriever(self.config)
        self.keyword = KeywordRetriever()   # 零依赖关键词兜底
        self.tag_matcher = TagMatcher()     # 标签匹配层（jieba 关键词交集）
        self.local_model = LocalModelInference(self.config)
        self.cloud = CloudAPI(self.config)

        # 联网搜索（需要 BING_SEARCH_API_KEY 环境变量或配置）
        bing_key = self.config.get("web_search", {}).get("bing_api_key", "")
        if not bing_key:
            bing_key = os.environ.get("BING_SEARCH_API_KEY", "")
        self.web_searcher = WebSearcher(api_key=bing_key) if bing_key else None

        # 加载错误码表（精确匹配）
        self.error_codes = self._load_error_codes()

        # 启动时构建索引
        self._init_index()

    def _auto_adjust_config(self):
        """根据硬件自动调整配置"""
        hw = self.hardware
        cfg = self.config["local_model"]
        if hw.total_ram_gb < cfg.get("min_ram_gb", 6):
            cfg["enabled"] = False
            logger.info(f"内存 {hw.total_ram_gb}GB < {cfg.get('min_ram_gb', 6)}GB，自动禁用本地模型")
        if not hw.has_gpu and hw.total_ram_gb < 8:
            # 无 GPU + 小内存 = 本地模型太慢，直接关掉
            cfg["enabled"] = False

    def _init_index(self):
        """从知识库构建语义索引 + 关键词索引 + 标签索引"""
        rows = KnowledgeBridge.load_all()
        if rows:
            self.semantic.build_index(rows)
            self.keyword.build_index(rows)  # 关键词兜底，永远可用
            self.tag_matcher.build_index(rows)  # 标签匹配层
        else:
            logger.warning("知识库为空，跳过索引构建。请先导入知识。")

    def _load_error_codes(self) -> dict:
        """加载蓝屏错误码表"""
        code_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "error_codes.json")
        try:
            with open(code_path, "r", encoding="utf-8") as f:
                codes = json.load(f)
            index = {}
            for item in codes:
                key = item.get("code", "").lower()
                if key:
                    index[key] = item
            if index:
                logger.info(f"错误码表已加载，共 {len(index)} 条（精确匹配）")
            return index
        except Exception as e:
            logger.error(f"加载错误码表失败: {e}")
            return {}

    def _ensure_json_semantic(self) -> bool:
        """
        确保语义索引已从 knowledge_base.json 构建完成。
        模型懒加载（首次查询才触发），向量缓存 knowledge_vectors.npy。
        """
        if getattr(self, "_json_semantic_ready", False):
            return True
        try:
            kb_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge_base.json")
            if not os.path.exists(kb_path):
                logger.warning("knowledge_base.json 不存在，无法构建语义索引")
                return False
            with open(kb_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not data:
                logger.warning("knowledge_base.json 为空")
                return False
            rows = [(idx, d.get("question", ""), d.get("answer", ""),
                     1.0, d.get("source", ""), d.get("tags", ""), d.get("severity", "中"))
                    for idx, d in enumerate(data, 1)]
            # 复用现有语义检索器：懒加载模型 + 向量缓存 knowledge_vectors.npy
            ok = self.semantic.build_index(rows)
            self._json_semantic_ready = ok
            return ok
        except Exception as e:
            logger.error(f"构建 JSON 语义索引失败: {e}")
            return False

    def search(self, query: str):
        """
        语义检索入口（第一阶段优化）

        从 knowledge_base.json 加载知识，使用 SentenceTransformer 做语义匹配；
        模型懒加载（首次查询才初始化），向量缓存 knowledge_vectors.npy。

        返回:
            (answer: str, score: float)  命中时
            None                          无结果或语义检索不可用时
        """
        if not query or not query.strip():
            return None
        try:
            if not self._ensure_json_semantic():
                return None
            results = self.semantic.search(query, top_k=1)
            if not results:
                return None
            best = results[0]
            return (best.get("answer", ""), float(best.get("score", 0.0)))
        except Exception as e:
            logger.error(f"语义检索失败: {e}")
            return None

    def _exact_match(self, question: str) -> Optional[dict]:
        """精确匹配：错误码、已知指令等"""
        import re

        # 1. 蓝屏错误码精确匹配（支持 0x / 0X 大小写前缀）
        code_pattern = re.search(r'0[xX][0-9a-fA-F]{8}', question)
        if code_pattern:
            code = code_pattern.group().lower()
            if code in self.error_codes:
                item = self.error_codes[code]
                return {
                    "success": True,
                    "answer": f"蓝屏代码 {code.upper()} ({item['name']})\n原因: {item['cause']}\n\n{item['solution']}",
                    "layer": "exact_match",
                    "type": "exact",
                    "score": 1.0,
                    "confidence": "high",
                    "source": "蓝屏错误码表（微软官方）",
                    "tags": ','.join(item.get('tags', [])),
                    "severity": item.get('severity', '高'),
                    "knowledge_id": None,  # 错误码表不在 SQLite 知识库中
                    "elapsed_ms": 0,
                }

        # 2. 其他精确匹配规则可以在这里扩展
        return None

    def _confidence_label(self, score: float, layer: str) -> str:
        """根据分数和来源返回置信度标签"""
        if layer == "exact_match":
            return "high"
        if score >= 0.85:
            return "high"
        elif score >= 0.5:
            return "medium"
        else:
            return "low"

    def ask(self, question: str, mode: str = "auto") -> dict:
        """
        主查询接口 — 多层查询 + AI模式切换
        
        mode:
            'auto'   - 智能模式：精确→语义→关键词→联网→云端（自动回退）
            'local'  - 本地优先：仅精确+语义+关键词+本地模型，不联网
            'cloud'  - 云端优先：精确后直跳云端API
            'search' - 联网优先：精确后优先联网搜索
        
        返回:
            {
                "success": True/False,
                "answer": "...",
                "layer": "exact_match|semantic|keyword|web_search|local_model|cloud|fallback",
                "score": 0.92,
                "confidence": "high|medium|low",
                "source": "...",
                "tags": "...",
                "severity": "高",
                "knowledge_id": 3,
                "elapsed_ms": 123,
                "mode": "auto",
            }
        """
        t0 = time.time()
        _query_stats["total"] += 1
        logger.info(f"收到查询[{mode}]: {question[:80]}")

        # ---- 第0层：精确匹配（任何模式下都优先）----
        exact = self._exact_match(question)
        if exact:
            exact["layer_label"] = "精确匹配"
            exact["elapsed_ms"] = round((time.time() - t0) * 1000)
            exact["mode"] = mode
            logger.info(f"命中精确匹配({exact['elapsed_ms']}ms): {exact.get('title', question[:30])}")
            _query_stats["exact_hits"] += 1
            return format_answer(exact)

        # ---- 第1层（半精确）：标签匹配层（local/auto 模式）----
        if mode in ("auto", "local"):
            if self.tag_matcher.ready:
                tag_results = self.tag_matcher.search(question, top_k=3)
                if tag_results:
                    best_tag = tag_results[0]
                    if best_tag["score"] >= 0.5:
                        logger.info(f"命中标签匹配({best_tag['score']:.3f})")
                        _query_stats["tag_hits"] = _query_stats.get("tag_hits", 0) + 1
                        raw = {
                            "success": True,
                            "answer": best_tag["answer"],
                            "layer": "tag_match",
                            "type": "tag",
                            "score": best_tag["score"],
                            "confidence": best_tag.get("confidence", self._confidence_label(best_tag["score"], "tag_match")),
                            "source": best_tag.get("source", "电脑医生知识库"),
                            "tags": best_tag.get("tags", ""),
                            "severity": best_tag.get("severity", "中"),
                            "knowledge_id": best_tag["id"],
                            "matched_tags": best_tag.get("matched_tags", ""),
                            "layer_label": "标签匹配",
                            "candidates": tag_results[1:],
                            "elapsed_ms": round((time.time() - t0) * 1000),
                            "mode": mode,
                        }
                        return format_answer(raw)

        # ---- 第2层：语义检索 / 关键词兜底（local/auto 模式）----
        semantic_hit = None
        if mode in ("auto", "local"):
            retriever = self.semantic if self.semantic.ready else self.keyword
            if retriever.ready:
                results = retriever.search(question, top_k=3)
                if results:
                    best = results[0]
                    layer_name = "semantic" if self.semantic.ready else "keyword"
                    threshold = self.config["semantic"]["threshold"] if layer_name == "semantic" else 0.06
                    if best["score"] >= threshold:
                        logger.info(f"命中{layer_name}检索({best['score']:.3f}): {best.get('question', '')[:40]}")
                        if layer_name == "semantic":
                            _query_stats["semantic_hits"] += 1
                        else:
                            _query_stats["keyword_hits"] += 1
                        semantic_hit = format_answer({
                            "success": True,
                            "answer": best["answer"],
                            "layer": layer_name,
                            "type": "semantic" if layer_name == "semantic" else "keyword",
                            "score": best["score"],
                            "confidence": best.get("confidence", self._confidence_label(best["score"], layer_name)),
                            "source": best.get("source", "电脑医生知识库"),
                            "tags": best.get("tags", ""),
                            "severity": best.get("severity", "中"),
                            "knowledge_id": best["id"],
                            "layer_label": "语义检索" if layer_name == "semantic" else "关键词匹配",
                            "candidates": results[1:],
                            "elapsed_ms": round((time.time() - t0) * 1000),
                            "mode": mode,
                        })
            # local 模式下，语义命中高置信度直接返回，否则走本地模型
            if mode == "local":
                if semantic_hit:
                    score_check = semantic_hit.get("score", 0)
                    if score_check >= 0.6:
                        return semantic_hit
                # 低分语义结果
                result = self.local_model.ask(question)
                if result:
                    logger.info(f"命中本地模型: {result['answer'][:40]}...")
                    _query_stats["local_hits"] += 1
                    answer = result["answer"]
                    score_v = result.get("score", 0.7)
                    if self.config["learning"]["auto_learn"]:
                        kid = KnowledgeBridge.add_knowledge(question, answer, source="local_model")
                        if kid:
                            self.semantic.add_document(kid, question, answer)
                            self.keyword.add_document(kid, question, answer)
                            self.tag_matcher.add_document(kid, question, answer)
                    return format_answer({
                        "success": True, "answer": answer,
                        "layer": "local_model", "type": "local_model",
                        "score": score_v,
                        "confidence": self._confidence_label(score_v, "local_model"),
                        "source": "本地AI模型生成", "tags": "", "severity": "中",
                        "layer_label": "本地AI模型",
                        "model_note": "由本地AI生成，仅供参考",
                        "elapsed_ms": round((time.time() - t0) * 1000),
                        "mode": mode,
                    })
                # 本地模式无结果
                if semantic_hit:
                    return semantic_hit
                return self._fallback_answer(question, t0, mode)

        # ---- cloud 模式：精确后直接云端 ----
        if mode == "cloud":
            result = self.cloud.ask(question)
            if result:
                logger.info(f"云模式命中: {result['answer'][:40]}...")
                _query_stats["cloud_hits"] += 1
                return format_answer({
                    "success": True,
                    "answer": result["answer"],
                    "layer": "cloud", "type": "cloud",
                    "score": result.get("score", 0.5),
                    "confidence": self._confidence_label(result.get("score", 0.5), "cloud"),
                    "source": "云端AI模型", "tags": "", "severity": "中",
                    "layer_label": "云端AI模型",
                    "model": result.get("model", ""),
                    "elapsed_ms": round((time.time() - t0) * 1000),
                    "mode": mode,
                })
            return self._fallback_answer(question, t0, mode)

        # ---- search 模式：联网优先 ----
        if mode == "search":
            web_result = self._web_search(question)
            if web_result:
                return web_result
            # 联网无结果，尝试云端
            result = self.cloud.ask(question)
            if result:
                _query_stats["cloud_hits"] += 1
                return format_answer({
                    "success": True,
                    "answer": result["answer"],
                    "layer": "cloud", "type": "cloud",
                    "score": result.get("score", 0.5),
                    "confidence": self._confidence_label(result.get("score", 0.5), "cloud"),
                    "source": "云端AI模型（联网无结果后回退）", "tags": "", "severity": "中",
                    "layer_label": "云端AI模型",
                    "elapsed_ms": round((time.time() - t0) * 1000),
                    "mode": mode,
                })
            return self._fallback_answer(question, t0, mode)

        # ---- auto 模式：语义得分高直接返回，否则联网→云端 ----
        if semantic_hit and semantic_hit.get("score", 0) >= 0.7:
            return semantic_hit

        # auto 模式：联网搜索（本地知识不足时补充）
        web_result = self._web_search(question)
        if web_result:
            return web_result
        if semantic_hit:
            return semantic_hit

        # 本地模型
        result = self.local_model.ask(question)
        if result:
            logger.info(f"命中本地模型: {result['answer'][:40]}...")
            _query_stats["local_hits"] += 1
            answer = result["answer"]
            score_v = result.get("score", 0.7)
            if self.config["learning"]["auto_learn"]:
                kid = KnowledgeBridge.add_knowledge(question, answer, source="local_model")
                if kid:
                    self.semantic.add_document(kid, question, answer)
                    self.keyword.add_document(kid, question, answer)
                    self.tag_matcher.add_document(kid, question, answer)
            return format_answer({
                "success": True, "answer": answer,
                "layer": "local_model", "type": "local_model",
                "score": score_v,
                "confidence": self._confidence_label(score_v, "local_model"),
                "source": "本地AI模型生成", "tags": "", "severity": "中",
                "layer_label": "本地AI模型",
                "model_note": "由本地AI生成，仅供参考",
                "elapsed_ms": round((time.time() - t0) * 1000),
                "mode": mode,
            })

        # 云端 API
        result = self.cloud.ask(question)
        if result:
            logger.info(f"命中云端API: {result['answer'][:40]}...")
            _query_stats["cloud_hits"] += 1
            answer = result["answer"]
            if self.config["learning"]["auto_learn"]:
                kid = KnowledgeBridge.add_knowledge(question, answer, source="ai")
                if kid:
                    self.semantic.add_document(kid, question, answer)
                    self.keyword.add_document(kid, question, answer)
                    self.tag_matcher.add_document(kid, question, answer)
            return format_answer({
                "success": True, "answer": answer,
                "layer": "cloud", "type": "cloud",
                "score": result.get("score", 0.5),
                "confidence": self._confidence_label(result.get("score", 0.5), "cloud"),
                "source": "云端AI模型", "tags": "", "severity": "中",
                "layer_label": "云端AI模型",
                "model": result.get("model", ""),
                "elapsed_ms": round((time.time() - t0) * 1000),
                "mode": mode,
            })

        return self._fallback_answer(question, t0, mode)

    def _web_search(self, question: str) -> Optional[dict]:
        """联网搜索辅助方法，返回格式化答案或 None
        搜索结果自动缓存到本地知识库，下次同样问题可直接本地命中"""
        if not self.web_searcher:
            return None
        t0 = time.time()
        text = self.web_searcher.search(question, count=3)
        if text:
            logger.info(f"命中联网搜索: {question[:40]}...")
            _query_stats["cloud_hits"] += 1  # 复用 cloud 计数
            # 搜索结果自动存入本地知识库（标记 source=web，低初始权重 0.3）
            if self.config["learning"]["auto_learn"]:
                try:
                    kid = KnowledgeBridge.add_knowledge(
                        question, text, source="web_search",
                        tags="联网搜索,在线资源", severity="低", weight=0.3
                    )
                    if kid:
                        self.semantic.add_document(kid, question, text, 0.3,
                                                   source="web_search", tags="联网搜索,在线资源", severity="低")
                        self.keyword.add_document(kid, question, text, 0.3,
                                                  source="web_search", tags="联网搜索,在线资源", severity="低")
                        self.tag_matcher.add_document(kid, question, text, 0.3,
                                                      source="web_search", tags="联网搜索,在线资源", severity="低")
                        logger.info(f"联网搜索结果已缓存到本地知识库: id={kid}")
                except Exception as e:
                    logger.warning(f"缓存联网搜索结果失败: {e}")
            return format_answer({
                "success": True,
                "answer": text,
                "layer": "web_search",
                "type": "web_search",
                "score": 0.55,
                "confidence": "medium",
                "source": "Bing 搜索引擎",
                "tags": "",
                "severity": "低",
                "layer_label": "联网搜索",
                "elapsed_ms": round((time.time() - t0) * 1000),
                "mode": "search",
            })
        return None

    def _fallback_answer(self, question: str, t0: float, mode: str) -> dict:
        """全部失败时的兜底回复"""
        KnowledgeBridge.log_unanswered(question)
        logger.info(f"未找到答案[{mode}]: {question[:60]}")
        _query_stats["misses"] += 1
        if len(_query_stats["low_confidence_queries"]) >= 20:
            _query_stats["low_confidence_queries"].pop(0)
        _query_stats["low_confidence_queries"].append({
            "query": question[:100], "time": time.strftime("%m-%d %H:%M")
        })
        hint = "知识库中暂无相关内容，请尝试换个说法描述您的问题。"
        if mode == "search":
            hint = "联网搜索无结果，请尝试更换关键词或切换到其他AI模式。"
        return format_answer({
            "success": False,
            "answer": hint,
            "layer": "fallback",
            "type": "fallback",
            "score": 0,
            "confidence": "low",
            "source": "",
            "tags": "",
            "severity": "",
            "layer_label": "未匹配",
            "elapsed_ms": round((time.time() - t0) * 1000),
            "mode": mode,
        })

    def feedback(self, knowledge_id, is_helpful: bool, user_question: str = "") -> None:
        """
        用户反馈 — 同步更新 SQLite 和内存中的权重
        knowledge_id 支持 int 或 str 类型
        """
        KnowledgeBridge.record_feedback(knowledge_id, is_helpful, user_question)

        # 同步更新内存中的权重（语义检索、关键词检索、标签匹配三层）
        delta = 0.1 if is_helpful else -0.2
        str_id = str(knowledge_id)

        for retriever in [self.semantic, self.keyword, self.tag_matcher]:
            for doc in retriever.documents:
                doc_id = str(doc.get("id", ""))
                if doc_id == str_id or doc_id == str(knowledge_id):
                    old_w = doc.get("weight", 1.0)
                    new_w = max(0.01, min(5.0, old_w + delta))
                    doc["weight"] = new_w
                    logger.info(
                        f"反馈更新权重: id={str_id} is_helpful={is_helpful} "
                        f"{old_w:.2f} → {new_w:.2f}"
                    )
                    break

    def add_knowledge(self, question: str, answer: str, source: str = "user",
                      tags: str = "", severity: str = "中") -> Optional[int]:
        """手动添加知识（用户贡献默认低权重0.5，经点赞后可逐步提升）"""
        # 用户贡献的知识初始权重为0.5，人工审核后可提升至1.0
        initial_weight = 0.5 if source == "user" else 1.0
        kid = KnowledgeBridge.add_knowledge(question, answer, source=source,
                                            tags=tags, severity=severity,
                                            weight=initial_weight)
        if kid:
            self.semantic.add_document(kid, question, answer, initial_weight,
                                       source=source, tags=tags, severity=severity)
            self.keyword.add_document(kid, question, answer, initial_weight,
                                      source=source, tags=tags, severity=severity)
            self.tag_matcher.add_document(kid, question, answer, initial_weight,
                                          source=source, tags=tags, severity=severity)
        return kid

    def rebuild_index(self) -> bool:
        """重建语义索引 + 关键词索引 + 标签索引"""
        rows = KnowledgeBridge.load_all()
        ok1 = self.semantic.build_index(rows)
        ok2 = self.keyword.build_index(rows)
        ok3 = self.tag_matcher.build_index(rows)
        return ok1 or ok2 or ok3

    def get_status(self) -> dict:
        """获取引擎状态"""
        stats = KnowledgeBridge.get_stats()
        return {
            "semantic_ready": self.semantic.ready,
            "keyword_ready": self.keyword.ready,
            "tag_ready": self.tag_matcher.ready,
            "semantic_docs": len(self.semantic.documents),
            "keyword_docs": len(self.keyword.documents),
            "tag_docs": len(self.tag_matcher.documents),
            "local_model_available": self.local_model.check_availability(),
            "cloud_configured": (self.config["cloud"]["enabled"] and
                                 bool(self.cloud._get_api_key())),
            "hardware": {
                "ram_gb": self.hardware.total_ram_gb,
                "has_gpu": self.hardware.has_gpu,
                "gpu_name": self.hardware.gpu_name,
            },
            "knowledge_stats": stats,
        }


# ============================================================
# 7. 便捷函数（兼容旧接口）
# ============================================================

# 全局单例
_engine: Optional[AIEngine] = None


def get_engine() -> AIEngine:
    """获取全局引擎实例（懒加载，线程安全）"""
    global _engine
    if _engine is None:
        # 双检锁确保只创建一次
        with _MODULE_LOCK:
            if _engine is None:
                _engine = AIEngine()
    return _engine


def search(query: str):
    """模块级语义检索便捷函数（第一阶段优化）。

    返回 (answer, score) 或 None。详见 AIEngine.search。
    """
    return get_engine().search(query)


def ask(question: str, mode: str = "auto") -> dict:
    """快速查询
    mode: 'auto' | 'local' | 'cloud' | 'search'
    """
    return get_engine().ask(question, mode=mode)


def web_search(query: str) -> Optional[str]:
    """独立的联网搜索入口"""
    engine = get_engine()
    if engine.web_searcher:
        return engine.web_searcher.search(query)
    return None


def feedback(knowledge_id, is_helpful: bool = None, user_question: str = "", helpful: bool = None) -> None:
    """快速反馈（knowledge_id 支持 int 或 str）
    兼容两种调用方式：is_helpful= 与 helpful=（helpful 优先）
    """
    effective = helpful if helpful is not None else is_helpful
    if effective is None:
        effective = True
    get_engine().feedback(knowledge_id, effective, user_question)


def get_status() -> dict:
    """模块级状态查询（兼容 test_ai.py 调用）"""
    return get_engine().get_status()


def add_knowledge(question: str, answer: str, **kwargs) -> Optional[int]:
    """快速添加知识（支持 tags, severity, source 等扩展参数）"""
    return get_engine().add_knowledge(question, answer,
                                      source=kwargs.get("source", "user"),
                                      tags=kwargs.get("tags", ""),
                                      severity=kwargs.get("severity", "中"))


def rebuild() -> bool:
    """重建索引"""
    return get_engine().rebuild_index()


def status() -> dict:
    """获取状态"""
    return get_engine().get_status()


def delete_knowledge(knowledge_id) -> bool:
    """删除知识条目"""
    try:
        KnowledgeBridge.delete(knowledge_id)
        get_engine().rebuild_index()
        return True
    except Exception as e:
        logger.error(f"删除知识失败: {e}")
        return False


def reload_knowledge() -> bool:
    """重新加载知识库并重建索引"""
    return get_engine().rebuild_index()


def get_query_stats() -> dict:
    """获取查询统计（监控用）"""
    with _stats_lock:
        stats = dict(_query_stats)
        stats["low_confidence_queries"] = list(_query_stats["low_confidence_queries"])
    stats["exact_rate"] = round(stats["exact_hits"] / max(stats["total"], 1) * 100, 1)
    stats["semantic_rate"] = round(stats["semantic_hits"] / max(stats["total"], 1) * 100, 1)
    stats["keyword_rate"] = round(stats["keyword_hits"] / max(stats["total"], 1) * 100, 1)
    stats["miss_rate"] = round(stats["misses"] / max(stats["total"], 1) * 100, 1)
    return stats


# ============================================================
# 8. 测试辅助函数（模块级暴露，供 pytest 使用）
# ============================================================

def _load_error_codes() -> list:
    """加载错误码表为列表格式（供测试使用）"""
    import json as _json
    code_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "error_codes.json")
    if not os.path.exists(code_path):
        logger.warning(f"错误码文件不存在: {code_path}")
        return []
    try:
        with open(code_path, "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception as e:
        logger.error(f"加载错误码表失败: {e}")
        return []


def _exact_match(question: str):
    """精确匹配查询（供测试使用，无需初始化完整引擎）"""
    engine = get_engine()
    return engine._exact_match(question)


def _init_keyword_index():
    """强制初始化关键词索引（供测试使用）"""
    engine = get_engine()
    rows = KnowledgeBridge.load_all()
    if rows:
        engine.keyword.build_index(rows)


# ============================================================
# 9. 自检 & 命令行工具
# ============================================================

if __name__ == "__main__":
    print("=" * 50)
    print("  电脑医生 AI 引擎 — 自检")
    print("=" * 50)

    engine = AIEngine()
    st = engine.get_status()

    print(f"\n  硬件检测:")
    print(f"    内存: {st['hardware']['ram_gb']} GB")
    print(f"    GPU: {'有 (' + st['hardware']['gpu_name'] + ')' if st['hardware']['has_gpu'] else '无'}")

    print(f"\n  引擎状态:")
    print(f"    语义检索: {'[OK] 就绪' if st['semantic_ready'] else '[--] 未就绪'} ({st['semantic_docs']} 条)")
    print(f"    关键词匹配: {'[OK] 就绪' if st['keyword_ready'] else '[--] 未就绪'} ({st['keyword_docs']} 条)")
    print(f"    本地模型: {'[OK] 可用' if st['local_model_available'] else '[--] 不可用'}")
    print(f"    云端 API: {'[OK] 已配置' if st['cloud_configured'] else '[--] 未配置'}")

    print(f"\n  知识库:")
    print(f"    总知识: {st['knowledge_stats']['total_knowledge']}")
    print(f"    未回答: {st['knowledge_stats']['total_unanswered']}")
    print(f"    反馈数: {st['knowledge_stats']['total_feedback']}")

    can_chat = st['semantic_ready'] or st['keyword_ready'] or st['cloud_configured'] or st['local_model_available']
    if can_chat:
        print("\n" + "=" * 50)
        print("  交互测试（输入 q 退出）")
        print("=" * 50)
        if not st['semantic_ready'] and st['keyword_ready']:
            print("  提示: 语义模型未下载，当前使用关键词匹配（离线可用）")
            print("  升级语义检索: 设置 HF_ENDPOINT=https://hf-mirror.com 后重启")
        elif not st['semantic_ready']:
            print("  提示: 无本地检索通道，将直接尝试云端/本地模型")
        while True:
            q = input("\n  你的问题 > ").strip()
            if q.lower() in ("q", "quit", "exit"):
                break
            if not q:
                continue
            result = engine.ask(q)
            print(f"  [{result['layer']}] (score={result['score']}, {result['elapsed_ms']}ms)")
            print(f"  {result['answer'][:200]}")
    else:
        print("\n  当前无可用的回答通道，请先完成以下任一操作：")
        print("    1. pip install sentence-transformers    （启用语义检索，推荐）")
        print("    2. 编辑 ai_config.json 填入云 API 密钥（启用云端兜底）")
        print("    3. 安装 Ollama 并拉取本地模型          （启用本地推理）")
