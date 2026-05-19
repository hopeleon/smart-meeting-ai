"""
音频去噪模块 — 对齐 D:\InsightEye\app\audio_denoiser.py
在音频送入 ASR 前进行预处理，提高识别准确率

主要使用 RNNoise（实时专用，轻量高效）

用法:
    denoiser = AudioDenoiser(backend="rnnoise")
    clean_audio = denoiser.denoise(audio, sample_rate=16000)
"""

from __future__ import annotations

import numpy as np
from typing import Optional, Literal

# 全局单例（延迟加载）
_denoiser_instance: Optional["AudioDenoiser"] = None


class RNNoiseDenoiser:
    """
    RNNoise 风格实时降噪器
    纯 Python/NumPy 实现核心算法，无需外部 C 库

    算法：STFT → 噪声估计 → 维纳滤波 → iSTFT
    帧长 512 samples @ 16kHz = 32ms（与 VAD 对齐）
    窗函数：汉宁窗，重叠 50%（256 samples）
    """

    def __init__(self, frame_size: int = 512):
        self.frame_size = frame_size
        self.hop_size = frame_size // 2
        self.window = np.hanning(frame_size).astype(np.float32)

        self._noise_profile: Optional[np.ndarray] = None
        self._noise_frames = 0
        self._noise_frames_max = 200
        self._is_noise_frame = True
        self._frame_count = 0

        self._speech_energy_threshold = 0.03
        self._speech_ratio_threshold = 0.5

        self._alpha_speech = 0.98
        self._alpha_noise = 0.85
        self._min_snr = 0.1
        self._eps = 1e-8

        self._gamma_prev: Optional[np.ndarray] = None

        self._output_buffer = np.zeros(frame_size * 2, dtype=np.float32)
        self._input_buffer = np.zeros(frame_size, dtype=np.float32)

        print(f"[RNNoise] 初始化完成，帧长={frame_size}样本({frame_size/16000*1000:.1f}ms)，重叠={self.hop_size}样本")

    def _is_speech_frame(self, frame: np.ndarray) -> bool:
        energy = np.sqrt(np.mean(frame ** 2))
        if energy < self._speech_energy_threshold:
            return False

        windowed = frame * self.window
        spectrum = np.fft.rfft(windowed)
        mag = np.abs(spectrum)
        total_energy = np.sum(mag ** 2)

        low_freq_energy = np.sum(mag[5:50] ** 2)
        if total_energy > self._eps:
            ratio = low_freq_energy / total_energy
            return ratio > self._speech_ratio_threshold

        return energy > self._speech_energy_threshold * 2

    def _estimate_noise(self, frame: np.ndarray, is_speech: bool) -> np.ndarray:
        windowed = frame * self.window
        spectrum = np.fft.rfft(windowed)
        mag = np.abs(spectrum)

        if self._noise_profile is None:
            self._noise_profile = mag.copy()
            self._noise_frames = 1
            return self._noise_profile

        if is_speech:
            self._noise_profile = self._alpha_noise * self._noise_profile + (1 - self._alpha_noise) * mag
        else:
            beta = 0.8
            self._noise_profile = beta * self._noise_profile + (1 - beta) * mag
            self._noise_frames = min(self._noise_frames + 1, self._noise_frames_max)

        return self._noise_profile

    def _compute_wiener_gain(self, mag: np.ndarray, noise_profile: np.ndarray) -> np.ndarray:
        n_bins = len(mag)
        gamma = np.zeros(n_bins, dtype=np.float32)

        noise_power = noise_profile ** 2 + self._eps
        signal_power = mag ** 2
        with np.errstate(divide='ignore', invalid='ignore'):
            gamma = signal_power / noise_power
            gamma = np.clip(gamma, self._min_snr, 100.0)

        if self._gamma_prev is None:
            self._gamma_prev = np.clip(gamma - 1, self._min_snr, None)

        xi = self._alpha_speech * self._gamma_prev ** 2 + (1 - self._alpha_speech) * np.maximum(gamma - 1, 0)

        with np.errstate(divide='ignore', invalid='ignore'):
            gain = xi / (1.0 + xi)
            gain = np.nan_to_num(gain, nan=0.3, posinf=1.0, neginf=0.0)
            gain = np.clip(gain, 0.05, 1.0)

        self._gamma_prev = gamma.copy()
        return gain.astype(np.float32)

    def denoise_frame(self, frame: np.ndarray) -> np.ndarray:
        if len(frame) != self.frame_size:
            if len(frame) < self.frame_size:
                padded = np.zeros(self.frame_size, dtype=np.float32)
                padded[:len(frame)] = frame
                frame = padded
            else:
                frame = frame[:self.frame_size]

        is_speech = self._is_speech_frame(frame)
        self._estimate_noise(frame, is_speech)

        windowed = frame * self.window
        spectrum = np.fft.rfft(windowed)
        mag = np.abs(spectrum)
        phase = np.angle(spectrum)

        if self._noise_profile is not None:
            gain = self._compute_wiener_gain(mag, self._noise_profile)
            enhanced_mag = mag * gain
        else:
            enhanced_mag = mag

        enhanced_spectrum = enhanced_mag * np.exp(1j * phase)
        enhanced_frame = np.fft.irfft(enhanced_spectrum, n=self.frame_size)

        output = np.zeros(self.frame_size + self.hop_size, dtype=np.float32)
        output[:self.frame_size] += enhanced_frame * self.window
        output[:self.frame_size] += enhanced_frame * self.window

        return output.astype(np.float32)

    def reset(self):
        self._noise_profile = None
        self._noise_frames = 0
        self._gamma_prev = None
        self._frame_count = 0
        print("[RNNoise] 状态已重置")


