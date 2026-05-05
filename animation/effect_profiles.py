from __future__ import annotations

from copy import deepcopy
from typing import Any


_BEHAVIOR_KEYWORDS: list[tuple[set[str], str]] = [
    ({"feed", "eat", "snack", "chew"}, "eat/feed"),
    ({"bath", "clean", "groom", "wash", "brush", "self_care", "cleanliness"}, "bath/groom"),
    ({"sleep", "rest", "nap", "doze", "sleepy", "drowsy_idle", "pause", "settle", "cozy_pause", "pause_and_settle", "cozy_nap", "stretch_yawn"}, "sleep/rest"),
    ({"play", "playful", "run", "jump", "exercise", "train", "play_ball", "paw_bounce", "swim", "water_play", "splash", "chase_tail", "zoomies"}, "play/exercise"),
    ({"explore", "wander", "search", "show_off", "showcase", "sniff_floor", "sniff", "inspect", "inspect_corner", "peek_window", "look_outside", "investigate_sound", "listen"}, "explore/show_off"),
    ({"beg", "beg_food", "complain", "ask_food", "whine", "request", "bowl_check"}, "beg/complain"),
    ({"stroke", "pet", "hug", "chat", "comfort", "greet", "social", "greet_user", "nuzzle"}, "comfort/social"),
    ({"idle", "happy_idle", "sad_idle", "look_around", "blink", "scared", "surprised"}, "idle/emote"),
]

_DEFAULT_IMPACT_LEVEL = {
    "eat/feed": "medium",
    "bath/groom": "medium",
    "sleep/rest": "medium",
    "play/exercise": "medium",
    "explore/show_off": "medium",
    "beg/complain": "small",
    "comfort/social": "small",
    "idle/emote": "tiny",
}

_DEFAULT_TEMPLATES: dict[str, dict[str, Any]] = {
    "eat/feed": {
        "impact_level": "medium",
        "settlement": {
            "on_start": {"hunger": 0, "cleanliness": 0, "energy": 0, "mood": 2},
            "progressive": {"hunger": 22, "cleanliness": -2, "energy": 0, "mood": 1},
            "on_finish": {"hunger": 0, "cleanliness": 0, "energy": 0, "mood": 1, "exp": 2, "intimacy": 1},
        },
        "derived_tags_on_apply": ["well_fed"],
        "cooldown_sec": 120,
        "stack_decay_group": "feed_family",
    },
    "bath/groom": {
        "impact_level": "medium",
        "settlement": {
            "on_start": {"hunger": 0, "cleanliness": 0, "energy": -1, "mood": -1},
            "progressive": {"hunger": 0, "cleanliness": 28, "energy": -1, "mood": 2},
            "on_finish": {"hunger": 0, "cleanliness": 0, "energy": 0, "mood": 2, "exp": 1, "intimacy": 0},
        },
        "derived_tags_on_apply": ["clean"],
        "cooldown_sec": 180,
        "stack_decay_group": "groom_family",
    },
    "sleep/rest": {
        "impact_level": "medium",
        "settlement": {
            "on_start": {"hunger": 0, "cleanliness": 0, "energy": 2, "mood": 0},
            "progressive": {"hunger": -4, "cleanliness": 0, "energy": 18, "mood": 2},
            "on_finish": {"hunger": 0, "cleanliness": 0, "energy": 0, "mood": 2, "exp": 1, "intimacy": 0},
        },
        "derived_tags_on_apply": ["sleepy"],
        "cooldown_sec": 240,
        "stack_decay_group": "rest_family",
    },
    "play/exercise": {
        "impact_level": "medium",
        "settlement": {
            "on_start": {"hunger": -1, "cleanliness": 0, "energy": -2, "mood": 3},
            "progressive": {"hunger": -3, "cleanliness": -2, "energy": -4, "mood": 4},
            "on_finish": {"hunger": 0, "cleanliness": 0, "energy": 0, "mood": 3, "exp": 4, "intimacy": 1},
        },
        "derived_tags_on_apply": ["restless", "proud"],
        "cooldown_sec": 90,
        "stack_decay_group": "play_family",
    },
    "explore/show_off": {
        "impact_level": "medium",
        "settlement": {
            "on_start": {"hunger": 0, "cleanliness": 0, "energy": -1, "mood": 2},
            "progressive": {"hunger": -2, "cleanliness": -1, "energy": -4, "mood": 3},
            "on_finish": {"hunger": 0, "cleanliness": 0, "energy": 0, "mood": 1, "exp": 3, "intimacy": 0},
        },
        "derived_tags_on_apply": ["proud", "curious"],
        "cooldown_sec": 120,
        "stack_decay_group": "explore_family",
    },
    "beg/complain": {
        "impact_level": "small",
        "settlement": {
            "on_start": {"hunger": 0, "cleanliness": 0, "energy": -1, "mood": -2},
            "progressive": {"hunger": -1, "cleanliness": 0, "energy": -1, "mood": -1},
            "on_finish": {"hunger": 0, "cleanliness": 0, "energy": 0, "mood": 0, "exp": 0, "intimacy": 1},
        },
        "derived_tags_on_apply": ["hungry", "clingy"],
        "cooldown_sec": 60,
        "stack_decay_group": "complain_family",
    },
    "comfort/social": {
        "impact_level": "small",
        "settlement": {
            "on_start": {"hunger": 0, "cleanliness": 0, "energy": 0, "mood": 4},
            "progressive": {"hunger": 0, "cleanliness": 0, "energy": -1, "mood": 2},
            "on_finish": {"hunger": 0, "cleanliness": 0, "energy": 0, "mood": 1, "exp": 1, "intimacy": 2},
        },
        "derived_tags_on_apply": ["clingy"],
        "cooldown_sec": 45,
        "stack_decay_group": "social_family",
    },
    "idle/emote": {
        "impact_level": "tiny",
        "settlement": {
            "on_start": {"hunger": 0, "cleanliness": 0, "energy": 0, "mood": 1},
            "progressive": {"hunger": 0, "cleanliness": 0, "energy": 0, "mood": 0},
            "on_finish": {"hunger": 0, "cleanliness": 0, "energy": 0, "mood": 0, "exp": 0, "intimacy": 0},
        },
        "derived_tags_on_apply": ["low_spirited"],
        "cooldown_sec": 30,
        "stack_decay_group": "idle_family",
    },
}

