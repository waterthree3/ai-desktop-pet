from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


DEFAULT_TAG_SYNONYMS = {
    "idle": ["neutral", "calm"],
    "playful": ["excited", "bouncy"],
    "curious": ["alert", "investigating"],
    "rest": ["sleepy", "cozy"],
}

BASE_MOTION_RECIPES = [
    {
        "slot": "IDLE_NEUTRAL",
        "id_suffix": "idle_neutral",
        "tags": ["idle", "neutral", "calm"],
        "behavior_type": "idle",
        "loop": True,
        "reuse_scope": "idle_only",
        "action_desc": "rests in place with a soft stable posture and a tiny gentle bounce",
    },
    {
        "slot": "WALK",
        "id_suffix": "walk_wander",
        "tags": ["walking", "wander", "explore"],
        "behavior_type": "walk",
        "loop": True,
        "reuse_scope": "direct_only",
        "action_desc": "takes short bouncy steps in place with a curious wandering rhythm",
    },
    {
        "slot": "FORCE_SLEEP",
        "id_suffix": "force_sleep_exhausted",
        "tags": ["sleep", "sleeping", "exhausted"],
        "behavior_type": "force_sleep",
        "loop": True,
        "reuse_scope": "threshold_only",
        "action_desc": "drops into an exhausted sleep pose as if all energy has run out",
    },
    {
        "slot": "CARRIED",
        "id_suffix": "carried_scared",
        "tags": ["carried", "lifted", "scared"],
        "behavior_type": "carried",
        "loop": True,
        "reuse_scope": "direct_only",
        "action_desc": "is gently lifted into the air with a slightly tense wobble and startled expression",
    },
    {
        "slot": "STROKE",
        "id_suffix": "stroke_calm",
        "tags": ["pet_stroke", "happy", "calm"],
        "behavior_type": "pet_stroke",
        "loop": False,
        "reuse_scope": "direct_only",
        "action_desc": "leans into a gentle petting reaction with a happy soft bounce and relaxed face",
    },
    {
        "slot": "PLAY",
        "id_suffix": "play_ball",
        "tags": ["play_ball", "playful", "excited"],
        "behavior_type": "play_ball",
        "loop": False,
        "reuse_scope": "ai_exact_only",
        "action_desc": "does a cheerful playful hop with a bright energetic rebound and eager attention",
    },
    {
        "slot": "EAT",
        "id_suffix": "eat_fed",
        "tags": ["eat", "eating", "fed"],
        "behavior_type": "eat",
        "loop": False,
        "reuse_scope": "direct_only",
        "action_desc": "does a happy little eating reaction with eager nibbling and a satisfied bounce",
    },
    {
        "slot": "BATH",
        "id_suffix": "bath_shaking",
        "tags": ["bath", "cleanliness", "shaking"],
        "behavior_type": "bath",
        "loop": False,
        "reuse_scope": "direct_only",
        "action_desc": "reacts to a bath with a tiny wet shake and a slightly grumbly cute face",
    },
    {
        "slot": "DOUBLE_CLICK",
        "id_suffix": "double_click_excited",
        "tags": ["excited_spin", "excited", "jumping"],
        "behavior_type": "double_click",
        "loop": False,
        "reuse_scope": "direct_only",
        "action_desc": "bursts into a quick excited hop with a bright energetic reaction",
    },
    {
        "slot": "DROWSY",
        "id_suffix": "drowsy_idle",
        "tags": ["drowsy_idle", "sleepy", "yawning"],
        "behavior_type": "drowsy_idle",
        "loop": True,
        "reuse_scope": "threshold_only",
        "action_desc": "fights off sleep with heavy eyelids, a slow sway, and a tiny yawn",
    },
    {
        "slot": "FORCE_HUNGRY",
        "id_suffix": "force_hungry_beg_food",
        "tags": ["starving", "hungry", "beg_food"],
        "behavior_type": "force_hungry",
        "loop": False,
        "reuse_scope": "threshold_only",
        "action_desc": "looks very hungry and begs for food with needy bouncing and pleading eyes",
    },
    {
        "slot": "FORCE_DIRTY",
        "id_suffix": "force_dirty_miserable",
        "tags": ["filthy", "dirty_shake", "miserable"],
        "behavior_type": "force_dirty",
        "loop": False,
        "reuse_scope": "threshold_only",
        "action_desc": "looks sticky and miserable, shaking off grime with an embarrassed pout",
    },
    {
        "slot": "FORCE_SAD",
        "id_suffix": "force_sad_comfort",
        "tags": ["depressed", "comforting", "sad_pet"],
        "behavior_type": "force_sad",
        "loop": False,
        "reuse_scope": "threshold_only",
        "action_desc": "looks downcast and slowly perks up from a comforting soothing reaction",
    },
]


def _normalize_asset_id(text: str) -> str:
    normalized = str(text or "").strip().lower().replace(" ", "_").replace("-", "_")
    return normalized or "character"


def _manifest_payload(
    asset_id: str,
    label: str,
    prompt_profile: str,
    subject_kind: str,
    reference_image_name: str,
) -> dict:
    english_label = label if label.isascii() else asset_id.replace("_", " ")
    return {
        "manifest_version": 1,
        "asset_id": asset_id,
        "prompt_profile": prompt_profile,
        "subject_kind": subject_kind,
        "default_label": label,
        "reference_image": reference_image_name,
        "persona": {
            "identity": f"cute desktop companion {english_label}",
            "autonomous": f"a playful desktop companion {english_label} with a stable mood and personality",
            "quick_reply": f"a cute desktop companion {english_label} speaking in character",
            "chat": f"a cute desktop companion {english_label} chatting directly with the user in character",
        },
    }


