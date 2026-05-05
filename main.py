import sys
import json
import time as _time
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore    import QTimer, QPoint, QObject, pyqtSignal, QProcess
from PyQt6.QtGui     import QGuiApplication

from config import (
    ANIM_INDEX_PATH, REF_IMAGE_PATH, GEN_ANIM_DIR, DECAY_PERSIST_EVERY,
    COMFYUI_URL, THRESHOLD_CHECK_MS, COMFYUI_API_WORKFLOW_PATH,
    HUNGRY_REMIND_S, DROWSY_REMIND_S,
    ENERGY_SLEEP_RECOVERY,
    ENERGY_DROWSY_THRESHOLD,
    HUNGER_DECAY, CLEANLINESS_DECAY,
    WALK_MIN_DIST_PX, CURRENT_PET_ID, CURRENT_PET_LABEL, CURRENT_PET_PROFILE, CURRENT_SUBJECT_KIND,
)
from core.event_bus            import EventBus
from core.movement_sm          import MovementStateMachine, MoveState
from core.event_arbiter        import EventArbiter
from core.event_request        import EventPriority, EventSource
from core.interaction_capabilities import get_interaction_availability, interaction_label
from core.request_factory      import build_user_request, build_threshold_request, build_ai_request
from core.pet_data             import PetData, ThresholdEvent
from core.interaction_map      import InteractionMap
from core.character_package_manager import list_character_packages, save_runtime_asset_selection
from data.db_manager           import DBManager
from animation.effect_profiles import infer_behavior_family
from animation.library_manager import LibraryManager
from animation.comfyui_client  import ComfyUIClient
from animation.prompt_builder  import build_prompt_bundle_from_request
from ai.intent_engine          import IntentEngine
from ai.memory_manager         import MemoryManager
from ai.pet_brain              import PetBrain
from ai.prompt_templates       import normalize_emotion
from ui.chat_bubble            import ChatBubble
from ui.gallery_panel          import GalleryPanel
from ui.pet_window             import PetWindow
from ui.event_anim_layer       import EventAnimLayer
from ui.tray_icon              import TrayIcon
from ui.stats_panel            import StatsPanel


class _GenerationBridge(QObject):
    finished = pyqtSignal(object, object)


_DROWSY_AUTO_SLEEP_DELAY_S = 5.0
_AI_BEHAVIOR_VOCAB = [
    "inspect_corner", "sniff_floor", "peek_window", "look_outside", "investigate_sound",
    "paw_bounce", "play_ball", "chase_tail", "zoomies", "swim", "water_play", "splash",
    "cozy_pause", "pause_and_settle", "curl_up", "stretch_yawn", "doze",
    "groom_paw", "scratch_ear", "shake_off", "tidy_fur",
    "greet_user", "nuzzle", "attention_beg", "cuddle_pose",
    "beg_food", "bowl_check", "whine_wait",
    "show_off", "proud_pose",
    "playful", "curious", "rest", "self_care", "social", "showcase", "request",
]

_AUTONOMOUS_BEHAVIOR_TEMPLATES = [
    {"behavior_type": "bowl_check", "intent_mode": "request", "min_hunger_below": 45, "priority_bonus": 0.6, "reason": "food curiosity fits when hunger is low"},
    {"behavior_type": "beg_food", "intent_mode": "request", "min_hunger_below": 35, "priority_bonus": 1.6, "reason": "open food requesting fits when hunger is very low"},
    {"behavior_type": "groom_paw", "intent_mode": "self_care", "min_clean_below": 60, "reason": "self-grooming fits when cleanliness is slipping"},
    {"behavior_type": "shake_off", "intent_mode": "self_care", "min_clean_below": 48, "reason": "a quick shake-off fits a messy feeling"},
    {"behavior_type": "cozy_nap", "intent_mode": "rest", "priority_bonus": 0.4, "max_energy_below": 45, "reason": "resting fits when energy is low"},
    {"behavior_type": "stretch_yawn", "intent_mode": "rest", "max_energy_below": 58, "reason": "a stretch and yawn fits early tiredness"},
    {"behavior_type": "pause_and_settle", "intent_mode": "rest", "max_energy_below": 52, "reason": "settling down fits a quiet low-energy moment"},
    {"behavior_type": "greet_user", "intent_mode": "social", "priority_bonus": 0.5, "min_mood_above": 52, "reason": "a friendly greeting fits a positive mood"},
    {"behavior_type": "peek_window", "intent_mode": "curious", "priority_bonus": 1.0, "min_curious_above": 0.55, "reason": "looking outside fits a curious pet"},
    {"behavior_type": "investigate_sound", "intent_mode": "curious", "priority_bonus": 0.8, "min_curious_above": 0.5, "reason": "listening for a sound fits alert curiosity"},
    {"behavior_type": "sniff_floor", "intent_mode": "curious", "min_curious_above": 0.45, "reason": "sniffing around fits ambient exploration"},
    {"behavior_type": "inspect_corner", "intent_mode": "curious", "min_curious_above": 0.45, "reason": "checking a corner fits exploratory attention"},
    {"behavior_type": "play_ball", "intent_mode": "playful", "priority_bonus": 0.5, "min_energy_above": 58, "min_mood_above": 52, "reason": "fetch-style play fits high energy"},
    {"behavior_type": "chase_tail", "intent_mode": "playful", "priority_bonus": 0.7, "min_energy_above": 64, "min_mood_above": 56, "reason": "tail-chasing fits extra playful energy"},
    {"behavior_type": "paw_bounce", "intent_mode": "playful", "priority_bonus": 0.4, "min_energy_above": 50, "min_mood_above": 48, "reason": "a playful bounce fits a lively mood"},
    {"behavior_type": "show_off", "intent_mode": "showcase", "priority_bonus": 0.6, "min_mood_above": 62, "reason": "showing off fits when mood is especially high"},
]


def _should_dispatch_pending_generation(
    gen_id: str | None,
    comfy_busy: bool,
    generation_jobs: dict[str, dict],
) -> bool:
    if gen_id is None:
        return False
    if comfy_busy:
        return False
    return gen_id not in generation_jobs


def _is_generation_job_inflight(
    gen_id: str | None,
    generation_jobs: dict[str, dict],
) -> bool:
    if gen_id is None:
        return False
    return gen_id in generation_jobs


def _build_generated_animation_tags(req) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()

    for raw in getattr(req, "anim_tags", []) or []:
        text = str(raw or "").strip().lower().replace(" ", "_").replace("-", "_")
        if not text or text in seen:
            continue
        seen.add(text)
        tags.append(text)

    return tags


def _build_generated_output_basename(req) -> str:
    tags = _build_generated_animation_tags(req)
    prompt_request = getattr(req, "prompt_request", {}) or {}
    pet_key = str(
        prompt_request.get("pet_id")
        or prompt_request.get("pet_profile")
        or CURRENT_PET_ID
    ).strip().lower()
    safe_pet = pet_key.replace(" ", "_").replace("-", "_") or "pet"
    behavior_type = str(getattr(req, "behavior_type", "") or "").strip().lower().replace(" ", "_").replace("-", "_")
    safe_tags = [
        str(tag).strip().lower().replace(" ", "_").replace("-", "_")
        for tag in tags
        if str(tag).strip()
    ]
    if behavior_type and behavior_type not in safe_tags:
        safe_tags.insert(0, behavior_type)
    safe_tags = safe_tags[:4]
    if not safe_tags:
        safe_tags = ["generated"]
    return "_".join([safe_pet, *safe_tags])


def _build_generated_animation_entry_id(media_path: str | None) -> str:
    stem = Path(str(media_path or "")).stem.strip().lower()
    if not stem:
        return f"generated_{int(_time.time())}"
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in stem)
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe.strip("_") or f"generated_{int(_time.time())}"


def _normalize_generated_media_path(media_path: str | None) -> str:
    text = str(media_path or "").strip()
    if not text:
        return ""
    path = Path(text)
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except Exception:
        return path.as_posix()


def _build_generation_dialogue(req) -> str:
    dialogue = str(getattr(req, "dialogue", "") or "").strip()
    action_desc = str(getattr(req, "action_desc", "") or "").strip()
    if dialogue and action_desc:
        return f"{dialogue}\n动作: {action_desc}"
    if action_desc:
        return f"我想试试这个动作：{action_desc}"
    if dialogue:
        return dialogue
    return "我在想一个新动作……"


