import json
import os
import struct
from pathlib import Path


BASE_DIR = Path(__file__).parent
ASSETS_ROOT_DIR = BASE_DIR / "assets"
ASSETS_BASE_DIR = ASSETS_ROOT_DIR / "base"


RUNTIME_SETTINGS_PATH = BASE_DIR / "runtime_settings.json"


def _normalize_asset_id(value: object, fallback: str) -> str:
    text = str(value or fallback).strip().lower().replace(" ", "_").replace("-", "_")
    return text or fallback


def _load_runtime_settings() -> dict:
    if not RUNTIME_SETTINGS_PATH.exists():
        return {}
    try:
        payload = json.loads(RUNTIME_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _runtime_asset_exists(asset_id: str) -> bool:
    normalized = _normalize_asset_id(asset_id, "")
    if not normalized:
        return False
    index_path = ASSETS_BASE_DIR / normalized / "animation_index.json"
    return index_path.exists() and index_path.is_file()


def _list_valid_runtime_assets() -> list[str]:
    if not ASSETS_BASE_DIR.exists():
        return []
    valid: list[str] = []
    for path in sorted(ASSETS_BASE_DIR.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_dir():
            continue
        if not (path / "animation_index.json").exists():
            continue
        valid.append(_normalize_asset_id(path.name, ""))
    return valid


def _save_runtime_settings_asset(asset_id: str) -> None:
    normalized = _normalize_asset_id(asset_id, "slime_chan")
    payload = {"current_asset_id": normalized}
    RUNTIME_SETTINGS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _resolve_current_pet_from_sources(
    env_value: str,
    runtime_settings: dict | None,
    *,
    default_asset_id: str = "slime_chan",
) -> str:
    normalized_env = _normalize_asset_id(env_value, "")
    if normalized_env:
        if _runtime_asset_exists(normalized_env):
            return normalized_env
        print(
            f"[WARN][config] ignored invalid DESKTOP_PET_ASSET_ID={normalized_env!r} because "
            "assets/base/<asset_id>/animation_index.json is missing",
            flush=True,
        )

    runtime_settings = runtime_settings if isinstance(runtime_settings, dict) else {}
    persisted_asset_id = _normalize_asset_id(runtime_settings.get("current_asset_id"), "")
    if persisted_asset_id and _runtime_asset_exists(persisted_asset_id):
        return persisted_asset_id

    fallback_candidates = [default_asset_id, *_list_valid_runtime_assets()]
    seen: set[str] = set()
    for raw_candidate in fallback_candidates:
        candidate = _normalize_asset_id(raw_candidate, "")
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if not _runtime_asset_exists(candidate):
            continue
        if persisted_asset_id and not normalized_env and persisted_asset_id != candidate:
            print(
                f"[WARN][config] runtime current_asset_id={persisted_asset_id!r} is invalid; "
                f"falling back to {candidate!r}",
                flush=True,
            )
            _save_runtime_settings_asset(candidate)
        return candidate

    return _normalize_asset_id(default_asset_id, "slime_chan")


def _resolve_current_pet() -> str:
    return _resolve_current_pet_from_sources(
        os.getenv("DESKTOP_PET_ASSET_ID", ""),
        _load_runtime_settings(),
    )


# Character package selection
# Override with DESKTOP_PET_ASSET_ID=slime_chan to switch packages without editing code.
CURRENT_PET = _resolve_current_pet()


# Paths
ASSETS_DIR = ASSETS_ROOT_DIR
BASE_ANIM_DIR = ASSETS_DIR / "base" / CURRENT_PET
GEN_ANIM_DIR = ASSETS_DIR / "generated" / CURRENT_PET
ANIM_INDEX_PATH = BASE_ANIM_DIR / "animation_index.json"
PET_MANIFEST_PATH = BASE_ANIM_DIR / "pet_manifest.json"
WORKFLOW_DIR = ASSETS_DIR / "workflows"
LEGACY_REF_IMAGE_PATH = ASSETS_DIR / "ref_image.png"


def _load_current_pet_manifest() -> dict:
    default_manifest = {
        "manifest_version": 1,
        "asset_id": CURRENT_PET,
        "pet_id": CURRENT_PET,
        "prompt_profile": CURRENT_PET,
        "subject_kind": "character",
        "kind": "character",
        "default_label": CURRENT_PET.replace("_", " "),
        "reference_image": f"{CURRENT_PET}.png",
        "display": {},
        "persona": {},
    }
    if not PET_MANIFEST_PATH.exists():
        return default_manifest
    try:
        raw = json.loads(PET_MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default_manifest

    def _normalized_slug(value: object, fallback: str) -> str:
        text = str(value or fallback).strip().lower().replace(" ", "_").replace("-", "_")
        return text or fallback

    def _normalized_phrase(value: object, fallback: str) -> str:
        text = " ".join(str(value or fallback).strip().replace("_", " ").replace("-", " ").split())
        return text.lower() or fallback

    persona = raw.get("persona") if isinstance(raw.get("persona"), dict) else {}
    display = raw.get("display") if isinstance(raw.get("display"), dict) else {}
    asset_id = _normalized_slug(raw.get("asset_id") or raw.get("pet_id"), default_manifest["asset_id"])
    prompt_profile = _normalized_slug(
        raw.get("prompt_profile") or raw.get("profile_id") or raw.get("pet_profile"),
        default_manifest["prompt_profile"],
    )
    subject_kind = _normalized_phrase(
        raw.get("subject_kind") or raw.get("kind"),
        default_manifest["subject_kind"],
    )
    return {
        "manifest_version": int(raw.get("manifest_version") or default_manifest["manifest_version"]),
        "asset_id": asset_id,
        "pet_id": asset_id,
        "prompt_profile": prompt_profile,
        "subject_kind": subject_kind,
        "kind": subject_kind,
        "default_label": str(raw.get("default_label") or default_manifest["default_label"]).strip() or default_manifest["default_label"],
        "reference_image": str(raw.get("reference_image") or default_manifest["reference_image"]).strip() or default_manifest["reference_image"],
        "display": display,
        "persona": persona,
    }


def _read_image_size(path: Path) -> tuple[int, int] | None:
    if not path.exists():
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:6] in {b"GIF87a", b"GIF89a"} and len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    if data.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 9 < len(data):
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in {0xD8, 0xD9}:
                continue
            if offset + 2 > len(data):
                break
            segment_length = struct.unpack(">H", data[offset:offset + 2])[0]
            if marker in {
                0xC0, 0xC1, 0xC2, 0xC3,
                0xC5, 0xC6, 0xC7,
                0xC9, 0xCA, 0xCB,
                0xCD, 0xCE, 0xCF,
            } and offset + 7 < len(data):
                height, width = struct.unpack(">HH", data[offset + 3:offset + 7])
                return width, height
            offset += max(0, segment_length)
    return None


PET_MANIFEST = _load_current_pet_manifest()
CURRENT_ASSET_ID = PET_MANIFEST["asset_id"]
CURRENT_PET_ID = PET_MANIFEST["pet_id"]
CURRENT_PET_PROFILE = PET_MANIFEST["prompt_profile"]
CURRENT_SUBJECT_KIND = PET_MANIFEST["subject_kind"]
CURRENT_PET_KIND = PET_MANIFEST["kind"]
CURRENT_PET_LABEL = PET_MANIFEST["default_label"]
CURRENT_PET_PERSONA = PET_MANIFEST["persona"]
DEFAULT_REF_IMAGE_PATH = BASE_ANIM_DIR / PET_MANIFEST["reference_image"]
REF_IMAGE_PATH = (
    LEGACY_REF_IMAGE_PATH
    if LEGACY_REF_IMAGE_PATH.exists()
    else DEFAULT_REF_IMAGE_PATH
)
COMFYUI_API_WORKFLOW_PATH = ASSETS_DIR / "comfyui_api_workflow.json"
COMFYUI_FRONTEND_WORKFLOW_PATH = WORKFLOW_DIR / "desktop_pet_workflow.json"
COMFYUI_WORKFLOW_MANIFEST_PATH = WORKFLOW_DIR / "desktop_pet_workflow_manifest.json"
PROMPT_PROFILE_PATH = ASSETS_DIR / "prompt_profiles.json"
DB_PATH = BASE_DIR / "pet.db"


# Pet display
_DEFAULT_PET_MAX_SIDE = 150


def _resolve_pet_display_size() -> tuple[int, int]:
    display = PET_MANIFEST.get("display") if isinstance(PET_MANIFEST.get("display"), dict) else {}
    explicit_width = int(display.get("width") or 0)
    explicit_height = int(display.get("height") or 0)
    if explicit_width > 0 and explicit_height > 0:
        return explicit_width, explicit_height

    max_side = max(32, int(display.get("max_side") or _DEFAULT_PET_MAX_SIDE))
    image_size = _read_image_size(DEFAULT_REF_IMAGE_PATH)
    if not image_size:
        return max_side, max_side

    original_width, original_height = image_size
    longest = max(original_width, original_height)
    if longest <= 0:
        return max_side, max_side
    scale = float(max_side) / float(longest)
    width = max(32, int(round(original_width * scale)))
    height = max(32, int(round(original_height * scale)))
    return width, height


PET_W, PET_H = _resolve_pet_display_size()
PET_MAX_SIDE = max(PET_W, PET_H)


# Movement and interaction
POSITION_LERP = 0.15
WALK_THRESHOLD_PX = 80
NEAR_MOUSE_PX = 80
WALK_MIN_DIST_PX = 300
WALK_SPEED_PX_S = 80
STARTLED_DIST_PX = PET_MAX_SIDE * 2
STARTLED_SPEED_PX_S = 600
NEAR_MOUSE_IDLE_S = 2.0
TICK_MS = 16
DRAG_HOLD_MS = 150


# Autonomous behavior
AUTONOMOUS_INTERVAL_S = 300


# Attribute decay rates (per second), slowed to hour-level pacing
ENERGY_DECAY = 0.0008333
HUNGER_DECAY = 0.0022222
CLEANLINESS_DECAY = 0.0005556


# Progressive recovery rates (per second)
ENERGY_SLEEP_RECOVERY = 1.6667
HUNGER_FEED_RECOVERY = 1.6667
CLEAN_BATH_RECOVERY = 1.6667
MOOD_PLAY_RECOVERY = 1.6667
DECAY_PERSIST_EVERY = 10


# Threshold checks and reminder cadence
THRESHOLD_CHECK_MS = 5000
HUNGRY_REMIND_S = 30
DROWSY_REMIND_S = 120


# Thresholds
HUNGER_FORCE_THRESHOLD = 10
ENERGY_FORCE_THRESHOLD = 5
ENERGY_DROWSY_THRESHOLD = 20


# Animation matching
TAG_MATCH_THRESHOLD = 0.7


# Generated animation limits
GENERATION_DAILY_LIMIT = 5
GENERATION_COOLDOWN_MIN = 10


# ComfyUI
COMFYUI_URL = "http://127.0.0.1:8188"
COMFYUI_WORKFLOW_ID = "desktop_pet_gen"


# LLM
LLM_MODEL_PATH = BASE_DIR / "models" / "qwen2.5-1.5b-q4.gguf"
LLM_MAX_TOKENS = 256
LLM_TEMPERATURE = 0.7
LLM_N_CTX = 2048
