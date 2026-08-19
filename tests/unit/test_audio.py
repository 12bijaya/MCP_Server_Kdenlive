"""Tests for the audio engine: waveform analysis, beat detection, beat-sync
editing, and the SFX placement scaffold.

Beat detection is tested primarily against a synthetically generated click
track with a known BPM (deterministic, no external dependency), plus one
smoke test against a real-world music file to prove the ffmpeg extraction
path works end to end on actual media.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from kdenlive_mcp.core.assets.model import MediaAsset
from kdenlive_mcp.core.assets.sfx import SFX_CATEGORIES, place_sfx, sfx_on_beat
from kdenlive_mcp.core.audio.beat_sync import (
    cut_on_beat,
    flash_on_beat,
    montage_on_beats,
    select_beats,
    shake_on_beat,
    speed_ramp_on_beat,
    zoom_on_beat,
)
from kdenlive_mcp.core.audio.beats import (
    detect_bpm_and_beats,
    detect_downbeats,
    detect_energy_sections,
    detect_music_sections,
    detect_silence,
)
from kdenlive_mcp.core.audio.waveform import analyze_waveform, extract_pcm
from kdenlive_mcp.core.timeline.model import Clip, new_id, new_project
from kdenlive_mcp.errors import InvalidOperationError, UnsupportedMediaError

REAL_AUDIO_CANDIDATES = [
    Path.home() / "Documents/kaam/Top_Down_Freeway.mp3",
    Path.home() / "Documents/kaam/Calculated_Reset.mp3",
]


# --------------------------------------------------------------- fixtures --

def _make_click_track(path: Path, *, bpm: float = 120.0, sr: int = 22050, n_beats: int = 32,
                       silence_gap: tuple[float, float] | None = None,
                       noise_amplitude: float = 0.002) -> Path:
    """Synthesize a click track at a known BPM, optionally with a real silent gap."""
    interval = 60.0 / bpm
    total_duration = n_beats * interval + 1.0
    n_samples = int(total_duration * sr)
    y = np.zeros(n_samples, dtype=np.float32)

    click_dur = 0.04
    t_click = np.arange(int(click_dur * sr)) / sr
    envelope = np.exp(-t_click * 60.0)
    click = (0.9 * np.sin(2 * np.pi * 1500.0 * t_click) * envelope).astype(np.float32)

    for i in range(n_beats):
        start = int(round(i * interval * sr))
        end = start + len(click)
        if end <= n_samples:
            y[start:end] += click

    rng = np.random.default_rng(42)
    y += (rng.standard_normal(n_samples) * noise_amplitude).astype(np.float32)

    if silence_gap is not None:
        s0, s1 = silence_gap
        y[int(s0 * sr):int(s1 * sr)] = 0.0

    sf.write(str(path), y, sr)
    return path


@pytest.fixture(scope="module")
def click_track(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("audio") / "click_120bpm.wav"
    return _make_click_track(path, bpm=120.0, n_beats=32)


@pytest.fixture(scope="module")
def click_track_with_silence(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("audio") / "click_120bpm_silence.wav"
    return _make_click_track(path, bpm=120.0, n_beats=32, silence_gap=(6.0, 8.5))


@pytest.fixture(scope="module")
def click_track_loud_floor(tmp_path_factory) -> Path:
    """A click track whose noise floor sits above the -40dB silencedetect
    threshold, so the ~0.46s gaps between individual clicks don't themselves
    register as silence -- isolates "no false positives" from the fact that
    a click is a very short, sparse sound relative to a 0.3s min_duration."""
    path = tmp_path_factory.mktemp("audio") / "click_120bpm_loud_floor.wav"
    return _make_click_track(path, bpm=120.0, n_beats=32, noise_amplitude=0.02)


@pytest.fixture(scope="module")
def real_audio_file() -> Path:
    for candidate in REAL_AUDIO_CANDIDATES:
        if candidate.exists():
            return candidate
    pytest.skip("No real audio fixture file found on this machine")


# ---------------------------------------------------------------- waveform -

class TestWaveform:
    def test_extract_pcm_shape_and_type(self, click_track):
        samples, sr = extract_pcm(click_track)
        assert isinstance(samples, np.ndarray)
        assert samples.dtype == np.float32
        assert samples.ndim == 1
        assert sr == 22050
        assert samples.size > sr  # more than one second of audio

    def test_extract_pcm_is_cached(self, click_track):
        samples1, sr1 = extract_pcm(click_track)
        samples2, sr2 = extract_pcm(click_track)
        assert sr1 == sr2
        assert samples1.shape == samples2.shape
        np.testing.assert_allclose(samples1, samples2)

    def test_extract_pcm_missing_file_raises(self, tmp_path):
        with pytest.raises(UnsupportedMediaError):
            extract_pcm(tmp_path / "does_not_exist.wav")

    def test_analyze_waveform_json_serializable(self, click_track):
        result = analyze_waveform(click_track)
        assert isinstance(result["duration"], float)
        assert isinstance(result["sample_rate"], int)
        assert isinstance(result["rms_envelope"], list)
        assert all(isinstance(v, float) for v in result["rms_envelope"])
        assert isinstance(result["rms_timestamps"], list)
        assert isinstance(result["peak_level"], float)
        assert isinstance(result["clipping"]["detected"], bool)
        assert isinstance(result["clipping"]["count"], int)
        assert result["loudness_rms_db"] is None or isinstance(result["loudness_rms_db"], float)

    def test_analyze_waveform_no_clipping_on_synthetic_track(self, click_track):
        result = analyze_waveform(click_track)
        assert result["clipping"]["detected"] is False
        assert result["clipping"]["count"] == 0
        assert 0.0 < result["peak_level"] < 1.0

    def test_analyze_waveform_real_file_smoke(self, real_audio_file):
        result = analyze_waveform(real_audio_file)
        assert result["duration"] > 0
        assert result["sample_rate"] > 0
        assert len(result["rms_envelope"]) > 0


# ------------------------------------------------------------------ beats -

class TestBeatDetection:
    def test_recovers_known_bpm(self, click_track):
        result = detect_bpm_and_beats(click_track)
        assert isinstance(result["bpm"], float)
        assert isinstance(result["beat_times"], list)
        assert all(isinstance(t, float) for t in result["beat_times"])
        error_pct = abs(result["bpm"] - 120.0) / 120.0
        assert error_pct < 0.08, f"expected ~120 BPM, got {result['bpm']}"
        assert result["beat_count"] == len(result["beat_times"])
        assert result["beat_count"] > 10

    def test_beat_times_are_sorted(self, click_track):
        result = detect_bpm_and_beats(click_track)
        times = result["beat_times"]
        assert times == sorted(times)

    def test_no_librosa_frame_indices_leak(self, click_track):
        result = detect_bpm_and_beats(click_track)
        assert set(result.keys()) == {"bpm", "beat_times", "beat_count"}

    def test_caching_via_asset(self, click_track):
        asset = MediaAsset(id="asset_click", path=str(click_track), kind="audio", duration=17.0)
        first = detect_bpm_and_beats(click_track, asset=asset)
        assert "beats" in asset.analysis
        # Corrupt the cached value in place; a second call must return this
        # corrupted value unchanged if (and only if) it actually hit the
        # cache instead of recomputing.
        asset.analysis["beats"]["bpm"] = -999.0
        second = detect_bpm_and_beats(click_track, asset=asset)
        assert second["bpm"] == -999.0
        assert first is not None

    def test_real_file_smoke(self, real_audio_file):
        result = detect_bpm_and_beats(real_audio_file)
        assert result["bpm"] > 0
        assert result["beat_count"] > 0


class TestDownbeats:
    def test_returns_subset_of_beats(self, click_track):
        beats = detect_bpm_and_beats(click_track)["beat_times"]
        downbeats = detect_downbeats(click_track)
        assert isinstance(downbeats, list)
        assert len(downbeats) <= len(beats)
        assert downbeats == sorted(downbeats)

    def test_caching_via_asset(self, click_track):
        asset = MediaAsset(id="asset_click2", path=str(click_track), kind="audio", duration=17.0)
        detect_downbeats(click_track, asset=asset)
        assert "downbeats" in asset.analysis
        asset.analysis["downbeats"] = ["cached_sentinel"]
        assert detect_downbeats(click_track, asset=asset) == ["cached_sentinel"]


class TestEnergySections:
    def test_labels_and_bounds(self, click_track):
        sections = detect_energy_sections(click_track, section_seconds=2.0)
        assert len(sections) > 0
        for s in sections:
            assert s["energy"] in ("low", "medium", "high")
            assert s["start"] < s["end"]
            assert isinstance(s["rms"], float)

    def test_relative_thresholds_produce_a_spread(self, click_track_with_silence):
        # the silent gap should register as distinctly lower energy than the
        # click-filled windows around it
        sections = detect_energy_sections(click_track_with_silence, section_seconds=1.0)
        labels = {s["energy"] for s in sections}
        assert "low" in labels


class TestMusicSections:
    def test_covers_full_duration(self, click_track):
        sections = detect_music_sections(click_track)
        assert len(sections) >= 1
        assert sections[0]["start"] == 0.0
        for s in sections:
            assert s["energy"] in ("low", "medium", "high")
            assert s["start"] <= s["end"]
        # contiguous: each section's end matches the next one's start
        for a, b in zip(sections, sections[1:]):
            assert abs(a["end"] - b["start"]) < 1e-6

    def test_labels_are_generic_not_semantic(self, click_track):
        sections = detect_music_sections(click_track)
        for s in sections:
            assert s["label"].startswith("section_")


class TestSilenceDetection:
    def test_finds_synthetic_silence_gap(self, click_track_with_silence):
        spans = detect_silence(click_track_with_silence, threshold_db=-40.0, min_duration=0.3)
        assert len(spans) >= 1
        # at least one detected span should overlap the injected 6.0-8.5s gap
        assert any(s["start"] < 8.5 and s["end"] > 6.0 for s in spans)

    def test_no_false_silence_with_noise_floor_above_threshold(self, click_track_loud_floor):
        spans = detect_silence(click_track_loud_floor, threshold_db=-40.0, min_duration=0.3)
        # the background noise floor is louder than the -40dB threshold, so
        # the gaps between individual clicks should not register as silence
        assert spans == []

    def test_raises_on_no_audio_stream(self, tmp_path):
        with pytest.raises(UnsupportedMediaError):
            detect_silence(tmp_path / "missing.wav")


# --------------------------------------------------------------- beat_sync -

class TestSelectBeats:
    def test_subtle_takes_every_fourth(self):
        beats = [float(i) for i in range(20)]
        selected = select_beats(beats, intensity="subtle", phrase_aware=False)
        assert selected == [0.0, 4.0, 8.0, 12.0, 16.0]

    def test_medium_takes_every_second(self):
        beats = [float(i) for i in range(10)]
        selected = select_beats(beats, intensity="medium", phrase_aware=False)
        assert selected == [0.0, 2.0, 4.0, 6.0, 8.0]

    def test_aggressive_takes_every_beat(self):
        beats = [float(i) for i in range(6)]
        selected = select_beats(beats, intensity="aggressive")
        assert selected == beats

    def test_phrase_aware_adds_downbeat_anchors(self):
        beats = [float(i) for i in range(20)]
        # index 5 would be skipped by "subtle" (every 4th: 0,4,8,...)
        selected = select_beats(beats, intensity="subtle", phrase_aware=True, downbeat_times=[5.02])
        assert 5.0 in selected

    def test_phrase_aware_false_ignores_downbeats(self):
        beats = [float(i) for i in range(20)]
        selected = select_beats(beats, intensity="subtle", phrase_aware=False, downbeat_times=[5.0])
        assert 5.0 not in selected

    def test_empty_beats_returns_empty(self):
        assert select_beats([], intensity="medium") == []

    def test_invalid_intensity_raises(self):
        with pytest.raises(InvalidOperationError):
            select_beats([0.0, 1.0], intensity="bogus")


def _make_clip(position: int, length: int, *, track_id: str = "t1") -> Clip:
    return Clip(
        id=new_id("clip"), track_id=track_id, clip_type="video",
        position=position, in_point=0, out_point=length,
    )


class TestCutOnBeat:
    def test_retimes_clips_preserving_duration_and_order(self):
        clips = [_make_clip(0, 30), _make_clip(100, 60), _make_clip(300, 15)]
        original_lengths = [c.timeline_length for c in clips]
        beat_times = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
        fps = Fraction(30)
        cut_on_beat(clips, beat_times, fps, intensity="medium")
        # medium => every 2nd beat: 0.0, 1.0, 2.0, ...
        assert clips[0].position == 0
        assert clips[1].position == 30  # 1.0s * 30fps
        assert clips[2].position == 60  # 2.0s * 30fps
        assert [c.timeline_length for c in clips] == original_lengths
        assert [c.id for c in clips] == [clips[0].id, clips[1].id, clips[2].id]

    def test_overflow_falls_back_to_full_beat_list(self):
        clips = [_make_clip(0, 10) for _ in range(4)]
        beat_times = [0.0, 1.0, 2.0]  # only 3 beats, aggressive selects all 3
        fps = Fraction(30)
        cut_on_beat(clips, beat_times, fps, intensity="aggressive")
        assert clips[0].position == 0
        assert clips[1].position == 30
        assert clips[2].position == 60
        # 4th clip: ran out of beats entirely, chained after previous
        assert clips[3].position == clips[2].position + clips[2].timeline_length

    def test_empty_clips_does_not_crash(self):
        cut_on_beat([], [0.0, 1.0], Fraction(30))


class TestBeatEffects:
    def test_zoom_on_beat_adds_keyframes(self):
        clip = _make_clip(0, 300)
        beat_times = [float(i) * 0.5 for i in range(8)]
        effects = zoom_on_beat(clip, 1920, 1080, beat_times, Fraction(30), intensity="medium")
        assert len(effects) > 0
        assert clip.effects
        rect_track = clip.effects[0].keyframed_params["rect"]
        assert len(rect_track.keyframes) > 0

    def test_shake_on_beat_adds_keyframes(self):
        clip = _make_clip(0, 300)
        beat_times = [float(i) * 0.5 for i in range(8)]
        effects = shake_on_beat(clip, 1920, 1080, beat_times, Fraction(30), intensity="medium")
        assert len(effects) > 0
        assert clip.effects
        rect_track = clip.effects[0].keyframed_params["rect"]
        assert len(rect_track.keyframes) > 0

    def test_flash_on_beat_adds_opacity_spikes(self):
        clip = _make_clip(0, 300)
        beat_times = [float(i) * 0.5 for i in range(8)]
        effects = flash_on_beat(clip, 1920, 1080, beat_times, Fraction(30), intensity="medium")
        assert len(effects) > 0
        rect_track = clip.effects[0].keyframed_params["rect"]
        opacities = {kf.value[4] for kf in rect_track.keyframes}
        assert 1.0 in opacities  # peak opacity was written

    def test_speed_ramp_on_beat_not_implemented(self):
        clip = _make_clip(0, 300)
        with pytest.raises(NotImplementedError):
            speed_ramp_on_beat(clip, [0.0, 0.5, 1.0], Fraction(30))


class TestMontageOnBeats:
    def test_places_clips_sequentially_ending_on_beats(self):
        project = new_project("montage_test")
        seq = project.active_sequence()
        video_track = seq.video_tracks()[0]
        clips_with_sources = [
            ("asset_a", 0, 10000),
            ("asset_b", 0, 10000),
            ("asset_c", 0, 10000),
        ]
        beat_times = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
        fps = Fraction(30)
        placed = montage_on_beats(
            project, seq.id, video_track.id, clips_with_sources, beat_times, fps,
            intensity="aggressive",
        )
        assert len(placed) == 3
        for a, b in zip(placed, placed[1:]):
            assert a.end <= b.position  # no overlap
        assert placed[0].position == 0

    def test_produces_valid_non_overlapping_timeline(self):
        project = new_project("montage_test2")
        seq = project.active_sequence()
        video_track = seq.video_tracks()[0]
        clips_with_sources = [("asset_x", 0, 10000) for _ in range(5)]
        beat_times = [i * 0.4 for i in range(1, 12)]
        fps = Fraction(25)
        placed = montage_on_beats(
            project, seq.id, video_track.id, clips_with_sources, beat_times, fps,
            intensity="medium",
        )
        assert len(placed) == 5
        assert video_track.overlaps(0, video_track.duration()) is not None  # sanity: track has clips
        # re-verify no overlaps by construction of the track itself
        sorted_clips = video_track.sorted_clips()
        for a, b in zip(sorted_clips, sorted_clips[1:]):
            assert a.end <= b.position


# --------------------------------------------------------------------- sfx -

class TestSfx:
    def test_place_sfx_valid_category(self):
        project = new_project("sfx_test")
        seq = project.active_sequence()
        audio_track = seq.audio_tracks()[0]
        asset = MediaAsset(id="asset_hit", path="/fake/hit.wav", kind="audio", duration=0.5)
        clip = place_sfx(
            project, seq.id, audio_track.id,
            asset_id=asset.id, at_frame=100, category="hit", asset=asset,
        )
        assert clip.metadata["sfx_category"] == "hit"
        assert clip.position == 100
        assert clip.clip_type == "audio"
        assert clip.timeline_length == 15  # 0.5s * 30fps

    def test_place_sfx_invalid_category_raises(self):
        project = new_project("sfx_test2")
        seq = project.active_sequence()
        audio_track = seq.audio_tracks()[0]
        asset = MediaAsset(id="asset_bad", path="/fake/bad.wav", kind="audio", duration=0.5)
        with pytest.raises(InvalidOperationError):
            place_sfx(
                project, seq.id, audio_track.id,
                asset_id=asset.id, at_frame=0, category="not_a_real_category", asset=asset,
            )

    def test_all_categories_are_accepted(self):
        project = new_project("sfx_test3")
        seq = project.active_sequence()
        audio_track = seq.audio_tracks()[0]
        cursor = 0
        for category in SFX_CATEGORIES:
            asset = MediaAsset(id=f"asset_{category}", path=f"/fake/{category}.wav", kind="audio", duration=0.1)
            clip = place_sfx(
                project, seq.id, audio_track.id,
                asset_id=asset.id, at_frame=cursor, category=category, asset=asset,
            )
            assert clip.metadata["sfx_category"] == category
            cursor += clip.timeline_length + 5

    def test_sfx_on_beat_places_one_per_selected_beat(self):
        project = new_project("sfx_test4")
        seq = project.active_sequence()
        audio_track = seq.audio_tracks()[0]
        asset = MediaAsset(id="asset_click_sfx", path="/fake/click.wav", kind="audio", duration=0.1)
        beat_times = [i * 0.5 for i in range(10)]  # 5s of beats, well spaced for a 0.1s hit
        fps = Fraction(30)
        clips = sfx_on_beat(
            project, seq.id, audio_track.id, asset, beat_times, fps,
            intensity="medium", category="click",
        )
        selected = select_beats(beat_times, intensity="medium")
        assert len(clips) == len(selected)
        for c in clips:
            assert c.metadata["sfx_category"] == "click"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