def _build_discovery_dialogue(req, animation_record: dict, is_new_discovery: bool) -> str:
    title = str(animation_record.get("display_name") or animation_record.get("behavior_type") or "新动作")
    body = _build_generation_dialogue(req)
    if is_new_discovery:
        return f"发现新动画！{title}\n{body}"
    return body


def _build_generated_animation_record(
    req,
    media_path: str,
    generated_tags: list[str],
    prompt_request: dict,
    prompts,
    generated_video_fps: int,
) -> dict:
    stored_media_path = _normalize_generated_media_path(media_path)
    pet_id = str(prompt_request.get("pet_id") or CURRENT_PET_ID).strip().lower() or CURRENT_PET_ID
    pet_profile = str(prompt_request.get("pet_profile") or CURRENT_PET_PROFILE).strip().lower() or CURRENT_PET_PROFILE
    pet_label = str(prompt_request.get("pet_label") or CURRENT_PET_LABEL).strip() or CURRENT_PET_LABEL
    return {
        "id": _build_generated_animation_entry_id(stored_media_path or media_path),
        "file": stored_media_path or media_path,
        "tags": list(generated_tags),
        "loop": req.anim_loop,
        "fps": generated_video_fps,
        "source": "generated",
        "blocked": False,
        "rating": 0,
        "rarity": "rare",
        "behavior_type": req.behavior_type,
        "pet_id": pet_id,
        "pet_profile": pet_profile,
        "pet_label": pet_label,
        "generated_prompt": {
            "prompt_request": prompt_request,
            "image_prompt": prompts.image_prompt,
            "image_negative_prompt": prompts.image_negative_prompt,
            "video_prompt": prompts.video_prompt,
            "video_negative_prompt": prompts.video_negative_prompt,
        },
    }


def _register_generated_animation(
    lib: LibraryManager,
    req,
    media_path: str,
    generated_tags: list[str],
    prompt_request: dict,
    prompts,
    generated_video_fps: int,
) -> tuple[dict, bool]:
    record = _build_generated_animation_record(
        req,
        media_path,
        generated_tags,
        prompt_request,
        prompts,
        generated_video_fps,
    )
    saved_record = lib.add(record)
    is_new_discovery = lib.mark_discovered(saved_record["id"])
    entries = {
        entry["id"]: entry
        for entry in lib.get_collection_entries(include_blocked=True)
    }
    return entries.get(saved_record["id"], saved_record), is_new_discovery


def _intent_mode_for_quick_trigger(trigger: str) -> str:
    trigger = str(trigger or "").strip().lower()
    parts = [part for part in trigger.split("_") if part]
    family = infer_behavior_family({"behavior_type": trigger, "tags": [trigger, *parts]})
    family_to_intent = {
        "explore/show_off": "curious",
        "play/exercise": "playful",
        "sleep/rest": "sleep" if trigger in {"sleep", "dream", "doze"} else "rest",
        "bath/groom": "self_care",
        "beg/complain": "request",
        "comfort/social": "social",
        "idle/emote": "social",
    }
    if trigger in {"wander", "explore"}:
        return "curious"
    if trigger == "show_off":
        return "showcase"
    if trigger == "rest":
        return "rest"
    if trigger == "sleep":
        return "sleep"
    if family in family_to_intent:
        return family_to_intent[family]
    return "social"


def _refine_followup_trigger(trigger: str, context_text: str | None = None) -> str:
    normalized = str(trigger or "").strip().lower()
    text = str(context_text or "").lower()
    if normalized == "play":
        if any(keyword in text for keyword in ("游泳", "玩水", "下水", "swim", "water", "pool")):
            return "swim"
        if any(keyword in text for keyword in ("球", "ball", "飞盘", "frisbee")):
            return "play_ball"
    return normalized


def _build_followup_trigger_spec(trigger: str, emotion: str, event_type: str) -> dict:
    readable_trigger = trigger.replace("_", " ")
    specs = {
        "swim": {
            "intent_mode": "playful",
            "anim_tags": ["swim", "water_play", "splash", "playful", emotion],
            "movement": "stay",
            "action_desc": "paddles happily in the water with playful splashes",
            "prompt_request": {
                "tags": ["swim", "water_play", "splash", "playful", emotion],
                "behavior_type": "swim",
                "action_desc": "paddles happily in the water with playful splashes",
                "pose_focus": [f"{CURRENT_SUBJECT_KIND} paddling on water", "front paws splashing lightly"],
                "motion_focus": ["looping paddle motion", "small water splash rhythm"],
                "video_overrides": ["water-play action", "full body visible while paddling"],
                "negative_overrides": ["no beach props", "no human swimmer", "no pool toys unless requested"],
            },
        },
        "play_ball": {
            "intent_mode": "playful",
            "anim_tags": ["play_ball", "fetch", "playful", emotion],
            "movement": "stay",
            "action_desc": "gets excited to chase and play with a ball",
            "prompt_request": {},
        },
    }
    default_spec = {
        "intent_mode": _intent_mode_for_quick_trigger(trigger),
        "anim_tags": [trigger, emotion],
        "movement": "wander" if trigger in {"wander", "explore"} else "stay",
        "action_desc": f"reacts to {event_type} and follows through with a {readable_trigger} behavior",
        "prompt_request": {},
    }
    return specs.get(trigger, default_spec)


def _refine_followup_trigger_richer(trigger: str, context_text: str | None = None) -> str:
    normalized = str(trigger or "").strip().lower()
    text = str(context_text or "").lower()
    if normalized == "play":
        if any(keyword in text for keyword in ("swim", "water", "pool", "游泳", "玩水", "下水")):
            return "swim"
        if any(keyword in text for keyword in ("ball", "frisbee", "fetch", "球", "飞盘")):
            return "play_ball"
        if any(keyword in text for keyword in ("tail", "chase_tail", "spin", "zoomies", "尾巴", "转圈")):
            return "chase_tail"
    if normalized in {"explore", "wander", "curious"}:
        if any(keyword in text for keyword in ("window", "outside", "bird", "rain", "peek", "窗外", "窗边")):
            return "peek_window"
        if any(keyword in text for keyword in ("sound", "noise", "listen", "声音", "动静")):
            return "investigate_sound"
    if normalized in {"rest", "sleep", "dream"}:
        if any(keyword in text for keyword in ("blanket", "bed", "nap", "cozy", "sleep", "被窝", "床上", "睡觉")):
            return "cozy_nap"
        if any(keyword in text for keyword in ("stretch", "yawn", "伸懒腰", "哈欠")):
            return "stretch_yawn"
    if normalized in {"social", "show_off"}:
        if any(keyword in text for keyword in ("hello", "hi", "welcome", "greet", "打招呼", "欢迎")):
            return "greet_user"
    if normalized in {"request", "beg_food", "play"}:
        if any(keyword in text for keyword in ("bowl", "food", "snack", "eat", "饭", "碗", "零食")):
            return "bowl_check"
    return normalized


