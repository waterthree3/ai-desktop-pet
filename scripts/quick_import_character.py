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
from core.character_package_manager import save_runtime_asset_selection
from scripts.scaffold_character_package import scaffold_character_package


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
MOTION_EXTS = {".mp4", ".gif", ".mov", ".webm", ".avi"}
SLOT_ALIAS_GROUPS = {
    "IDLE_NEUTRAL": {"idle_neutral", "idle", "neutral"},
    "WALK": {"walk", "walking", "wander"},
    "FORCE_SLEEP": {"force_sleep", "sleep", "sleeping", "sleep_exhausted", "exhausted"},
    "CARRIED": {"carried", "lifted", "drag", "drag_start"},
    "STROKE": {"stroke", "pet_stroke", "pat"},
    "PLAY": {"play", "play_ball"},
    "EAT": {"eat", "feed", "eating", "fed"},
    "BATH": {"bath", "wash", "clean"},
    "DOUBLE_CLICK": {"double_click", "double", "excited_spin"},
    "DROWSY": {"drowsy", "sleepy", "yawning"},
    "FORCE_HUNGRY": {"force_hungry", "hungry", "beg_food", "starving"},
    "FORCE_DIRTY": {"force_dirty", "dirty", "filthy"},
    "FORCE_SAD": {"force_sad", "sad", "depressed", "comfort"},
}


def _normalize_token(value: object) -> str:
    text = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return text.strip("_")


def _pretty_label(asset_id: str) -> str:
    return str(asset_id or "character").replace("_", " ").strip() or "character"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _resolve_slot_from_name(stem: str) -> str | None:
    normalized = _normalize_token(stem)
    if not normalized:
        return None
    for slot, aliases in SLOT_ALIAS_GROUPS.items():
        if normalized == _normalize_token(slot) or normalized in aliases:
            return slot
    return None


def _detect_reference_image(source_dir: Path, asset_id: str, explicit_path: Path | None) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        if not path.exists():
            raise FileNotFoundError(f"reference image not found: {path}")
        return path

    candidates = [path for path in sorted(source_dir.iterdir()) if path.is_file() and path.suffix.lower() in IMAGE_EXTS]
    if not candidates:
        raise FileNotFoundError(f"no reference image found under: {source_dir}")

    preferred_names = [
        _normalize_token(asset_id),
        _normalize_token(source_dir.name),
        "reference",
        "ref",
        "character",
        "avatar",
    ]
    for preferred in preferred_names:
        for candidate in candidates:
            if _normalize_token(candidate.stem) == preferred:
                return candidate

    if len(candidates) == 1:
        return candidates[0]

    raise FileExistsError(
        "multiple candidate reference images found; please pass --reference-image explicitly: "
        + ", ".join(path.name for path in candidates)
    )


