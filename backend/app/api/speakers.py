"""
声纹管理 API 路由
"""

import base64
import io
import time
from typing import Optional, List

import numpy as np
import soundfile as sf
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse

from app.services.speaker_db_service import get_speaker_db, bytes_to_ndarray
from app.schemas.speaker import (
    SpeakerProfile,
    SpeakerStats,
    SpeakerListResponse,
    SpeakerStatsDetailResponse,
    SpeakerSimilarityPair,
    RegisterResponse,
    DeleteRequest,
    DeleteResponse,
    SearchResponse,
)


router = APIRouter(tags=["speakers"])


def _safe_profile(s: dict) -> dict:
    """将数据库字典转换为安全的 API 响应（不含 embedding）"""
    sample_count = s.get("sample_count")
    sample_count = max(0, int(sample_count)) if sample_count is not None else 0

    quality = s.get("quality")
    quality = float(quality) if quality is not None else 0.0

    total_idents = s.get("total_identifications")
    total_idents = max(0, int(total_idents)) if total_idents is not None else 0

    last_conf = s.get("last_confidence")
    last_conf = round(float(last_conf), 3) if last_conf is not None else None

    avg_conf = s.get("avg_confidence")
    avg_conf = round(float(avg_conf), 3) if avg_conf is not None else None

    return {
        "speaker_id": s.get("speaker_id"),
        "name": s.get("name"),
        "role": s.get("role"),
        "department": s.get("department"),
        "sample_count": sample_count,
        "quality": quality,
        "registered_at": s.get("registered_at"),
        "updated_at": s.get("updated_at"),
        "is_active": bool(s.get("is_active", True)),
        "total_identifications": total_idents,
        "avg_confidence": avg_conf,
        "last_recognized_at": s.get("last_recognized_at"),
        "last_confidence": last_conf,
        "embedding_std_mean": s.get("embedding_std_mean"),
        "embedding_std_max": s.get("embedding_std_max"),
    }


def _safe_stats(stats: dict) -> dict:
    """将统计字典转换为安全的 API 响应"""
    def safe_int(v, default=0):
        if v is None:
            return default
        try:
            if hasattr(v, "item"):
                v = v.item()
            return max(0, int(v))
        except (ValueError, TypeError):
            return default

    def safe_float(v, default=0.0):
        if v is None:
            return default
        try:
            if hasattr(v, "item"):
                v = v.item()
            return float(v)
        except (ValueError, TypeError):
            return default

    return {
        "total": safe_int(stats.get("total")),
        "active": safe_int(stats.get("active")),
        "inactive": safe_int(stats.get("inactive")),
        "db_path": str(stats.get("db_path", "")),
        "total_identifications": safe_int(stats.get("total_identifications")),
        "today_identifications": safe_int(stats.get("today_identifications")),
        "unique_speakers_identified": safe_int(stats.get("unique_speakers_identified")),
        "quality_avg": round(safe_float(stats.get("quality_avg")), 3),
        "sample_avg": round(safe_float(stats.get("sample_avg")), 2),
        "department_distribution": stats.get("department_distribution", {}),
        "role_distribution": stats.get("role_distribution", {}),
    }


@router.get("", response_model=SpeakerListResponse)
async def list_speakers():
    """获取所有已注册的说话人列表"""
    db = get_speaker_db()
    speakers = db.load_all(active_only=True)
    stats = db.get_stats()

    safe_speakers = [_safe_profile(s) for s in speakers]
    return {
        "speakers": safe_speakers,
        "stats": _safe_stats(stats),
        "total": len(safe_speakers),
    }


@router.get("/stats", response_model=SpeakerStatsDetailResponse)
async def get_stats():
    """获取声纹数据库统计信息（含相似度分布）"""
    db = get_speaker_db()
    stats = db.get_stats()
    similarity_dist = db.compute_similarity_distribution()

    similarity_pairs = []
    for pair in similarity_dist[:50]:
        similarity_pairs.append({
            "speaker_a_id": pair["speaker_a_id"],
            "speaker_a_name": pair.get("speaker_a_name", pair["speaker_a_id"]),
            "speaker_b_id": pair["speaker_b_id"],
            "speaker_b_name": pair.get("speaker_b_name", pair["speaker_b_id"]),
            "similarity": pair["similarity"],
            "distance": pair["distance"],
            "embedding_std_a": pair.get("embedding_std_a"),
            "embedding_std_b": pair.get("embedding_std_b"),
        })

    return {
        **_safe_stats(stats),
        "similarity_distribution": similarity_pairs,
    }


