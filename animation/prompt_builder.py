from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


_POSE_CUES = {
    "idle": ["relaxed idle standing pose", "balanced posture ready for a seamless loop"],
    "neutral": ["neutral attentive expression", "full body facing forward in a stable stance"],
    "carried": ["being gently lifted off the ground", "legs slightly tucked in"],
    "lifted": ["being held in the air", "body gathered into a carried pose"],
    "held": ["compact held pose", "full body clearly visible"],
    "dragging": ["being moved while held", "slightly startled carried posture"],
    "scared": ["slightly startled expression", "tense compact posture"],
    "play_ball": ["engaging with a toy ball", "playful ready-to-pounce stance"],
    "playful": ["alert playful posture"],
    "excited": ["spring-loaded energetic stance"],
    "jumping": ["mid-bounce pose that can return to the same stance"],
    "eat": ["head lowered toward food", "chewing pose"],
    "eating": ["focused eating pose"],
    "fed": ["content post-meal posture"],
    "pet_stroke": ["relaxed posture enjoying attention"],
    "happy": ["bright cheerful expression"],
    "calm": ["calm settled posture"],
    "walking": ["balanced stepping posture"],
    "wander": ["sniffing and exploring posture"],
    "explore": ["curious searching posture"],
    "starving": ["begging posture looking upward"],
    "hungry": ["expectant food-seeking posture"],
    "beg_food": ["one front limb slightly raised to beg"],
    "sleep": ["curled resting posture", "eyes closed"],
    "sleeping": ["deep sleeping posture"],
    "exhausted": ["collapsed tired posture"],
    "drowsy_idle": ["droopy half-awake posture"],
    "sleepy": ["sleepy heavy-eyed posture"],
    "yawning": ["mouth opening into a yawn"],
    "bath": ["body prepared for a shake"],
    "shaking": ["body tensed for a shake"],
    "cleanliness": ["freshly washed pose"],
}

_MOTION_CUES = {
    "idle": ["gentle breathing loop", "soft blink that returns to the same pose"],
    "neutral": ["tiny ear twitch", "minimal body sway with no stepping"],
    "carried": ["gentle suspended body bob", "small paw sway", "loopable held motion"],
    "lifted": ["small hanging paw motion", "gentle body sway"],
    "held": ["tiny breathing motion", "small body sway"],
    "dragging": ["small carried sway that stays centered", "very light body bob"],
    "scared": ["small startled body tension", "tiny paw curl motion"],
    "play_ball": ["small repeated interaction with the toy ball", "light playful bounce"],
    "playful": ["light playful bounce", "quick attentive reaction"],
    "excited": ["quick full-body bounce", "brief excited body motion"],
    "jumping": ["small repeated hop that returns to the same pose"],
    "eat": ["small chewing motion", "gentle head bob"],
    "eating": ["steady chewing loop", "subtle satisfied body motion"],
    "fed": ["small satisfied head movement", "relaxed idle motion"],
    "pet_stroke": ["gentle head tilt", "soft blink", "relaxed body response"],
    "happy": ["soft happy body sway"],
    "calm": ["slow blink", "gentle breathing"],
    "walking": ["small stepping-in-place loop"],
    "wander": ["sniffing motion", "small stepping loop"],
    "explore": ["nose-led sniffing motion", "small attentive head turns"],
    "starving": ["small begging motion", "restless body sway"],
    "hungry": ["light impatient head bob"],
    "beg_food": ["small repeated begging motion", "hopeful head lift"],
    "sleep": ["gentle breathing loop", "tiny ear twitch"],
    "sleeping": ["slow breathing loop", "tiny paw twitch"],
    "exhausted": ["very slow breathing", "heavy sleepy sway"],
    "drowsy_idle": ["slow head dip", "brief sleepy sway"],
    "sleepy": ["slow blink", "light head nod"],
    "yawning": ["short yawn loop", "head dipping slightly"],
    "bath": ["brief body shake", "small water-shake follow-through"],
    "shaking": ["quick shake loop that returns to the same frame"],
    "cleanliness": ["small fresh energetic motion"],
}

_PROMPT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "assets" / "prompt_profiles.json"


@dataclass(frozen=True)
class PromptBundle:
    image_prompt: str
    image_negative_prompt: str
    video_prompt: str
    video_negative_prompt: str


def build_prompt_bundle_from_request(request: dict) -> PromptBundle:
    image_overrides = _merge_request_prompt_parts(
        request.get("image_overrides"),
        request.get("pose_focus"),
        _string_as_list(request.get("framing")),
        _string_as_list(request.get("background")),
        _props_to_prompt_parts(request.get("props"), for_video=False),
    )
    video_overrides = _merge_request_prompt_parts(
        request.get("video_overrides"),
        request.get("motion_focus"),
        _string_as_list(request.get("loop_style")),
        _motion_intensity_to_parts(request.get("motion_intensity")),
        _props_to_prompt_parts(request.get("props"), for_video=True),
    )
    negative_overrides = _merge_request_prompt_parts(request.get("negative_overrides"))

    return build_prompt_bundle(
        pet_name=str(request.get("pet_label") or request.get("pet_name") or request.get("pet_profile") or "pet"),
        pet_profile=request.get("pet_profile"),
        tags=_coerce_list(request.get("tags")),
        action_desc=str(request.get("action_desc") or ""),
        behavior_type=str(request.get("behavior_type") or ""),
        image_overrides=image_overrides,
        video_overrides=video_overrides,
        negative_overrides=negative_overrides,
    )


