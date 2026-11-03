from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from true_fps import FfprobeNotFoundError, FpsDetector, FpsResult, FpsStatus, InstallResult, VideoTools

from conftest import write_video
from neuro_dataset_collector.sources import prepare_video_tools, video_tools
from neuro_dataset_collector.sources.video import VideoReader


@pytest.fixture
def variable_rate_video(tmp_path: Path) -> Path:
    """ Видео как с камеры: в заголовке 60 к/с, а кадры идут неровно и реже (как у записей с переменной частотой). """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required to make a variable frame rate video")
    path = tmp_path / "cam1.avi"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=64x48:r=60", "-t", "6",
                    "-vf", r"select='not(mod(n\,4))+eq(mod(n\,9)\,2)'", "-fps_mode", "vfr", "-c:v", "mpeg4",
                    "-q:v", "2", str(path)], check=True)
    return path


def test_regular_video_is_read_as_before(tmp_path: Path) -> None:
    video = write_video(tmp_path / "cam1.avi", frames=12)
    with VideoReader(video) as reader:
        assert not reader.variable_rate and reader.fps == pytest.approx(10.0) and reader.frame_count == 12
        frame = reader.read(7)
        assert frame is not None and frame.index == 7 and frame.timestamp == pytest.approx(0.7)
        assert [f.index for f in reader.frames(step=5)] == [0, 5, 10]


def test_real_fps_is_cached_next_to_frame_times(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = write_video(tmp_path / "cam1.avi", frames=12)
    monkeypatch.setattr(VideoReader, "index_dir", tmp_path / "index")
    calls: list[Path] = []
    original = FpsDetector.detect

    def counting(self: FpsDetector, path: Path) -> FpsResult:
        calls.append(path)
        return original(self, path)

    monkeypatch.setattr(FpsDetector, "detect", counting)
    for _ in range(2):
        with VideoReader(video) as reader:
            assert reader.fps == pytest.approx(10.0)
    assert len(calls) == 1
    assert len(list((tmp_path / "index").glob("*.fps.json"))) == 1


def test_header_is_used_when_frame_rate_cannot_be_measured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = write_video(tmp_path / "cam1.avi", frames=12)
    unmeasured = FpsResult(None, FpsStatus.INSUFFICIENT_DATA, None, (), None, "no timestamps")
    monkeypatch.setattr(FpsDetector, "detect", lambda self, path: unmeasured)
    with VideoReader(video) as reader:
        assert not reader.variable_rate and reader.fps == 10.0


def test_missing_ffprobe_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = write_video(tmp_path / "cam1.avi", frames=12)
    monkeypatch.setattr(VideoReader, "ffprobe_path", tmp_path / "no_ffprobe")
    with pytest.raises(FfprobeNotFoundError):
        VideoReader(video)


def test_prepared_tools_are_used_by_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools = VideoTools(ffmpeg=tmp_path / "ffmpeg", ffprobe=tmp_path / "ffprobe")
    monkeypatch.setattr(VideoReader, "ffprobe_path", None)
    result = InstallResult(True, "ok", tools)
    monkeypatch.setattr(video_tools, "ensure_video_tools", lambda tools_dir, on_status: result)
    assert prepare_video_tools(tmp_path).success
    assert VideoReader.ffprobe_path == tools.ffprobe


def test_failed_tools_preparation_keeps_reader_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(VideoReader, "ffprobe_path", None)
    monkeypatch.setattr(video_tools, "ensure_video_tools", lambda tools_dir, on_status: InstallResult(False, "no net"))
    assert not prepare_video_tools(tmp_path).success
    assert VideoReader.ffprobe_path is None


def test_variable_rate_video_frames_are_exact(variable_rate_video: Path, tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(VideoReader, "index_dir", tmp_path / "index")
    with VideoReader(variable_rate_video) as reader:
        sequence = [f.image for f in reader.frames()]
    with VideoReader(variable_rate_video) as reader:
        assert reader.variable_rate and reader.frame_count == len(sequence) < 200
        assert 10 < reader.fps < 30
        for index in (90, 3, 4, 60, len(sequence) - 1, 0):
            frame = reader.read(index)
            assert frame is not None and frame.index == index
            assert np.array_equal(frame.image, sequence[index])
        assert reader.read(len(sequence)) is None
        sampled = list(reader.frames(step=10))
        assert [f.index for f in sampled] == list(range(0, len(sequence), 10))
        # Время кадров — настоящее: до конца видео около 6 секунд, а не 6 * 60 / fps.
        assert sampled[-1].timestamp is not None and 5.0 < sampled[-1].timestamp < 6.2
    cached = sorted(path.name.endswith(".fps.json") for path in (tmp_path / "index").glob("*.json"))
    assert cached == [False, True]   # время кадров и частота кадров



def test_missing_files_of_unreviewed_frames_do_not_block_new_ones(tmp_path: Path) -> None:
    from datetime import datetime, timedelta

    from conftest import make_frame
    from neuro_dataset_collector import Collector, CollectorConfig
    from neuro_dataset_collector.catalog import Catalog

    config = CollectorConfig.model_validate({
        "classes": ["EMPTY"], "catalog": {"root": str(tmp_path / "data")},
        "auto": {"triggers": [{"type": "always"}], "limits": [{"type": "dedup", "threshold": 0}]},
        "runtime": {"async_write": False}, "storage": {"min_free_gb": 0},
    })
    with Collector(config) as collector:
        assert collector.offer(make_frame(1))[0].ok
        assert not collector.offer(make_frame(1, index=5))[0].ok          # дубль
    shutil.rmtree(tmp_path / "data" / "_unsorted")                          # папку удалили, чтобы начать заново
    with Catalog(tmp_path / "data") as catalog:
        old = (datetime.now().astimezone() - timedelta(hours=1)).isoformat(timespec="seconds")
        catalog._conn.execute("UPDATE samples SET created_at = ?", (old,))
        catalog._conn.commit()
    with Collector(config) as collector:
        result = collector.offer(make_frame(1, index=9))[0]
        assert result.ok, result.message
