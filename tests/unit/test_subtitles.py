"""Tests for subtitle support: SRT parse/format round-trip, the ops layer,
and the writer/parser wiring (sibling .srt file + avfilter.subtitles
filter, per KDE's own dev-docs/fileformat.md -- verified separately to
actually load in real melt during development).
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from kdenlive_mcp.core.assets.model import MediaIndex
from kdenlive_mcp.core.subtitles import ops as sub_ops
from kdenlive_mcp.core.subtitles.srt import format_srt, parse_srt
from kdenlive_mcp.core.timeline.model import new_project
from kdenlive_mcp.errors import InvalidOperationError
from kdenlive_mcp.kdenlive.adapter.xml_parser import KdenliveXmlParser
from kdenlive_mcp.kdenlive.adapter.xml_writer import KdenliveXmlWriter

SAMPLE_SRT = """1
00:00:01,000 --> 00:00:04,500
Hello world
This is line two

2
00:00:05,000 --> 00:00:07,250
Second subtitle
"""


def test_parse_srt_extracts_all_entries():
    subs = parse_srt(SAMPLE_SRT, Fraction(30, 1))
    assert len(subs) == 2
    assert subs[0].text == "Hello world\nThis is line two"
    assert subs[0].start_frame == 30
    assert subs[1].start_frame == 150


def test_format_and_reparse_round_trips():
    fps = Fraction(25, 1)
    subs = parse_srt(SAMPLE_SRT, fps)
    text = format_srt(subs, fps)
    reparsed = parse_srt(text, fps)
    assert len(reparsed) == len(subs)
    for a, b in zip(subs, reparsed):
        assert a.text == b.text
        assert a.start_frame == b.start_frame
        assert a.end_frame == b.end_frame


def test_ops_add_edit_move_split_merge():
    project = new_project("Subtitle Ops Test")
    seq = project.active_sequence()

    s1 = sub_ops.add_subtitle(project, seq.id, start_frame=0, end_frame=100, text="hello")
    assert seq.subtitles == [s1]

    edited = sub_ops.edit_subtitle_text(project, seq.id, s1.id, text="hello world")
    assert edited.text == "hello world"

    moved = sub_ops.move_subtitle(project, seq.id, s1.id, start_frame=10, end_frame=110)
    assert (moved.start_frame, moved.end_frame) == (10, 110)

    first, second = sub_ops.split_subtitle(project, seq.id, s1.id, at_frame=60,
                                            first_text="part one", second_text="part two")
    assert first.end_frame == 60
    assert second.start_frame == 60
    assert second.end_frame == 110
    assert len(seq.subtitles) == 2

    merged = sub_ops.merge_subtitles(project, seq.id, [first.id, second.id], separator=" / ")
    assert merged.text == "part one / part two"
    assert merged.start_frame == 10
    assert merged.end_frame == 110
    assert len(seq.subtitles) == 1

    sub_ops.remove_subtitle(project, seq.id, merged.id)
    assert seq.subtitles == []


def test_split_rejects_point_outside_range():
    project = new_project("Split Bounds Test")
    seq = project.active_sequence()
    s = sub_ops.add_subtitle(project, seq.id, start_frame=0, end_frame=30, text="x")
    with pytest.raises(InvalidOperationError):
        sub_ops.split_subtitle(project, seq.id, s.id, at_frame=30)
    with pytest.raises(InvalidOperationError):
        sub_ops.split_subtitle(project, seq.id, s.id, at_frame=100)


def test_import_export_srt_files(tmp_path):
    srt_path = tmp_path / "input.srt"
    srt_path.write_text(SAMPLE_SRT, encoding="utf-8")

    project = new_project("Import Export Test")
    seq = project.active_sequence()
    imported = sub_ops.import_srt(project, seq.id, str(srt_path))
    assert len(imported) == 2
    assert len(seq.subtitles) == 2

    export_target = tmp_path / "exported.srt"
    exported_path = sub_ops.export_srt(project, seq.id, str(export_target))
    assert exported_path.exists()

    reimported = parse_srt(exported_path.read_text(), project.settings.fps)
    assert len(reimported) == 2


def test_writer_creates_sibling_srt_and_parser_reads_it_back(tmp_path):
    project = new_project("Writer Parser Roundtrip")
    seq = project.active_sequence()
    sub_ops.add_subtitle(project, seq.id, start_frame=0, end_frame=60, text="Hello")
    sub_ops.add_subtitle(project, seq.id, start_frame=90, end_frame=150, text="World")

    media_index = MediaIndex(index_path=None)
    out_path = tmp_path / "project.kdenlive"
    KdenliveXmlWriter(project, media_index).write(out_path)

    srt_path = Path(str(out_path) + ".srt")
    assert srt_path.exists()

    parser = KdenliveXmlParser(out_path.read_text(), source_path=out_path)
    reparsed_project, _ = parser.parse_project()
    reparsed_subs = reparsed_project.sequences[0].sorted_subtitles()
    assert len(reparsed_subs) == 2
    assert reparsed_subs[0].text == "Hello"
    assert reparsed_subs[1].text == "World"


def test_no_subtitle_filter_written_when_sequence_has_none(tmp_path):
    project = new_project("No Subtitles")
    media_index = MediaIndex(index_path=None)
    out_path = tmp_path / "project.kdenlive"
    xml_text = KdenliveXmlWriter(project, media_index).to_string(target_path=out_path)
    assert "avfilter.subtitles" not in xml_text
