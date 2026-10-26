"""Fixtures shared across test modules."""

import pytest
from fastapi.testclient import TestClient

from nasflow.config import Config
from nasflow.db import Store
from nasflow.pipeline import Pipeline
from nasflow.web.app import create_app

from .test_pipeline import FakeQbit, FakeTmdb


@pytest.fixture
def api(tmp_path):
    downloads = tmp_path / "downloads"
    staging = tmp_path / "staging"
    downloads.mkdir()
    staging.mkdir()
    cfg = Config(
        downloads_root=downloads, staging_root=staging, db_path=tmp_path / "nasflow.db",
        qb_url="http://qb", qb_username="u", qb_password="super-secret", tmdb_api_key="key-123",
    )
    store = Store(cfg.db_path)
    qbit = FakeQbit()
    tmdb = FakeTmdb()
    pipeline = Pipeline(cfg, store, qbit, tmdb)
    client = TestClient(create_app(pipeline, store, cfg))
    return client, pipeline, store, qbit, tmdb, downloads, staging
