"""PCM extraction and waveform analysis via ffmpeg + soundfile.

Any input media file (audio or video, any container/codec ffmpeg can read)
is decoded to a cached mono WAV under the config cache dir, then loaded into
a numpy float32 array for analysis. Nothing here talks to librosa -- that
lives in beats.py, which calls back into `extract_pcm`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from kdenlive_mcp.config import get_config
from kdenlive_mcp.errors import UnsupportedMediaError
from kdenlive_mcp.media.ffmpeg.runner import run
from kdenlive_mcp.media.ffprobe.probe import probe_media


def _cache_key(path: Path, sample_rate: int) -> str:
    mtime_ns = path.stat().st_mtime_ns
    h = hashlib.sha1(f"{path.resolve()}|{sample_rate}|{mtime_ns}".encode()).hexdigest()
    return h[:24]


def _wav_cache_path(path: Path, sample_rate: int) -> Path:
    cfg = get_config()
    audio_dir = cfg.cache_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    return audio_dir / f"{_cache_key(path, sample_rate)}.wav"


def extract_pcm(path: Path, *, sample_rate: int = 22050) -> tuple[np.ndarray, int]:
    """Decode any input audio/video file to mono PCM at `sample_rate`.

    Caches the intermediate WAV under cfg.cache_dir / "audio", keyed by
    (resolved path, sample_rate, mtime) so repeated analysis calls on the
    same file don't re-invoke ffmpeg. Returns (samples, sample_rate) where
    samples is a 1-D float32 numpy array.
    """
    path = Path(path)
    if not path.exists():
        raise UnsupportedMediaError(
            f"File does not exist: {path}",
            suggestion="Check the path and try again.",
        )

    meta = probe_media(path)
    if not meta.audio_streams:
        raise UnsupportedMediaError(
            f"File has no audio stream: {path}",
            suggestion="Beat/waveform analysis requires an audio stream; pick a different source file.",
        )

    wav_path = _wav_cache_path(path, sample_rate)
    if not wav_path.exists():
        run("ffmpeg", [
            "-y",
            "-i", str(path),
            "-vn",
            "-ac", "1",
            "-ar", str(sample_rate),
            "-f", "wav",
            str(wav_path),
        ])

    samples, sr = sf.read(str(wav_path), dtype="float32", always_2d=False)
    if samples.ndim > 1:
        samples = samples.mean(axis=1).astype(np.float32)
    return samples, sr


def analyze_waveform(path: Path) -> dict[str, Any]:
    """RMS envelope, peak level, clipping detection and a simple loudness estimate.

    Returns a plain JSON-serializable dict (no numpy scalar/array types).
    """
    samples, sr = extract_pcm(path)
    if samples.size == 0:
        return {
            "duration": 0.0,
            "sample_rate": int(sr),
            "rms_envelope": [],
            "rms_timestamps": [],
            "peak_level": 0.0,
            "clipping": {"detected": False, "count": 0},
            "loudness_rms_db": None,
        }

    hop_seconds = 0.1  # 100ms hops
    hop_samples = max(1, int(round(hop_seconds * sr)))

    envelope: list[float] = []
    timestamps: list[float] = []
    for start in range(0, samples.size, hop_samples):
        window = samples[start:start + hop_samples]
        if window.size == 0:
            continue
        rms = float(np.sqrt(np.mean(np.square(window, dtype=np.float64))))
        envelope.append(rms)
        timestamps.append(start / sr)

    peak_level = float(np.max(np.abs(samples)))

    clip_threshold = 0.999
    clipped_mask = np.abs(samples) >= clip_threshold
    clip_count = int(np.sum(clipped_mask))
    clipping_detected = clip_count > 0

    overall_rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    loudness_rms_db = float(20.0 * np.log10(overall_rms)) if overall_rms > 0 else None

    return {
        "duration": float(samples.size / sr),
        "sample_rate": int(sr),
        "rms_envelope": envelope,
        "rms_timestamps": timestamps,
        "peak_level": peak_level,
        "clipping": {"detected": clipping_detected, "count": clip_count},
        "loudness_rms_db": loudness_rms_db,
    }