def build_prompt_bundle(
    pet_name: str,
    tags: list[str],
    action_desc: str = "",
    behavior_type: str = "",
    pet_profile: str | None = None,
    image_overrides: list[str] | None = None,
    video_overrides: list[str] | None = None,
    negative_overrides: list[str] | None = None,
) -> PromptBundle:
    defaults, profile = _resolve_profile(pet_profile or pet_name)
    cleaned_tags = _normalize_tags(tags)
    pose_cues = _collect_cues(cleaned_tags, _POSE_CUES)
    motion_cues = _collect_cues(cleaned_tags, _MOTION_CUES)

    if not pose_cues:
        pose_cues = ["natural readable action pose"]
    if not motion_cues:
        motion_cues = ["subtle looped body motion", "gentle idle motion"]

    action_hint = _clean_text(action_desc)
    behavior_hint = _clean_text(behavior_type.replace("_", " ")) if behavior_type else ""
    pet_label = _clean_text(pet_name.replace("_", " ")) or "pet"
    subject = defaults["subject_template"].format(pet_label=pet_label)

    image_parts = [
        subject,
        *profile["identity_cues"],
        *defaults["image_style_cues"],
        *pose_cues,
        "single character only",
        profile["image_consistency"],
        *(image_overrides or []),
    ]
    if behavior_hint:
        image_parts.append(f"action category: {behavior_hint}")
    if action_hint:
        image_parts.append(f"action intent: {action_hint}")

    video_parts = [
        subject,
        *profile["identity_cues"],
        *defaults["video_style_cues"],
        *motion_cues,
        profile["video_consistency"],
        *(video_overrides or []),
    ]
    if behavior_hint:
        video_parts.append(f"motion category: {behavior_hint}")
    if action_hint:
        video_parts.append(f"motion intent: {action_hint}")

    return PromptBundle(
        image_prompt=_join_parts(image_parts),
        image_negative_prompt=_join_parts([
            *defaults["image_negative_base"],
            *profile["image_negative_extra"],
            *(negative_overrides or []),
        ]),
        video_prompt=_join_parts(video_parts),
        video_negative_prompt=_join_parts([
            *defaults["video_negative_base"],
            *profile["video_negative_extra"],
            *(negative_overrides or []),
        ]),
    )


def _collect_cues(tags: list[str], mapping: dict[str, list[str]]) -> list[str]:
    seen: set[str] = set()
    cues: list[str] = []
    for tag in tags:
        for cue in mapping.get(tag, []):
            if cue in seen:
                continue
            seen.add(cue)
            cues.append(cue)
            if len(cues) >= 4:
                return cues
    return cues


def _normalize_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in tags:
        tag = _clean_text(raw).replace(" ", "_").lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        ordered.append(tag)
    return ordered


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    return text.strip(",")


def _coerce_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _string_as_list(value: object) -> list[str]:
    text = _clean_text(str(value or ""))
    return [text] if text else []


def _props_to_prompt_parts(value: object, for_video: bool) -> list[str]:
    props = _coerce_list(value)
    if not props:
        return []
    joined = ", ".join(_clean_text(prop) for prop in props if _clean_text(prop))
    if not joined:
        return []
    if for_video:
        return [f"keep the prop consistent: {joined}"]
    return [f"include the prop: {joined}"]


def _motion_intensity_to_parts(value: object) -> list[str]:
    intensity = _clean_text(str(value or "")).lower()
    mapping = {
        "tiny": ["extremely small looped motion"],
        "subtle": ["subtle looped motion"],
        "medium": ["readable but controlled motion"],
    }
    return mapping.get(intensity, [intensity] if intensity else [])


def _merge_request_prompt_parts(*values: object) -> list[str]:
    merged: list[str] = []
    for value in values:
        merged.extend(_coerce_list(value))
    return merged


def _join_parts(parts: list[str]) -> str:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in parts:
        part = _clean_text(raw)
        if not part:
            continue
        key = part.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(part)
    return ", ".join(ordered)


def _resolve_profile(profile_name: str) -> tuple[dict, dict]:
    config = _load_prompt_config()
    defaults = config["defaults"]
    profiles = config["profiles"]
    normalized = _clean_text(profile_name).replace(" ", "_").lower()
    return defaults, profiles.get(normalized, profiles["default"])


@lru_cache(maxsize=1)
def _load_prompt_config() -> dict:
    return json.loads(_PROMPT_CONFIG_PATH.read_text(encoding="utf-8"))
