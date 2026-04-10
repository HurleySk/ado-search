# tests/conftest.py
import pytest
from pathlib import Path

from ado_search.db import Database


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / ".ado-search"
    d.mkdir()
    return d


@pytest.fixture
def db(data_dir):
    database = Database(data_dir / "index.db")
    database.initialize()
    yield database
    database.close()
