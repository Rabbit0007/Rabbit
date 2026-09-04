from __future__ import annotations

import io
import tarfile

import pytest

from cairn.dispatcher.runtime.containers import ContainerManager


def test_text_file_archive_extracts_below_existing_top_level_directory() -> None:
    archive_path, payload = ContainerManager._text_file_archive(
        "/tmp/cairn-prompts/reason_execute-123/graph.yaml",
        "facts:\n- id: f001\n",
    )

    assert archive_path == "/tmp"
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        names = archive.getnames()
        assert names == [
            "cairn-prompts",
            "cairn-prompts/reason_execute-123",
            "cairn-prompts/reason_execute-123/graph.yaml",
        ]
        assert "tmp" not in names
        graph = archive.extractfile("cairn-prompts/reason_execute-123/graph.yaml")
        assert graph is not None
        assert graph.read() == b"facts:\n- id: f001\n"


@pytest.mark.parametrize("path", ["relative.txt", "/", "/tmp/../escape.txt"])
def test_text_file_archive_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValueError):
        ContainerManager._text_file_archive(path, "content")
