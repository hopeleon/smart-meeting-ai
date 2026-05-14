"""
说话人分离服务接口规范
===================
算法团队需实现此文件中的所有函数。
main.py 中的 FastAPI 路由会调用这些函数。
请勿修改函数签名。

接入说明：
- 输入音频格式：PCM 16kHz 16bit mono
- 输出格式：dict，包含 segments 和 num_speakers_detected
- 模型文件放在 models/ 目录下
- 依赖添加到 requirements.txt
"""


async def diarize(audio_path: str, num_speakers: int | None = None) -> dict:
    """
    说话人分离。

    输入：
        audio_path: 音频文件路径（WAV 格式，16kHz mono）
        num_speakers: 预期说话人数量（可选，None 表示自动检测）

    输出：
        {
            "segments": [
                {"speaker_id": "Speaker_1", "start": 0.0, "end": 5.0},
                {"speaker_id": "Speaker_2", "start": 5.0, "end": 10.0}
            ],
            "num_speakers_detected": 2
        }

    TODO: 算法团队实现此接口
    """
    # STUB 实现 - 返回 mock 数据
    return {
        "segments": [
            {"speaker_id": "Speaker_1", "start": 0.0, "end": 5.0},
            {"speaker_id": "Speaker_2", "start": 5.0, "end": 10.0},
            {"speaker_id": "Speaker_1", "start": 10.0, "end": 15.0},
            {"speaker_id": "Speaker_3", "start": 15.0, "end": 20.0},
            {"speaker_id": "Speaker_2", "start": 20.0, "end": 25.0},
        ],
        "num_speakers_detected": 3,
    }
