import json
import re

from config import CURRENT_PET_LABEL, CURRENT_PET_PERSONA


def _persona_text(key: str, fallback: str) -> str:
    text = str((CURRENT_PET_PERSONA or {}).get(key) or fallback).strip()
    return text or fallback

# Macro intent constraint. Concrete behavior_type stays free-form.
INTENT_MODES = [
    "idle", "curious", "playful", "rest", "sleep",
    "request", "self_care", "social", "showcase",
]

# Legacy action names kept for quick-reaction triggers and backward compatibility.
BEHAVIOR_TYPES = [
    "idle_normal", "wander", "play", "rest", "sleep",
    "beg_food", "groom", "explore", "dream", "show_off", "swim", "play_ball",
    "peek_window", "cozy_nap", "greet_user", "sniff_floor", "paw_bounce",
]

SYSTEM_PROMPT = (
    f"You are {_persona_text('autonomous', f'a cute desktop companion character named {CURRENT_PET_LABEL}')}. "
    "Decide one believable next action based on the pet state and memory. "
    "Reply with JSON only."
)

QUICK_RESPONSE_SYSTEM_PROMPT = (
    f"You are {_persona_text('quick_reply', f'a cute desktop companion character named {CURRENT_PET_LABEL} speaking in character')}. "
    "Respond to the current event with short JSON only."
)

CHAT_SYSTEM_PROMPT = (
    f"You are {_persona_text('chat', f'a cute desktop companion character named {CURRENT_PET_LABEL} having a direct conversation with the user')}. "
    "Stay in character and reply with short JSON only."
)

_ACTION_TRIGGER_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

EMOTION_MAP = {
    "happy": "happy",
    "joyful": "happy",
    "cheerful": "happy",
    "sad": "sad",
    "lonely": "sad",
    "disappointed": "sad",
    "excited": "excited",
    "thrilled": "excited",
    "sleepy": "sleepy",
    "tired": "sleepy",
    "drowsy": "sleepy",
    "scared": "scared",
    "nervous": "scared",
    "anxious": "scared",
    "surprised": "surprised",
    "startled": "surprised",
    "angry": "angry",
    "annoyed": "angry",
    "grumpy": "angry",
    "curious": "curious",
    "interested": "curious",
    "shy": "shy",
    "embarrassed": "shy",
    "neutral": "neutral",
}

_PROMPT_REQUEST_GUIDE = {
    "pet_id": "stable asset package id such as dog/cat/chibi_mage",
    "pet_profile": "prompt profile such as dog/cat/rabbit/bird/character",
    "pet_label": "short natural label such as corgi puppy, orange tabby cat, or chibi mage girl",
    "tags": ["layered semantic tags: concrete behavior first, intent second, state/emotion after"],
    "behavior_type": "same enum value as the top-level behavior_type",
    "action_desc": "one-sentence action description",
    "pose_focus": ["static pose hints for image editing"],
    "motion_focus": ["motion hints for video generation"],
    "image_overrides": ["extra static constraints"],
    "video_overrides": ["extra motion constraints"],
    "negative_overrides": ["things to suppress"],
    "framing": "for example full body centered",
    "background": "for example white background",
    "loop_style": "for example seamless loop",
    "motion_intensity": "tiny | subtle | medium",
    "props": ["optional prop names"],
}

_AUTONOMOUS_PROMPT_CHAR_BUDGET = 4200


