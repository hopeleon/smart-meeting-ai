"""
测试音频生成器
==============
从已注册的声纹数据库中选取说话人，
随机选择音频片段，随机打乱顺序拼接，生成多人会议测试音频 + Ground Truth JSON。

功能：
  1. 从 speaker_voiceprints.db 随机选取 N 个说话人
  2. 从每个说话人的音频中随机选择若干片段
  3. 所有片段随机打乱顺序拼接（模拟真实会议）
  4. 生成 Ground Truth JSON

输出结构：
  backend/data/test_audio/
  └── test_run_20260516_183500/
      ├── test_spliced_audio_20260516_183500.wav   — 拼接音频
      ├── test_ground_truth_20260516_183500.json    — 标准答案
      └── test_registration_20260516_183500.json     — 注册摘要
"""

from __future__ import annotations

import json
import os
import random
import wave
from pathlib import Path
from collections import defaultdict
from datetime import datetime

import numpy as np

# ======================== 配置 ========================

# Primewords 数据集根目录（解压后包含 audio_files/ 和 JSON 文件）
DATASET_ROOT = r"E:\primewords_md_2018_set1\primewords_md_2018_set1"

# 数据集 JSON 文件名
_JSON_FILENAME = "set1_transcript.json"

# 输出根目录（自动检测脚本位置）
def _get_out_base_dir():
    script_dir = Path(__file__).parent.resolve()
    # 脚本可能在 backend/data/ 或 Generate_Test_Audio/ 下
    if script_dir.name == "data" and (script_dir / "speaker_voiceprints.db").exists():
        # 脚本在 backend/data/ 目录
        return script_dir / "test_audio"
    return script_dir.parent / "backend" / "data" / "test_audio"

OUT_BASE_DIR = _get_out_base_dir()

# 测试生成参数
NUM_SPEAKERS = 8          # 随机选取的说话人数量
MIN_SEGMENTS_PER_SPEAKER = 3  # 每个说话人最少片段数
MAX_SEGMENTS_PER_SPEAKER = 10  # 每个说话人最多片段数
TARGET_DURATION_SEC = 180  # 目标总时长（秒）
MIN_SEGMENT_DURATION_SEC = 3  # 最小片段时长（秒）
MAX_SEGMENT_DURATION_SEC = 15  # 最大片段时长（秒）

# 拼接参数
SILENCE_BETWEEN_SEC = 0.5  # 片段之间的静音时长（秒）
OVERLAP_RATIO = 0.0       # 重叠比例（0 = 无重叠）

# 随机种子（保证可复现，None 表示每次使用不同种子）
RANDOM_SEED = None


# ======================== 数据库读取 ========================

def load_speaker_db(db_path: str):
    """从 SQLite 数据库加载说话人信息"""
    import sqlite3
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    # 查询说话人及其样本
    rows = conn.execute("""
        SELECT 
            p.speaker_id,
            p.name,
            p.role,
            p.department,
            p.sample_count,
            p.quality
        FROM speaker_profiles p
        WHERE p.is_active = 1
        ORDER BY p.speaker_id
    """).fetchall()
    
    speakers = []
    for row in rows:
        speakers.append({
            "speaker_id": row["speaker_id"],
            "name": row["name"] or row["speaker_id"],
            "role": row["role"],
            "department": row["department"],
            "sample_count": row["sample_count"],
            "quality": row["quality"],
        })
    
    conn.close()
    return speakers


def load_dataset_json():
    """加载 Primewords 数据集的转录 JSON
    
    Primewords 数据集结构:
    - JSON: {"id": "64869", "file": "xxx.wav", "user_id": "1013", "text": "...", "length": "8.64"}
    - 音频: audio_files/{user_id[0]}/{user_id[1:]}/{uuid}.wav
    """
    json_path = Path(DATASET_ROOT) / _JSON_FILENAME
    if not json_path.exists():
        raise FileNotFoundError(f"数据集 JSON 未找到: {json_path}")
    
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # 构建音频文件映射: {user_id}_{uuid} -> info
    audio_to_text = {}
    audio_files_dir = Path(DATASET_ROOT) / "audio_files"
    
    for item in data:
        audio_file = item.get("file", "")
        if not audio_file:
            continue
        
        user_id = str(item.get("user_id", ""))
        if not user_id:
            continue
        
        # 解析 UUID（去掉 .wav）
        uuid = Path(audio_file).stem
        
        # 音频目录结构: audio_files/{uuid[0]}/{uuid[:2]}/{uuid}.wav
        # 例如: audio_files/d/d6/d6b26896-f2ef-4085-950d-47cd24f21d8f.wav
        if len(uuid) >= 2:
            audio_dir = audio_files_dir / uuid[0] / uuid[:2]
        else:
            audio_dir = audio_files_dir / uuid[0]
        
        full_path = audio_dir / audio_file
        
        if full_path.exists():
            # key 格式: {user_id}_{uuid}
            key = f"{user_id}_{uuid}"
            audio_to_text[key] = {
                "text": item.get("text", ""),
                "audio_path": str(full_path),
                "audio_file": audio_file,
                "user_id": user_id,
            }
    
    return audio_to_text


