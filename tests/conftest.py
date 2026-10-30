import shutil
from pathlib import Path

import pytest

REPO_DATA = Path(__file__).resolve().parent / "fixtures" / "data"  # văn bản mẫu 3 trang, nội dung giữ chỗ


@pytest.fixture
def data_dir(tmp_path):
    """Bản sao của tests/fixtures/data để test không ghi ảnh trang vào repo."""
    target = tmp_path / "data"
    shutil.copytree(REPO_DATA, target, ignore=shutil.ignore_patterns("pages"))
    return target