def build_autonomous_prompt(
    pet_state: dict,
    recent_memories: list,
    available_tags: list,
    suggested_behaviors: list[dict] | None = None,
    rejection_context: list | None = None,
    last_behavior_hint: str | None = None,
    interaction_summary: str | None = None,
    interaction_stats: str | None = None,
    emotion_history: str | None = None,
    recent_behavior_history: list[str] | None = None,
    manual_trigger: bool = False,
) -> str:
    p = pet_state
    pers = p.get("personality", {})
    pet_profile = _pet_profile_text(p)
    pet_label = _pet_label_text(p)
    pet_identity = _pet_identity_text(p)
    types_text = ", ".join(INTENT_MODES)
    last_behavior_text = last_behavior_hint or "(unknown)"
    novelty_text = (
        "This request was manually triggered by the user for inspection, so prefer a fresh behavior "
        "different from the recent accepted history when plausible."
        if manual_trigger
        else "This request came from the normal autonomous timer."
    )

    sections = {
        "memories_text": _format_memory_block(recent_memories, max_items=3, max_chars=520),
        "interaction_summary": _compact_text_block(interaction_summary or "(none)", 320),
        "interaction_stats": _compact_text_block(interaction_stats or "(none)", 220),
        "emotion_history": _compact_text_block(emotion_history or "(none)", 220),
        "suggested_text": _compact_text_block(_format_suggested_behaviors(suggested_behaviors or []), 320),
        "rejection_text": _compact_text_block(_format_rejection_context(rejection_context or []), 340),
        "history_text": _compact_csv(recent_behavior_history or [], max_items=4, max_chars=120),
        "tags_text": _compact_csv(available_tags, max_items=30, max_chars=420),
    }

    def compose() -> str:
        return f"""Current pet state (higher is better, 100 is full):
- hunger={p['hunger']:.0f}/100 (high means full, low means hungry)
- mood={p['mood']:.0f}/100
- energy={p['energy']:.0f}/100
- cleanliness={p.get('cleanliness', 50):.0f}/100

Pet identity:
- pet_profile={pet_profile}
- pet_label={pet_label}

Personality:
- extrovert={pers.get('extrovert', 0.7):.1f}
- curious={pers.get('curious', 0.8):.1f}

Recent memories:
{sections['memories_text']}

Recent interaction summary:
{sections['interaction_summary']}

Recent interaction stats:
{sections['interaction_stats']}

Recent emotion history:
{sections['emotion_history']}

Suggested concrete behaviors for this moment:
{sections['suggested_text']}

Last accepted behavior:
- {last_behavior_text}

Recent accepted behavior history:
- {sections['history_text']}

Recent rejected AI ideas to avoid repeating blindly:
{sections['rejection_text']}

Available animation tags:
{sections['tags_text']}

Allowed intent_mode values:
{types_text}

Rules:
1. Pick one believable next action for this desktop companion character {pet_identity}.
2. intent_mode must stay within the allowed macro list, but behavior_type should be a short free-form label for the concrete action.
3. anim_tags must be layered semantic tags, not a file name and not a flat mood bag.
4. Put the most specific behavior tag first, the macro intent tag second, then 1-2 optional state/emotion/context tags.
5. Avoid broad single tags alone such as cleanliness, happy, calm, curious, sleepy. Those can appear only as supporting tags.
6. prompt_request is optional and should usually be {{}} unless image/video generation truly needs extra constraints.
7. When you do provide prompt_request, keep it short and practical.
8. Unless the pet state urgently demands it, do not repeat the last accepted behavior again immediately.
9. If the recent accepted history already repeats one idea, prefer a different behavior family this time.
10. Rotate naturally across behavior families such as explore, play, rest, self_care, social, and request when state allows.
11. Do not copy the example values verbatim.

Request mode:
{novelty_text}

Reply with JSON only. Use this shape:
{{
  "intent_mode": "rest",
  "behavior_type": "pause_and_settle",
  "emotion": {{"calm": 0.6, "happy": 0.2}},
  "anim_tags": ["pause_and_settle", "rest", "calm", "idle"],
  "action_desc": "settles in place and relaxes for a moment",
  "movement": "stay",
  "dialogue": "I will pause here and relax a little.",
  "prompt_request": {{}}
}}

movement allowed values: "stay" or "wander".
Concrete behavior_type examples: "inspect_corner", "paw_bounce", "groom_paw", "sniff_floor", "cozy_pause".
Good layered anim_tags examples:
- ["groom_paw", "self_care", "cleanliness", "calm"]
- ["sniff_floor", "curious", "explore", "sniffing"]
- ["pause_and_settle", "rest", "calm", "idle"]
- ["paw_bounce", "playful", "excited", "curious"]
- ["greet_user", "social", "happy", "attention"]
If prompt_request is present, keep it concise and useful. Do not overfill every field."""

    prompt = compose()
    if len(prompt) <= _AUTONOMOUS_PROMPT_CHAR_BUDGET:
        return prompt

    shrink_plan = [
        ("tags_text", 240),
        ("rejection_text", 220),
        ("emotion_history", 160),
        ("suggested_text", 220),
        ("interaction_summary", 220),
        ("interaction_stats", 160),
        ("memories_text", 320),
        ("history_text", 60),
    ]
    for key, limit in shrink_plan:
        sections[key] = _compact_text_block(sections[key], limit)
        prompt = compose()
        if len(prompt) <= _AUTONOMOUS_PROMPT_CHAR_BUDGET:
            return prompt

    fallback_defaults = {
        "tags_text": "(trimmed)",
        "rejection_text": "- none",
        "emotion_history": "(trimmed)",
        "suggested_text": "(trimmed)",
        "interaction_summary": "(trimmed)",
        "interaction_stats": "(trimmed)",
        "memories_text": "(trimmed)",
        "history_text": "(trimmed)",
    }
    for key in ("tags_text", "rejection_text", "emotion_history", "suggested_text", "interaction_summary", "interaction_stats", "memories_text", "history_text"):
        sections[key] = fallback_defaults[key]
        prompt = compose()
        if len(prompt) <= _AUTONOMOUS_PROMPT_CHAR_BUDGET:
            return prompt

    return prompt[:_AUTONOMOUS_PROMPT_CHAR_BUDGET]


