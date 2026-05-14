"""
ASR 模型服务接口规范
==================
算法团队需实现此文件中的所有函数。
main.py 中的 FastAPI 路由会调用这些函数。
请勿修改函数签名。

接入说明：
- 输入音频格式：PCM 16kHz 16bit mono
- 输出格式：dict，包含 text/start/end/speaker_id/confidence 字段
- 模型文件放在 models/ 目录下
- 依赖添加到 requirements.txt
"""

from typing import AsyncGenerator


async def transcribe_stream(audio_chunk: bytes) -> AsyncGenerator[dict, None]:
    """
    流式转写。

    输入：PCM 音频 chunk（16kHz, 16bit, mono）
    输出：yield {"text": str, "start": float, "end": float, "speaker_id": str, "confidence": float}

    TODO: 算法团队实现此接口
    """
    # STUB 实现 - 返回 mock 数据
    mock_segments = [
        {"text": "大家好", "start": 0.0, "end": 1.0, "speaker_id": "Speaker_1", "confidence": 0.95},
        {"text": "今天我们讨论一下", "start": 1.0, "end": 2.5, "speaker_id": "Speaker_1", "confidence": 0.93},
        {"text": "好的我先汇报", "start": 2.5, "end": 4.0, "speaker_id": "Speaker_2", "confidence": 0.91},
    ]
    for seg in mock_segments:
        yield seg


async def transcribe_file(audio_path: str) -> list[dict]:
    """
    文件转写。

    输入：音频文件路径（WAV 格式，16kHz mono）
    输出：转写片段列表，格式如下：
    [
        {
            "text": "转写文本",
            "start": 0.0,
            "end": 3.5,
            "speaker_id": "Speaker_1",
            "confidence": 0.95
        }
    ]

    TODO: 算法团队实现此接口
    """
    # STUB 实现 - 返回 mock 数据
    return [
        {"text": "大家好，今天我们讨论一下Q2的产品规划。", "start": 0.0, "end": 3.5, "speaker_id": "Speaker_1", "confidence": 0.95},
        {"text": "好的，我先汇报一下上个季度的完成情况。", "start": 3.5, "end": 7.0, "speaker_id": "Speaker_2", "confidence": 0.92},
        {"text": "请说，我们都在听。", "start": 7.0, "end": 8.5, "speaker_id": "Speaker_1", "confidence": 0.97},
        {"text": "上季度我们完成了三个核心模块的开发，用户反馈整体不错。", "start": 8.5, "end": 13.0, "speaker_id": "Speaker_2", "confidence": 0.93},
        {"text": "我想补充一下，性能优化方面还有提升空间。", "start": 13.0, "end": 16.5, "speaker_id": "Speaker_3", "confidence": 0.91},
    ]
