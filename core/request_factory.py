"""
EventRequest 构造工厂 — 三个事件源各自的构造逻辑集中管理。

用户交互 → build_user_request()
属性阈值 → build_threshold_request()
AI 决策  → build_ai_request()
"""
import random
from config import CURRENT_PET_ID, CURRENT_PET_LABEL, CURRENT_PET_PROFILE
from core.event_request import (
    EventRequest, EventSource, EventPriority, EventKind, MovementIntent,
)
from core.interaction_map import InteractionMap, InteractionEvent
from core.pet_data import ThresholdEvent


_EVENT_TYPE_MAP = {
    "click":        InteractionEvent.CLICK,
    "double_click": InteractionEvent.DOUBLE_CLICK,
    "feed":         InteractionEvent.FEED,
    "play":         InteractionEvent.PLAY,
    "bath":         InteractionEvent.BATH,
    "stroke":       InteractionEvent.STROKE,
    "walk_mode":    InteractionEvent.WALK_MODE,
}

_THRESHOLD_TO_INTERACTION = {
    ThresholdEvent.FORCE_HUNGRY: InteractionEvent.FORCE_HUNGRY,
    ThresholdEvent.FORCE_SLEEP:  InteractionEvent.FORCE_SLEEP,
    ThresholdEvent.DROWSY:       InteractionEvent.DROWSY,
}

_AI_INTENT_MAP = {
    "idle":       (EventKind.ACTION, MovementIntent.STAY),
    "curious":    (EventKind.ACTION, MovementIntent.WANDER),
    "playful":    (EventKind.ACTION, MovementIntent.STAY),
    "rest":       (EventKind.ACTION, MovementIntent.STAY),
    "sleep":      (EventKind.STATE, MovementIntent.SLEEP),
    # "request" still needs a real animation / generation chain (e.g. beg_food),
    # so it should behave like an action instead of a disposable notify bubble.
    "request":    (EventKind.ACTION, MovementIntent.STAY),
    "self_care":  (EventKind.ACTION, MovementIntent.STAY),
    "social":     (EventKind.ACTION, MovementIntent.STAY),
    "showcase":   (EventKind.ACTION, MovementIntent.STAY),
}


def build_user_request(
    event_type: str,
    imap: InteractionMap,
    screen_rect=None,
    current_pos=None,
    walk_min_dist: int = 300,
) -> EventRequest | None:
    """从用户交互类型构造 EventRequest。返回 None 表示未知事件类型。"""
    if event_type == "drag_start":
        return EventRequest(
            source=EventSource.USER,
            priority=EventPriority.USER_PHYSICAL,
            kind=EventKind.STATE,
            event_type="drag_start",
            movement=MovementIntent.CARRIED,
            anim_tags=["carried", "lifted", "scared"],
            anim_loop=True,
            generate_if_missing=True,
            action_desc=(
                f"the {CURRENT_PET_LABEL} is gently lifted and carried in the air, "
                "full body visible, compact cute carried pose"
            ),
            behavior_type="carried",
            prompt_request={
                "pet_id": CURRENT_PET_ID,
                "pet_profile": CURRENT_PET_PROFILE,
                "pet_label": CURRENT_PET_LABEL,
                "tags": ["carried", "lifted", "scared"],
                "behavior_type": "carried",
                "action_desc": f"the {CURRENT_PET_LABEL} is gently lifted and carried in the air",
                "pose_focus": ["being gently lifted off the ground", "compact held pose"],
                "motion_focus": ["gentle suspended body bob", "small paw sway"],
                "video_overrides": ["very small motion range", "keep the pet centered while being held"],
                "negative_overrides": ["no accessories", "no extra props"],
            },
        )
    if event_type == "drag_end":
        return EventRequest(
            source=EventSource.USER,
            priority=EventPriority.USER_PHYSICAL,
            kind=EventKind.ACTION,
            event_type="drag_end",
            movement=MovementIntent.RETURN_DEFAULT,
        )

    ie = _EVENT_TYPE_MAP.get(event_type)
    if ie is None:
        return None
    action = imap.get(ie)
    if action is None:
        return None

    # WALK_MODE 双路路由
    movement = MovementIntent.STAY
    target_pos = None
    if ie == InteractionEvent.WALK_MODE:
        movement = MovementIntent.WANDER
        if screen_rect and current_pos:
            import math
            for _attempt in range(20):
                tx = random.randint(50, screen_rect.width() - 200)
                ty = random.randint(50, screen_rect.height() - 200)
                dx = tx - current_pos.x()
                dy = ty - current_pos.y()
                dist = math.sqrt(dx * dx + dy * dy)
                if dist >= walk_min_dist:
                    break
            from PyQt6.QtCore import QPoint
            target_pos = QPoint(tx, ty)

    return EventRequest(
        source=EventSource.USER,
        priority=EventPriority.USER_INTERACTION,
        kind=EventKind.ACTION,
        event_type=event_type,
        movement=movement,
        target_pos=target_pos,
        anim_tags=action.get("tags", []),
        anim_loop=action.get("loop", False),
        dialogue=action.get("dialogue", ""),
        wakes_from_sleep=action.get("wakes_from_sleep", False),
        blocked_while_sleep=action.get("blocked_while_sleep", False),
        blocked_low_energy=action.get("blocked_low_energy", False),
    )