def _build_followup_trigger_spec_richer(trigger: str, emotion: str, event_type: str) -> dict:
    base = _build_followup_trigger_spec(trigger, emotion, event_type)
    richer_specs = {
        "play_ball": {
            "intent_mode": "playful",
            "anim_tags": ["play_ball", "fetch", "playful", emotion],
            "movement": "stay",
            "action_desc": "gets excited to chase and play with a ball",
            "prompt_request": {
                "tags": ["play_ball", "fetch", "playful", emotion],
                "behavior_type": "play_ball",
                "action_desc": "gets excited to chase and play with a ball",
                "motion_focus": ["short playful pounce", "ball-chasing readiness"],
            },
        },
        "chase_tail": {
            "intent_mode": "playful",
            "anim_tags": ["chase_tail", "spin", "playful", emotion],
            "movement": "stay",
            "action_desc": "spins playfully in place while chasing its own tail",
            "prompt_request": {
                "tags": ["chase_tail", "spin", "playful", emotion],
                "behavior_type": "chase_tail",
                "action_desc": "spins playfully in place while chasing its own tail",
                "motion_focus": ["tight circular turn", "tail-chasing loop"],
            },
        },
        "peek_window": {
            "intent_mode": "curious",
            "anim_tags": ["peek_window", "look_outside", "curious", emotion],
            "movement": "stay",
            "action_desc": "stands alert and peers outward as if watching something beyond the window",
            "prompt_request": {
                "tags": ["peek_window", "look_outside", "curious", emotion],
                "behavior_type": "peek_window",
                "action_desc": "stands alert and peers outward as if watching something beyond the window",
                "pose_focus": ["head lifted toward a window", "alert listening posture"],
                "motion_focus": ["small head tilt", "attentive gaze shifts"],
            },
        },
        "investigate_sound": {
            "intent_mode": "curious",
            "anim_tags": ["investigate_sound", "listen", "curious", emotion],
            "movement": "stay",
            "action_desc": "pauses and listens closely, reacting to an interesting sound",
            "prompt_request": {
                "tags": ["investigate_sound", "listen", "curious", emotion],
                "behavior_type": "investigate_sound",
                "action_desc": "pauses and listens closely, reacting to an interesting sound",
                "motion_focus": ["ear twitch", "small alert head turn"],
            },
        },
        "cozy_nap": {
            "intent_mode": "rest",
            "anim_tags": ["cozy_nap", "rest", "calm", emotion],
            "movement": "stay",
            "action_desc": "settles down into a cozy nap posture and relaxes quietly",
            "prompt_request": {
                "tags": ["cozy_nap", "rest", "calm", emotion],
                "behavior_type": "cozy_nap",
                "action_desc": "settles down into a cozy nap posture and relaxes quietly",
                "pose_focus": ["curled resting pose", "soft sleepy posture"],
                "motion_focus": ["gentle breathing loop", "small sleepy blink"],
            },
        },
        "stretch_yawn": {
            "intent_mode": "rest",
            "anim_tags": ["stretch_yawn", "rest", "sleepy", emotion],
            "movement": "stay",
            "action_desc": "stretches its body and lets out a sleepy yawn",
            "prompt_request": {
                "tags": ["stretch_yawn", "rest", "sleepy", emotion],
                "behavior_type": "stretch_yawn",
                "action_desc": "stretches its body and lets out a sleepy yawn",
                "motion_focus": ["front-leg stretch", "wide yawn loop"],
            },
        },
        "greet_user": {
            "intent_mode": "social",
            "anim_tags": ["greet_user", "social", "happy", emotion],
            "movement": "stay",
            "action_desc": "greets the user warmly with an eager, friendly posture",
            "prompt_request": {
                "tags": ["greet_user", "social", "happy", emotion],
                "behavior_type": "greet_user",
                "action_desc": "greets the user warmly with an eager, friendly posture",
                "motion_focus": ["friendly bounce", "small tail-led greeting"],
            },
        },
        "bowl_check": {
            "intent_mode": "request",
            "anim_tags": ["bowl_check", "request", "hungry", emotion],
            "movement": "stay",
            "action_desc": "checks the food bowl expectantly and waits for something tasty",
            "prompt_request": {
                "tags": ["bowl_check", "request", "hungry", emotion],
                "behavior_type": "bowl_check",
                "action_desc": "checks the food bowl expectantly and waits for something tasty",
                "pose_focus": ["looking down toward a bowl", "hopeful waiting posture"],
                "motion_focus": ["small hopeful glance", "short eager head dip"],
            },
        },
    }
    return richer_specs.get(trigger, base)


def _build_ai_available_tags(lib: LibraryManager) -> list[str]:
    tags = {
        str(tag).strip().lower().replace(" ", "_").replace("-", "_")
        for tag in lib.all_tags()
        if str(tag).strip()
    }
    tags.update(_AI_BEHAVIOR_VOCAB)
    return sorted(tags)


def _build_autonomous_behavior_suggestions(
    pet_state: dict,
    recent_behavior_history: list[str] | None = None,
) -> list[dict]:
    recent = {
        str(item).strip().lower().replace(" ", "_").replace("-", "_")
        for item in (recent_behavior_history or [])[:3]
        if str(item).strip()
    }
    personality = pet_state.get("personality", {}) if isinstance(pet_state.get("personality"), dict) else {}
    hunger = float(pet_state.get("hunger", 50) or 50)
    energy = float(pet_state.get("energy", 50) or 50)
    mood = float(pet_state.get("mood", 50) or 50)
    cleanliness = float(pet_state.get("cleanliness", 50) or 50)
    curious = float(personality.get("curious", 0.5) or 0.5)

    candidates: list[tuple[float, int, dict]] = []
    for index, template in enumerate(_AUTONOMOUS_BEHAVIOR_TEMPLATES):
        behavior = template["behavior_type"]
        if behavior in recent:
            continue
        score = 0.0
        if "min_hunger_below" in template and hunger >= float(template["min_hunger_below"]):
            continue
        if "min_hunger_below" in template:
            score += 3.0 + max(0.0, float(template["min_hunger_below"]) - hunger) / 12.0
        if "max_energy_below" in template and energy >= float(template["max_energy_below"]):
            continue
        if "max_energy_below" in template:
            score += 3.0 + max(0.0, float(template["max_energy_below"]) - energy) / 12.0
        if "min_energy_above" in template and energy <= float(template["min_energy_above"]):
            continue
        if "min_energy_above" in template:
            score += 3.2 + max(0.0, energy - float(template["min_energy_above"])) / 12.0
        if "min_mood_above" in template and mood <= float(template["min_mood_above"]):
            continue
        if "min_mood_above" in template:
            score += 2.2 + max(0.0, mood - float(template["min_mood_above"])) / 18.0
        if "min_clean_below" in template and cleanliness >= float(template["min_clean_below"]):
            continue
        if "min_clean_below" in template:
            score += 2.8 + max(0.0, float(template["min_clean_below"]) - cleanliness) / 12.0
        if "min_curious_above" in template and curious <= float(template["min_curious_above"]):
            continue
        if "min_curious_above" in template:
            threshold = float(template["min_curious_above"])
            score += 1.8 + max(0.0, curious - threshold) * 6.0 + threshold * 2.0
        score += float(template.get("priority_bonus", 0.0) or 0.0)
        if score == 0.0:
            score = 1.0
        candidates.append((score, -index, template))

    suggestions: list[dict] = []
    seen: set[str] = set()
    family_counts: dict[str, int] = {}
    overflow: list[dict] = []
    for _, _, template in sorted(candidates, reverse=True):
        behavior = template["behavior_type"]
        if behavior in seen:
            continue
        entry = {
            "behavior_type": behavior,
            "intent_mode": template["intent_mode"],
            "reason": template["reason"],
        }
        family = str(template["intent_mode"] or "")
        if family_counts.get(family, 0) >= 2:
            overflow.append(entry)
            continue
        seen.add(behavior)
        family_counts[family] = family_counts.get(family, 0) + 1
        suggestions.append(entry)
        if len(suggestions) >= 6:
            break

    if len(suggestions) < 6:
        for entry in overflow:
            if entry["behavior_type"] in seen:
                continue
            seen.add(entry["behavior_type"])
            suggestions.append(entry)
            if len(suggestions) >= 6:
                break

    if not suggestions:
        for behavior, intent_mode, reason in [
            ("peek_window", "curious", "a safe fallback for ambient curiosity"),
            ("cozy_nap", "rest", "a safe fallback for quiet downtime"),
            ("greet_user", "social", "a safe fallback for light social behavior"),
        ]:
            if behavior in recent:
                continue
            suggestions.append({
                "behavior_type": behavior,
                "intent_mode": intent_mode,
                "reason": reason,
            })
    return suggestions[:6]


def _build_l2_followup_decision(event_type: str, result: dict, context_text: str | None = None) -> dict | None:
    trigger = str(result.get("action_trigger") or "").strip()
    if not trigger or trigger == "idle_normal":
        return None

    emotion = normalize_emotion(result.get("emotion"))
    trigger = _refine_followup_trigger_richer(trigger, context_text=context_text)
    spec = _build_followup_trigger_spec_richer(trigger, emotion, event_type)
    return {
        "intent_mode": spec["intent_mode"],
        "behavior_type": trigger,
        "anim_tags": spec["anim_tags"],
        "movement": spec["movement"],
        "dialogue": "",
        "action_desc": spec["action_desc"],
        "emotion": {"primary": emotion},
        # User-triggered follow-up should survive same-level warning notifies such as DROWSY,
        # but still yield to critical state changes and new direct user interactions.
        "priority_override": int(EventPriority.THRESHOLD_WARNING),
        "prompt_request": spec["prompt_request"],
    }


def _build_quick_reply_followup_decision(event_type: str, result: dict) -> dict | None:
    return _build_l2_followup_decision(event_type, result)


def _should_pause_autonomous_for_chat(chat_visible: bool, chat_inflight: bool) -> bool:
    return bool(chat_visible or chat_inflight)


