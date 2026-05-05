from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from animation.library_manager import LibraryManager


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_asset_id(value: str) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _normalize_repo_path(path: str | Path) -> str:
    return str(path or "").replace("\\", "/").strip()


def _resolve_record_file(
    root: Path,
    asset_id: str,
    source: str,
    file_value: str,
) -> str:
    text = _normalize_repo_path(file_value)
    if not text:
        return text

    lower_text = text.lower()
    if lower_text.startswith("assets/"):
        return text
    if lower_text.startswith("base/") or lower_text.startswith("generated/"):
        return f"assets/{text}"

    path = Path(text)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()

    base_dir = root / "assets" / "base" / asset_id
    generated_dir = root / "assets" / "generated" / asset_id
    if (base_dir / text).exists():
        return f"assets/base/{asset_id}/{text}".replace("\\", "/")
    if (generated_dir / text).exists():
        return f"assets/generated/{asset_id}/{text}".replace("\\", "/")

    stem_name = Path(text).name
    if source == "generated":
        return f"assets/generated/{asset_id}/{stem_name}".replace("\\", "/")
    return f"assets/base/{asset_id}/{stem_name}".replace("\\", "/")


def _normalize_prompt_request(
    record: dict,
    *,
    asset_id: str,
    pet_profile: str,
    pet_label: str,
) -> None:
    generated_prompt = record.get("generated_prompt")
    if not isinstance(generated_prompt, dict):
        return
    prompt_request = generated_prompt.get("prompt_request")
    if not isinstance(prompt_request, dict):
        prompt_request = {}
        generated_prompt["prompt_request"] = prompt_request

    prompt_request.setdefault("pet_id", asset_id)
    prompt_request.setdefault("pet_profile", pet_profile)
    prompt_request.setdefault("pet_label", pet_label)
    prompt_request.setdefault("tags", list(record.get("tags") or []))
    prompt_request.setdefault("behavior_type", str(record.get("behavior_type") or ""))


def migrate_animation_index(
    index_path: Path,
    *,
    root: Path,
    backup: bool = True,
    dry_run: bool = False,
) -> dict:
    index_path = Path(index_path).resolve()
    root = Path(root).resolve()
    base_dir = index_path.parent
    manifest_path = base_dir / "pet_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"pet manifest not found for index: {manifest_path}")

    manifest = _load_json(manifest_path)
    asset_id = _normalize_asset_id(str(manifest.get("asset_id") or manifest.get("pet_id") or base_dir.name))
    pet_profile = _normalize_asset_id(str(manifest.get("prompt_profile") or asset_id))
    pet_label = str(manifest.get("default_label") or asset_id.replace("_", " ")).strip() or asset_id

    original = _load_json(index_path)
    migrated_seed = {
        "animations": [],
        "tag_synonyms": dict(original.get("tag_synonyms") or {}),
    }
    temp_path = index_path.with_suffix(".migrating.json")
    temp_path.write_text(json.dumps(migrated_seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_lib = LibraryManager(str(temp_path))

    migrated_count = 0
    rewritten_paths = 0
    enriched_prompt_requests = 0

    for raw_record in original.get("animations", []):
        record = dict(raw_record)
        original_file = str(record.get("file") or "")
        normalized_file = _resolve_record_file(
            root,
            asset_id,
            str(record.get("source") or "").strip().lower(),
            original_file,
        )
        if normalized_file != original_file:
            rewritten_paths += 1
        record["file"] = normalized_file
        record["pet_id"] = asset_id
        record["pet_profile"] = pet_profile
        if "pet_label" in record and not str(record.get("pet_label") or "").strip():
            record.pop("pet_label", None)
        before_prompt = json.dumps((record.get("generated_prompt") or {}).get("prompt_request") or {}, ensure_ascii=False, sort_keys=True)
        _normalize_prompt_request(
            record,
            asset_id=asset_id,
            pet_profile=pet_profile,
            pet_label=pet_label,
        )
        after_prompt = json.dumps((record.get("generated_prompt") or {}).get("prompt_request") or {}, ensure_ascii=False, sort_keys=True)
        if before_prompt != after_prompt:
            enriched_prompt_requests += 1
        temp_lib.add(record)
        migrated_count += 1

    if dry_run:
        payload = _load_json(temp_path)
        temp_path.unlink(missing_ok=True)
        return {
            "index_path": str(index_path),
            "asset_id": asset_id,
            "migrated_count": migrated_count,
            "rewritten_paths": rewritten_paths,
            "enriched_prompt_requests": enriched_prompt_requests,
            "payload": payload,
        }

    if backup:
        backup_path = index_path.with_suffix(index_path.suffix + ".bak")
        shutil.copy2(index_path, backup_path)
    shutil.move(str(temp_path), str(index_path))
    return {
        "index_path": str(index_path),
        "asset_id": asset_id,
        "migrated_count": migrated_count,
        "rewritten_paths": rewritten_paths,
        "enriched_prompt_requests": enriched_prompt_requests,
    }


def _iter_index_paths(root: Path, asset_id: str | None, migrate_all: bool) -> list[Path]:
    if migrate_all:
        return sorted((root / "assets" / "base").glob("*/animation_index.json"))
    if asset_id:
        return [root / "assets" / "base" / _normalize_asset_id(asset_id) / "animation_index.json"]
    raise ValueError("provide --index-path, --asset-id, or --all")


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize character animation indexes to the current package schema.")
    parser.add_argument("--index-path", help="Path to one animation_index.json")
    parser.add_argument("--asset-id", help="Character package id under assets/base/")
    parser.add_argument("--all", action="store_true", help="Migrate every package under assets/base/")
    parser.add_argument("--root", default=PROJECT_ROOT, help="Project root path")
    parser.add_argument("--dry-run", action="store_true", help="Show migrated payload without writing it back")
    parser.add_argument("--no-backup", action="store_true", help="Skip writing .bak backups")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if args.index_path:
        paths = [Path(args.index_path)]
    else:
        paths = _iter_index_paths(root, args.asset_id, args.all)

    reports = [
        migrate_animation_index(
            path,
            root=root,
            backup=not args.no_backup,
            dry_run=args.dry_run,
        )
        for path in paths
    ]
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
