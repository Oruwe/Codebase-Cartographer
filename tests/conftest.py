import io
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN = REPO_ROOT / "fixtures" / "golden_repo"

sys.path.insert(0, str(REPO_ROOT))

from orgono.app.core.config import Config  # noqa: E402
from orgono.app.core.extract import extract_repo  # noqa: E402
from orgono.app.core.obs import Logger  # noqa: E402


@pytest.fixture
def silent_log():
    return Logger(stream=io.StringIO(), capture=True)


@pytest.fixture
def golden_dir():
    return GOLDEN


@pytest.fixture
def golden_graph(silent_log):
    return extract_repo(GOLDEN, Config(), silent_log)


@pytest.fixture
def repo_root():
    return REPO_ROOT