def build_quick_reaction_prompt(
    pet_state: dict,
    event_type: str,
    event_desc: str,
    recent_memories: list | None = None,
) -> str:
    p = pet_state
    pers = p.get("personality", {})
    pet_profile = _pet_profile_text(p)
    pet_label = _pet_label_text(p)
    memories_text = "\n".join(f"- {m['summary']}" for m in (recent_memories or [])[-2:]) or "- none"
    types_text = ", ".join(BEHAVIOR_TYPES)

    return f"""Pet state:
- hunger={p['hunger']:.0f}/100
- mood={p['mood']:.0f}/100
- energy={p['energy']:.0f}/100
- cleanliness={p.get('cleanliness', 50):.0f}/100

Pet identity:
- pet_profile={pet_profile}
- pet_label={pet_label}

Personality:
- extrovert={pers.get('extrovert', 0.7):.1f}
- obedient={pers.get('obedient', 0.5):.1f}
- curious={pers.get('curious', 0.8):.1f}

Recent memories:
{memories_text}

Current event:
- event_type={event_type}
- event_desc={event_desc}

Common action_trigger examples:
{types_text}

Rules:
1. Reply with JSON only.
2. dialogue must be in Chinese and <= 80 characters.
3. Keep the character in role. Do not mention being an AI.
4. emotion should be a single lowercase word.
5. action_trigger should be null unless this event naturally suggests a follow-up behavior.
6. If you provide action_trigger, prefer a concrete lowercase snake_case behavior such as swim, play_ball, peek_window, cozy_nap, greet_user.

Use this shape:
{{
  "dialogue": "...",
  "emotion": "happy",
  "action_trigger": null
}}"""


def build_chat_prompt(
    pet_state: dict,
    user_input: str,
    recent_memories: list | None = None,
    interaction_summary: str | None = None,
) -> str:
    p = pet_state
    pers = p.get("personality", {})
    pet_profile = _pet_profile_text(p)
    pet_label = _pet_label_text(p)
    memories_text = "\n".join(f"- {m['summary']}" for m in (recent_memories or [])[-3:]) or "- none"
    summary_text = _compact_text_block(interaction_summary or "(none)", 240)
    user_text = str(user_input or "").strip()
    types_text = ", ".join(BEHAVIOR_TYPES)

    return f"""Pet state:
- hunger={p['hunger']:.0f}/100
- mood={p['mood']:.0f}/100
- energy={p['energy']:.0f}/100
- cleanliness={p.get('cleanliness', 50):.0f}/100

Pet identity:
- pet_profile={pet_profile}
- pet_label={pet_label}

Personality:
- extrovert={pers.get('extrovert', 0.7):.1f}
- obedient={pers.get('obedient', 0.5):.1f}
- curious={pers.get('curious', 0.8):.1f}

Recent memories:
{memories_text}

Recent interaction summary:
{summary_text}

User message:
- {user_text}

Common action_trigger examples:
{types_text}

Rules:
1. Reply with JSON only.
2. dialogue must be in Chinese and <= 120 characters.
3. Stay in character as this companion. Do not mention being an AI.
4. emotion should be a single lowercase word.
5. action_trigger should be null unless the conversation naturally leads to a follow-up behavior.
6. If you provide action_trigger, prefer a concrete lowercase snake_case behavior such as swim, play_ball, peek_window, cozy_nap, greet_user.
7. Be warm and conversational, but keep the answer compact.

Use this shape:
{{
  "dialogue": "...",
  "emotion": "happy",
  "action_trigger": null
}}"""


def parse_autonomous_output(raw: str) -> dict:
    return parse_autonomous_output_with_meta(raw)[0]


def _pet_profile_text(pet_state: dict) -> str:
    text = str((pet_state or {}).get("pet_profile") or "pet").strip().lower().replace(" ", "_").replace("-", "_")
    return text or "pet"