def find_audio_for_speaker(speaker_id: str, audio_db: dict) -> list:
    """
    根据说话人 ID 查找对应的音频片段
    
    Primewords 数据集: audio_files/{uuid[0]}/{uuid[:3]}/{uuid}.wav
    audio_db 的 key 格式: {user_id}_{uuid}
    """
    matches = []
    
    # 遍历 audio_db 找到所有属于该说话人的音频
    for key, info in audio_db.items():
        # key 格式: {user_id}_{uuid}
        if key.startswith(f"{speaker_id}_"):
            matches.append({
                "speaker_id": speaker_id,
                "audio_file": info.get("audio_file", ""),
                "audio_path": info.get("audio_path", ""),
                "text": info.get("text", ""),
            })
    
    return matches


def load_audio_segment(audio_path: str, min_duration_sec: float = 3.0, 
                       max_duration_sec: float = 15.0) -> tuple:
    """
    加载音频片段
    
    Returns:
        (audio_data: np.ndarray, sample_rate: int, duration_sec: float, text: str)
    """
    import soundfile as sf
    
    audio_data, sample_rate = sf.read(audio_path, dtype="float32")
    
    # 如果是立体声，转为单声道
    if len(audio_data.shape) > 1:
        audio_data = np.mean(audio_data, axis=1)
    
    # 计算时长
    duration_sec = len(audio_data) / sample_rate
    
    # 如果音频太短，跳过
    if duration_sec < min_duration_sec:
        return None
    
    # 如果音频太长，随机截取一段
    if duration_sec > max_duration_sec:
        max_start = len(audio_data) - int(max_duration_sec * sample_rate)
        if max_start > 0:
            start_idx = random.randint(0, max_start)
            end_idx = start_idx + int(max_duration_sec * sample_rate)
            audio_data = audio_data[start_idx:end_idx]
            duration_sec = len(audio_data) / sample_rate
    
    return audio_data, sample_rate, duration_sec


# ======================== 音频拼接 ========================

def create_silence(duration_sec: float, sample_rate: int) -> np.ndarray:
    """创建静音片段"""
    num_samples = int(duration_sec * sample_rate)
    return np.zeros(num_samples, dtype=np.float32)


def splice_audio(segments: list, silence_between: float, sample_rate: int) -> np.ndarray:
    """
    拼接音频片段
    
    Args:
        segments: 音频数据列表
        silence_between: 片段之间的静音时长（秒）
        sample_rate: 采样率
    
    Returns:
        拼接后的音频数据
    """
    silence = create_silence(silence_between, sample_rate)
    result = []
    
    for i, audio in enumerate(segments):
        if i > 0:
            result.append(silence)
        result.append(audio)
    
    return np.concatenate(result)


def save_audio(audio_data: np.ndarray, sample_rate: int, output_path: str):
    """保存音频为 WAV 文件"""
    import soundfile as sf
    sf.write(output_path, audio_data, sample_rate)


# ======================== 主流程 ========================