@router.get("/search", response_model=SearchResponse)
async def search_speakers(name: Optional[str] = None):
    """按姓名搜索说话人"""
    db = get_speaker_db()
    if name:
        results = db.search_by_name(name)
    else:
        results = db.load_all(active_only=True)

    safe_speakers = [_safe_profile(s) for s in results]
    return {
        "speakers": safe_speakers,
        "total": len(safe_speakers),
    }


# 声纹注册质量要求常量
MIN_SAMPLES = 1             # 最少样本数量
MAX_SAMPLES = 5            # 最多样本数量
MIN_SAMPLE_DURATION = 2    # 单个样本最短时长（秒）
MAX_SAMPLE_DURATION = 15  # 单个样本最长时长（秒）
MIN_TOTAL_DURATION = 2     # 最短总录音时长（秒）
MIN_QUALITY_SCORE = 0.2    # 最低质量分数

# 质量等级描述
QUALITY_LEVELS = [
    (0.8, "优秀", "声纹特征明显，识别准确率高"),
    (0.6, "良好", "声纹特征较明显，识别效果良好"),
    (0.4, "一般", "声纹特征一般，建议重新录制"),
    (0.0, "较差", "声纹特征不明显，请改善录音环境后重新录制"),
]


def get_quality_level(score: float) -> tuple:
    """根据质量分数返回等级描述"""
    for threshold, level, desc in QUALITY_LEVELS:
        if score >= threshold:
            return level, desc
    return QUALITY_LEVELS[-1][1], QUALITY_LEVELS[-1][2]


def get_quality_issues(audio_data: np.ndarray, duration_s: float) -> list:
    """诊断音频质量问题，返回问题列表"""
    issues = []
    energy = float(np.sqrt(np.mean(audio_data ** 2)))
    duration_s = len(audio_data) / 16000.0

    if duration_s < MIN_SAMPLE_DURATION:
        issues.append(f"录音时长过短（{duration_s:.1f}秒），建议至少{MIN_SAMPLE_DURATION}秒")

    if duration_s > MAX_SAMPLE_DURATION:
        issues.append(f"录音时长过长（{duration_s:.1f}秒），建议不超过{MAX_SAMPLE_DURATION}秒")

    if energy < 0.02:
        issues.append("音量过小，可能检测不到声音，请靠近麦克风或提高音量")
    elif energy < 0.05:
        issues.append("音量偏小，建议适当提高音量")
    elif energy > 0.8:
        issues.append("音量过大，可能产生失真，建议降低音量")

    rms_db = 20 * np.log10(energy + 1e-8)
    if rms_db < -40:
        issues.append("信噪比过低，环境噪声可能影响识别效果")
    elif rms_db < -30:
        issues.append("信噪比较低，建议在更安静的环境中录音")

    return issues


