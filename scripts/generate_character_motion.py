from __future__ import annotations

import argparse
import json
import shutil
import sys
import threading
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from animation.comfyui_client import ComfyUIClient
from animation.library_manager import LibraryManager
from animation.prompt_builder import build_prompt_bundle_from_request
from config import COMFYUI_URL


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_asset_id(value: str) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _find_seed_entry(seed_payload: dict, slot: str) -> dict:
    normalized_slot = str(slot or "").strip().upper()
    for entry in seed_payload.get("animations", []):
        file_name = Path(str(entry.get("file") or "")).name
        if Path(file_name).stem.upper() == normalized_slot:
            return dict(entry)
    raise KeyError(f"slot not found in animation_index.seed.json: {slot}")


def _find_request_entry(request_payload: dict, slot: str) -> dict:
    normalized_slot = str(slot or "").strip().upper()
    for entry in request_payload.get("requests", []):
        if str(entry.get("slot") or "").strip().upper() == normalized_slot:
            return dict(entry)
    raise KeyError(f"slot not found in first_batch_prompt_requests.json: {slot}")


def _wait_for_generation(
    comfy: ComfyUIClient,
    prompt_payload: dict,
    ref_image_path: Path,
    wait_timeout_s: int,
) -> str:
    done = threading.Event()
    result_holder: dict[str, str | None] = {"path": None}

    def _on_done(media_path):
        result_holder["path"] = str(media_path) if media_path else None
        done.set()

    started = comfy.generate(prompt_payload, str(ref_image_path), on_done=_on_done)
    if not started:
        raise RuntimeError("ComfyUI client is busy; generation was not started.")
    if not done.wait(wait_timeout_s):
        raise TimeoutError(f"generation did not finish within {wait_timeout_s}s")

    media_path = result_holder["path"]
    if not media_path:
        raise RuntimeError(comfy.get_last_error() or "generation finished without an output file")
    return media_path