class AudioDenoiser:
    """
    音频去噪器
    - RNNoise（轻量，实时友好，默认）
    - Demucs（高质量，离线场景）
    """

    def __init__(
        self,
        backend: Literal["rnnoise", "demucs"] = "rnnoise",
        device: str = "cpu",
        model_name: str = "hdemucs.lowercase",
        frame_size: int = 512,
    ):
        self.backend = backend
        self.device = device
        self.model_name = model_name
        self.frame_size = frame_size
        self._model = None
        self._initialized = False

        self._rnnoise: Optional[RNNoiseDenoiser] = None
        self._stream_buffer: np.ndarray = np.array([], dtype=np.float32)

    def _ensure_initialized(self):
        if self._initialized:
            return

        if self.backend == "rnnoise":
            self._init_rnnoise()
        elif self.backend == "demucs":
            self._init_demucs()
        else:
            raise ValueError(f"Unknown backend: {self.backend}")

        self._initialized = True

    def _init_rnnoise(self):
        self._rnnoise = RNNoiseDenoiser(frame_size=self.frame_size)
        self._model = self._rnnoise
        print("[去噪] RNNoise 去噪器已加载（帧长=32ms，实时优化）")

    def _init_demucs(self):
        try:
            from demucs.pretrained import get_model
            self._model = get_model(self.model_name, device=self.device)
            print(f"[去噪] Demucs 模型加载完成，设备: {self.device}")
        except ImportError:
            print("[去噪] demucs 未安装，将使用 RNNoise 备选")
            self._init_rnnoise()

    def denoise(self, audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
        self._ensure_initialized()

        if self._model is None:
            return audio

        if self.backend == "rnnoise":
            return self._denoise_rnnoise(audio, sample_rate)
        elif self.backend == "demucs":
            return self._denoise_demucs(audio, sample_rate)

        return audio

    def _denoise_rnnoise(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        if self._rnnoise is None:
            return audio

        hop_size = self.frame_size // 2
        total_needed = self.frame_size + hop_size

        self._stream_buffer = np.concatenate([self._stream_buffer, audio.astype(np.float32)])

        output_frames = []
        while len(self._stream_buffer) >= total_needed:
            frame = self._stream_buffer[:self.frame_size]
            self._stream_buffer = self._stream_buffer[hop_size:]

            denoised = self._rnnoise.denoise_frame(frame)
            output_frames.append(denoised[:hop_size])

        if output_frames:
            return np.concatenate(output_frames)
        return audio

    def _denoise_demucs(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        try:
            import torch
            from demucs.separate import separate_sources

            audio_tensor = torch.from_numpy(audio).float()
            if audio_tensor.ndim == 1:
                audio_tensor = audio_tensor.unsqueeze(0)

            mix = audio_tensor.unsqueeze(0)

            with torch.no_grad():
                sources = separate_sources(
                    self._model,
                    mix=mix,
                    device=self.device,
                    shifts=0,
                    split=True,
                    progress=False,
                )

            if sources is None or len(sources) == 0:
                print("[去噪] Demucs 分离失败，使用原始音频")
                return audio

            source = sources[0][0, 0, :]
            result = source.numpy()

            max_val = np.abs(result).max()
            if max_val > 0:
                result = result / max_val

            return result.astype(np.float32)

        except Exception as e:
            print(f"[去噪] Demucs 处理失败: {e}")
            return audio

    def is_available(self) -> bool:
        self._ensure_initialized()
        return self._model is not None

    def reset(self):
        if self._rnnoise:
            self._rnnoise.reset()
        self._stream_buffer = np.array([], dtype=np.float32)


def get_denoiser(
    backend: Literal["rnnoise", "demucs"] = "rnnoise",
    device: str = "cpu",
    model_name: str = "hdemucs.lowercase",
    frame_size: int = 512,
) -> AudioDenoiser:
    """获取全局去噪器单例"""
    global _denoiser_instance

    if _denoiser_instance is None:
        _denoiser_instance = AudioDenoiser(
            backend=backend,
            device=device,
            model_name=model_name,
            frame_size=frame_size,
        )

    return _denoiser_instance


def denoise_audio(
    audio: np.ndarray,
    sample_rate: int = 16000,
    backend: Literal["rnnoise", "demucs"] = "rnnoise",
) -> np.ndarray:
    """便捷函数：对音频进行去噪"""
    denoiser = get_denoiser(backend=backend)
    return denoiser.denoise(audio, sample_rate)