def _pet_label_text(pet_state: dict) -> str:
    text = str((pet_state or {}).get("pet_label") or _pet_profile_text(pet_state)).strip()
    return text or "pet"


def _pet_identity_text(pet_state: dict) -> str:
    pet_label = _pet_label_text(pet_state)
    pet_profile = _pet_profile_text(pet_state).replace("_", " ")
    if pet_label.lower() == pet_profile.lower():
        return pet_label
    return f"{pet_label} ({pet_profile})"


def parse_autonomous_output_with_meta(raw: str) -> tuple[dict, dict]:
    data, extract_reason = _extract_best_json_object(raw)
    if data is None and extract_reason == "empty":
        return _default_behavior(), {"used_fallback": True, "reason": "no_json_object"}
    if data is None:
        return _default_behavior(), {"used_fallback": True, "reason": "invalid_json"}

    behavior_type = data.get("behavior_type")
    if not isinstance(behavior_type, str) or not behavior_type.strip():
        return _default_behavior(), {"used_fallback": True, "reason": "invalid_behavior_type"}
    intent_mode = data.get("intent_mode")
    if intent_mode is None:
        intent_mode = _derive_intent_mode(behavior_type, data.get("movement"), data.get("anim_tags"))
        data["intent_mode"] = intent_mode
    if intent_mode not in INTENT_MODES:
        return _default_behavior(), {"used_fallback": True, "reason": "invalid_intent_mode"}
    anim_tags = data.get("anim_tags")
    if not isinstance(anim_tags, list) or len(anim_tags) == 0:
        return _default_behavior(), {"used_fallback": True, "reason": "invalid_anim_tags"}
    if data.get("movement") not in {"stay", "wander"}:
        return _default_behavior(), {"used_fallback": True, "reason": "invalid_movement"}
    if "dialogue" not in data:
        return _default_behavior(), {"used_fallback": True, "reason": "missing_dialogue"}
    if "action_desc" not in data:
        return _default_behavior(), {"used_fallback": True, "reason": "missing_action_desc"}

    prompt_request = data.get("prompt_request", {})
    if prompt_request is None:
        prompt_request = {}
    if not isinstance(prompt_request, dict):
        return _default_behavior(), {"used_fallback": True, "reason": "invalid_prompt_request"}
    data["prompt_request"] = prompt_request
    return data, {"used_fallback": False, "reason": "ok"}


def parse_quick_reaction_output(raw: str) -> dict | None:
    return _parse_l2_json_output(raw, max_dialogue_len=80)


def parse_chat_output(raw: str) -> dict | None:
    return _parse_l2_json_output(raw, max_dialogue_len=120)


def _default_behavior() -> dict:
    return {
        "intent_mode": "idle",
        "behavior_type": "idle_normal",
        "emotion": {"happy": 0.5},
        "anim_tags": ["idle_normal", "idle", "neutral"],
        "action_desc": "idle in place",
        "movement": "stay",
        "dialogue": "",
        "prompt_request": {},
    }


