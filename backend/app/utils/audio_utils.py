"""
音频格式转换工具
===============
提供音频格式检测、转换等功能。
"""

import os
import subprocess


def get_audio_info(filepath: str) -> dict:
    """
    获取音频文件信息。

    TODO: 接入 ffprobe 获取详细信息
    当前返回基本信息。
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Audio file not found: {filepath}")

    size_bytes = os.path.getsize(filepath)
    ext = os.path.splitext(filepath)[1].lower()

    return {
        "filepath": filepath,
        "size_bytes": size_bytes,
        "format": ext.lstrip("."),
        "duration": None,  # TODO: 使用 ffprobe 获取
    }


def convert_to_wav(input_path: str, output_path: str, sample_rate: int = 16000) -> str:
    """
    将音频转换为 WAV 格式（16kHz, 16bit, mono）。

    TODO: 算法团队可能需要此功能，当前为 stub。
    """
    # STUB: 实际需要 ffmpeg
    try:
        cmd = [
            "ffmpeg", "-i", input_path,
            "-ar", str(sample_rate),
            "-ac", "1",
            "-sample_fmt", "s16",
            "-y",
            output_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return output_path
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found. Please install ffmpeg.")