def generate_test_audio(
    db_path: str,
    num_speakers: int = NUM_SPEAKERS,
    min_segments: int = MIN_SEGMENTS_PER_SPEAKER,
    max_segments: int = MAX_SEGMENTS_PER_SPEAKER,
    target_duration: float = TARGET_DURATION_SEC,
    silence_between: float = SILENCE_BETWEEN_SEC,
    seed: int = None,
):
    """
    生成测试音频的主函数
    
    Args:
        db_path: 声纹数据库路径
        num_speakers: 随机选取的说话人数量
        min_segments: 每个说话人最少片段数
        max_segments: 每个说话人最多片段数
        target_duration: 目标总时长（秒）
        silence_between: 片段之间的静音时长（秒）
        seed: 随机种子（None 表示使用当前时间戳）
    
    Returns:
        dict: 包含生成结果的元信息
    """
    # 生成时间戳
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    
    # 如果没有指定种子，使用时间戳的后6位
    if seed is None:
        seed = int(now.strftime("%H%M%S"))
    
    random.seed(seed)
    np.random.seed(seed)
    
    print("=" * 60)
    print("测试音频生成器")
    print(f"时间戳: {timestamp}")
    print(f"随机种子: {seed}")
    print("=" * 60)
    
    # Step 1: 加载声纹数据库
    print(f"\n[1/6] 加载声纹数据库: {db_path}")
    speakers = load_speaker_db(db_path)
    print(f"      数据库中共有 {len(speakers)} 位说话人")
    
    if len(speakers) < num_speakers:
        raise ValueError(f"数据库中说话人数量 ({len(speakers)}) 少于所需数量 ({num_speakers})")
    
    # Step 2: 加载数据集，找出有音频的说话人
    print(f"\n[2/6] 加载 Primewords 数据集...")
    audio_db = load_dataset_json()
    print(f"      找到 {len(audio_db)} 个音频文件")
    
    # 找出有音频的说话人
    speakers_with_audio = set()
    for key, info in audio_db.items():
        if "_" in key:
            user_id = key.split("_")[0]
            speakers_with_audio.add(user_id)
    
    print(f"      有音频的说话人数量: {len(speakers_with_audio)}")
    
    # 过滤出同时在数据库和有音频列表中的说话人
    available_speakers = [sp for sp in speakers if sp["speaker_id"] in speakers_with_audio]
    print(f"      数据库中同时有音频的说话人: {len(available_speakers)}")
    
    if len(available_speakers) < num_speakers:
        raise ValueError(
            f"有音频的说话人数量 ({len(available_speakers)}) 少于所需数量 ({num_speakers})"
        )
    
    # Step 3: 从有音频的说话人中随机选取
    print(f"\n[3/6] 随机选取 {num_speakers} 位说话人...")
    selected_speakers = random.sample(available_speakers, num_speakers)
    for sp in selected_speakers:
        print(f"      - {sp['speaker_id']}: {sp['name']} (role={sp['role']}, dept={sp['department']})")
    
    # Step 4: 为每个说话人选择音频片段
    speaker_segments = {}  # speaker_id -> list of segment info
    
    for sp in selected_speakers:
        speaker_id = sp["speaker_id"]
        
        # 查找该说话人的音频
        audio_list = find_audio_for_speaker(speaker_id, audio_db)
        
        if not audio_list:
            print(f"      警告: 说话人 {speaker_id} 没有找到对应的音频文件")
            continue
        
        # 随机选择片段数量
        num_to_select = random.randint(min_segments, min(max_segments, len(audio_list)))
        selected = random.sample(audio_list, num_to_select)
        
        segments = []
        for audio_info in selected:
            # 加载音频并截取
            result = load_audio_segment(
                audio_info["audio_path"],
                min_duration_sec=MIN_SEGMENT_DURATION_SEC,
                max_duration_sec=MAX_SEGMENT_DURATION_SEC
            )
            
            if result is not None:
                audio_data, sample_rate, duration = result
                segments.append({
                    "audio_data": audio_data,
                    "sample_rate": sample_rate,
                    "duration_sec": duration,
                    "text": audio_info["text"],
                    "audio_file": audio_info["audio_file"],
                    "speaker_id": speaker_id,
                    "speaker_name": sp["name"],
                })
        
        if segments:
            speaker_segments[speaker_id] = segments
            print(f"      {sp['name']}: 选择了 {len(segments)} 个片段")
    
    # Step 5: 随机打乱并拼接
    print(f"\n[5/6] 随机打乱并拼接音频...")
    
    # 收集所有片段
    all_segments = []
    for sid, segs in speaker_segments.items():
        all_segments.extend(segs)
    
    print(f"      总共 {len(all_segments)} 个片段")
    
    # 随机打乱顺序
    random.shuffle(all_segments)
    
    # 拼接音频
    sample_rate = all_segments[0]["sample_rate"] if all_segments else 16000
    audio_data_list = [seg["audio_data"] for seg in all_segments]
    spliced_audio = splice_audio(audio_data_list, silence_between, sample_rate)
    
    print(f"      拼接后总时长: {len(spliced_audio) / sample_rate:.2f} 秒")
    
    # Step 6: 生成输出文件
    print(f"\n[6/6] 生成输出文件...")
    
    # 创建带时间戳的输出目录
    run_dir = OUT_BASE_DIR / f"test_run_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    
    # 生成带时间戳的文件名前缀
    file_prefix = f"test_run_{timestamp}"
    
    # 保存拼接音频
    audio_path = run_dir / f"{file_prefix}_audio.wav"
    save_audio(spliced_audio, sample_rate, str(audio_path))
    print(f"      保存音频: {audio_path}")
    
    # 计算时间戳并生成 Ground Truth
    current_time_ms = 0
    segments_with_timing = []
    
    for i, seg in enumerate(all_segments):
        duration_ms = int(seg["duration_sec"] * 1000)
        start_ms = current_time_ms
        end_ms = start_ms + duration_ms
        
        segments_with_timing.append({
            "id": i + 1,
            "speaker_id": seg["speaker_id"],
            "speaker_name": seg["speaker_name"],
            "start_ms": start_ms,
            "end_ms": end_ms,
            "duration_ms": duration_ms,
            "text": seg["text"],
            "audio_file": seg["audio_file"],
        })
        
        current_time_ms = end_ms + int(silence_between * 1000)
    
    # 生成 Ground Truth JSON
    ground_truth = {
        "_meta": {
            "total_duration_sec": len(spliced_audio) / sample_rate,
            "num_speakers": len(speaker_segments),
            "num_segments": len(all_segments),
            "target_duration_sec": target_duration,
            "generated_at": now.isoformat(),
            "timestamp": timestamp,
            "splicing_method": "random_shuffle",
            "random_seed": seed,
            "speakers": [
                {
                    "speaker_id": sp["speaker_id"],
                    "speaker_name": sp["name"],
                    "num_segments": len(speaker_segments.get(sp["speaker_id"], []))
                }
                for sp in selected_speakers
                if sp["speaker_id"] in speaker_segments
            ]
        },
        "segments": segments_with_timing
    }
    
    ground_truth_path = run_dir / f"{file_prefix}_ground_truth.json"
    with open(ground_truth_path, "w", encoding="utf-8") as f:
        json.dump(ground_truth, f, ensure_ascii=False, indent=2)
    print(f"      保存 Ground Truth: {ground_truth_path}")
    
    # 生成注册摘要 JSON
    registration = {
        "generated_at": now.isoformat(),
        "timestamp": timestamp,
        "num_speakers": len(speaker_segments),
        "random_seed": seed,
        "speakers": [
            {
                "speaker_id": sp["speaker_id"],
                "name": sp["name"],
                "num_segments": len(speaker_segments.get(sp["speaker_id"], []))
            }
            for sp in selected_speakers
            if sp["speaker_id"] in speaker_segments
        ]
    }
    
    registration_path = run_dir / f"{file_prefix}_registration.json"
    with open(registration_path, "w", encoding="utf-8") as f:
        json.dump(registration, f, ensure_ascii=False, indent=2)
    print(f"      保存注册摘要: {registration_path}")
    
    print("\n" + "=" * 60)
    print("生成完成!")
    print("=" * 60)
    print(f"  输出目录: {run_dir}")
    print(f"  说话人数: {len(speaker_segments)}")
    print(f"  片段总数: {len(all_segments)}")
    print(f"  总时长: {len(spliced_audio) / sample_rate:.2f} 秒")
    print(f"  音频文件: {audio_path.name}")
    print(f"  Ground Truth: {ground_truth_path.name}")
    print(f"  注册摘要: {registration_path.name}")
    
    return {
        "run_dir": str(run_dir),
        "timestamp": timestamp,
        "seed": seed,
        "audio_path": str(audio_path),
        "ground_truth_path": str(ground_truth_path),
        "registration_path": str(registration_path),
        **ground_truth
    }