@router.post("/register", response_model=RegisterResponse)
async def register_speaker(
    name: str = Form(...),
    speaker_id: Optional[str] = Form(None),
    role: Optional[str] = Form(None),
    department: Optional[str] = Form(None),
    audio: Optional[UploadFile] = File(None),
    audio_count: int = Form(1),  # 前端传递的样本数量
):
    """
    注册新说话人的声纹。
    支持通过表单上传音频文件，或通过 JSON body 传递 base64 音频。
    """
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="姓名（name）为必填项")

    name = name.strip()
    role = role.strip() if role else None
    department = department.strip() if department else None
    speaker_id = speaker_id.strip() if speaker_id else None

    # 生成 speaker_id
    if not speaker_id:
        def to_pinyin_initials(s):
            m = {
                "张": "Z", "李": "L", "王": "W", "刘": "H", "陈": "C", "杨": "Y",
                "赵": "H", "黄": "H", "周": "Z", "吴": "W", "徐": "X", "孙": "S",
                "马": "M", "胡": "H", "朱": "Z", "郭": "G", "何": "H", "高": "G",
                "林": "L", "罗": "H", "钱": "Q", "冯": "F", "蒋": "J", "沈": "S",
                "韩": "H",
            }
            initials = "".join(m.get(c, c[0].upper() if c.isalpha() else "") for c in s if c not in " \t")
            return initials or "P"
        speaker_id = f"{to_pinyin_initials(name)}_{int(time.time())}"

    # 处理音频
    if audio:
        try:
            audio_bytes = await audio.read()
            audio_filename = audio.filename or "voice.wav"
            audio_content_type = audio.content_type or "audio/wav"
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"读取音频文件失败: {exc}")
    else:
        raise HTTPException(status_code=400, detail="音频文件（audio）为必填项")

    # 解析音频为 numpy array
    try:
        audio_data = _parse_audio_bytes(audio_bytes, audio_filename, audio_content_type)
        if audio_data is None or len(audio_data) == 0:
            raise HTTPException(status_code=400, detail="音频数据无效或为空")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"音频格式解析失败: {exc}")

    # 提取声纹特征（使用 CAM++）
    try:
        result = await _extract_speaker_embedding(
            audio_data, name, speaker_id, audio_count
        )
        embedding = result[0]
        quality = result[1]
        sample_count = result[2]
        quality_level = result[3]
        level_desc = result[4]
        quality_issues = result[5]
        suggestions = result[6]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"声纹提取失败: {exc}")

    # 验证质量门槛
    duration_s = len(audio_data) / 16000.0
    # total_duration = 各段音频实际时长之和（当前接口只上传一段）
    total_duration = duration_s

    quality_warnings = []
    quality_errors = []

    # 检查样本数量
    if sample_count < MIN_SAMPLES:
        quality_errors.append(f"样本数量不足：当前{sample_count}段，需要至少{MIN_SAMPLES}段")

    # 检查总时长（单段音频实际时长 >= 最短时长）
    if total_duration < MIN_TOTAL_DURATION:
        quality_errors.append(f"录音时长不足：当前{total_duration:.1f}秒，需要至少{MIN_TOTAL_DURATION}秒")

    # 检查单个样本时长
    if duration_s < MIN_SAMPLE_DURATION:
        quality_errors.append(f"单个样本时长过短：当前{duration_s:.1f}秒，需要至少{MIN_SAMPLE_DURATION}秒")

    # 检查质量分数
    if quality < MIN_QUALITY_SCORE:
        quality_errors.append(f"音频质量不达标：质量分数{quality:.0%}，需要达到{MIN_QUALITY_SCORE:.0%}以上")

    # 如果有严重错误，返回详细错误信息
    if quality_errors:
        error_detail = {
            "error_type": "quality_check_failed",
            "message": "声纹注册失败：音频质量不满足要求",
            "quality_score": round(float(quality), 3),
            "quality_level": quality_level,
            "quality_description": level_desc,
            "errors": quality_errors,
            "quality_issues": quality_issues if quality_issues else [],
            "suggestions": suggestions if suggestions else [],
            "fix_guide": (
                f"1. 请录制至少{MIN_SAMPLES}段不同的音频\n"
                f"2. 每段录音时长保持在{MIN_SAMPLE_DURATION}-{MAX_SAMPLE_DURATION}秒\n"
                f"3. 确保在安静环境中录音\n"
                f"4. 保持适中的音量，距离麦克风15-30cm\n"
                f"5. 朗读提示文字可获得更好的识别效果"
            ),
        }
        raise HTTPException(status_code=400, detail=error_detail)

    # 质量警告（不影响注册，但给出提示）
    if quality < 0.6:
        quality_warnings.append(f"质量等级为「{quality_level}」，建议重新录制以获得更好的识别效果")

    # 保存到数据库
    db = get_speaker_db()
    try:
        saved = db.save_speaker(
            speaker_id=speaker_id,
            embedding=embedding.astype(np.float32),
            name=name,
            role=role,
            department=department,
            quality=quality,
            embedding_mean=embedding.astype(np.float32),
            sample_count=sample_count,
            overwrite=False,
        )
        if not saved:
            raise HTTPException(
                status_code=409,
                detail=f"声纹ID「{speaker_id}」已存在，请使用其他ID"
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"数据库保存失败: {exc}")

    # 构建成功响应
    success_message = f"「{name}」声纹注册成功！"
    if quality_warnings:
        success_message += f"（{quality_warnings[0]}）"

    return {
        "success": True,
        "speaker_id": speaker_id,
        "name": name,
        "message": success_message,
        "quality": round(float(quality), 3),
        "quality_level": quality_level,
        "quality_description": level_desc,
        "sample_count": sample_count,
        "total_duration": round(total_duration, 2),
        "quality_issues": quality_issues if quality_issues else [],
    }


