import json
import random
import re
import time
from dataclasses import replace
from pathlib import Path

from ai.prompt_templates import normalize_emotion
from core.event_request import EventRequest, EventSource

RESPONSE_TEMPLATES_PATH = Path(__file__).parent.parent / "assets" / "response_templates.json"

_L1_EVENTS = {
    "click",
    "double_click",
    "stroke",
    "drag_start",
    "drag_end",
}

_L2_EVENTS = {
    "feed",
    "play",
    "bath",
    "walk_mode",
    "FORCE_HUNGRY",
    "DROWSY",
}


class PetBrain:
    def __init__(
        self,
        llm_engine,
        memory_manager,
        template_path: Path | None = None,
        overlay_template_path: Path | None = None,
    ):
        self._llm = llm_engine
        self._memory = memory_manager
        self._template_path = Path(template_path or RESPONSE_TEMPLATES_PATH)
        self._overlay_template_path = Path(overlay_template_path) if overlay_template_path else None
        self._templates = self._load_merged_templates(
            self._template_path,
            self._overlay_template_path,
        )
        self._last_l2_at = 0.0

    def enrich_request(self, req: EventRequest | None, pet_state: dict) -> EventRequest | None:
        if req is None:
            return None

        started_at = time.perf_counter()
        level = self._route(req)
        print(
            f"[AI-DIAG][PetBrain] enrich start event={req.event_type} source={req.source.value} "
            f"level={level}",
            flush=True,
        )
        if level == "L1":
            enriched = self._apply_template(req, pet_state)
        elif level == "L2":
            enriched = self._apply_template(req, pet_state)
        else:
            enriched = req
        print(
            f"[AI-DIAG][PetBrain] enrich done event={req.event_type} level={level} "
            f"dialogue_changed={enriched.dialogue != req.dialogue} "
            f"emotion={enriched.emotion.get('primary', '')} "
            f"dt_ms={(time.perf_counter() - started_at) * 1000:.1f}",
            flush=True,
        )
        return enriched

    def maybe_request_quick_reply(self, req: EventRequest | None, pet_state: dict) -> bool:
        if req is None:
            return False
        if self._route(req) != "L2":
            return False

        now = time.monotonic()
        if now - self._last_l2_at < 60:
            print(
                f"[AI-DIAG][PetBrain] L2 async request rate-limited event={req.event_type} "
                f"cooldown_remaining_s={max(0.0, 60 - (now - self._last_l2_at)):.1f}",
                flush=True,
            )
            return False
        if not self._llm.enabled:
            print(f"[AI-DIAG][PetBrain] L2 async request skipped because LLM is disabled event={req.event_type}", flush=True)
            return False

        recent_memories = self._memory.get_latest(limit=3)
        started = self._llm.request_quick_reply_async(
            pet_state=pet_state,
            event_type=req.event_type,
            event_desc=self._describe_event(req),
            recent_memories=recent_memories,
            meta={
                "source": req.source.value,
                "event_type": req.event_type,
            },
        )
        if started:
            self._last_l2_at = now
            print(f"[AI-DIAG][PetBrain] L2 async request started event={req.event_type}", flush=True)
        else:
            print(f"[AI-DIAG][PetBrain] L2 async request was not started event={req.event_type}", flush=True)
        return started

    def request_chat_async(self, user_text: str, pet_state: dict) -> tuple[bool, str]:
        text = str(user_text or "").strip()
        if not text:
            return False, "empty"
        if not self._llm.enabled:
            print("[AI-DIAG][PetBrain] chat skipped because LLM is disabled", flush=True)
            return False, "disabled"
        if self._llm.is_busy():
            print("[AI-DIAG][PetBrain] chat skipped because LLM is busy", flush=True)
            return False, "busy"

        recent_memories = self._memory.get_latest(limit=4)
        interaction_summary = self._memory.get_recent_interaction_summary(limit=3)
        started = self._llm.request_chat_async(
            pet_state=pet_state,
            user_text=text,
            recent_memories=recent_memories,
            interaction_summary=interaction_summary,
            meta={"source": "chat"},
        )
        if started:
            print(f"[AI-DIAG][PetBrain] chat request started user_text_len={len(text)}", flush=True)
            return True, "started"

        print("[AI-DIAG][PetBrain] chat request was not started", flush=True)
        return False, "busy"

    def route_name(self, req: EventRequest | None) -> str:
        if req is None:
            return "none"
        return self._route(req)

    def should_log_interaction(self, req: EventRequest | None) -> bool:
        return req is not None and self._route(req) == "L2"

    def _route(self, req: EventRequest) -> str:
        if req.source == EventSource.AI:
            return "L3"
        if req.event_type in _L1_EVENTS:
            return "L1"
        if req.event_type in _L2_EVENTS:
            return "L2"
        return "L1"

    def _apply_template(self, req: EventRequest, pet_state: dict) -> EventRequest:
        template = self._select_template(req.event_type, pet_state)
        if not template:
            print(f"[AI-DIAG][PetBrain] L1 no template match event={req.event_type}", flush=True)
            return req

        dialogue = random.choice(template.get("responses") or [req.dialogue]).strip()
        emotion_hint = str(template.get("emotion_hint") or "").strip()
        print(
            f"[AI-DIAG][PetBrain] L1 template selected event={req.event_type} "
            f"emotion_hint={emotion_hint or 'none'} dialogue_len={len(dialogue)}",
            flush=True,
        )
        new_emotion = dict(req.emotion)
        if emotion_hint:
            new_emotion["primary"] = normalize_emotion(emotion_hint)
        return replace(
            req,
            dialogue=dialogue or req.dialogue,
            emotion=new_emotion,
        )

    def _describe_event(self, req: EventRequest) -> str:
        parts = [f"source={req.source.value}", f"type={req.event_type}"]
        if req.action_desc:
            parts.append(f"action={req.action_desc}")
        elif req.dialogue:
            parts.append(f"default_reply={req.dialogue}")
        if req.anim_tags:
            parts.append(f"tags={', '.join(req.anim_tags[:4])}")
        return "; ".join(parts)

    def _select_template(self, event_type: str, pet_state: dict) -> dict | None:
        event_templates = self._templates.get(event_type, {})
        for item in event_templates.get("conditions", []):
            when = item.get("when") or {}
            if self._match_when(when, pet_state):
                return item
        return None

    def _match_when(self, when: dict, pet_state: dict) -> bool:
        for key, expected in when.items():
            value = self._lookup_value(pet_state, key)
            if value is None or not self._matches(value, expected):
                return False
        return True

    def _lookup_value(self, pet_state: dict, key: str):
        if key in pet_state:
            return pet_state[key]
        personality = pet_state.get("personality", {})
        if key in personality:
            return personality[key]
        return None

    def _matches(self, value, expected) -> bool:
        if isinstance(expected, (int, float)):
            return value == expected
        if not isinstance(expected, str):
            return value == expected

        text = expected.strip()
        range_match = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)", text)
        if range_match:
            lo = float(range_match.group(1))
            hi = float(range_match.group(2))
            return lo <= float(value) <= hi

        for op in (">=", "<=", ">", "<"):
            if text.startswith(op):
                threshold = float(text[len(op):].strip())
                numeric = float(value)
                if op == ">=":
                    return numeric >= threshold
                if op == "<=":
                    return numeric <= threshold
                if op == ">":
                    return numeric > threshold
                return numeric < threshold

        return str(value) == text

    @staticmethod
    def _load_templates(path: Path | None) -> dict:
        if path is None:
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @classmethod
    def _load_merged_templates(
        cls,
        base_path: Path | None,
        overlay_path: Path | None,
    ) -> dict:
        base_templates = cls._load_templates(base_path)
        overlay_templates = cls._load_templates(overlay_path)
        if not overlay_templates:
            return base_templates
        merged = dict(base_templates)
        for event_type, payload in overlay_templates.items():
            merged[event_type] = payload
        return merged