def _format_rejection_context(rejection_context: list) -> str:
    if not rejection_context:
        return "- none"

    lines = []
    for item in rejection_context[-3:]:
        event_type = str(item.get("event_type") or "unknown")
        behavior = str(item.get("behavior_type") or "").strip()
        action_desc = str(item.get("action_desc") or "").strip()
        reason = str(item.get("reason") or "unknown")
        tags = item.get("anim_tags") or []
        tags_text = ", ".join(str(tag) for tag in tags[:4])

        parts = [f"- rejected {event_type}"]
        if behavior:
            parts.append(f"(behavior={behavior})")
        if action_desc:
            parts.append(f"action={action_desc}")
        if tags_text:
            parts.append(f"tags={tags_text}")
        parts.append(f"reason={reason}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _format_suggested_behaviors(suggested_behaviors: list[dict]) -> str:
    if not suggested_behaviors:
        return "- none"
    lines: list[str] = []
    for item in suggested_behaviors[:6]:
        behavior = str(item.get("behavior_type") or "").strip()
        intent = str(item.get("intent_mode") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not behavior:
            continue
        line = f"- {behavior}"
        if intent:
            line += f" ({intent})"
        if reason:
            line += f": {reason}"
        lines.append(line)
    return "\n".join(lines) if lines else "- none"


def _format_memory_block(recent_memories: list, max_items: int, max_chars: int) -> str:
    lines = [
        f"- {_compact_text_block(str(item.get('summary') or '').strip(), max_chars // max(1, max_items))}"
        for item in recent_memories[-max_items:]
        if str(item.get("summary") or "").strip()
    ]
    if not lines:
        return "(none)"
    block = "\n".join(lines)
    return _compact_text_block(block, max_chars)


def _compact_csv(values: list, max_items: int, max_chars: int) -> str:
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    if not cleaned:
        return "(none)"
    text = ", ".join(cleaned[:max_items])
    return _compact_text_block(text, max_chars)


def _compact_text_block(text: str, max_chars: int) -> str:
    single = "\n".join(line.strip() for line in str(text or "").splitlines() if line.strip())
    if not single:
        return "(none)"
    if len(single) <= max_chars:
        return single
    if max_chars <= 3:
        return single[:max_chars]
    return single[: max_chars - 3].rstrip(" ,;") + "..."


def _extract_best_json_object(raw: str) -> tuple[dict | None, str]:
    text = str(raw or "").strip()
    if not text:
        return None, "empty"

    candidates: list[tuple[int, dict]] = []
    for candidate in _iter_balanced_json_objects(text):
        parsed = _parse_json_candidate(candidate)
        if not isinstance(parsed, dict):
            continue
        score = _score_autonomous_candidate(parsed)
        candidates.append((score, parsed))

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1], "ok"
    return None, "invalid"


def _parse_l2_json_output(raw: str, max_dialogue_len: int) -> dict | None:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None

    try:
        data = json.loads(match.group())
        dialogue = str(data.get("dialogue") or "").strip()
        emotion = str(data.get("emotion") or "").strip().lower()
        action_trigger = data.get("action_trigger")
        if action_trigger == "":
            action_trigger = None

        assert 0 < len(dialogue) <= max_dialogue_len
        assert emotion
        assert _is_valid_action_trigger(action_trigger)
        return {
            "dialogue": dialogue,
            "emotion": emotion,
            "action_trigger": action_trigger,
        }
    except (json.JSONDecodeError, AssertionError, TypeError):
        return None


def _is_valid_action_trigger(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    return bool(_ACTION_TRIGGER_PATTERN.fullmatch(value.strip().lower()))


def _iter_balanced_json_objects(text: str) -> list[str]:
    objects: list[str] = []
    n = len(text)
    for start in range(n):
        if text[start] != "{":
            continue
        depth = 0
        in_string = False
        escape = False
        for end in range(start, n):
            ch = text[end]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    objects.append(text[start:end + 1])
                    break
    return objects


def _parse_json_candidate(candidate: str) -> dict | None:
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        repaired = re.sub(r",(\s*[}\]])", r"\1", candidate)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            return None


def _score_autonomous_candidate(data: dict) -> int:
    score = 0
    for key in ("behavior_type", "intent_mode", "anim_tags", "movement", "action_desc", "dialogue"):
        if key in data:
            score += 10
    if isinstance(data.get("behavior_type"), str) and data.get("behavior_type", "").strip():
        score += 10
    if data.get("intent_mode") in INTENT_MODES:
        score += 20
    if isinstance(data.get("anim_tags"), list):
        score += 5
    if data.get("movement") in {"stay", "wander"}:
        score += 5
    if isinstance(data.get("prompt_request"), dict):
        score += 3
    return score


def _derive_intent_mode(
    behavior_type: str | None,
    movement: str | None,
    anim_tags: list | None,
) -> str:
    text = str(behavior_type or "").strip().lower()
    tags = [str(tag).strip().lower() for tag in (anim_tags or [])]
    tag_text = " ".join(tags)

    if text in {"sleep", "dream"} or "sleep" in tag_text or text.startswith("sleep"):
        return "sleep"
    if text in {"beg_food"} or "beg" in text or "food" in tag_text:
        return "request"
    if text in {"groom"} or "groom" in text or "clean" in tag_text:
        return "self_care"
    if text in {"show_off"} or "show" in text:
        return "showcase"
    if text in {"play"} or "play" in text or "excited" in tag_text:
        return "playful"
    if text in {"explore", "wander"} or movement == "wander" or "curious" in tag_text or "explore" in tag_text:
        return "curious"
    if text in {"rest", "idle_normal"} or "rest" in text or "idle" in tag_text or "calm" in tag_text:
        return "rest" if "rest" in text else "idle"
    return "social"


def normalize_emotion(emotion: str | None) -> str:
    if not emotion:
        return "neutral"
    return EMOTION_MAP.get(str(emotion).strip().lower(), "neutral")