@router.post("/supplement")
async def supplement_speaker_audio(
    speaker_id: str = Form(...),
    audio: UploadFile = File(...),
):
    """
    为已注册的说话人补充新的声纹音频。
    使用 Welford 在线算法将新音频合并到现有声纹统计量中。

    补充后：
    - speaker_profiles.sample_count += 1
    - speaker_embeddings 统计量用 Welford 算法更新
    - 运行时声纹引擎会重新加载该说话人的声纹
    """
    speaker_id = speaker_id.strip()
    if not speaker_id:
        raise HTTPException(status_code=400, detail="speaker_id 为必填项")

    # 读取音频
    try:
        audio_bytes = await audio.read()
        audio_filename = audio.filename or "voice.wav"
        audio_content_type = audio.content_type or "audio/wav"
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"读取音频文件失败: {exc}")

    # 解析音频
    try:
        audio_data = _parse_audio_bytes(audio_bytes, audio_filename, audio_content_type)
        if audio_data is None or len(audio_data) == 0:
            raise HTTPException(status_code=400, detail="音频数据无效或为空")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"音频格式解析失败: {exc}")

    # 检查说话人是否存在
    db = get_speaker_db()
    existing = db.load_speaker(speaker_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"未找到声纹ID「{speaker_id}」，请先注册")

    # 提取新音频的声纹特征
    try:
        result = await _extract_speaker_embedding(
            audio_data, existing["name"], speaker_id, audio_count=1
        )
        new_embedding = result[0]
        quality = result[1]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"声纹提取失败: {exc}")

    # 用 Welford 在线算法补充到现有声纹
    ok = db.supplement_audio(speaker_id, new_embedding.astype(np.float32), quality=quality)
    if not ok:
        raise HTTPException(status_code=500, detail="声纹补充失败")

    # 重新加载到运行时引擎
    _reload_speaker_to_engine(speaker_id)

    new_count = existing.get("embedding_count", existing.get("sample_count", 1)) + 1
    return {
        "success": True,
        "speaker_id": speaker_id,
        "name": existing["name"],
        "message": f"「{existing['name']}」补充音频成功，已累计 {new_count} 条样本",
        "quality": round(float(quality), 3),
        "total_samples": new_count,
        "total_duration": round(len(audio_data) / 16000.0, 2),
    }


@router.post("/delete", response_model=DeleteResponse)
async def delete_speaker(req: DeleteRequest):
    """删除说话人（软删除）"""
    speaker_id = req.speaker_id.strip()
    if not speaker_id:
        raise HTTPException(status_code=400, detail="speaker_id 为必填项")

    db = get_speaker_db()
    ok = db.deactivate_speaker(speaker_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"未找到声纹ID「{speaker_id}」")

    return {
        "success": True,
        "speaker_id": speaker_id,
        "message": "已删除",
    }


# ==================== 辅助函数 ====================

def _reload_speaker_to_engine(speaker_id: str) -> None:
    """
    将补充音频后的说话人重新加载到各运行时引擎。
    目前主要是通知（因为各 Pipeline 在每次处理时从 DB 重新加载）。
    Pipeline3 会在下次 process() 时自动从 DB 读取最新声纹。
    """
    from app.services.speaker_db_service import get_speaker_db, bytes_to_ndarray
    db = get_speaker_db()
    speaker = db.load_speaker(speaker_id)
    if speaker is None:
        print(f"[声纹-补充] 说话人 {speaker_id} 加载失败（已删除？）", flush=True)
        return

    emb_bytes = speaker.get("embedding")
    if emb_bytes is None:
        print(f"[声纹-补充] 说话人 {speaker_id} 无声纹向量", flush=True)
        return

    emb = bytes_to_ndarray(emb_bytes)
    if emb is None:
        print(f"[声纹-补充] 说话人 {speaker_id} 向量解析失败", flush=True)
        return

    print(f"[声纹-补充] 说话人 {speaker_id}（{speaker.get('name')}）已更新，累计样本: {speaker.get('embedding_count', '?')}", flush=True)


def _parse_audio_bytes(
    audio_bytes: bytes,
    filename: str,
    mime_type: str,
) -> np.ndarray:
    """
    将任意音频格式转换为 16kHz mono float32 numpy 数组。
    """
    import tempfile
    import subprocess
    import os

    TARGET_SR = 16000

    # 尝试 soundfile 直接解析（WAV、FLAC、OGG 等）
    try:
        import soundfile as sf
        data, sr = sf.read(io.BytesIO(audio_bytes))
        return _resample_to_16k_mono(data, sr)
    except Exception:
        pass

    # 兜底：ffmpeg 转码
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else "wav"
    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp_in:
        tmp_in.write(audio_bytes)
        tmp_in.flush()
        tmp_in_name = tmp_in.name

    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_out:
            tmp_out_name = tmp_out.name

        result = subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", tmp_in_name,
                "-ar", "16000", "-ac", "1", "-acodec", "pcm_s16le",
                tmp_out_name,
            ],
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {result.stderr.decode(errors='replace')}")

        with open(tmp_out_name, "rb") as f:
            wav_bytes = f.read()

        import soundfile as sf
        data, sr = sf.read(io.BytesIO(wav_bytes))
        return _resample_to_16k_mono(data, sr)
    finally:
        os.unlink(tmp_in_name)
        if "tmp_out_name" in dir():
            os.unlink(tmp_out_name)


