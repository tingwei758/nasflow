"""Fixtures shared across test modules."""

import pytest
from fastapi.testclient import TestClient

from nasflow.config import Config
from nasflow.db import Store
from nasflow.health import HealthRegistry, build_checks
from nasflow.pipeline import Pipeline
from nasflow.web.app import create_app

from .test_pipeline import FakeQbit, FakeTmdb


@pytest.fixture
def web_cfg(tmp_path):
    downloads = tmp_path / "downloads"
    staging = tmp_path / "staging"
    downloads.mkdir()
    staging.mkdir()
    return Config(
        downloads_root=downloads, staging_root=staging, db_path=tmp_path / "nasflow.db",
        qb_url="http://qb", qb_username="u", qb_password="super-secret", tmdb_api_key="key-123",
        # These fixtures test processing; the first-run baseline has its own tests.
        skip_existing_on_first_run=False,
    )


@pytest.fixture
def web_pipeline(web_cfg):
    store = Store(web_cfg.db_path)
    return Pipeline(web_cfg, store, FakeQbit(), FakeTmdb())


@pytest.fixture
def health(web_cfg, web_pipeline):
    """The registry the board reads. Nothing has probed it, so every check is unknown."""
    return HealthRegistry(
        build_checks(web_cfg, web_pipeline.store, web_pipeline), secrets=web_cfg.secrets()
    )


@pytest.fixture
def api(web_cfg, web_pipeline, health):
    client = TestClient(
        create_app(web_pipeline, web_pipeline.store, web_cfg, health=health)
    )
    return (
        client,
        web_pipeline,
        web_pipeline.store,
        web_pipeline.qbit,
        web_pipeline.tmdb,
        web_cfg.downloads_root,
        web_cfg.staging_root,
    )