_SCALE_FACTORS = {
    "tiny": 0.45,
    "small": 0.75,
    "medium": 1.0,
    "large": 1.3,
}

_SETTLEMENT_PHASES = ("on_start", "progressive", "on_finish")


def normalize_token(value: str | None) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def infer_behavior_family(animation: dict[str, Any]) -> str:
    candidates: list[str] = []
    explicit = normalize_token(animation.get("behavior_type"))
    if explicit:
        candidates.append(explicit)

    generated_prompt = animation.get("generated_prompt") or {}
    if isinstance(generated_prompt, dict):
        prompt_request = generated_prompt.get("prompt_request") or {}
        prompt_behavior = normalize_token(prompt_request.get("behavior_type"))
        if prompt_behavior:
            candidates.append(prompt_behavior)

    for tag in animation.get("tags", []) or []:
        normalized = normalize_token(tag)
        if normalized:
            candidates.append(normalized)

    candidate_set = set(candidates)
    for keywords, family in _BEHAVIOR_KEYWORDS:
        if candidate_set & keywords:
            return family
    return "idle/emote"


def infer_impact_level(animation: dict[str, Any], behavior_family: str | None = None) -> str:
    explicit = normalize_token(animation.get("impact_level"))
    if explicit in _SCALE_FACTORS:
        return explicit
    family = behavior_family or infer_behavior_family(animation)
    return _DEFAULT_IMPACT_LEVEL.get(family, "medium")


def build_effect_profile(animation: dict[str, Any], effect_hint: dict[str, Any] | None = None) -> dict[str, Any]:
    family = infer_behavior_family(animation)
    impact_level = infer_impact_level(animation, family)
    template = deepcopy(_DEFAULT_TEMPLATES[family])
    scale = _SCALE_FACTORS.get(impact_level, 1.0)
    profile = {
        "version": 1,
        "behavior_family": family,
        "impact_level": impact_level,
        "settlement": _scale_settlement(template["settlement"], scale),
        "derived_tags_on_apply": list(template.get("derived_tags_on_apply", [])),
        "cooldown_sec": int(template.get("cooldown_sec", 60)),
        "stack_decay_group": str(template.get("stack_decay_group", f"{family.replace('/', '_')}_group")),
        "effect_source": "rule+ai" if effect_hint else "rule",
    }
    if effect_hint:
        _apply_effect_hint(profile, effect_hint)
    return profile


