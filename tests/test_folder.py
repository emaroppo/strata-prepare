"""A directory of files already in shape: declared, and carried across as they are.

What a catalog used to guess from a directory — that a frame's folder is
its video — is written down here instead, where a frame with no folder is
refused rather than quietly made a group of one.
"""

import os
from pathlib import Path

import pytest

from strata.contracts import Frames, Image, Text, check
from strata.prepare import FramesFolder, ImageFolder, PreparerError, TextFolder, run
from strata.prepare.conformance import PreparerContract


def tree(root: Path, files: dict[str, bytes]) -> Path:
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def sources(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


# ----------------------------------------------------------------------
# Frames
# ----------------------------------------------------------------------


def test_a_frame_s_video_is_the_directory_it_sits_in(tmp_path):
    src = tree(
        tmp_path / "src",
        {"clip-a/001.jpg": b"a1", "clip-a/002.jpg": b"a2", "day2/clip-b/001.jpg": b"b1"},
    )
    index = run(FramesFolder(), sources(src), tmp_path / "out", root=src)
    videos = {key: entry.metadata["video"] for key, entry in index.samples.items()}
    # However deep it sits, the directory is the video
    assert videos == {
        "clip-a/001.jpg": "clip-a",
        "clip-a/002.jpg": "clip-a",
        "day2/clip-b/001.jpg": "day2/clip-b",
    }
    assert check(index, Frames(), "frames", set(index.samples)).ok


def test_a_frame_in_no_directory_is_refused(tmp_path):
    src = tree(tmp_path / "src", {"loose.jpg": b"x"})
    with pytest.raises(PreparerError, match="directory of its own"):
        run(FramesFolder(), sources(src), tmp_path / "out", root=src)


# ----------------------------------------------------------------------
# Carrying files across
# ----------------------------------------------------------------------


def test_the_layout_is_kept_and_the_bytes_are_the_same(tmp_path):
    src = tree(tmp_path / "src", {"cats/1.jpg": b"cat", "dogs/2.png": b"dog"})
    out = tmp_path / "out"
    index = run(ImageFolder(), sources(src), out, root=src)
    assert set(index.samples) == {"cats/1.jpg", "dogs/2.png"}
    assert (out / "cats/1.jpg").read_bytes() == b"cat"
    assert index.type == "image"


def test_a_file_is_shared_rather_than_copied_where_it_can_be(tmp_path):
    src = tree(tmp_path / "src", {"a/1.jpg": b"x" * 1024})
    out = tmp_path / "out"
    run(ImageFolder(), sources(src), out, root=src)
    # Same filesystem here, so the one inode; a corpus of images costs no
    # second copy to prepare
    assert os.path.samefile(src / "a/1.jpg", out / "a/1.jpg")


def test_a_changed_source_replaces_what_was_carried(tmp_path):
    src = tree(tmp_path / "src", {"a/1.jpg": b"old"})
    out = tmp_path / "out"
    run(ImageFolder(), sources(src), out, root=src)
    (src / "a/1.jpg").unlink()
    (src / "a/1.jpg").write_bytes(b"new")
    run(ImageFolder(), sources(src), out, root=src)
    assert (out / "a/1.jpg").read_bytes() == b"new"


def test_text_is_carried_byte_for_byte(tmp_path):
    # Canonical form is the catalog's to apply; with no spans riding on the
    # text, nothing here depends on the characters
    src = tree(tmp_path / "src", {"notes/a.txt": b"line\r\n"})
    out = tmp_path / "out"
    index = run(TextFolder(), sources(src), out, root=src)
    assert (out / "notes/a.txt").read_bytes() == b"line\r\n"
    assert index.samples["notes/a.txt"].value is None


def test_each_reads_what_its_type_admits():
    assert ImageFolder.sources == Image.extensions
    assert FramesFolder.sources == Frames.extensions
    assert TextFolder.sources == Text.extensions


# ----------------------------------------------------------------------
# The contract, run against each
# ----------------------------------------------------------------------


class TestImageFolderConforms(PreparerContract):
    @pytest.fixture
    def preparer(self):
        return ImageFolder()

    @pytest.fixture
    def source(self, tmp_path):
        return tree(tmp_path / "src", {"cats/1.jpg": b"cat"}) / "cats/1.jpg"


class TestFramesFolderConforms(PreparerContract):
    @pytest.fixture
    def preparer(self):
        return FramesFolder()

    @pytest.fixture
    def source(self, tmp_path):
        return tree(tmp_path / "src", {"clip/000001.jpg": b"f"}) / "clip/000001.jpg"

    @pytest.fixture
    def root(self, tmp_path):
        # Where the video's directory is a directory, not the root itself
        return tmp_path / "src"


class TestTextFolderConforms(PreparerContract):
    @pytest.fixture
    def preparer(self):
        return TextFolder()

    @pytest.fixture
    def source(self, tmp_path):
        return tree(tmp_path / "src", {"a.txt": b"hello\n"}) / "a.txt"
