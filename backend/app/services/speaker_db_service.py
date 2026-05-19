"""
声纹数据库模块 - 持久化存储员工声纹向量
=========================================
使用独立的 SQLite 文件存储，与主数据库分离。
支持异步 FastAPI 调用。

表结构：
    speaker_profiles        说话人元数据表
    speaker_embeddings      声纹向量表（独立存储避免行过大）
    speaker_identification_log  识别记录日志
"""

import os
import time
import json
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any

import numpy as np


# ==================== 默认路径配置 ====================

def _get_default_db_path() -> str:
    backend_dir = Path(__file__).resolve().parent.parent.parent
    db_dir = backend_dir / "data"
    db_dir.mkdir(exist_ok=True)
    # 优先使用已迁移的声纹数据库
    default_path = str(db_dir / "speaker_voiceprints.db")
    # 如果主数据库不存在或为空，尝试从 InsightEye 迁移
    if os.path.exists(default_path):
        import sqlite3
        try:
            conn = sqlite3.connect(default_path)
            cur = conn.execute("SELECT COUNT(*) FROM speaker_profiles")
            count = cur.fetchone()[0]
            conn.close()
            if count > 0:
                return default_path
        except Exception:
            pass
    # 回退到 app/data
    app_data_dir = backend_dir / "app" / "data"
    app_data_dir.mkdir(exist_ok=True)
    return str(app_data_dir / "speaker_voiceprints.db")


# ==================== 向量序列化工具 ====================

def ndarray_to_bytes(arr: Optional[np.ndarray]) -> bytes:
    """numpy 数组序列化为压缩字节串"""
    if arr is None:
        return b""
    return arr.astype(np.float32).tobytes()


def bytes_to_ndarray(data: bytes, dtype=np.float32) -> Optional[np.ndarray]:
    """字节串反序列化为 numpy 数组"""
    if data is None or (hasattr(data, "__len__") and len(data) == 0):
        return None
    if isinstance(data, np.ndarray):
        return data.astype(dtype)
    return np.frombuffer(data, dtype=dtype)


# ==================== SpeakerDatabase ====================