def normalize_effect_profile(animation: dict[str, Any]) -> dict[str, Any]:
    existing = animation.get("effect_profile")
    if not isinstance(existing, dict):
        return build_effect_profile(animation)

    profile = deepcopy(existing)
    profile.setdefault("version", 1)
    family = normalize_token(profile.get("behavior_family")) or infer_behavior_family(animation)
    impact_level = normalize_token(profile.get("impact_level")) or infer_impact_level(animation, family)
    profile["behavior_family"] = family
    profile["impact_level"] = impact_level
    settlement = profile.setdefault("settlement", {})
    template = _scale_settlement(_DEFAULT_TEMPLATES.get(family, _DEFAULT_TEMPLATES["idle/emote"])["settlement"], 1.0)
    for phase in _SETTLEMENT_PHASES:
        phase_values = settlement.get(phase)
        settlement[phase] = _normalize_phase_values(phase_values if isinstance(phase_values, dict) else template[phase])
    profile["derived_tags_on_apply"] = [normalize_token(tag) for tag in profile.get("derived_tags_on_apply", []) if normalize_token(tag)]
    if not profile["derived_tags_on_apply"]:
        profile["derived_tags_on_apply"] = list(_DEFAULT_TEMPLATES.get(family, {}).get("derived_tags_on_apply", []))
    profile["cooldown_sec"] = int(profile.get("cooldown_sec") or _DEFAULT_TEMPLATES.get(family, {}).get("cooldown_sec", 60))
    profile["stack_decay_group"] = str(profile.get("stack_decay_group") or _DEFAULT_TEMPLATES.get(family, {}).get("stack_decay_group", f"{family.replace('/', '_')}_group"))
    profile["effect_source"] = str(profile.get("effect_source") or "rule")
    return profile


def summarize_effect_profile(profile: dict[str, Any]) -> dict[str, Any]:
    settlement = profile.get("settlement") or {}
    totals = {key: 0 for key in ("hunger", "cleanliness", "energy", "mood", "exp", "intimacy")}
    for phase in _SETTLEMENT_PHASES:
        phase_values = settlement.get(phase) or {}
        for key in totals:
            totals[key] += int(phase_values.get(key, 0) or 0)
    return {
        "stats": {k: totals[k] for k in ("hunger", "cleanliness", "energy", "mood")},
        "growth": {k: totals[k] for k in ("exp", "intimacy")},
    }


def _scale_settlement(settlement: dict[str, dict[str, int]], scale: float) -> dict[str, dict[str, int]]:
    scaled: dict[str, dict[str, int]] = {}
    for phase, values in settlement.items():
        scaled[phase] = {}
        for key, value in values.items():
            scaled[phase][key] = _scaled_value(key, value, scale)
    return scaled


def _scaled_value(key: str, value: int | float, scale: float) -> int:
    if key in {"exp", "intimacy"}:
        return max(0, int(round(value * min(scale, 1.2))))
    return int(round(value * scale))


def _normalize_phase_values(values: dict[str, Any]) -> dict[str, int]:
    normalized: dict[str, int] = {}
    for key in ("hunger", "cleanliness", "energy", "mood", "exp", "intimacy"):
        normalized[key] = int(values.get(key, 0) or 0)
    return normalized


def _apply_effect_hint(profile: dict[str, Any], effect_hint: dict[str, Any]) -> None:
    bias = effect_hint.get("effect_hint") if isinstance(effect_hint.get("effect_hint"), dict) else effect_hint
    if not isinstance(bias, dict):
        return

    progressive = profile["settlement"]["progressive"]
    allowed = {
        "energy_bias": ("energy", -2, 2),
        "mood_bias": ("mood", -3, 3),
        "cleanliness_bias": ("cleanliness", -2, 2),
    }
    for bias_key, (target, low, high) in allowed.items():
        raw = bias.get(bias_key, 0)
        try:
            amount = int(raw)
        except Exception:
            amount = 0
        amount = max(low, min(high, amount))
        progressive[target] = int(progressive.get(target, 0) + amount)
