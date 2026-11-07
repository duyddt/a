import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "data"  # văn bản mẫu 3 trang, nội dung giữ chỗ
SAMPLE_DIR = FIXTURES / "ND254_2026"


@pytest.fixture
def data_dir(tmp_path):
    """Bản sao của tests/fixtures/data để test không ghi ảnh trang vào repo."""
    target = tmp_path / "data"
    shutil.copytree(FIXTURES, target, ignore=shutil.ignore_patterns("pages"))
    return target


@pytest.fixture
def sample_pdf() -> bytes:
    return (SAMPLE_DIR / "source.pdf").read_bytes()


@pytest.fixture
def sample_json() -> bytes:
    return (SAMPLE_DIR / "document.json").read_bytes()
