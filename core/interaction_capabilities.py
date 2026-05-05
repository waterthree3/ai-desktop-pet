from __future__ import annotations

from typing import Iterable


_MANUAL_INTERACTION_RULES: dict[str, dict[str, object]] = {
    "feed": {
        "label": "喂食",
        "required_any_tags": ("eat", "eating", "fed"),
    },
    "play": {
        "label": "玩耍",
        "required_any_tags": ("play_ball",),
    },
    "bath": {
        "label": "洗澡",
        "required_any_tags": ("bath",),
    },
    "stroke": {
        "label": "抚摸",
        "required_any_tags": ("pet_stroke",),
    },
    "walk_mode": {
        "label": "散步",
        "required_any_tags": ("walking", "wander", "explore"),
    },
}


def interaction_label(event_type: str) -> str:
    rule = _MANUAL_INTERACTION_RULES.get(str(event_type or "").strip().lower())
    if not rule:
        return str(event_type or "").strip() or "该动作"
    return str(rule.get("label") or event_type)


def manual_interaction_types() -> list[str]:
    return list(_MANUAL_INTERACTION_RULES.keys())


def get_interaction_availability(library_manager) -> dict[str, bool]:
    return {
        event_type: is_interaction_supported(library_manager, event_type)
        for event_type in _MANUAL_INTERACTION_RULES
    }


def is_interaction_supported(library_manager, event_type: str) -> bool:
    normalized = str(event_type or "").strip().lower()
    rule = _MANUAL_INTERACTION_RULES.get(normalized)
    if not rule:
        return True

    required_any_tags = tuple(
        _normalize_tag(tag)
        for tag in rule.get("required_any_tags") or ()
        if _normalize_tag(tag)
    )
    if not required_any_tags:
        return True

    for tags in _iter_animation_tag_sets(library_manager):
        if any(tag in tags for tag in required_any_tags):
            return True
    return False


def unsupported_interaction_reason(event_type: str) -> str:
    return f"unsupported_interaction:{str(event_type or '').strip().lower()}"


def _iter_animation_tag_sets(library_manager) -> Iterable[set[str]]:
    if hasattr(library_manager, "animations"):
        for anim in getattr(library_manager, "animations") or []:
            yield {
                _normalize_tag(tag)
                for tag in (anim.get("tags", []) or [])
                if _normalize_tag(tag)
            }
        return

    files = getattr(library_manager, "_FILES", None)
    if isinstance(files, dict):
        for tag in files:
            normalized = _normalize_tag(tag)
            if normalized:
                yield {normalized}


def _normalize_tag(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
