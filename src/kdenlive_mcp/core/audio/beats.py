"""Beat-sync engine core: BPM/beat/downbeat/section/silence detection.

Everything here works in *seconds*, never in librosa's internal analysis-frame
unit or in this codebase's timeline (fps-based) frame unit -- callers convert
seconds to timeline frames themselves via
`kdenlive_mcp.core.timeline.timecode.seconds_to_frames`. This keeps "audio
analysis frames" (an implementation detail of librosa's STFT hop size) fully
out of any public return value here, so nothing downstream can accidentally
treat one frame unit as the other.

All public functions accept an optional `asset: MediaAsset | None = None`.
When given and the relevant key is already present in `asset.analysis`, the
cached value is returned as-is without recomputing. When computed fresh and
an asset was given, the result is written back into `asset.analysis[key]` in
memory only -- callers are responsible for persisting it via
`MediaIndex.upsert` if they want it to survive.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import librosa
import numpy as np

from kdenlive_mcp.core.assets.model import MediaAsset
from kdenlive_mcp.core.audio.waveform import extract_pcm
from kdenlive_mcp.errors import UnsupportedMediaError
from kdenlive_mcp.media.ffmpeg.runner import run
from kdenlive_mcp.media.ffprobe.probe import probe_media


def _cache_get(asset: MediaAsset | None, key: str) -> Any:
    if asset is not None and key in asset.analysis:
        return asset.analysis[key]
    return None


def _cache_set(asset: MediaAsset | None, key: str, value: Any) -> Any:
    if asset is not None:
        asset.analysis[key] = value
    return value


# --------------------------------------------------------------- bpm/beats -

def detect_bpm_and_beats(path: Path, asset: MediaAsset | None = None) -> dict[str, Any]:
    """Detect BPM and beat timestamps with `librosa.beat.beat_track`.

    Returns `{"bpm": float, "beat_times": list[float] seconds, "beat_count": int}`.
    Deliberately omits librosa's raw beat-frame indices (see module docstring)
    -- only seconds are returned, so `snap_to_beat`/`add_marker` callers must
    convert via `timecode.seconds_to_frames(t, fps)` before touching the
    timeline model.
    """
    cached = _cache_get(asset, "beats")
    if cached is not None:
        return cached

    y, sr = extract_pcm(path)
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)

    # librosa's beat_track sometimes returns tempo as a length-1 ndarray
    # (one estimate per "tempo band") rather than a bare float.
    tempo_value = float(np.asarray(tempo).reshape(-1)[0]) if np.asarray(tempo).size else 0.0

    result = {
        "bpm": tempo_value,
        "beat_times": [float(t) for t in beat_times],
        "beat_count": int(len(beat_times)),
    }
    return _cache_set(asset, "beats", result)


def detect_downbeats(path: Path, asset: MediaAsset | None = None) -> list[float]:
    """Estimate downbeat (bar-start) timestamps, in seconds.

    Heuristic, not a real downbeat tracker: librosa has no dedicated
    downbeat/meter model, so this samples onset-strength energy at each
    detected beat, autocorrelates that beat-synchronous energy sequence to
    estimate the most likely bar length N in {2..6} beats, and returns every
    Nth beat as a downbeat starting from the first beat. This works
    reasonably for music with a clear, steady accent pattern (e.g. a strong
    kick on beat 1) and will be unreliable for syncopated, rubato, or
    odd-meter material -- treat the result as a rough anchor for "musical
    phrasing", not ground truth bar lines.
    """
    cached = _cache_get(asset, "downbeats")
    if cached is not None:
        return cached

    beats = detect_bpm_and_beats(path, asset=asset)
    beat_times = beats["beat_times"]
    if len(beat_times) < 4:
        result = [float(t) for t in beat_times]
        return _cache_set(asset, "downbeats", result)

    y, sr = extract_pcm(path)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    onset_times = librosa.times_like(onset_env, sr=sr)
    beat_strengths = np.interp(beat_times, onset_times, onset_env)

    detrended = beat_strengths - beat_strengths.mean()
    best_n, best_score = 4, -np.inf
    for n in range(2, 7):
        if n >= len(detrended):
            continue
        a, b = detrended[:-n], detrended[n:]
        if len(a) < 2:
            continue
        denom = np.std(a) * np.std(b)
        score = float(np.mean(a * b) / denom) if denom > 0 else -np.inf
        if score > best_score:
            best_score, best_n = score, n

    downbeats = [float(t) for t in beat_times[0::best_n]]
    return _cache_set(asset, "downbeats", downbeats)


# ------------------------------------------------------------- energy/sects

def detect_energy_sections(path: Path, *, section_seconds: float = 2.0, asset: MediaAsset | None = None) -> list[dict[str, Any]]:
    """Segment the track into fixed windows classified low/medium/high energy.

    Thresholds are percentile-based *within this track's own* RMS
    distribution (33rd/66th percentile), not fixed absolute dB values, so
    "high energy" means "high relative to the rest of this track".
    """
    cache_key = f"energy_sections_{section_seconds}"
    cached = _cache_get(asset, cache_key)
    if cached is not None:
        return cached

    y, sr = extract_pcm(path)
    window = max(1, int(round(section_seconds * sr)))
    n = len(y)

    windows: list[tuple[float, float, float]] = []
    for start in range(0, n, window):
        seg = y[start:start + window]
        if seg.size == 0:
            continue
        rms = float(np.sqrt(np.mean(np.square(seg, dtype=np.float64))))
        end = min(start + window, n)
        windows.append((start / sr, end / sr, rms))

    if not windows:
        return _cache_set(asset, cache_key, [])

    rms_values = np.array([w[2] for w in windows])
    low_thresh = float(np.percentile(rms_values, 33))
    high_thresh = float(np.percentile(rms_values, 66))

    sections = []
    for start, end, rms in windows:
        if rms <= low_thresh:
            energy = "low"
        elif rms >= high_thresh:
            energy = "high"
        else:
            energy = "medium"
        sections.append({"start": float(start), "end": float(end), "energy": energy, "rms": float(rms)})

    return _cache_set(asset, cache_key, sections)


def detect_music_sections(path: Path, asset: MediaAsset | None = None) -> list[dict[str, Any]]:
    """Best-effort structural segmentation into contiguous sections.

    Uses agglomerative clustering (`librosa.segment.agglomerative`) over
    stacked MFCC + chroma features to find boundary points, then labels the
    resulting spans generically as "section_1", "section_2", etc. This is
    NOT semantic structure recognition -- it cannot tell you "this is the
    chorus" vs "this is a verse"; that requires a supervised model trained
    on labelled song structure, which this module does not implement. Each
    section also gets a relative low/medium/high energy label, computed the
    same percentile-within-track way as `detect_energy_sections`.
    """
    cached = _cache_get(asset, "music_sections")
    if cached is not None:
        return cached

    y, sr = extract_pcm(path)
    duration = len(y) / sr

    if duration < 4.0:
        result = [{"label": "section_1", "start": 0.0, "end": float(duration), "energy": "medium", "rms": 0.0}]
        return _cache_set(asset, "music_sections", result)

    hop_length = 512
    mfcc = librosa.feature.mfcc(y=y, sr=sr, hop_length=hop_length, n_mfcc=13)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)
    n_frames = min(mfcc.shape[1], chroma.shape[1])
    features = np.vstack([mfcc[:, :n_frames], chroma[:, :n_frames]])

    k = int(np.clip(round(duration / 15), 3, 8))
    k = max(1, min(k, n_frames))

    if k < 2:
        result = [{"label": "section_1", "start": 0.0, "end": float(duration), "energy": "medium", "rms": 0.0}]
        return _cache_set(asset, "music_sections", result)

    bound_frames = librosa.segment.agglomerative(features, k)
    bound_times = librosa.frames_to_time(bound_frames, sr=sr, hop_length=hop_length)
    bounds = sorted({0.0, *[float(t) for t in bound_times], float(duration)})

    sections = []
    for i in range(len(bounds) - 1):
        start, end = bounds[i], bounds[i + 1]
        seg = y[int(start * sr):int(end * sr)]
        rms = float(np.sqrt(np.mean(np.square(seg, dtype=np.float64)))) if seg.size else 0.0
        sections.append({"label": f"section_{i + 1}", "start": float(start), "end": float(end), "rms": rms})

    if sections:
        rms_arr = np.array([s["rms"] for s in sections])
        low_t = float(np.percentile(rms_arr, 33))
        high_t = float(np.percentile(rms_arr, 66))
        for s in sections:
            if s["rms"] <= low_t:
                s["energy"] = "low"
            elif s["rms"] >= high_t:
                s["energy"] = "high"
            else:
                s["energy"] = "medium"

    return _cache_set(asset, "music_sections", sections)


# ---------------------------------------------------------------- silence -

_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?[\d.]+)\s*\|\s*silence_duration:\s*(-?[\d.]+)")


def detect_silence(path: Path, *, threshold_db: float = -40.0, min_duration: float = 0.3, asset: MediaAsset | None = None) -> list[dict[str, Any]]:
    """List of {"start": float, "end": float} silence spans, via ffmpeg's `silencedetect` filter.

    Implemented by parsing ffmpeg's own stderr output rather than
    reimplementing silence detection in Python, since ffmpeg already has a
    real, well-tested filter for this and it's in the allowlisted runner.
    """
    cache_key = f"silence_{threshold_db}_{min_duration}"
    cached = _cache_get(asset, cache_key)
    if cached is not None:
        return cached

    path = Path(path)
    meta = probe_media(path)
    if not meta.audio_streams:
        raise UnsupportedMediaError(
            f"File has no audio stream: {path}",
            suggestion="Silence detection requires an audio stream; pick a different source file.",
        )

    result = run("ffmpeg", [
        "-i", str(path),
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_duration}",
        "-f", "null", "-",
    ], check=False)

    spans: list[dict[str, Any]] = []
    current_start: float | None = None
    for line in result.stderr.splitlines():
        m_start = _SILENCE_START_RE.search(line)
        if m_start:
            current_start = float(m_start.group(1))
            continue
        m_end = _SILENCE_END_RE.search(line)
        if m_end:
            end = float(m_end.group(1))
            duration_val = float(m_end.group(2))
            start = current_start if current_start is not None else max(0.0, end - duration_val)
            spans.append({"start": float(start), "end": float(end)})
            current_start = None

    return _cache_set(asset, cache_key, spans)