def _request_has_real_animation(req) -> bool:
    return bool(
        getattr(req, "anim_id", None)
        or list(getattr(req, "anim_tags", []) or [])
        or getattr(req, "generate_if_missing", False)
    )


def _update_drowsy_auto_sleep_state(
    state: dict,
    req,
    accepted: bool,
    now: float | None = None,
) -> None:
    if not accepted or req is None:
        return

    now = _time.monotonic() if now is None else now
    event_type = str(getattr(req, "event_type", "") or "")
    if event_type == "DROWSY":
        state["active"] = True
        state["deadline"] = now + _DROWSY_AUTO_SLEEP_DELAY_S
        state["source_event"] = event_type
        return

    if _request_has_real_animation(req):
        state["active"] = False
        state["deadline"] = 0.0
        state["source_event"] = ""


def _should_trigger_drowsy_auto_sleep(
    state: dict,
    current_event_type: str | None,
    energy: float,
    is_sleeping: bool,
    now: float | None = None,
) -> bool:
    if not state.get("active"):
        return False

    now = _time.monotonic() if now is None else now
    if is_sleeping or energy > ENERGY_DROWSY_THRESHOLD:
        return False
    if now < float(state.get("deadline") or 0.0):
        return False
    return current_event_type in {None, "", "DROWSY"}


