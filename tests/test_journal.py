from __future__ import annotations

from pathlib import Path

from neuro_dataset_collector.journal import (
    JournalMeta,
    JournalWriter,
    JournalZone,
    ReviewFile,
    ReviewKind,
    build_segments,
    read_journal,
    read_journal_meta,
    read_review,
    review_path,
    write_review,
)
from neuro_dataset_collector.sources.video import is_annotated_video, list_videos


def test_journal_write_and_read(tmp_path: Path) -> None:
    meta = JournalMeta(video="W:/v/cam25.avi", fps=15.0, width=1280, height=720, source="regression",
                       zones=[JournalZone(id="1", polygon=[(0, 0), (10, 0), (10, 10)])], classes=["EMPTY", "FULL"])
    with JournalWriter(tmp_path / "run" / "cam25", meta) as writer:
        writer.write(0, 0.0, "1", "EMPTY", 0.91, "EMPTY")
        writer.write(1, 1 / 15, "1", None, None)
    rows = list(read_journal(writer.csv_path))
    assert [(r.frame, r.label, r.conf, r.state) for r in rows] == [(0, "EMPTY", 0.91, "EMPTY"), (1, None, None, None)]
    loaded = read_journal_meta(writer.meta_path)
    assert loaded.fps == 15.0 and loaded.zones[0].polygon[1] == (10.0, 0.0)
    assert list(read_journal(tmp_path / "run" / "cam25")) == rows      # по общей части имени


def test_journal_meta_updated_on_close(tmp_path: Path) -> None:
    writer = JournalWriter(tmp_path / "v", JournalMeta(video="v.avi"))
    writer.close(model="best.pt")
    assert read_journal_meta(writer.meta_path).model == "best.pt"


def test_segments_merge_overlapping_windows(tmp_path: Path) -> None:
    events = [
        ("1", 300, ReviewKind.MISSING, "+1", "нет", "пропуск"),
        ("1", 330, ReviewKind.EXTRA, "нет", "+1", ""),          # пересекается с первым — объединяется
        ("1", 900, ReviewKind.MARKED, "", "", "лишний счёт"),
        ("2", 310, ReviewKind.CHANGED, "+1", "+2", ""),
    ]
    segments = build_segments(events, fps=10.0, before_seconds=5, after_seconds=2, frame_count=920)
    assert [(s.zone, s.start_frame, s.end_frame, s.kind) for s in segments] == [
        ("1", 250, 350, ReviewKind.MISSING), ("1", 850, 919, ReviewKind.MARKED), ("2", 260, 330, ReviewKind.CHANGED)]
    review = ReviewFile(video="cam25.avi", segments=segments, run_id="20261022_143616_872")
    path = write_review(review_path(tmp_path, Path("cam25.avi")), review)
    assert path.name == "cam25.review.json" and read_review(path).segments == segments


def test_annotated_videos_are_skipped(tmp_path: Path) -> None:
    for name in ("cam25_1310.avi", "cam25_1310.annotated.mp4", "cam26.ANNOTATED.avi"):
        (tmp_path / name).write_bytes(b"")
    assert [p.name for p in list_videos(tmp_path, [".avi", ".mp4"])] == ["cam25_1310.avi"]
    assert is_annotated_video(Path("x.annotated.mp4")) and not is_annotated_video(Path("annotated_cam.mp4"))


def test_journal_table_filters_flips_and_answer(tmp_path: Path) -> None:
    from neuro_dataset_collector.journal import JournalFilter, JournalTable, find_journals

    meta = JournalMeta(video="v.avi", fps=10.0, zones=[JournalZone(id="1", polygon=[(0, 0), (4, 0), (4, 4)])],
                       classes=["EMPTY", "FULL"])
    labels = ["EMPTY", "EMPTY", "FULL", "FULL", None, "EMPTY"]
    with JournalWriter(tmp_path / "runs" / "v", meta) as writer:
        for frame, label in enumerate(labels):
            writer.write(frame, frame / 10, "1", label, 0.9 if label else None, "S" if frame > 2 else None)
            writer.write(frame, frame / 10, "2", "FULL", 0.4)
    assert find_journals(tmp_path) == [tmp_path / "runs" / "v"]
    table = JournalTable.load(tmp_path / "runs" / "v.predictions.json")
    assert len(table) == 12 and table.zones == ["1", "2"]
    page = table.query(JournalFilter(zones=["1"], flips=True))
    assert [r.frame for r in page.rows] == [2, 4, 5] and page.labels == {"FULL": 1, "": 1, "EMPTY": 1}
    assert table.query(JournalFilter(labels=[""])).total == 1
    assert table.query(JournalFilter(conf_max=0.5)).total == 6
    assert table.query(JournalFilter(states=["S"], zones=["1"])).total == 3
    assert table.query(JournalFilter(frame_from=4), offset=1, limit=2).total == 4
    answer = table.answer(3, "1")
    assert answer is not None and answer.label == "FULL" and answer.state == "S"
    assert table.answer(3, "9") is None
