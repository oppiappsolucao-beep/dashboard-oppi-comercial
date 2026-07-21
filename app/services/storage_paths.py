"""Caminho persistente para arquivos locais do CRM."""
from __future__ import annotations

import os
from pathlib import Path


def get_storage_dir() -> Path:
    configured = os.getenv("APP_STORAGE_DIR", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent.parent.parent / "storage"