def _discover_motion_files(source_dir: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in sorted(source_dir.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file() or path.suffix.lower() not in MOTION_EXTS:
            continue
        slot = _resolve_slot_from_name(path.stem)
        if not slot or slot in found:
            continue
        found[slot] = path
    return found


def _ensure_character_package(
    root: Path,
    *,
    asset_id: str,
    label: str,
    prompt_profile: str,
    subject_kind: str,
    reference_image: Path,
    force: bool,
) -> Path:
    base_dir = root / "assets" / "base" / asset_id
    manifest_path = base_dir / "pet_manifest.json"
    index_path = base_dir / "animation_index.json"
    seed_path = base_dir / "animation_index.seed.json"
    prompt_request_path = base_dir / "base_prompt_requests.json"

    if not base_dir.exists():
        return scaffold_character_package(
            root=root,
            asset_id=asset_id,
            label=label,
            prompt_profile=prompt_profile,
            subject_kind=subject_kind,
            reference_image_path=reference_image,
            force=False,
        )

    missing = [path.name for path in (manifest_path, index_path, seed_path, prompt_request_path) if not path.exists()]
    if missing:
        raise FileNotFoundError(
            f"existing package is incomplete ({', '.join(missing)} missing): {base_dir}. "
            "Please repair it first or choose a new asset id."
        )

    destination = base_dir / reference_image.name
    if destination.exists() and not force and destination.resolve() != reference_image.resolve():
        raise FileExistsError(f"reference image already exists: {destination}. Use --force to overwrite it.")
    shutil.copy2(reference_image, destination)

    manifest = _load_json(manifest_path)
    manifest["asset_id"] = asset_id
    manifest["default_label"] = label
    manifest["prompt_profile"] = prompt_profile
    manifest["subject_kind"] = subject_kind
    manifest["reference_image"] = destination.name
    _save_json(manifest_path, manifest)
    return base_dir


def quick_import_character(
    root: Path,
    source_dir: Path,
    *,
    asset_id: str | None = None,
    label: str | None = None,
    prompt_profile: str = "character",
    subject_kind: str = "character",
    reference_image: Path | None = None,
    set_current: bool = False,
    force: bool = False,
) -> dict:
    root = Path(root).resolve()
    source_dir = Path(source_dir).resolve()
    if not source_dir.exists():
        raise FileNotFoundError(f"source directory not found: {source_dir}")

    normalized_asset_id = _normalize_token(asset_id or source_dir.name) or "character"
    resolved_label = str(label or _pretty_label(normalized_asset_id)).strip() or _pretty_label(normalized_asset_id)
    resolved_profile = _normalize_token(prompt_profile or "character") or "character"
    resolved_subject_kind = str(subject_kind or "character").strip() or "character"

    reference = _detect_reference_image(source_dir, normalized_asset_id, reference_image)
    motions = _discover_motion_files(source_dir)

    base_dir = _ensure_character_package(
        root,
        asset_id=normalized_asset_id,
        label=resolved_label,
        prompt_profile=resolved_profile,
        subject_kind=resolved_subject_kind,
        reference_image=reference,
        force=force,
    )
    index_path = base_dir / "animation_index.json"
    seed_path = base_dir / "animation_index.seed.json"
    lib = LibraryManager(str(index_path))
    seed_payload = _load_json(seed_path)
    seed_by_slot = {
        Path(str(entry.get("file") or "")).stem.upper(): dict(entry)
        for entry in seed_payload.get("animations", [])
    }

    imported: list[dict] = []
    for slot, source_path in motions.items():
        seed = seed_by_slot.get(slot.upper())
        if not seed:
            continue
        destination = base_dir / f"{slot}{source_path.suffix.lower()}"
        if destination.exists() and not force:
            raise FileExistsError(f"motion already exists: {destination}. Use --force to overwrite it.")
        shutil.copy2(source_path, destination)
        record = dict(seed)
        record["file"] = f"assets/base/{normalized_asset_id}/{destination.name}"
        saved = lib.add(record)
        imported.append(
            {
                "slot": slot,
                "source_file": source_path.name,
                "saved_file": destination.name,
                "animation_id": saved.get("id"),
            }
        )

    settings_path = None
    if set_current:
        settings_path = save_runtime_asset_selection(
            normalized_asset_id,
            settings_path=root / "runtime_settings.json",
        )

    return {
        "asset_id": normalized_asset_id,
        "label": resolved_label,
        "prompt_profile": resolved_profile,
        "subject_kind": resolved_subject_kind,
        "reference_image": reference.name,
        "package_dir": str(base_dir),
        "imported_slots": imported,
        "missing_slots": sorted(slot for slot in seed_by_slot if slot not in motions),
        "set_current": bool(set_current),
        "runtime_settings_path": str(settings_path) if settings_path else "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Quick import a user-provided character package from a simple folder.")
    parser.add_argument("--source-dir", required=True, help="Folder that contains one reference image and motion files.")
    parser.add_argument("--asset-id", help="Stable character package id. Defaults to the source folder name.")
    parser.add_argument("--label", help="Display label shown in prompts and UI.")
    parser.add_argument("--prompt-profile", default="character", help="Prompt profile id. Defaults to 'character'.")
    parser.add_argument("--subject-kind", default="character", help="Human-readable character kind for prompts.")
    parser.add_argument("--reference-image", help="Optional explicit reference image path.")
    parser.add_argument("--root", default=PROJECT_ROOT, help="Project root path.")
    parser.add_argument("--set-current", action="store_true", help="Switch the app to this character package after import.")
    parser.add_argument("--force", action="store_true", help="Overwrite copied files if they already exist.")
    args = parser.parse_args()

    summary = quick_import_character(
        root=Path(args.root),
        source_dir=Path(args.source_dir),
        asset_id=args.asset_id,
        label=args.label,
        prompt_profile=args.prompt_profile,
        subject_kind=args.subject_kind,
        reference_image=Path(args.reference_image) if args.reference_image else None,
        set_current=args.set_current,
        force=args.force,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