class SpeakerDatabase:
    """
    说话人声纹数据库（SQLite）

    表结构：
        speaker_profiles:
            speaker_id      TEXT    PRIMARY KEY
            name            TEXT
            role            TEXT
            department      TEXT
            sample_count    INTEGER
            quality         REAL
            registered_at   TEXT    (ISO 8601 时间戳)
            updated_at      TEXT    (ISO 8601 时间戳)
            is_active       INTEGER DEFAULT 1  (1=在职, 0=离职)

        speaker_embeddings:
            speaker_id      TEXT    PRIMARY KEY
            embedding        BLOB    (192-dim float32, 768 bytes)
            embedding_mean  BLOB    (可选)
            embedding_std   BLOB    (可选)

        speaker_identification_log:
            id              INTEGER PRIMARY KEY AUTOINCREMENT
            speaker_id      TEXT
            session_id      TEXT
            confidence      REAL
            recognized_at   TEXT
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS speaker_profiles (
        speaker_id      TEXT    PRIMARY KEY,
        name            TEXT,
        role            TEXT,
        department      TEXT,
        sample_count    INTEGER DEFAULT 0,
        quality         REAL    DEFAULT 0.0,
        registered_at   TEXT,
        updated_at      TEXT,
        is_active       INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS speaker_embeddings (
        speaker_id      TEXT    PRIMARY KEY,
        embedding       BLOB,
        embedding_mean  BLOB,
        embedding_std   BLOB
    );

    CREATE TABLE IF NOT EXISTS speaker_identification_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        speaker_id      TEXT,
        session_id      TEXT,
        confidence      REAL,
        recognized_at   TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_profiles_is_active ON speaker_profiles(is_active);
    CREATE INDEX IF NOT EXISTS idx_profiles_department ON speaker_profiles(department);
    CREATE INDEX IF NOT EXISTS idx_log_speaker_id ON speaker_identification_log(speaker_id);
    CREATE INDEX IF NOT EXISTS idx_log_recognized_at ON speaker_identification_log(recognized_at);
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path: str = db_path or _get_default_db_path()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """确保数据库和表存在"""
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(self.SCHEMA)
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _iso_now(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # ==================== 增 / 改 ====================

    def save_speaker(
        self,
        speaker_id: str,
        embedding: np.ndarray,
        name: Optional[str] = None,
        role: Optional[str] = None,
        department: Optional[str] = None,
        individual_embeddings: Optional[List[np.ndarray]] = None,
        quality: float = 0.0,
        embedding_mean: Optional[np.ndarray] = None,
        embedding_std: Optional[np.ndarray] = None,
        sample_count: int = 1,
        overwrite: bool = False
    ) -> bool:
        """保存或更新一个说话人的声纹到数据库"""
        if embedding is None:
            raise ValueError("embedding 不能为空")
        emb_norm = np.linalg.norm(embedding)
        if emb_norm < 1e-7:
            raise ValueError("embedding 全零或无效")

        now = self._iso_now()

        with self._conn() as conn:
            row = conn.execute(
                "SELECT speaker_id FROM speaker_profiles WHERE speaker_id = ?",
                (speaker_id,)
            ).fetchone()
            if row is not None and not overwrite:
                return False

            conn.execute("""
                INSERT OR REPLACE INTO speaker_profiles
                (speaker_id, name, role, department, sample_count, quality,
                 registered_at, updated_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?,
                        COALESCE(
                            (SELECT registered_at FROM speaker_profiles WHERE speaker_id = ?),
                            ?
                        ),
                        ?, 1)
            """, (
                speaker_id, name, role, department, sample_count, quality,
                speaker_id, now, now
            ))

            conn.execute("""
                INSERT OR REPLACE INTO speaker_embeddings
                (speaker_id, embedding, embedding_mean, embedding_std)
                VALUES (?, ?, ?, ?)
            """, (
                speaker_id,
                ndarray_to_bytes(embedding),
                ndarray_to_bytes(embedding_mean) if embedding_mean is not None else None,
                ndarray_to_bytes(embedding_std) if embedding_std is not None else None,
            ))
            conn.commit()
        return True

    # ==================== 查 ====================

    def load_speaker(self, speaker_id: str) -> Optional[Dict[str, Any]]:
        """加载单个说话人的全部数据"""
        with self._conn() as conn:
            profile_row = conn.execute(
                "SELECT * FROM speaker_profiles WHERE speaker_id = ? AND is_active = 1",
                (speaker_id,)
            ).fetchone()
            if profile_row is None:
                return None
            emb_row = conn.execute(
                "SELECT embedding, embedding_mean, embedding_std FROM speaker_embeddings WHERE speaker_id = ?",
                (speaker_id,)
            ).fetchone()
            return _row_to_dict(profile_row, emb_row)

    def load_all(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """加载所有说话人"""
        with self._conn() as conn:
            if active_only:
                profile_rows = conn.execute(
                    "SELECT * FROM speaker_profiles WHERE is_active = 1"
                ).fetchall()
            else:
                profile_rows = conn.execute(
                    "SELECT * FROM speaker_profiles"
                ).fetchall()

            results = []
            for pr in profile_rows:
                emb_row = conn.execute(
                    "SELECT embedding, embedding_mean, embedding_std FROM speaker_embeddings WHERE speaker_id = ?",
                    (pr["speaker_id"],)
                ).fetchone()
                results.append(_row_to_dict(pr, emb_row))
            return results

    def load_by_department(self, department: str) -> List[Dict[str, Any]]:
        """按部门加载"""
        with self._conn() as conn:
            profile_rows = conn.execute(
                "SELECT * FROM speaker_profiles WHERE department = ? AND is_active = 1",
                (department,)
            ).fetchall()
            results = []
            for pr in profile_rows:
                emb_row = conn.execute(
                    "SELECT embedding, embedding_mean, embedding_std FROM speaker_embeddings WHERE speaker_id = ?",
                    (pr["speaker_id"],)
                ).fetchone()
                results.append(_row_to_dict(pr, emb_row))
            return results

    def search_by_name(self, keyword: str) -> List[Dict[str, Any]]:
        """按姓名模糊搜索"""
        with self._conn() as conn:
            profile_rows = conn.execute(
                "SELECT * FROM speaker_profiles WHERE name LIKE ? AND is_active = 1",
                (f"%{keyword}%",)
            ).fetchall()
            results = []
            for pr in profile_rows:
                emb_row = conn.execute(
                    "SELECT embedding, embedding_mean, embedding_std FROM speaker_embeddings WHERE speaker_id = ?",
                    (pr["speaker_id"],)
                ).fetchone()
                results.append(_row_to_dict(pr, emb_row))
            return results

    def exists(self, speaker_id: str) -> bool:
        """说话人是否存在于数据库（在职）"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM speaker_profiles WHERE speaker_id = ? AND is_active = 1",
                (speaker_id,)
            ).fetchone()
            return row is not None

    # ==================== 改 ====================

    def update_speaker(self, speaker_id: str, **fields) -> bool:
        """更新说话人元数据（姓名、角色、部门）"""
        allowed = {"name", "role", "department"}
        invalid = set(fields.keys()) - allowed
        if invalid:
            raise ValueError(f"不支持的字段: {invalid}")
        if not fields:
            return False

        set_clauses = [f"{k} = ?" for k in fields]
        set_clauses.append("updated_at = ?")
        values = list(fields.values())
        values.append(self._iso_now())
        values.append(speaker_id)

        with self._conn() as conn:
            cur = conn.execute(
                f"UPDATE speaker_profiles SET {', '.join(set_clauses)} "
                "WHERE speaker_id = ? AND is_active = 1",
                values
            )
            conn.commit()
            return cur.rowcount > 0

    def deactivate_speaker(self, speaker_id: str) -> bool:
        """离职处理 - 软删除"""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE speaker_profiles SET is_active = 0, updated_at = ? "
                "WHERE speaker_id = ? AND is_active = 1",
                (self._iso_now(), speaker_id)
            )
            conn.commit()
            return cur.rowcount > 0

    def reactivate_speaker(self, speaker_id: str) -> bool:
        """重新入职，撤销软删除"""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE speaker_profiles SET is_active = 1, updated_at = ? "
                "WHERE speaker_id = ? AND is_active = 0",
                (self._iso_now(), speaker_id)
            )
            conn.commit()
            return cur.rowcount > 0

    # ==================== 删 ====================

    def delete_speaker(self, speaker_id: str) -> bool:
        """彻底删除（物理删除）"""
        with self._conn() as conn:
            cur_p = conn.execute(
                "DELETE FROM speaker_profiles WHERE speaker_id = ?",
                (speaker_id,)
            )
            cur_e = conn.execute(
                "DELETE FROM speaker_embeddings WHERE speaker_id = ?",
                (speaker_id,)
            )
            conn.commit()
            return cur_p.rowcount > 0

    def clear_identification_log(self) -> None:
        """清空识别记录日志"""
        with self._conn() as conn:
            conn.execute("DELETE FROM speaker_identification_log")
            conn.commit()

    # ==================== 统计 ====================

    def get_stats(self) -> Dict[str, Any]:
        """获取数据库统计信息"""
        def safe_int(value, default=0):
            if value is None:
                return default
            try:
                if hasattr(value, "item"):
                    value = value.item()
                return max(0, int(value))
            except (ValueError, TypeError):
                return default

        def safe_float(value, default=0.0):
            if value is None:
                return default
            try:
                if hasattr(value, "item"):
                    value = value.item()
                return float(value)
            except (ValueError, TypeError):
                return default

        with self._conn() as conn:
            total = safe_int(conn.execute("SELECT COUNT(*) FROM speaker_profiles").fetchone()[0])
            active = safe_int(conn.execute(
                "SELECT COUNT(*) FROM speaker_profiles WHERE is_active = 1"
            ).fetchone()[0])
            inactive = max(0, total - active)

            total_idents = safe_int(conn.execute(
                "SELECT COUNT(*) FROM speaker_identification_log"
            ).fetchone()[0])
            today_idents = safe_int(conn.execute(
                "SELECT COUNT(*) FROM speaker_identification_log WHERE date(recognized_at) = date('now')"
            ).fetchone()[0])
            unique_spk = safe_int(conn.execute(
                "SELECT COUNT(DISTINCT speaker_id) FROM speaker_identification_log"
            ).fetchone()[0])

            quality_rows = []
            try:
                quality_rows = conn.execute(
                    "SELECT quality FROM speaker_profiles WHERE is_active = 1 AND quality > 0"
                ).fetchall()
            except Exception:
                pass
            quality_avg = sum(safe_float(r["quality"]) for r in quality_rows) / len(quality_rows) if quality_rows else 0.0

            sample_rows = []
            try:
                sample_rows = conn.execute(
                    "SELECT sample_count FROM speaker_profiles WHERE is_active = 1"
                ).fetchall()
            except Exception:
                pass
            sample_avg = sum(safe_int(r["sample_count"]) for r in sample_rows) / len(sample_rows) if sample_rows else 0

            dept_dist = {}
            try:
                dept_rows = conn.execute(
                    "SELECT department, COUNT(*) as cnt FROM speaker_profiles WHERE is_active = 1 AND department IS NOT NULL AND department != '' GROUP BY department ORDER BY cnt DESC"
                ).fetchall()
                dept_dist = {str(r["department"]): safe_int(r["cnt"]) for r in dept_rows}
            except Exception:
                pass

            role_dist = {}
            try:
                role_rows = conn.execute(
                    "SELECT role, COUNT(*) as cnt FROM speaker_profiles WHERE is_active = 1 AND role IS NOT NULL AND role != '' GROUP BY role ORDER BY cnt DESC"
                ).fetchall()
                role_dist = {str(r["role"]): safe_int(r["cnt"]) for r in role_rows}
            except Exception:
                pass

            return {
                "total": total,
                "active": active,
                "inactive": inactive,
                "db_path": str(self.db_path),
                "total_identifications": total_idents,
                "today_identifications": today_idents,
                "unique_speakers_identified": unique_spk,
                "quality_avg": round(float(quality_avg), 3),
                "sample_avg": round(float(sample_avg), 2),
                "department_distribution": dept_dist,
                "role_distribution": role_dist,
            }

    # ==================== 识别记录 ====================

    def log_identification(
        self,
        speaker_id: str,
        session_id: str,
        confidence: float,
    ) -> None:
        """记录一次声纹识别事件"""
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO speaker_identification_log (speaker_id, session_id, confidence, recognized_at)
                VALUES (?, ?, ?, ?)
            """, (speaker_id, session_id, confidence, self._iso_now()))
            conn.commit()

    def get_identification_stats(self, speaker_id: str) -> Dict[str, Any]:
        """获取指定说话人的识别统计信息"""
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM speaker_identification_log WHERE speaker_id = ?",
                (speaker_id,)
            ).fetchone()[0] or 0

            last_row = conn.execute(
                "SELECT recognized_at, confidence FROM speaker_identification_log "
                "WHERE speaker_id = ? ORDER BY recognized_at DESC LIMIT 1",
                (speaker_id,)
            ).fetchone()

            avg_conf = conn.execute(
                "SELECT AVG(confidence) FROM speaker_identification_log WHERE speaker_id = ?",
                (speaker_id,)
            ).fetchone()[0] or 0.0

            recent_rows = conn.execute(
                "SELECT recognized_at FROM speaker_identification_log "
                "WHERE speaker_id = ? ORDER BY recognized_at DESC LIMIT 30",
                (speaker_id,)
            ).fetchall()

            return {
                "total_identifications": max(0, int(total)),
                "avg_confidence": round(float(avg_conf), 3),
                "last_recognized_at": last_row["recognized_at"] if last_row else None,
                "last_confidence": round(float(last_row["confidence"]), 3) if last_row and last_row["confidence"] else None,
                "recent_recognition_dates": [r["recognized_at"] for r in recent_rows],
            }

    def get_all_identification_stats(self) -> Dict[str, Dict[str, Any]]:
        """获取所有说话人的识别统计"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT speaker_id FROM speaker_profiles WHERE is_active = 1"
            ).fetchall()
        return {r["speaker_id"]: self.get_identification_stats(r["speaker_id"])
                for r in rows}

    # ==================== 声纹相似度分析 ====================

    def compute_similarity_distribution(self) -> List[Dict[str, Any]]:
        """计算所有说话人两两之间的余弦相似度分布"""
        speakers = self.load_all(active_only=True)
        if len(speakers) < 2:
            return []

        pairs = []
        for i in range(len(speakers)):
            for j in range(i + 1, len(speakers)):
                a = speakers[i]
                b = speakers[j]
                if a.get("embedding") is None or b.get("embedding") is None:
                    continue
                emb_a = a["embedding"].astype(np.float32)
                emb_b = b["embedding"].astype(np.float32)
                norm_a = np.linalg.norm(emb_a)
                norm_b = np.linalg.norm(emb_b)
                if norm_a < 1e-7 or norm_b < 1e-7:
                    continue
                similarity = float(np.dot(emb_a, emb_b) / (norm_a * norm_b))

                std_a = a.get("embedding_std")
                std_b = b.get("embedding_std")
                std_a_val = float(np.mean(np.abs(std_a))) if std_a is not None and std_a.size > 0 else None
                std_b_val = float(np.mean(np.abs(std_b))) if std_b is not None and std_b.size > 0 else None

                pairs.append({
                    "speaker_a_id": a["speaker_id"],
                    "speaker_a_name": a.get("name", a["speaker_id"]),
                    "speaker_b_id": b["speaker_id"],
                    "speaker_b_name": b.get("name", b["speaker_id"]),
                    "similarity": round(similarity, 4),
                    "distance": round(1.0 - similarity, 4),
                    "embedding_std_a": round(std_a_val, 4) if std_a_val is not None else None,
                    "embedding_std_b": round(std_b_val, 4) if std_b_val is not None else None,
                })

        pairs.sort(key=lambda x: x["similarity"], reverse=True)
        return pairs


