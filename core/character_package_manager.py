from __future__ import annotations

import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
ASSETS_BASE_DIR = BASE_DIR / "assets" / "base"
RUNTIME_SETTINGS_PATH = BASE_DIR / "runtime_settings.json"


def _normalize_asset_id(value: object, fallback: str = "") -> str:
    text = str(value or fallback).strip().lower().replace(" ", "_").replace("-", "_")
    return text or fallback


def list_character_packages(base_dir: Path | None = None) -> list[dict]:
    root = Path(base_dir or ASSETS_BASE_DIR)
    if not root.exists():
        return []

    packages: list[dict] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_dir():
            continue
        if not (path / "animation_index.json").exists():
            continue
        manifest_path = path / "pet_manifest.json"
        payload = {}
        if manifest_path.exists():
            try:
                loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    payload = loaded
            except Exception:
                payload = {}

        asset_id = _normalize_asset_id(payload.get("asset_id") or payload.get("pet_id"), path.name)
        packages.append(
            {
                "asset_id": asset_id,
                "prompt_profile": _normalize_asset_id(payload.get("prompt_profile") or payload.get("profile_id"), asset_id),
                "label": str(payload.get("default_label") or asset_id.replace("_", " ")).strip() or asset_id,
                "subject_kind": str(payload.get("subject_kind") or payload.get("kind") or "character").strip() or "character",
                "reference_image": str(payload.get("reference_image") or f"{asset_id}.png").strip() or f"{asset_id}.png",
                "base_dir": str(path),
            }
        )
    return packages


def save_runtime_asset_selection(asset_id: str, settings_path: Path | None = None) -> Path:
    normalized_asset_id = _normalize_asset_id(asset_id, "slime_chan")
    path = Path(settings_path or RUNTIME_SETTINGS_PATH)
    payload = {"current_asset_id": normalized_asset_id}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
