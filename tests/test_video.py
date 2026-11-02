from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from conftest import write_video
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
        assert not reader.variable_rate and reader.fps == 10.0 and reader.frame_count == 12
        frame = reader.read(7)
        assert frame is not None and frame.index == 7 and frame.timestamp == pytest.approx(0.7)
        assert [f.index for f in reader.frames(step=5)] == [0, 5, 10]


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
    assert len(list((tmp_path / "index").glob("*.json"))) == 1