# ==================== 工具函数 ====================

def _row_to_dict(profile_row: sqlite3.Row, emb_row: Optional[sqlite3.Row]) -> Dict[str, Any]:
    """将数据库行转换为 speaker dict"""
    d = dict(profile_row)
    d["is_active"] = bool(d["is_active"])

    if emb_row is not None:
        d["embedding"] = bytes_to_ndarray(emb_row["embedding"]) if emb_row["embedding"] else None
        d["embedding_mean"] = bytes_to_ndarray(emb_row["embedding_mean"]) if emb_row["embedding_mean"] else None
        d["embedding_std"] = bytes_to_ndarray(emb_row["embedding_std"]) if emb_row["embedding_std"] else None
    else:
        d["embedding"] = None
        d["embedding_mean"] = None
        d["embedding_std"] = None

    if d.get("embedding_std") is not None and d["embedding_std"].size > 0:
        std_arr = d["embedding_std"]
        d["embedding_std_mean"] = float(np.mean(np.abs(std_arr)))
        d["embedding_std_max"] = float(np.max(np.abs(std_arr)))
        d["embedding_std_std"] = float(np.std(std_arr))
    else:
        d["embedding_std_mean"] = None
        d["embedding_std_max"] = None
        d["embedding_std_std"] = None

    return d


# ==================== 全局单例 ====================

_db_instance: Optional[SpeakerDatabase] = None


def get_speaker_db() -> SpeakerDatabase:
    """获取声纹数据库全局单例"""
    global _db_instance
    if _db_instance is None:
        _db_instance = SpeakerDatabase()
    return _db_instance
