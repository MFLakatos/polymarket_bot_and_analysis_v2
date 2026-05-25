"""Unit tests for polymarket_graph infrastructure config."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from polymarket_graph.infrastructure.config import AppConfig, load_config


def test_default_config():
    cfg = AppConfig()
    assert cfg.api_mode == "hybrid"
    assert cfg.neo4j.uri == "bolt://localhost:7687"
    assert cfg.clustering.algorithm == "kmeans"
    assert cfg.clustering.kmeans.k == 6


def test_load_config_from_yaml(tmp_path):
    yaml_content = """
api_mode: gamma_api
clustering:
  algorithm: dbscan
  dbscan:
    eps: 0.3
    min_samples: 5
logging:
  level: DEBUG
  use_json: false
"""
    cfg_file = tmp_path / "polymarket.yaml"
    cfg_file.write_text(yaml_content)
    cfg = load_config(str(cfg_file))
    assert cfg.api_mode == "gamma_api"
    assert cfg.clustering.algorithm == "dbscan"
    assert cfg.clustering.dbscan.eps == 0.3
    assert cfg.logging.level == "DEBUG"


def test_env_overrides_neo4j(monkeypatch, tmp_path):
    monkeypatch.setenv("NEO4J_URI", "bolt://testhost:7687")
    monkeypatch.setenv("NEO4J_PASSWORD", "supersecret")
    cfg_file = tmp_path / "empty.yaml"
    cfg_file.write_text("{}")
    cfg = load_config(str(cfg_file))
    assert cfg.neo4j.uri == "bolt://testhost:7687"
    assert cfg.neo4j.password.get_secret_value() == "supersecret"


def test_load_config_falls_back_to_defaults_if_no_file():
    cfg = load_config("/nonexistent/path.yaml")
    assert isinstance(cfg, AppConfig)
    assert cfg.api_mode == "hybrid"


def test_neo4j_config_has_password_as_secret():
    cfg = AppConfig()
    # SecretStr should not expose password in repr
    assert "neo4j" not in repr(cfg.neo4j.password)