def _validate_chat_submission(
    user_text: str,
    llm_enabled: bool,
    llm_busy: bool,
    has_active_event: bool,
) -> str | None:
    text = str(user_text or "").strip()
    if not text:
        return "empty"
    if not llm_enabled:
        return "disabled"
    if llm_busy:
        return "busy"
    if has_active_event:
        return "event_active"
    return None


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    screen = QGuiApplication.primaryScreen().availableGeometry()

    # ── 初始化 ────────────────────────────────────────────
    bus     = EventBus.instance()
    db      = DBManager.instance()
    pet     = PetData(db, asset_id=CURRENT_PET_ID)
    lib     = LibraryManager(str(ANIM_INDEX_PATH))
    imap    = InteractionMap()
    mem     = MemoryManager(db, asset_id=CURRENT_PET_ID)
    move_sm = MovementStateMachine()
    llm     = IntentEngine()
    llm.load_model()
    brain   = PetBrain(llm, mem)
    llm_state = {"enabled": llm.enabled}
    ai_diag = {
        "last_autonomous_block_log_at": 0.0,
        "last_autonomous_disabled_log_at": 0.0,
        "last_generation_busy_log_at": 0.0,
        "last_ai_status_reconcile_log_at": 0.0,
    }
    ai_runtime = {
        "last_ai_event_active": False,
    }
    drowsy_auto_sleep = {
        "active": False,
        "deadline": 0.0,
        "source_event": "",
    }
    generation_bridge = _GenerationBridge()
    generation_jobs: dict[str, dict] = {}
    imported_generated = lib.sync_generated_assets(GEN_ANIM_DIR)
    if imported_generated:
        print(
            f"[AI-DIAG][Main] imported loose generated assets count={len(imported_generated)}",
            flush=True,
        )

    # ComfyUI 客户端
    workflow_template = (
        json.loads(COMFYUI_API_WORKFLOW_PATH.read_text(encoding="utf-8"))
        if COMFYUI_API_WORKFLOW_PATH.exists()
        else {}
    )
    comfy = ComfyUIClient(
        base_url=COMFYUI_URL,
        workflow_template=workflow_template,
        ref_image_path=str(REF_IMAGE_PATH),
        output_dir=str(GEN_ANIM_DIR)
    )
    generated_video_fps = int(
        workflow_template.get("496", {})
        .get("inputs", {})
        .get("frame_rate", 16)
    )
    print(
        f"[AI-DIAG][Main] ComfyUI setup configured={comfy.is_configured()} "
        f"workflow_nodes={comfy.get_workflow_node_count()} ref_image_exists={REF_IMAGE_PATH.exists()} "
        f"url={COMFYUI_URL}",
        flush=True,
    )

    # UI：PetWindow → EventAnimLayer → EventArbiter
    win = PetWindow(move_sm, bus, lib)
    event_anim = EventAnimLayer(win._body)
    win.set_event_anim_layer(event_anim)

    def update_stats():
        stats_panel.update_stats(
            pet.hunger,
            pet.energy,
            pet.cleanliness,
            pet.mood,
            exp=pet.exp,
            intimacy=pet.intimacy,
            level=pet.level,
            growth_stage=pet.growth_stage,
        )

    arbiter = EventArbiter(
        move_sm, event_anim, pet, lib,
        on_dialogue=win.show_dialogue,
        on_stats_update=update_stats,
    )

    available_character_packages = list_character_packages()
    interaction_availability = get_interaction_availability(lib)
    current_character_meta = next(
        (item for item in available_character_packages if item.get("asset_id") == CURRENT_PET_ID),
        {"asset_id": CURRENT_PET_ID, "label": CURRENT_PET_LABEL},
    )

    def toggle_llm_mode(enabled: bool) -> bool:
        started_at = _time.perf_counter()
        print(
            f"[AI-DIAG][Main] toggle_llm_mode requested enabled={enabled} "
            f"current_enabled={llm_state['enabled']}",
            flush=True,
        )
        actual = llm.set_enabled(enabled, persist=True)
        llm_state["enabled"] = actual
        print(
            f"[AI-DIAG][Main] toggle_llm_mode completed requested={enabled} actual={actual} "
            f"dt_ms={(_time.perf_counter() - started_at) * 1000:.1f}",
            flush=True,
        )
        if enabled and actual:
            set_ai_status("空闲", "idle")
            win.show_dialogue("自主AI模式已开启")
        elif enabled and not actual:
            set_ai_status("开启失败", "error")
            win.show_dialogue("自主AI模式开启失败")
        else:
            set_ai_status("已关闭", "off")
            win.show_dialogue("自主AI模式已关闭")
        return actual

    def request_manual_ai() -> bool:
        if not llm_state["enabled"]:
            set_ai_status("已关闭", "off")
            win.show_dialogue("请先开启自主AI模式")
            return False
        if arbiter.current:
            set_ai_status("事件进行中", "working")
            win.show_dialogue("等我先把当前动作做完，再认真想一下")
            return False
        if llm.is_busy():
            set_ai_status("思考中", "working")
            win.show_dialogue("我已经在思考啦，稍等我一下")
            return False

        memories = mem.get_recent(limit=5)
        print(
            f"[AI-DIAG][Main] manual_ai_request fired hunger={pet.hunger:.1f} mood={pet.mood:.1f} "
            f"energy={pet.energy:.1f} memories={len(memories)}",
            flush=True,
        )
        set_ai_status("思考中", "working")
        win.show_dialogue("我来主动想想接下来做什么……", 2200)
        pet_context = pet.to_context_dict()
        recent_ai_behaviors = mem.get_recent_ai_behaviors(limit=3)
        llm.request(
            pet_state=pet_context,
            memories=memories,
            available_tags=_build_ai_available_tags(lib),
            suggested_behaviors=_build_autonomous_behavior_suggestions(
                pet_context,
                recent_behavior_history=recent_ai_behaviors,
            ),
            rejection_context=arbiter.get_ai_rejection_context(),
            last_behavior_hint=mem.get_last_ai_behavior(),
            interaction_summary=mem.get_recent_interaction_summary(limit=3),
            interaction_stats=mem.get_recent_interaction_stats(limit=12),
            emotion_history=mem.get_recent_emotion_history(limit=4),
            recent_behavior_history=recent_ai_behaviors,
            manual_trigger=True,
        )
        return True

    chat_runtime = {
        "visible": False,
        "inflight": False,
    }
    chat_ui = {"widget": None}
    gallery_ui = {"widget": None}

    def _restart_application() -> None:
        started = QProcess.startDetached(sys.executable, sys.argv)
        print(
            f"[AI-DIAG][Main] restart_application started={started} argv={sys.argv}",
            flush=True,
        )
        app.quit()

    def switch_character_package(asset_id: str, source: str = "unknown") -> None:
        normalized_asset_id = str(asset_id or "").strip().lower().replace(" ", "_").replace("-", "_")
        if not normalized_asset_id:
            return
        target = next(
            (item for item in available_character_packages if item.get("asset_id") == normalized_asset_id),
            None,
        )
        if target is None:
            win.show_dialogue(f"没有找到角色包：{normalized_asset_id}", 2600)
            return
        if normalized_asset_id == CURRENT_PET_ID:
            win.show_dialogue(f"当前已经是 {target.get('label') or normalized_asset_id} 啦。", 2200)
            return
        save_runtime_asset_selection(normalized_asset_id)
        stats_panel.set_current_character(normalized_asset_id, str(target.get("label") or normalized_asset_id))
        print(
            f"[AI-DIAG][Main] character package switched asset_id={normalized_asset_id} source={source}",
            flush=True,
        )
        win.show_dialogue(f"切换到 {target.get('label') or normalized_asset_id}，我现在重启一下。", 2600)
        QTimer.singleShot(300, _restart_application)

    def open_gallery_panel(_data: dict | None = None) -> None:
        widget = gallery_ui["widget"]
        if widget is None:
            return
        widget.show_panel()

    def set_follow_mode(enabled: bool, source: str = "unknown") -> None:
        next_enabled = bool(enabled)
        move_sm.set_follow_mouse(next_enabled)
        stats_panel.set_follow_mode(next_enabled)
        print(
            f"[AI-DIAG][Main] follow_mode changed enabled={next_enabled} source={source}",
            flush=True,
        )
        win.show_dialogue(
            "我先不追着你跑啦。" if not next_enabled else "来呀，我陪你玩闹一会儿！",
            1800,
        )

    stats_panel = StatsPanel(
        on_interaction=lambda data: bus.emit("interaction", data),
        on_toggle_follow_mouse=lambda enabled: set_follow_mode(enabled, source="stats_panel"),
        on_toggle_llm_mode=toggle_llm_mode,
        on_manual_ai_request=request_manual_ai,
        on_open_chat=lambda: bus.emit("chat", {"source": "stats_panel"}),
        on_open_gallery=lambda: bus.emit("gallery", {"source": "stats_panel"}),
        on_switch_character=lambda asset_id: switch_character_package(asset_id, source="stats_panel"),
        character_options=available_character_packages,
        current_character_asset_id=CURRENT_PET_ID,
        current_character_label=str(current_character_meta.get("label") or CURRENT_PET_LABEL),
        on_layout_changed=lambda: chat_ui["widget"].refresh_position() if chat_ui["widget"] else None,
        llm_mode_enabled=llm_state["enabled"],
    )
    stats_panel.update_stats(
        pet.hunger,
        pet.energy,
        pet.cleanliness,
        pet.mood,
        exp=pet.exp,
        intimacy=pet.intimacy,
        level=pet.level,
        growth_stage=pet.growth_stage,
    )
    stats_panel.set_interaction_availability(interaction_availability)
    stats_panel.set_pet_window(win)
    stats_panel.show()
    gallery_ui["widget"] = GalleryPanel(lib, GEN_ANIM_DIR)
    tray = TrayIcon(
        app,
        on_quit=app.quit,
        character_options=available_character_packages,
        current_character_asset_id=CURRENT_PET_ID,
        on_switch_character=lambda asset_id: switch_character_package(asset_id, source="tray"),
    )

    def set_ai_status(text: str, tone: str = "idle"):
        stats_panel.set_ai_status(text, tone)

    # ── ComfyUI 生成辅助 ──────────────────────────────────

    def _format_generation_error(raw_error: str | None) -> str:
        if not raw_error:
            return "未返回可用结果"
        line = str(raw_error).strip().splitlines()[0]
        return line[:80]

    def _handle_generation_finished(gen_id: str, media_path):
        job = generation_jobs.get(gen_id)
        if not job:
            print(
                f"[AI-DIAG][Main] generation result ignored because job metadata is missing "
                f"gen_id={gen_id} path={media_path}",
                flush=True,
            )
            arbiter.on_generation_done(gen_id, media_path)
            _check_pending_generation()
            return

        req = job["req"]
        prompt_request = job["prompt_request"]
        prompts = job["prompts"]
        generated_tags = job["generated_tags"]
        print(
            f"[AI-DIAG][Main] ComfyUI generation finished gen_id={gen_id} "
            f"ok={bool(media_path)} path={media_path} "
            f"current_event={getattr(arbiter.current, 'event_type', None)} "
            f"pending_generation_id={arbiter.get_pending_generation_id()}",
            flush=True,
        )
        if media_path:
            set_ai_status("生成完成", "success")
            gallery_entry, is_new_discovery = _register_generated_animation(
                lib,
                req,
                media_path,
                generated_tags,
                prompt_request,
                prompts,
                generated_video_fps,
            )
            print(
                f"[AI-DIAG][Main] generated animation tags gen_id={gen_id} "
                f"behavior={req.behavior_type} tags={generated_tags} "
                f"rarity={gallery_entry.get('rarity')} discovered={gallery_entry.get('discovered')} "
                f"new_discovery={is_new_discovery}",
                flush=True,
            )
            if gallery_ui["widget"]:
                gallery_ui["widget"].refresh()
            arbiter.on_generation_done(gen_id, media_path)
            if llm_state["enabled"]:
                win.show_dialogue(
                    _build_discovery_dialogue(req, gallery_entry, is_new_discovery),
                    3600 if is_new_discovery else 3200,
                    emotion="excited" if is_new_discovery else None,
                )
            win.show_feedback(gallery_entry["id"])
        else:
            reason = _format_generation_error(comfy.get_last_error())
            set_ai_status("生成失败", "error")
            print(
                f"[AI-DIAG][Main] ComfyUI generation failed gen_id={gen_id} "
                f"reason={reason}",
                flush=True,
            )
            arbiter.on_generation_done(gen_id, None)
            if llm_state["enabled"]:
                win.show_dialogue(f"刚刚想做个新动作，但生成失败了：{reason}", 4500)

        generation_jobs.pop(gen_id, None)
        if (
            not result.accepted
            and not result.downgraded
            and str(result.reason).startswith("unsupported_interaction:")
        ):
            unsupported_event = str(result.reason).split(":", 1)[1]
            win.show_dialogue(f"{interaction_label(unsupported_event)}动作还没接入到这个角色包里。", 2400)

        _check_pending_generation()

    generation_bridge.finished.connect(_handle_generation_finished)

    def _check_pending_generation():
        """检查 arbiter 是否有待生成的动画，触发 ComfyUI。"""
        gen_id = arbiter.get_pending_generation_id()
        if gen_id is None:
            return
        if _is_generation_job_inflight(gen_id, generation_jobs):
            set_ai_status("生成中", "working")
            return
        if comfy.is_busy():
            set_ai_status("生成排队", "working")
            now = _time.monotonic()
            if now - ai_diag["last_generation_busy_log_at"] >= 2:
                print(
                    f"[AI-DIAG][Main] generation pending because ComfyUI is busy "
                    f"gen_id={gen_id} current_event={getattr(arbiter.current, 'event_type', None)}",
                    flush=True,
                )
                ai_diag["last_generation_busy_log_at"] = now
            return
        if not _should_dispatch_pending_generation(gen_id, comfy.is_busy(), generation_jobs):
            return
        req = arbiter.current
        if req is None:
            return
        generated_tags = _build_generated_animation_tags(req)
        reusable_match = lib.find_generated_equivalent(req.behavior_type, generated_tags)
        if reusable_match:
            discovered_now = lib.mark_discovered(reusable_match.animation_id)
            print(
                f"[AI-DIAG][Main] generation reused existing animation gen_id={gen_id} "
                f"event={req.event_type} behavior={req.behavior_type} "
                f"matched_id={reusable_match.animation_id} file={reusable_match.file_path} "
                f"discovered_now={discovered_now}",
                flush=True,
            )
            set_ai_status("澶嶇敤宸叉湁鍔ㄤ綔", "success")
            if gallery_ui["widget"] and discovered_now:
                gallery_ui["widget"].refresh()
            arbiter.on_generation_done(gen_id, reusable_match.file_path)
            if discovered_now:
                win.show_feedback(reusable_match.animation_id)
            return
        print(
            f"[AI-DIAG][Main] starting ComfyUI generation gen_id={gen_id} "
            f"event={req.event_type} behavior={req.behavior_type} tags={req.anim_tags}",
            flush=True,
        )
        if not comfy.is_configured():
            set_ai_status("生成未配置", "error")
            print(
                f"[AI-DIAG][Main] ComfyUI generation aborted because workflow is not configured "
                f"gen_id={gen_id} event={req.event_type}",
                flush=True,
            )
            if llm_state["enabled"]:
                win.show_dialogue("我想生成新动作，但 ComfyUI 工作流还没配置好。", 4500)
            return
        set_ai_status("生成中", "working")
        if llm_state["enabled"] and False:
            win.show_dialogue("我在想一个新动作……", 2200)
        output_basename = _build_generated_output_basename(req)
        prompt_request = {
            "pet_id": CURRENT_PET_ID,
            "pet_profile": CURRENT_PET_PROFILE,
            "pet_label": CURRENT_PET_LABEL,
            "tags": generated_tags,
            "behavior_type": req.behavior_type,
            "action_desc": req.action_desc,
        }
        if req.prompt_request:
            prompt_request.update(req.prompt_request)
            prompt_request.setdefault("tags", generated_tags)
            prompt_request.setdefault("behavior_type", req.behavior_type)
            prompt_request.setdefault("action_desc", req.action_desc)

        prompts = build_prompt_bundle_from_request(prompt_request)
        prompt_payload = {
            "image_prompt": prompts.image_prompt,
            "image_negative_prompt": prompts.image_negative_prompt,
            "video_prompt": prompts.video_prompt,
            "video_negative_prompt": prompts.video_negative_prompt,
            "filename_prefix": f"desktop_pet/{CURRENT_PET_ID}_{int(_time.time())}",
            "output_basename": output_basename,
        }

        generation_jobs[gen_id] = {
            "req": req,
            "prompt_request": dict(prompt_request),
            "prompts": prompts,
            "generated_tags": list(generated_tags),
        }

        def on_gen_done(media_path):
            generation_bridge.finished.emit(gen_id, media_path)

        started = comfy.generate(prompt_payload, str(REF_IMAGE_PATH), on_done=on_gen_done)
        if not started:
            generation_jobs.pop(gen_id, None)
            print(
                f"[AI-DIAG][Main] generation dispatch skipped because ComfyUI is still busy "
                f"gen_id={gen_id}",
                flush=True,
            )

    # ── 交互事件处理 ──────────────────────────────────────

    def _on_chat_visibility_changed(visible: bool) -> None:
        chat_runtime["visible"] = bool(visible)

    def open_chat_input(_data: dict | None = None) -> None:
        if not llm_state["enabled"]:
            set_ai_status("已关闭", "off")
            win.show_dialogue("请先开启自主AI模式，再和我聊天吧。")
            return

        widget = chat_ui["widget"]
        status_text = "想聊什么都可以告诉我。"
        if chat_runtime["inflight"] or llm.is_busy():
            status_text = "我还在想上一句，稍等我一下。"
        widget.show_input(status_text=status_text)

    def submit_chat_message(user_text: str) -> None:
        widget = chat_ui["widget"]
        reason = _validate_chat_submission(
            user_text=user_text,
            llm_enabled=llm_state["enabled"],
            llm_busy=llm.is_busy(),
            has_active_event=arbiter.current is not None,
        )
        if reason == "empty":
            widget.set_busy(False, "先输入一点内容吧。")
            widget.focus_input()
            return
        if reason == "disabled":
            set_ai_status("已关闭", "off")
            widget.set_busy(False, "先开启自主AI模式，我才能和你聊天。")
            return
        if reason == "busy":
            set_ai_status("思考中", "working")
            widget.set_busy(False, "我还在忙上一件事，稍等一下再聊。")
            return
        if reason == "event_active":
            set_ai_status("当前事件中", "working")
            widget.set_busy(False, "等我先把当前动作做完，再认真听你说。")
            return

        chat_runtime["inflight"] = True
        widget.set_busy(True, "我在认真听，也在想怎么回答你……")
        set_ai_status("对话中", "working")
        started, start_reason = brain.request_chat_async(user_text, pet.to_context_dict())
        if started:
            return

        chat_runtime["inflight"] = False
        widget.set_busy(False, "刚刚没接上，你再和我说一遍吧。")
        if start_reason == "disabled":
            set_ai_status("已关闭", "off")
        elif start_reason == "busy":
            set_ai_status("思考中", "working")
        else:
            set_ai_status("回复失败", "error")

    def _chat_avoid_rects() -> list:
        rects = list(stats_panel.get_overlay_rects())
        dialogue_rect = win.get_dialogue_overlay_rect()
        if dialogue_rect:
            rects.append(dialogue_rect)
        return rects

    chat_ui["widget"] = ChatBubble(
        win,
        on_submit=submit_chat_message,
        on_visibility_changed=_on_chat_visibility_changed,
        avoid_rects_provider=_chat_avoid_rects,
        preferred_panel_rect_provider=stats_panel.get_expanded_panel_rect,
    )

    def on_interaction(data: dict):
        event_type = data.get("type")
        print(f"[DBG] on_interaction  type={event_type}  t={_time.monotonic():.3f}", flush=True)

        build_started_at = _time.perf_counter()
        req = build_user_request(
            event_type, imap,
            screen_rect=screen,
            current_pos=win.pos(),
            walk_min_dist=WALK_MIN_DIST_PX,
        )
        if req is None:
            return
        print(
            f"[AI-DIAG][Main] user request built event={event_type} movement={req.movement.value} "
            f"target_pos={req.target_pos} dialogue_len={len(req.dialogue)} "
            f"dt_ms={(_time.perf_counter() - build_started_at) * 1000:.1f}",
            flush=True,
        )
        enrich_started_at = _time.perf_counter()
        req = brain.enrich_request(req, pet.to_context_dict())
        print(
            f"[AI-DIAG][Main] user request enriched event={event_type} source={req.source.value} "
            f"dialogue_len={len(req.dialogue)} dt_ms={(_time.perf_counter() - enrich_started_at) * 1000:.1f}",
            flush=True,
        )

        result = arbiter.request(req)
        print(f"[DBG] on_interaction  accepted={result.accepted}  reason={result.reason}", flush=True)
        _update_drowsy_auto_sleep_state(drowsy_auto_sleep, req, result.accepted)
        if result.accepted and brain.should_log_interaction(req):
            brain.maybe_request_quick_reply(req, pet.to_context_dict())
            mem.log_interaction(
                req.event_type,
                dialogue=req.dialogue,
                source=req.source.value,
                emotion=str(req.emotion.get("primary") or ""),
            )

        if not result.accepted and not result.downgraded:
            # 拒绝时的用户提示
            if "cooldown" in result.reason:
                remaining = result.reason.split(":")[1]
                win.show_dialogue(f"等一下再来吧～（{remaining}）")
            elif result.reason == "blocked_while_sleep":
                win.show_dialogue("太累了，先让我睡一会儿…")
            elif result.reason == "blocked_low_energy":
                win.show_dialogue("没力气了，先休息一下吧……")

        _check_pending_generation()

    def on_follow_mode(data: dict | None):
        payload = data or {}
        set_follow_mode(bool(payload.get("enabled")), source=str(payload.get("source") or "bus"))

    bus.subscribe("interaction", on_interaction)
    bus.subscribe("follow_mode", on_follow_mode)
    bus.subscribe("chat", open_chat_input)
    bus.subscribe("gallery", open_gallery_panel)

    # ── AI 自主行为处理 ──────────────────────────────────

    def on_ai_decision(decision: dict):
        if not llm_state["enabled"]:
            return
        print(
            f"[AI-DIAG][Main] on_ai_decision received intent_mode={decision.get('intent_mode')} "
            f"behavior={decision.get('behavior_type')} movement={decision.get('movement')} "
            f"tags={decision.get('anim_tags')} action_desc={decision.get('action_desc', '')!r} "
            f"prompt_request_tags={(decision.get('prompt_request') or {}).get('tags')}",
            flush=True,
        )
        req = build_ai_request(decision, screen)
        print(
            f"[AI-DIAG][Main] on_ai_decision built request event={req.event_type} "
            f"kind={req.kind.value} priority={int(req.priority)} "
            f"movement={req.movement.value} target_pos={req.target_pos} "
            f"generate_if_missing={req.generate_if_missing} action_desc={req.action_desc!r}",
            flush=True,
        )
        result = arbiter.request(req)
        print(
            f"[AI-DIAG][Main] on_ai_decision arbiter accepted={result.accepted} "
            f"reason={result.reason} event={req.event_type}",
            flush=True,
        )
        _update_drowsy_auto_sleep_state(drowsy_auto_sleep, req, result.accepted)
        mem.log_ai_decision(decision, accepted=result.accepted, reason=result.reason)
        if result.accepted:
            if arbiter.get_pending_generation_id() is None:
                set_ai_status("动作进行中", "working")
        else:
            set_ai_status("决策被拦截", "error")
        _check_pending_generation()

    llm.decision_ready.connect(on_ai_decision)

    def on_chat_ready(payload: dict):
        chat_runtime["inflight"] = False
        widget = chat_ui["widget"]
        widget.set_busy(False, "继续和我说话吧。")
        widget.clear_input()
        if widget.isVisible():
            widget.focus_input()

        if not llm_state["enabled"]:
            return

        result = payload.get("result") or {}
        if not payload.get("ok") or not result.get("dialogue"):
            set_ai_status("回复失败", "error")
            widget.set_busy(False, "这次没接上，再和我说一遍吧。")
            print(
                f"[AI-DIAG][Main] chat produced no usable dialogue "
                f"error={payload.get('error', '')}",
                flush=True,
            )
            return

        print(
            f"[AI-DIAG][Main] chat ready emotion={result.get('emotion')} "
            f"trigger={result.get('action_trigger')} dt_ms={payload.get('dt_ms', 0):.1f} "
            f"user_text_len={len(str(payload.get('user_text') or ''))}",
            flush=True,
        )
        normalized_emotion = normalize_emotion(result.get("emotion"))
        action_trigger = result.get("action_trigger")
        reply_text = str(result.get("dialogue") or "")
        user_text = str(payload.get("user_text") or "")

        pet.mood = min(100.0, pet.mood + 2.0)
        update_stats()

        mem.log_interaction(
            "chat",
            dialogue=reply_text,
            source="user",
            emotion=normalized_emotion,
            action_trigger=action_trigger,
        )
        mem.maybe_store_chat_memory(
            user_text=user_text,
            reply_text=reply_text,
            emotion=normalized_emotion,
            action_trigger=action_trigger,
        )
        if llm_state["enabled"] and arbiter.get_pending_generation_id() is None:
            set_ai_status("空闲", "idle")
        win.show_dialogue(reply_text, 3600, emotion=normalized_emotion)
        widget.refresh_position()

        followup = _build_l2_followup_decision("chat", result, context_text=f"{user_text}\n{reply_text}")
        if not followup:
            return
        if arbiter.current:
            print(
                f"[AI-DIAG][Main] chat followup skipped because another event is active "
                f"current_event={arbiter.current.event_type} trigger={action_trigger}",
                flush=True,
            )
            return

        print(
            f"[AI-DIAG][Main] chat followup dispatch trigger={action_trigger} "
            f"emotion={normalized_emotion}",
            flush=True,
        )
        followup_req = build_ai_request(followup, screen)
        print(
            f"[AI-DIAG][Main] chat followup built request event={followup_req.event_type} "
            f"kind={followup_req.kind.value} priority={int(followup_req.priority)} "
            f"tags={followup_req.anim_tags}",
            flush=True,
        )
        followup_result = arbiter.request(followup_req)
        print(
            f"[AI-DIAG][Main] chat followup accepted={followup_result.accepted} "
            f"reason={followup_result.reason} event={followup_req.event_type}",
            flush=True,
        )
        _update_drowsy_auto_sleep_state(drowsy_auto_sleep, followup_req, followup_result.accepted)
        if followup_result.accepted:
            set_ai_status("动作进行中", "working")
            _check_pending_generation()

    def on_quick_reply_ready(payload: dict):
        if not llm_state["enabled"]:
            print(
                f"[AI-DIAG][Main] quick reply ignored because AI mode is disabled "
                f"event={payload.get('event_type')}",
                flush=True,
            )
            return

        result = payload.get("result") or {}
        if not payload.get("ok") or not result.get("dialogue"):
            set_ai_status("回复失败", "error")
            print(
                f"[AI-DIAG][Main] quick reply produced no usable dialogue "
                f"event={payload.get('event_type')} error={payload.get('error', '')}",
                flush=True,
            )
            return

        print(
            f"[AI-DIAG][Main] quick reply ready event={payload.get('event_type')} "
            f"emotion={result.get('emotion')} trigger={result.get('action_trigger')} "
            f"dt_ms={payload.get('dt_ms', 0):.1f}",
            flush=True,
        )
        normalized_emotion = normalize_emotion(result.get("emotion"))
        action_trigger = result.get("action_trigger")
        mem.log_quick_reply(
            str(payload.get("event_type") or ""),
            dialogue=str(result.get("dialogue") or ""),
            emotion=normalized_emotion,
            action_trigger=action_trigger,
        )
        if llm_state["enabled"] and arbiter.get_pending_generation_id() is None:
            set_ai_status("空闲", "idle")
        win.show_dialogue(str(result.get("dialogue") or ""), emotion=normalized_emotion)
        chat_ui["widget"].refresh_position()

        followup = _build_quick_reply_followup_decision(str(payload.get("event_type") or ""), result)
        if not followup:
            return
        if arbiter.current:
            print(
                f"[AI-DIAG][Main] quick reply followup skipped because another event is active "
                f"current_event={arbiter.current.event_type} trigger={action_trigger}",
                flush=True,
            )
            return

        print(
            f"[AI-DIAG][Main] quick reply followup dispatch event={payload.get('event_type')} "
            f"trigger={action_trigger} emotion={normalized_emotion}",
            flush=True,
        )
        followup_req = build_ai_request(followup, screen)
        print(
            f"[AI-DIAG][Main] quick reply followup built request event={followup_req.event_type} "
            f"kind={followup_req.kind.value} priority={int(followup_req.priority)} "
            f"tags={followup_req.anim_tags}",
            flush=True,
        )
        followup_result = arbiter.request(followup_req)
        print(
            f"[AI-DIAG][Main] quick reply followup accepted={followup_result.accepted} "
            f"reason={followup_result.reason} event={followup_req.event_type}",
            flush=True,
        )
        _update_drowsy_auto_sleep_state(drowsy_auto_sleep, followup_req, followup_result.accepted)
        if followup_result.accepted:
            set_ai_status("动作进行中", "working")
            _check_pending_generation()

    llm.chat_ready.connect(on_chat_ready)
    llm.quick_reply_ready.connect(on_quick_reply_ready)

    # ── 属性阈值检查（独立定时器，每 5 秒）──────────────────

    _last_threshold_t: dict = {}

    def threshold_check():
        now = _time.monotonic()
        threshold_events = pet.check_thresholds()
        is_sleeping = move_sm.state == MoveState.SLEEP

        if threshold_events:
            print(f"[DBG] threshold_check  events={[e.name for e in threshold_events]}  "
                  f"sleeping={is_sleeping}  energy={pet.energy:.1f}  hunger={pet.hunger:.1f}", flush=True)

        for te in threshold_events:
            # FORCE_SLEEP 始终处理（arbiter 会去重）
            if te != ThresholdEvent.FORCE_SLEEP:
                remind_s = HUNGRY_REMIND_S if te == ThresholdEvent.FORCE_HUNGRY else DROWSY_REMIND_S
                elapsed = now - _last_threshold_t.get(te, 0)
                if elapsed < remind_s:
                    print(
                        f"[AI-DIAG][Main] threshold event skipped by reminder cooldown "
                        f"event={te.name} remaining_s={max(0.0, remind_s - elapsed):.1f}",
                        flush=True,
                    )
                    continue
            _last_threshold_t[te] = now

            req = build_threshold_request(te, imap, is_sleeping)
            req = brain.enrich_request(req, pet.to_context_dict())
            if req:
                result = arbiter.request(req)
                print(f"[DBG] threshold_check  event={te.name}  accepted={result.accepted}  "
                      f"reason={result.reason}", flush=True)
                _update_drowsy_auto_sleep_state(drowsy_auto_sleep, req, result.accepted)
                if result.accepted and req.source != EventSource.AI and brain.should_log_interaction(req):
                    brain.maybe_request_quick_reply(req, pet.to_context_dict())
                    mem.log_interaction(req.event_type, dialogue=req.dialogue, source=req.source.value)

    threshold_timer = QTimer()
    threshold_timer.timeout.connect(threshold_check)
    threshold_timer.start(THRESHOLD_CHECK_MS)

    # ── 反馈处理 ──────────────────────────────────────────

    def on_feedback(data: dict):
        ftype = data.get("type")
        aid   = data.get("id")
        if not aid:
            return
        if ftype == "block":
            lib.set_blocked(aid, True)
            arbiter.stop_current_event()
        elif ftype == "like":
            lib.set_rating(aid, 5)
        elif ftype == "retag":
            lib.update_tags(aid, data.get("tags", []))
        if gallery_ui["widget"]:
            gallery_ui["widget"].refresh()

    bus.subscribe("feedback", on_feedback)

    # ── 自主行为定时检查（每 tick）──────────────────────────

    def autonomous_tick():
        now = _time.monotonic()
        if not llm_state["enabled"]:
            if now - ai_diag["last_autonomous_disabled_log_at"] >= 5:
                print("[AI-DIAG][Main] autonomous_tick skipped because AI mode is disabled", flush=True)
                ai_diag["last_autonomous_disabled_log_at"] = now
            return
        if _should_pause_autonomous_for_chat(chat_runtime["visible"], chat_runtime["inflight"]):
            if now - ai_diag["last_autonomous_block_log_at"] >= 2:
                print(
                    f"[AI-DIAG][Main] autonomous_tick paused for chat "
                    f"visible={chat_runtime['visible']} inflight={chat_runtime['inflight']}",
                    flush=True,
                )
                ai_diag["last_autonomous_block_log_at"] = now
            return
        if llm.is_busy():
            if now - ai_diag["last_ai_status_reconcile_log_at"] >= 2:
                print(
                    f"[AI-DIAG][Main] autonomous_tick skipped because llm is busy "
                    f"running={llm.isRunning()} pending={llm.has_pending_request()}",
                    flush=True,
                )
                ai_diag["last_ai_status_reconcile_log_at"] = now
            return
        if arbiter.current:
            if now - ai_diag["last_autonomous_block_log_at"] >= 2:
                print(
                    f"[AI-DIAG][Main] autonomous_tick blocked current_event={arbiter.current.event_type} "
                    f"kind={arbiter.current.kind.value}",
                    flush=True,
                )
                ai_diag["last_autonomous_block_log_at"] = now
            return  # 有事件进行中，不触发自主行为
        if move_sm.tick_autonomous_timer():
            memories = mem.get_recent(limit=5)
            pet_context = pet.to_context_dict()
            recent_ai_behaviors = mem.get_recent_ai_behaviors(limit=3)
            print(
                f"[AI-DIAG][Main] autonomous_tick fired hunger={pet.hunger:.1f} mood={pet.mood:.1f} "
                f"energy={pet.energy:.1f} memories={len(memories)}",
                flush=True,
            )
            set_ai_status("思考中", "working")
            llm.request(
                pet_state=pet_context,
                memories=memories,
                available_tags=_build_ai_available_tags(lib),
                suggested_behaviors=_build_autonomous_behavior_suggestions(
                    pet_context,
                    recent_behavior_history=recent_ai_behaviors,
                ),
                rejection_context=arbiter.get_ai_rejection_context(),
                last_behavior_hint=mem.get_last_ai_behavior(),
                interaction_summary=mem.get_recent_interaction_summary(limit=3),
                interaction_stats=mem.get_recent_interaction_stats(limit=12),
                emotion_history=mem.get_recent_emotion_history(limit=4),
                recent_behavior_history=recent_ai_behaviors,
            )

    auto_timer = QTimer()
    auto_timer.timeout.connect(autonomous_tick)
    auto_timer.start(16)

    generation_timer = QTimer()
    generation_timer.timeout.connect(_check_pending_generation)
    generation_timer.start(1000)

    def reconcile_ai_status():
        if not llm_state["enabled"]:
            ai_runtime["last_ai_event_active"] = False
            return

        current = arbiter.current
        ai_event_active = bool(current and current.source == EventSource.AI)
        was_ai_event_active = ai_runtime["last_ai_event_active"]
        ai_runtime["last_ai_event_active"] = ai_event_active

        if not was_ai_event_active or ai_event_active:
            return

        now = _time.monotonic()
        if arbiter.get_pending_generation_id() is not None or comfy.is_busy():
            if now - ai_diag["last_ai_status_reconcile_log_at"] >= 2:
                print(
                    "[AI-DIAG][Main] ai event ended but generation is still active; keep current status",
                    flush=True,
                )
                ai_diag["last_ai_status_reconcile_log_at"] = now
            return

        if llm.is_busy():
            set_ai_status("思考中", "working")
            return

        if current is None:
            print("[AI-DIAG][Main] ai event finished, status -> idle", flush=True)
            set_ai_status("空闲", "idle")
            ai_diag["last_ai_status_reconcile_log_at"] = now
            return

        print(
            f"[AI-DIAG][Main] ai event ended but another event is active "
            f"current_event={current.event_type} kind={current.kind.value}",
            flush=True,
        )
        set_ai_status("当前事件中", "working")
        ai_diag["last_ai_status_reconcile_log_at"] = now

    ai_status_timer = QTimer()
    ai_status_timer.timeout.connect(reconcile_ai_status)
    ai_status_timer.start(250)

    # ── 恢复定时器（每 1s）─────────────────────────────────
    # 合并了交互渐进恢复 + 睡眠能量恢复

    def recovery_tick():
        # 交互属性渐进恢复（arbiter 内部处理）
        arbiter.recovery_tick()

        current_event_type = getattr(arbiter.current, "event_type", None)
        if drowsy_auto_sleep["active"] and (
            move_sm.state == MoveState.SLEEP or pet.energy > ENERGY_DROWSY_THRESHOLD
        ):
            print(
                f"[AI-DIAG][Main] drowsy auto sleep cleared "
                f"sleeping={move_sm.state == MoveState.SLEEP} energy={pet.energy:.1f}",
                flush=True,
            )
            drowsy_auto_sleep["active"] = False
            drowsy_auto_sleep["deadline"] = 0.0
            drowsy_auto_sleep["source_event"] = ""

        if _should_trigger_drowsy_auto_sleep(
            drowsy_auto_sleep,
            current_event_type=current_event_type,
            energy=pet.energy,
            is_sleeping=(move_sm.state == MoveState.SLEEP),
        ):
            print(
                f"[AI-DIAG][Main] drowsy auto sleep firing "
                f"current_event={current_event_type} energy={pet.energy:.1f}",
                flush=True,
            )
            drowsy_auto_sleep["active"] = False
            drowsy_auto_sleep["deadline"] = 0.0
            drowsy_auto_sleep["source_event"] = ""
            req = build_threshold_request(ThresholdEvent.FORCE_SLEEP, imap, move_sm.state == MoveState.SLEEP)
            req = brain.enrich_request(req, pet.to_context_dict())
            if req:
                result = arbiter.request(req)
                print(
                    f"[AI-DIAG][Main] drowsy auto sleep request accepted={result.accepted} "
                    f"reason={result.reason}",
                    flush=True,
                )
                _update_drowsy_auto_sleep_state(drowsy_auto_sleep, req, result.accepted)

        # 睡眠能量恢复（独立于 arbiter）
        if move_sm.state == MoveState.SLEEP:
            old_energy = pet.energy
            pet.energy = min(100.0, pet.energy + ENERGY_SLEEP_RECOVERY)
            if pet.energy != old_energy:
                print(f"[DBG] sleep_recovery  energy: {old_energy:.1f} -> {pet.energy:.1f}  "
                      f"(+{ENERGY_SLEEP_RECOVERY})", flush=True)
                update_stats()
            # 能量满后自然醒来
            if pet.energy >= 100.0:
                print(f"[DBG] sleep_recovery: energy full, waking up", flush=True)
                arbiter.finish_current_event()
                win.show_dialogue("睡好啦～精神多了！")

    recovery_timer = QTimer()
    recovery_timer.timeout.connect(recovery_tick)
    recovery_timer.start(1000)

    # ── 属性衰减持久化（每 10s）──────────────────────────

    def persist_decay():
        if move_sm.state == MoveState.SLEEP:
            # energy 由 recovery_tick 每秒处理，此处仅衰减其他属性
            pet.hunger      = max(0.0,   pet.hunger      - HUNGER_DECAY      * DECAY_PERSIST_EVERY)
            pet.cleanliness = max(0.0,   pet.cleanliness - CLEANLINESS_DECAY * DECAY_PERSIST_EVERY)
            pet.mood        = min(100.0, pet.mood        + HUNGER_DECAY * 0.3 * DECAY_PERSIST_EVERY)
        else:
            pet.apply_decay(seconds=DECAY_PERSIST_EVERY)
        pet.update_mood()
        pet.persist()
        update_stats()

    decay_timer = QTimer()
    decay_timer.timeout.connect(persist_decay)
    decay_timer.start(DECAY_PERSIST_EVERY * 1000)

    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