# ======================== 入口 ========================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="生成多人会议测试音频")
    parser.add_argument("--db", type=str, default=None,
                        help="声纹数据库路径（默认从环境变量或默认位置获取）")
    parser.add_argument("--num-speakers", type=int, default=NUM_SPEAKERS,
                        help=f"随机选取的说话人数量（默认: {NUM_SPEAKERS}）")
    parser.add_argument("--seed", type=int, default=None,
                        help="随机种子（默认: 使用当前时间）")
    
    args = parser.parse_args()
    
    # 确定数据库路径
    if args.db:
        db_path = args.db
    else:
        # 尝试从默认位置获取
        # 脚本位于 backend/data/generate_test_audio.py 或 Generate_Test_Audio/
        script_dir = Path(__file__).parent.resolve()
        
        # 优先查找 backend/data/speaker_voiceprints.db
        possible_paths = [
            script_dir / "speaker_voiceprints.db",  # backend/data/
            script_dir.parent / "backend" / "data" / "speaker_voiceprints.db",  # Generate_Test_Audio/
        ]
        
        default_db = None
        for p in possible_paths:
            if p.exists():
                default_db = p
                break
        
        if default_db is None:
            # 最后尝试标准的 backend/data/ 路径（从项目根目录）
            project_root = script_dir.parent.parent  # 假设脚本在 backend/data/ 下
            standard_path = project_root / "backend" / "data" / "speaker_voiceprints.db"
            if standard_path.exists():
                default_db = standard_path
        
        if default_db and default_db.exists():
            db_path = str(default_db)
        else:
            raise FileNotFoundError(
                f"声纹数据库未找到，请指定 --db 参数。\n"
                f"已搜索路径:\n" + 
                "\n".join(f"  - {p}" for p in possible_paths) +
                f"\n  - {standard_path if 'standard_path' in dir() else 'N/A'}"
            )
    
    result = generate_test_audio(
        db_path=db_path,
        num_speakers=args.num_speakers,
        seed=args.seed,
    )
    
    print(f"\n提示: 生成的测试数据位于: {result['run_dir']}")