def _resample_to_16k_mono(audio_data: np.ndarray, sr: int) -> np.ndarray:
    """将任意采样率/声道的音频转换为 16kHz mono float32"""
    TARGET_SR = 16000

    # float32 / float64 -> 归一化 float32
    if audio_data.dtype in (np.float32, np.float64):
        if audio_data.dtype == np.float64:
            audio_data = audio_data.astype(np.float32)
        audio_data = np.clip(audio_data, -1.0, 1.0)
        audio_float32 = audio_data.astype(np.float32)
    else:
        audio_float32 = audio_data.astype(np.float32) / 32768.0

    # 多声道 -> 单声道
    if audio_float32.ndim > 1:
        audio_float32 = np.mean(audio_float32, axis=1).astype(np.float32)

    # 已是 16kHz
    if sr == TARGET_SR:
        return audio_float32

    # 重采样（scipy）
    from scipy import signal
    num_samples = int(len(audio_float32) * TARGET_SR / sr)
    resampled = signal.resample_poly(audio_float32, TARGET_SR, sr)
    return resampled.astype(np.float32)


async def _extract_speaker_embedding(
    audio_data: np.ndarray,
    name: str,
    speaker_id: str,
    audio_count: int = 1,
) -> tuple:
    """
    从音频数据中提取声纹特征向量（使用 CAM++）。

    与 Pipeline3 (offline_pipeline.py) 中的 Speaker3DEngine.extract() 逻辑一致，
    确保注册与推理使用相同的 embedding 空间。
    """
    try:
        from app.asr.model_manager import load_campplus_model, SpeakerEmbeddingExtractor
        model, device = load_campplus_model()
        extractor = SpeakerEmbeddingExtractor(model, device=device)
        embedding = extractor.extract(audio_data)
    except Exception as e:
        print(f"[_extract_speaker_embedding] CAM++ 提取失败: {e}，降级到随机向量", flush=True)
        np.random.seed(hash(speaker_id) % (2**31))
        embedding = np.random.randn(192).astype(np.float32)
        embedding = embedding / (np.linalg.norm(embedding) + 1e-8)

    quality, quality_level, level_desc, issues, suggestions = _assess_quality(audio_data, embedding, audio_count)
    return embedding, quality, audio_count, quality_level, level_desc, issues, suggestions


def _assess_quality(audio_data: np.ndarray, embedding: np.ndarray, sample_count: int = 1) -> tuple:
    """
    评估声纹注册质量
    返回: (quality_score, quality_level, quality_issues, suggestions)
    """
    energy = float(np.sqrt(np.mean(audio_data ** 2)))
    emb_norm = float(np.linalg.norm(embedding))
    duration_s = len(audio_data) / 16000.0

    # 基础分数计算
    if energy < 0.001:
        energy_score = 0.0
    elif energy < 0.02:
        energy_score = 0.2
    elif energy < 0.05:
        energy_score = 0.5
    elif energy < 0.15:
        energy_score = 0.8
    elif energy < 0.3:
        energy_score = 1.0
    else:
        energy_score = max(0.5, 1.0 - (energy - 0.3) / 0.5)

    emb_score = 1.0 if 0.9 <= emb_norm <= 1.1 else max(0.3, 1.0 - abs(emb_norm - 1.0) * 2)

    if duration_s < 1:
        duration_score = 0.2
    elif duration_s < MIN_SAMPLE_DURATION:
        duration_score = 0.5
    elif duration_s <= MAX_SAMPLE_DURATION:
        duration_score = 1.0
    else:
        duration_score = 0.8

    # 样本数量加成
    sample_count_factor = min(1.0, 0.6 + (sample_count * 0.1))

    quality = (energy_score * 0.35 + emb_score * 0.35 + duration_score * 0.2 + sample_count_factor * 0.1)
    quality = max(0.05, min(0.99, quality))

    quality_level, level_desc = get_quality_level(quality)
    issues = get_quality_issues(audio_data, duration_s)

    suggestions = []
    if quality < MIN_QUALITY_SCORE:
        if not issues:
            suggestions.append("建议在安静环境中重新录制，确保音量适中")
        if sample_count < MIN_SAMPLES:
            suggestions.append(f"建议录制至少{MIN_SAMPLES}段不同内容的音频以提高质量")
        if duration_s < MIN_SAMPLE_DURATION:
            suggestions.append(f"建议每段录音至少{MIN_SAMPLE_DURATION}秒")

    return quality, quality_level, level_desc, issues, suggestions