def build_threshold_request(
    threshold_event: ThresholdEvent,
    imap: InteractionMap,
    is_sleeping: bool = False,
) -> EventRequest | None:
    """从阈值事件构造 EventRequest。"""
    ie = _THRESHOLD_TO_INTERACTION.get(threshold_event)
    if ie is None:
        return None
    action = imap.get(ie)
    if action is None:
        return None

    if threshold_event == ThresholdEvent.FORCE_SLEEP:
        return EventRequest(
            source=EventSource.THRESHOLD,
            priority=EventPriority.THRESHOLD_CRITICAL,
            kind=EventKind.STATE,
            event_type="FORCE_SLEEP",
            movement=MovementIntent.SLEEP,
            anim_tags=action.get("tags", []),
            anim_loop=True,
            dialogue=action.get("dialogue", "zzz..."),
        )

    if threshold_event == ThresholdEvent.FORCE_HUNGRY:
        # SLEEP 中降级为 NOTIFY
        kind = EventKind.NOTIFY if is_sleeping else EventKind.ACTION
        return EventRequest(
            source=EventSource.THRESHOLD,
            priority=EventPriority.THRESHOLD_CRITICAL,
            kind=kind,
            event_type="FORCE_HUNGRY",
            anim_tags=action.get("tags", []),
            dialogue=action.get("dialogue", ""),
        )

    if threshold_event == ThresholdEvent.DROWSY:
        return EventRequest(
            source=EventSource.THRESHOLD,
            priority=EventPriority.THRESHOLD_WARNING,
            kind=EventKind.NOTIFY,
            event_type="DROWSY",
            anim_tags=action.get("tags", []),
            dialogue=action.get("dialogue", ""),
        )

    return None


def build_ai_request(
    decision: dict,
    screen_rect=None,
) -> EventRequest:
    """从 LLM 决策输出构造 EventRequest。"""
    behavior = str(decision.get("behavior_type") or "idle_normal").strip()
    intent_mode = str(decision.get("intent_mode") or "").strip() or _derive_intent_mode(decision)
    anim_tags = _build_ai_anim_tags(decision, behavior, intent_mode)
    kind, default_movement = _AI_INTENT_MAP.get(intent_mode, (EventKind.ACTION, MovementIntent.STAY))
    movement = _movement_from_decision(decision, default_movement)
    priority = _priority_from_decision(decision, default=EventPriority.AI_DECISION)

    target_pos = None
    if movement == MovementIntent.WANDER and screen_rect:
        from PyQt6.QtCore import QPoint
        target_pos = QPoint(
            random.randint(50, screen_rect.width() - 200),
            random.randint(50, screen_rect.height() - 200),
        )

    return EventRequest(
        source=EventSource.AI,
        priority=priority,
        kind=kind,
        event_type=f"ai_{_slugify_behavior(behavior, intent_mode)}",
        movement=movement,
        target_pos=target_pos,
        anim_tags=anim_tags,
        dialogue=decision.get("dialogue", ""),
        action_desc=decision.get("action_desc", ""),
        emotion=decision.get("emotion", {}),
        generate_if_missing=True,
        behavior_type=behavior,
        prompt_request=_build_prompt_request_from_decision(decision),
    )