def generate_character_motion(
    root: Path,
    asset_id: str,
    slot: str,
    *,
    comfyui_url: str = COMFYUI_URL,
    dry_run: bool = False,
    overwrite: bool = False,
    wait_timeout_s: int = 900,
) -> dict:
    root = Path(root).resolve()
    asset_id = _normalize_asset_id(asset_id)
    base_dir = root / "assets" / "base" / asset_id
    index_path = base_dir / "animation_index.json"
    seed_path = base_dir / "animation_index.seed.json"
    request_path = base_dir / "base_prompt_requests.json"
    legacy_request_path = base_dir / "first_batch_prompt_requests.json"
    manifest_path = base_dir / "pet_manifest.json"

    if not base_dir.exists():
        raise FileNotFoundError(f"character package not found: {base_dir}")
    if not index_path.exists():
        raise FileNotFoundError(f"animation index not found: {index_path}")
    if not seed_path.exists():
        raise FileNotFoundError(f"seed animation index not found: {seed_path}")
    if not request_path.exists():
        request_path = legacy_request_path
    if not request_path.exists():
        raise FileNotFoundError(
            f"prompt request file not found: {base_dir / 'base_prompt_requests.json'} "
            f"(legacy fallback also missing: {legacy_request_path})"
        )
    if not manifest_path.exists():
        raise FileNotFoundError(f"pet manifest not found: {manifest_path}")

    manifest = _load_json(manifest_path)
    seed_payload = _load_json(seed_path)
    request_payload = _load_json(request_path)

    seed_entry = _find_seed_entry(seed_payload, slot)
    request_entry = _find_request_entry(request_payload, slot)
    prompt_request = dict(request_entry.get("prompt_request") or {})
    prompt_request.setdefault("pet_id", asset_id)
    prompt_request.setdefault("pet_profile", str(manifest.get("prompt_profile") or asset_id))
    prompt_request.setdefault("pet_label", str(manifest.get("default_label") or asset_id.replace("_", " ")))

    prompts = build_prompt_bundle_from_request(prompt_request)
    target_rel_path = str(seed_entry.get("file") or "").replace("\\", "/")
    if not target_rel_path:
        raise ValueError(f"seed entry for slot {slot} is missing file path")
    target_path = root / target_rel_path
    target_path.parent.mkdir(parents=True, exist_ok=True)

    prompt_payload = {
        "image_prompt": prompts.image_prompt,
        "image_negative_prompt": prompts.image_negative_prompt,
        "video_prompt": prompts.video_prompt,
        "video_negative_prompt": prompts.video_negative_prompt,
        "filename_prefix": f"desktop_pet/{asset_id}_{slot.lower()}_{int(time.time())}",
        "output_basename": f"{asset_id}_{slot.lower()}_base",
    }

    summary = {
        "asset_id": asset_id,
        "slot": str(slot).strip().upper(),
        "target_file": target_rel_path,
        "pet_profile": prompt_request["pet_profile"],
        "pet_label": prompt_request["pet_label"],
        "behavior_type": str(prompt_request.get("behavior_type") or ""),
        "tags": list(prompt_request.get("tags") or []),
        "image_prompt": prompts.image_prompt,
        "video_prompt": prompts.video_prompt,
    }
    if dry_run:
        return summary

    reference_image = base_dir / str(manifest.get("reference_image") or f"{asset_id}.png")
    if not reference_image.exists():
        raise FileNotFoundError(f"reference image not found: {reference_image}")
    if target_path.exists() and not overwrite:
        raise FileExistsError(f"target motion already exists: {target_path}")

    workflow_template = _load_json(root / "assets" / "comfyui_api_workflow.json")
    temp_output_dir = base_dir / ".tmp_generation"
    temp_output_dir.mkdir(parents=True, exist_ok=True)
    comfy = ComfyUIClient(
        base_url=comfyui_url,
        workflow_template=workflow_template,
        ref_image_path=str(reference_image),
        output_dir=str(temp_output_dir),
    )

    generated_path = Path(
        _wait_for_generation(
            comfy,
            prompt_payload,
            reference_image,
            wait_timeout_s=wait_timeout_s,
        )
    )

    if target_path.exists():
        target_path.unlink()
    shutil.move(str(generated_path), str(target_path))
    try:
        if temp_output_dir.exists() and not any(temp_output_dir.iterdir()):
            temp_output_dir.rmdir()
    except OSError:
        pass

    record = dict(seed_entry)
    record["file"] = target_rel_path
    record["generated_prompt"] = {
        "prompt_request": prompt_request,
        "image_prompt": prompts.image_prompt,
        "image_negative_prompt": prompts.image_negative_prompt,
        "video_prompt": prompts.video_prompt,
        "video_negative_prompt": prompts.video_negative_prompt,
    }
    lib = LibraryManager(str(index_path))
    saved = lib.add(record)

    summary["generated_path"] = str(target_path)
    summary["saved_record_id"] = str(saved.get("id") or "")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and register one base motion for a character package.")
    parser.add_argument("--asset-id", required=True, help="Character package id, e.g. slime_chan")
    parser.add_argument("--slot", required=True, help="Motion slot from animation_index.seed.json, e.g. IDLE_NEUTRAL")
    parser.add_argument("--root", default=PROJECT_ROOT, help="Project root path")
    parser.add_argument("--url", default=COMFYUI_URL, help="ComfyUI base URL")
    parser.add_argument("--timeout-s", type=int, default=900, help="Wait timeout for one generation job")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite an existing target file")
    parser.add_argument("--dry-run", action="store_true", help="Print the resolved prompt payload without generating")
    args = parser.parse_args()

    summary = generate_character_motion(
        root=Path(args.root),
        asset_id=args.asset_id,
        slot=args.slot,
        comfyui_url=args.url,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        wait_timeout_s=args.timeout_s,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