def _animation_index_payload() -> dict:
    return {
        "animations": [],
        "tag_synonyms": DEFAULT_TAG_SYNONYMS,
    }


def _animation_seed_payload(asset_id: str) -> dict:
    animations = []
    for recipe in BASE_MOTION_RECIPES:
        animations.append(
            {
                "id": f"{asset_id}_{recipe['id_suffix']}",
                "file": f"assets/base/{asset_id}/{recipe['slot']}.mp4",
                "tags": list(recipe["tags"]),
                "loop": bool(recipe["loop"]),
                "fps": 24,
                "source": "user_provided",
                "reuse_scope": str(recipe["reuse_scope"]),
                "blocked": False,
                "rating": 0,
                "behavior_type": str(recipe["behavior_type"]),
            }
        )
    return {
        "animations": animations,
        "tag_synonyms": DEFAULT_TAG_SYNONYMS,
    }


def _base_prompt_requests_payload(asset_id: str, label: str, prompt_profile: str) -> dict:
    requests = []
    for recipe in BASE_MOTION_RECIPES:
        requests.append(
            {
                "slot": recipe["slot"],
                "target_file": f"assets/base/{asset_id}/{recipe['slot']}.mp4",
                "prompt_request": {
                    "pet_id": asset_id,
                    "pet_profile": prompt_profile,
                    "pet_label": label,
                    "tags": list(recipe["tags"]),
                    "behavior_type": str(recipe["behavior_type"]),
                    "action_desc": str(recipe["action_desc"]),
                },
            }
        )
    return {"requests": requests}


def _readme_text(asset_id: str, reference_image_name: str) -> str:
    return (
        f"# {asset_id} Asset Package\n\n"
        f"Place the reference image at `assets/base/{asset_id}/{reference_image_name}`.\n\n"
        "Recommended reference image rules:\n"
        "- single character only\n"
        "- full body visible\n"
        "- white or very clean background\n"
        "- front or 3/4 angle with a clear silhouette\n"
        "- stable face and major silhouette details\n\n"
        "Generated scaffolding files:\n"
        "- `animation_index.seed.json`: standard base motion slots\n"
        "- `base_prompt_requests.json`: editable prompt recipes for each slot\n\n"
        "Recommended completion order:\n"
        "- `IDLE_NEUTRAL`, `WALK`, `FORCE_SLEEP`, `CARRIED`\n"
        "- `STROKE`, `PLAY`, `EAT`, `BATH`\n"
        "- `DROWSY`, `FORCE_HUNGRY`, `FORCE_DIRTY`, `FORCE_SAD`\n"
    )


def scaffold_character_package(
    root: Path,
    asset_id: str,
    label: str,
    prompt_profile: str | None = None,
    subject_kind: str = "character",
    reference_image_path: Path | None = None,
    force: bool = False,
) -> Path:
    asset_id = _normalize_asset_id(asset_id)
    profile = _normalize_asset_id(prompt_profile or asset_id)
    root = Path(root).resolve()
    base_dir = root / "assets" / "base" / asset_id
    generated_dir = root / "assets" / "generated" / asset_id
    base_dir.mkdir(parents=True, exist_ok=True)
    generated_dir.mkdir(parents=True, exist_ok=True)

    reference_image_name = f"{asset_id}.png"
    if reference_image_path:
        source = Path(reference_image_path)
        if not source.exists():
            raise FileNotFoundError(f"reference image not found: {source}")
        reference_image_name = source.name
        destination = base_dir / reference_image_name
        if destination.exists() and not force:
            raise FileExistsError(f"reference image already exists: {destination}")
        shutil.copy2(source, destination)

    files = {
        base_dir / "pet_manifest.json": json.dumps(
            _manifest_payload(asset_id, label, profile, subject_kind, reference_image_name),
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        base_dir / "animation_index.json": json.dumps(
            _animation_index_payload(),
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        base_dir / "animation_index.seed.json": json.dumps(
            _animation_seed_payload(asset_id),
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        base_dir / "base_prompt_requests.json": json.dumps(
            _base_prompt_requests_payload(asset_id, label, profile),
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        base_dir / "README.md": _readme_text(asset_id, reference_image_name),
    }
    for path, content in files.items():
        if path.exists() and not force:
            raise FileExistsError(f"file already exists: {path}")
        path.write_text(content, encoding="utf-8")

    return base_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Scaffold a new desktop pet character package.")
    parser.add_argument("--asset-id", required=True, help="Stable package id, e.g. slime_chan")
    parser.add_argument("--label", required=True, help="Display label used in prompts, e.g. 史莱姆酱")
    parser.add_argument("--prompt-profile", help="Prompt profile id. Defaults to asset id.")
    parser.add_argument("--subject-kind", default="character", help="Human-readable subject kind for prompts.")
    parser.add_argument("--reference-image", help="Optional path to a reference image to copy into the package.")
    parser.add_argument("--root", default=Path(__file__).resolve().parents[1], help="Project root path.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing package files.")
    args = parser.parse_args()

    base_dir = scaffold_character_package(
        root=Path(args.root),
        asset_id=args.asset_id,
        label=args.label,
        prompt_profile=args.prompt_profile,
        subject_kind=args.subject_kind,
        reference_image_path=Path(args.reference_image) if args.reference_image else None,
        force=args.force,
    )
    print(f"Scaffolded character package at {base_dir}")
    print(f"To switch packages for one run: set DESKTOP_PET_ASSET_ID={_normalize_asset_id(args.asset_id)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
