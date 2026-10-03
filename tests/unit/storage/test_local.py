import pytest

from app.services.storage.local import LocalStorage


def test_storage_rejects_prefix_sibling_traversal(tmp_path) -> None:
    root = tmp_path / "uploads"
    sibling = tmp_path / "uploads_evil"
    root.mkdir()
    sibling.mkdir()
    storage = LocalStorage(str(root))

    with pytest.raises(ValueError, match="storage key escapes root"):
        storage._path("../uploads_evil/secret.txt")


def test_storage_accepts_path_inside_root(tmp_path) -> None:
    storage = LocalStorage(str(tmp_path / "uploads"))

    assert storage._path("tenant/source/file.csv") == (
        tmp_path / "uploads" / "tenant" / "source" / "file.csv"
    )