def _priority_from_decision(decision: dict, default: EventPriority) -> EventPriority:
    raw = decision.get("priority_override")
    if raw is None:
        return default
    try:
        return EventPriority(int(raw))
    except (ValueError, TypeError):
        return default


def _build_prompt_request_from_decision(decision: dict) -> dict:
    prompt_request = decision.get("prompt_request") or {}
    if not isinstance(prompt_request, dict):
        prompt_request = {}

    behavior = str(decision.get("behavior_type") or "idle_normal").strip()
    intent_mode = str(decision.get("intent_mode") or "").strip() or _derive_intent_mode(decision)
    anim_tags = _build_ai_anim_tags(decision, behavior, intent_mode)

    merged = dict(prompt_request)
    merged.setdefault("pet_id", CURRENT_PET_ID)
    merged.setdefault("pet_profile", CURRENT_PET_PROFILE)
    merged.setdefault("pet_label", CURRENT_PET_LABEL)
    merged.setdefault("tags", anim_tags)
    merged.setdefault("behavior_type", behavior or "idle_normal")
    merged.setdefault("intent_mode", intent_mode)
    merged.setdefault("action_desc", decision.get("action_desc", ""))
    return merged


def _build_ai_anim_tags(decision: dict, behavior: str, intent_mode: str) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    def push(value: str) -> None:
        tag = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
        if not tag or tag in seen:
            return
        seen.add(tag)
        ordered.append(tag)

    behavior_slug = _slugify_behavior(behavior, intent_mode)
    if behavior_slug and behavior_slug not in {"ai_action"}:
        push(behavior_slug)

    if intent_mode:
        push(intent_mode)

    for raw in decision.get("anim_tags") or ["idle"]:
        push(str(raw))

    return ordered or ["idle"]


def _movement_from_decision(decision: dict, default_movement: MovementIntent) -> MovementIntent:
    movement = str(decision.get("movement") or "").strip().lower()
    if movement == "wander":
        return MovementIntent.WANDER
    if movement == "stay":
        return MovementIntent.STAY
    return default_movement


def _derive_intent_mode(decision: dict) -> str:
    behavior = str(decision.get("behavior_type") or "").strip().lower()
    tags = [str(tag).strip().lower() for tag in (decision.get("anim_tags") or [])]
    movement = str(decision.get("movement") or "").strip().lower()
    tag_text = " ".join(tags)

    if behavior in {"sleep", "dream"} or "sleep" in tag_text:
        return "sleep"
    if "beg" in behavior or "food" in tag_text:
        return "request"
    if "groom" in behavior or "clean" in tag_text:
        return "self_care"
    if "show" in behavior:
        return "showcase"
    if "play" in behavior or "excited" in tag_text:
        return "playful"
    if behavior in {"explore", "wander"} or movement == "wander" or "curious" in tag_text or "explore" in tag_text:
        return "curious"
    if "rest" in behavior or "idle" in tag_text or "calm" in tag_text:
        return "rest" if "rest" in behavior else "idle"
    return "social"


def _slugify_behavior(behavior: str, intent_mode: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9_]+", "_", behavior.strip().lower())
    slug = slug.strip("_")
    return slug or intent_mode or "ai_action"
