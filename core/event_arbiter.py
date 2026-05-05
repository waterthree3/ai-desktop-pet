"""
EventArbiter — 统一仲裁所有事件请求。

Phase 1: validate()   前置校验（冷却、属性门槛、AI 合理性）
Phase 2: arbitrate()  优先级 x EventKind 二维仲裁
Phase 3: cancel()     取消旧事件（停动画 + 清 recovery + 断回调）
Phase 4: dispatch()   路由到 Layer A / Layer B
"""
import time
from pathlib import Path
from config import (
    ENERGY_FORCE_THRESHOLD, ENERGY_DROWSY_THRESHOLD,
    HUNGER_FEED_RECOVERY, ENERGY_SLEEP_RECOVERY,
    CLEAN_BATH_RECOVERY, MOOD_PLAY_RECOVERY,
)
from core.interaction_capabilities import (
    is_interaction_supported,
    manual_interaction_types,
    unsupported_interaction_reason,
)
from core.event_request import (
    EventRequest, ArbiterResult,
    EventSource, EventPriority, EventKind, MovementIntent,
)


# InteractionMap 中的冷却秒数，按 event_type 查
_COOLDOWN_MAP = {
    "play": 600,
    "bath": 3600,
}

_RECOVERY_RATE = {
    "hunger": HUNGER_FEED_RECOVERY,
    "energy": ENERGY_SLEEP_RECOVERY,
    "cleanliness": CLEAN_BATH_RECOVERY,
    "mood": MOOD_PLAY_RECOVERY,
}

_AI_FALLBACK_REUSE_THRESHOLD = 0.5
_MANUAL_INTERACTION_TYPES = set(manual_interaction_types())


def _normalize_behavior_token(value: str | None) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _match_behavior_token(match) -> str:
    explicit = _normalize_behavior_token(getattr(match, "behavior_type", ""))
    if explicit:
        return explicit
    for tag in getattr(match, "tags", []) or []:
        normalized = _normalize_behavior_token(tag)
        if normalized:
            return normalized
    return ""


def _is_exact_behavior_reuse(req: EventRequest, match) -> bool:
    requested = _normalize_behavior_token(getattr(req, "behavior_type", ""))
    if not requested:
        return False
    if requested == _match_behavior_token(match):
        return True
    return requested in {
        _normalize_behavior_token(tag)
        for tag in getattr(match, "tags", []) or []
        if _normalize_behavior_token(tag)
    }


def _should_defer_ai_base_reuse(req: EventRequest, match) -> bool:
    if req.source != EventSource.AI:
        return False
    if not req.generate_if_missing or not str(req.action_desc or "").strip():
        return False
    if str(getattr(match, "source", "")).strip().lower() != "user_provided":
        return False
    return not _is_exact_behavior_reuse(req, match)


def _request_prefers_idle_reuse(req: EventRequest) -> bool:
    behavior = _normalize_behavior_token(getattr(req, "behavior_type", ""))
    if behavior in {"idle", "idle_normal"}:
        return True
    tags = {
        _normalize_behavior_token(tag)
        for tag in getattr(req, "anim_tags", []) or []
        if _normalize_behavior_token(tag)
    }
    return bool(tags & {"idle", "neutral", "calm"})


def _is_match_allowed_for_request(req: EventRequest, match) -> bool:
    scope = str(getattr(match, "reuse_scope", "") or "any").strip().lower()
    if not scope or scope == "any":
        return True
    if scope == "direct_only":
        return req.source == EventSource.USER
    if scope == "threshold_only":
        return req.source == EventSource.THRESHOLD
    if scope == "idle_only":
        return _request_prefers_idle_reuse(req)
    if scope == "ai_exact_only":
        return req.source == EventSource.USER or (
            req.source == EventSource.AI and _is_exact_behavior_reuse(req, match)
        )
    return True


def _should_apply_animation_effect(req: EventRequest) -> bool:
    # Sleep state recovery is owned by the runtime sleep loop, not the clip's
    # effect profile, so all stateful sleep entries behave consistently.
    return not (req.kind == EventKind.STATE and req.movement == MovementIntent.SLEEP)


class EventArbiter:
    def __init__(self, move_sm, event_anim_layer, pet_data, library_mgr,
                 on_dialogue=None, on_stats_update=None):
        self._move = move_sm
        self._anim = event_anim_layer
        self._pet = pet_data
        self._lib = library_mgr
        self._on_dialogue = on_dialogue
        self._on_stats_update = on_stats_update

        self._current: EventRequest | None = None
        self._cooldowns: dict[str, float] = {}
        self._rejection_log: list[dict] = []
        self._active_effect_profile: dict | None = None
        self._pending_effect_progressive = {
            "hunger": 0.0, "energy": 0.0, "cleanliness": 0.0, "mood": 0.0,
        }

        # 渐进恢复
        self._pending_recovery = {
            "hunger": 0.0, "energy": 0.0, "cleanliness": 0.0, "mood": 0.0,
        }
        self._pending_recovery_rates = {
            "hunger": 0.0, "energy": 0.0, "cleanliness": 0.0, "mood": 0.0,
        }

        # ComfyUI 生成追踪
        self._pending_generation_id: str | None = None
        self._generation_seq = 0

    @property
    def current(self) -> EventRequest | None:
        return self._current

    @property
    def pending_recovery(self) -> dict:
        return self._pending_recovery

    # ── 主入口 ────────────────────────────────────────

    def request(self, req: EventRequest) -> ArbiterResult:
        # Phase 1: 前置校验
        result = self._validate(req)
        if not result.accepted:
            if result.downgraded and req.dialogue:
                self._show_dialogue(req.dialogue)
            self._log_rejection(req, result)
            return result

        # Phase 2: 二维仲裁
        result = self._arbitrate(req)
        if not result.accepted:
            if result.downgraded and req.dialogue:
                self._show_dialogue(req.dialogue)
            self._log_rejection(req, result)
            return result

        # Phase 3: 取消旧事件
        if self._current:
            self._cancel_current()

        # Phase 4: 路由分派
        self._current = req
        self._dispatch(req)
        return ArbiterResult(accepted=True)

    # ── Phase 1: 前置校验 ────────────────────────────

    def _validate(self, req: EventRequest) -> ArbiterResult:
        if (
            req.source == EventSource.USER
            and req.event_type in _MANUAL_INTERACTION_TYPES
            and not is_interaction_supported(self._lib, req.event_type)
        ):
            return ArbiterResult(False, unsupported_interaction_reason(req.event_type))
        # 冷却
        cd = _COOLDOWN_MAP.get(req.event_type, 0)
        if cd > 0 and req.event_type in self._cooldowns:
            now = time.monotonic()
            last = self._cooldowns[req.event_type]
            if (now - last) < cd:
                remaining = int(cd - (now - last))
                return ArbiterResult(False, f"cooldown:{remaining}s")

        # SLEEP 中被阻塞的交互
        if req.blocked_while_sleep:
            from core.movement_sm import MoveState
            if self._move.state == MoveState.SLEEP or self._pet.energy <= ENERGY_FORCE_THRESHOLD:
                return ArbiterResult(False, "blocked_while_sleep")

        # 低能量阻塞
        if req.blocked_low_energy and self._pet.energy <= ENERGY_DROWSY_THRESHOLD:
            return ArbiterResult(False, "blocked_low_energy")

        # AI 专属校验
        if req.source == EventSource.AI:
            return self._validate_ai(req)

        return ArbiterResult(True)

    def _validate_ai(self, req: EventRequest) -> ArbiterResult:
        rules = {
            "ai_beg_food": lambda: self._pet.hunger > 50,
            "ai_sleep":    lambda: self._pet.energy > 50,
            "ai_play":     lambda: self._pet.energy <= ENERGY_FORCE_THRESHOLD,
            "ai_wander":   lambda: self._pet.energy <= ENERGY_DROWSY_THRESHOLD,
        }
        check = rules.get(req.event_type)
        if check and check():
            info = f"hunger={self._pet.hunger:.0f},energy={self._pet.energy:.0f}"
            return ArbiterResult(False, f"ai_attr_conflict:{req.event_type}:{info}")
        return ArbiterResult(True)

    # ── Phase 2: 二维仲裁 ────────────────────────────

    def _arbitrate(self, req: EventRequest) -> ArbiterResult:
        if not self._current:
            return ArbiterResult(True)

        cur = self._current

        # 规则 1: USER_PHYSICAL 无条件覆盖
        if req.priority >= EventPriority.USER_PHYSICAL:
            return ArbiterResult(True)

        # 规则 2: STATE 保护
        if cur.kind == EventKind.STATE:
            if req.kind == EventKind.NOTIFY:
                return ArbiterResult(False, "downgraded_to_notify", downgraded=True)

            if req.kind == EventKind.ACTION:
                if self._can_wake(req):
                    return ArbiterResult(True)
                return ArbiterResult(False, f"state_protected:{cur.event_type}")

            # STATE vs STATE
            if req.priority > cur.priority:
                return ArbiterResult(True)
            return ArbiterResult(False, f"state_same_or_lower:{cur.event_type}")

        # 规则 3: ACTION 对 ACTION
        if cur.kind == EventKind.ACTION:
            if req.priority > cur.priority:
                return ArbiterResult(True)
            if req.priority == cur.priority:
                return ArbiterResult(False, f"same_priority:{cur.event_type}")
            return ArbiterResult(False, f"lower_priority:{cur.event_type}")

        # NOTIFY 当前事件不阻塞任何新事件
        return ArbiterResult(True)

    def _can_wake(self, req: EventRequest) -> bool:
        from core.movement_sm import MoveState
        if self._move.state != MoveState.SLEEP:
            return False
        return req.wakes_from_sleep and self._pet.energy > ENERGY_FORCE_THRESHOLD

    # ── Phase 3: 取消旧事件 ──────────────────────────

    def _cancel_current(self):
        if self._current:
            print(
                f"[AI-DIAG][EventArbiter] cancel current event={self._current.event_type} "
                f"kind={self._current.kind.value} pending_generation_id={self._pending_generation_id}",
                flush=True,
            )
        self._anim.stop()
        self._discard_pending_recovery()
        self._discard_pending_effect_progressive()
        self._pending_generation_id = None
        self._current = None

    def _discard_pending_recovery(self):
        for attr in self._pending_recovery:
            self._pending_recovery[attr] = 0.0
            self._pending_recovery_rates[attr] = 0.0

    def _discard_pending_effect_progressive(self):
        self._active_effect_profile = None
        for attr in self._pending_effect_progressive:
            self._pending_effect_progressive[attr] = 0.0

    # ── Phase 4: 路由分派 ────────────────────────────

    def _dispatch(self, req: EventRequest):
        # 冷却记录
        cd = _COOLDOWN_MAP.get(req.event_type, 0)
        if cd > 0:
            self._cooldowns[req.event_type] = time.monotonic()

        # 属性变化
        self._apply_attr_deltas(req)

        # Layer A: 移动
        self._dispatch_movement(req)

        # Layer B: 动画
        has_layer_b_work = self._dispatch_animation(req)

        # 对话
        if req.dialogue:
            self._show_dialogue(req.dialogue)

        if not has_layer_b_work and req.kind != EventKind.STATE:
            self._on_event_done()

    def _apply_attr_deltas(self, req: EventRequest):
        if not req.attr_deltas:
            return

        has_anim = bool(req.anim_tags) or bool(req.anim_id)
        recovery_rates = getattr(req, "recovery_rates", {}) or {}

        for attr, delta in req.attr_deltas.items():
            if delta < 0:
                # 负值立即扣除
                old = getattr(self._pet, attr)
                setattr(self._pet, attr, max(0.0, old + delta))
            elif delta > 0:
                if has_anim and req.recovery_mode == "progressive":
                    # 清零旧 pending 后排队
                    self._pending_recovery[attr] = float(delta)
                    self._pending_recovery_rates[attr] = float(recovery_rates.get(attr, 0) or 0.0)
                else:
                    # 无动画或 immediate 模式：立即生效
                    old = getattr(self._pet, attr)
                    setattr(self._pet, attr, min(100.0, old + delta))
                    self._pending_recovery_rates[attr] = 0.0

        self._update_stats()

    def _dispatch_movement(self, req: EventRequest):
        from core.movement_sm import MoveState
        # 唤醒特例：wakes_from_sleep 的 ACTION 打断 SLEEP 后，Layer A 回默认
        if req.wakes_from_sleep and self._move.state == MoveState.SLEEP:
            self._move.enter_default()
            return

        match req.movement:
            case MovementIntent.CARRIED:
                self._move.enter_carried()
            case MovementIntent.SLEEP:
                self._move.enter_sleep()
            case MovementIntent.WANDER:
                self._move.enter_wander(req.target_pos)
            case MovementIntent.RETURN_DEFAULT:
                self._move.enter_default()
            case MovementIntent.STAY:
                pass

    def _dispatch_animation(self, req: EventRequest) -> bool:
        match = self._resolve_animation(req)
        if match:
            self._activate_animation_effect(req, match)
            if req.anim_loop or self._has_pending_recovery() or self._has_pending_effect_progressive():
                self._anim.play_loop(match.file_path, on_done=self._on_event_done)
            else:
                self._anim.play_once(match.file_path, on_done=self._on_event_done)
            return True
        elif req.generate_if_missing and req.action_desc:
            print(
                f"[AI-DIAG][EventArbiter] no base animation matched event={req.event_type} "
                f"behavior={req.behavior_type or '-'} tags={req.anim_tags} "
                f"action_desc={req.action_desc!r}; scheduling generation",
                flush=True,
            )
            self._pending_generation_id = self._next_generation_id()
            self._play_generation_placeholder(req)
            return True
        print(
            f"[AI-DIAG][EventArbiter] no animation work for event={req.event_type} "
            f"behavior={req.behavior_type or '-'} tags={req.anim_tags} "
            f"generate_if_missing={req.generate_if_missing}",
            flush=True,
        )
        # else: 无动画也无生成需求 — 静默完成

        return False

    def _resolve_animation(self, req: EventRequest):
        if req.anim_id:
            entry = self._lib.get_by_id(req.anim_id)
            if entry:
                print(
                    f"[AI-DIAG][EventArbiter] animation matched by id event={req.event_type} "
                    f"anim_id={req.anim_id} file={entry.file_path}",
                    flush=True,
                )
                return entry

        if req.anim_tags:
            match = self._lib.find(req.anim_tags)
            if match and match.score > 0:
                if not _is_match_allowed_for_request(req, match):
                    print(
                        f"[AI-DIAG][EventArbiter] matched animation blocked by reuse_scope "
                        f"event={req.event_type} behavior={req.behavior_type or '-'} "
                        f"matched_id={getattr(match, 'animation_id', 'unknown')} "
                        f"reuse_scope={getattr(match, 'reuse_scope', 'any')}",
                        flush=True,
                    )
                    return None
                if _should_defer_ai_base_reuse(req, match):
                    print(
                        f"[AI-DIAG][EventArbiter] exact-score base reuse deferred to generation "
                        f"event={req.event_type} behavior={req.behavior_type or '-'} "
                        f"requested_tags={req.anim_tags} matched_id={getattr(match, 'animation_id', 'unknown')} "
                        f"matched_behavior={_match_behavior_token(match) or '-'} score={match.score:.3f}",
                        flush=True,
                    )
                    return None
                match_id = getattr(match, "animation_id", getattr(match, "id", "unknown"))
                match_tags = getattr(match, "tags", req.anim_tags)
                print(
                    f"[AI-DIAG][EventArbiter] animation matched event={req.event_type} "
                    f"behavior={req.behavior_type or '-'} requested_tags={req.anim_tags} "
                    f"matched_id={match_id} matched_tags={match_tags} "
                    f"score={match.score:.3f} source={match.source} file={match.file_path}",
                    flush=True,
                )
                return match
            fallback = self._lib.find_or_fallback(req.anim_tags)
            if fallback and fallback.score > 0:
                if not _is_match_allowed_for_request(req, fallback):
                    print(
                        f"[AI-DIAG][EventArbiter] fallback animation blocked by reuse_scope "
                        f"event={req.event_type} behavior={req.behavior_type or '-'} "
                        f"matched_id={getattr(fallback, 'animation_id', 'unknown')} "
                        f"reuse_scope={getattr(fallback, 'reuse_scope', 'any')}",
                        flush=True,
                    )
                    return None
                if _should_defer_ai_base_reuse(req, fallback):
                    fallback_id = getattr(fallback, "animation_id", getattr(fallback, "id", "unknown"))
                    fallback_tags = getattr(fallback, "tags", req.anim_tags)
                    print(
                        f"[AI-DIAG][EventArbiter] broad base reuse deferred to generation "
                        f"event={req.event_type} behavior={req.behavior_type or '-'} "
                        f"requested_tags={req.anim_tags} matched_id={fallback_id} "
                        f"matched_behavior={_match_behavior_token(fallback) or '-'} matched_tags={fallback_tags} "
                        f"score={fallback.score:.3f}",
                        flush=True,
                    )
                    return None
                if self._should_prefer_generation(req, fallback.score):
                    fallback_id = getattr(fallback, "animation_id", getattr(fallback, "id", "unknown"))
                    fallback_tags = getattr(fallback, "tags", req.anim_tags)
                    print(
                        f"[AI-DIAG][EventArbiter] animation fallback deferred to generation "
                        f"event={req.event_type} behavior={req.behavior_type or '-'} "
                        f"requested_tags={req.anim_tags} matched_id={fallback_id} "
                        f"matched_tags={fallback_tags} score={fallback.score:.3f} "
                        f"threshold={_AI_FALLBACK_REUSE_THRESHOLD:.3f}",
                        flush=True,
                    )
                    return None
                fallback_id = getattr(fallback, "animation_id", getattr(fallback, "id", "unknown"))
                fallback_tags = getattr(fallback, "tags", req.anim_tags)
                print(
                    f"[AI-DIAG][EventArbiter] animation fallback matched event={req.event_type} "
                    f"behavior={req.behavior_type or '-'} requested_tags={req.anim_tags} "
                    f"matched_id={fallback_id} matched_tags={fallback_tags} "
                    f"score={fallback.score:.3f} source={fallback.source} file={fallback.file_path}",
                    flush=True,
                )
                return fallback

        if req.anim_fallback:
            print(
                f"[AI-DIAG][EventArbiter] animation using explicit fallback event={req.event_type} "
                f"file={req.anim_fallback}",
                flush=True,
            )
            return type("FallbackMatch", (), {"file_path": req.anim_fallback, "effect_profile": None})()
        return None

    @staticmethod
    def _should_prefer_generation(req: EventRequest, fallback_score: float) -> bool:
        return (
            req.source == EventSource.AI
            and req.generate_if_missing
            and bool(req.action_desc)
            and fallback_score < _AI_FALLBACK_REUSE_THRESHOLD
        )

    def _play_generation_placeholder(self, req: EventRequest) -> None:
        if req.source != EventSource.AI:
            return
        idle_match = self._lib.find(["idle", "neutral"])
        if not idle_match:
            idle_match = self._lib.find_or_fallback(["idle", "neutral"])
        if not idle_match or idle_match.score <= 0:
            return
        print(
            f"[AI-DIAG][EventArbiter] generation placeholder event={req.event_type} "
            f"file={idle_match.file_path} score={idle_match.score:.3f}",
            flush=True,
        )
        self._anim.play_loop(idle_match.file_path, on_done=self._on_event_done)

    # ── 事件完成 ─────────────────────────────────────

    def _on_event_done(self):
        """Layer B 动画播放完毕或被主动 finish() 时调用"""
        self._flush_pending_recovery()
        profile = self._active_effect_profile
        self._flush_pending_effect_progressive()
        if profile:
            self._pet.apply_effect_profile(profile, "on_finish")
        self._discard_pending_effect_progressive()
        old = self._current
        self._current = None

        # 如果是 STATE 型事件结束，Layer A 也要回默认
        if old and old.kind == EventKind.STATE:
            self._move.enter_default()

        self._update_stats()

    def on_event_done(self):
        """公开接口，供 pet_window 回调"""
        self._on_event_done()

    # ── 渐进恢复 ─────────────────────────────────────

    def finish_current_event(self):
        """Gracefully finish the current event from an external condition."""
        from ui.event_anim_layer import EventAnimState

        if self._anim.state != EventAnimState.NONE:
            print(
                f"[AI-DIAG][EventArbiter] finish_current_event finishing active animation "
                f"state={self._anim.state.value} current_event={getattr(self._current, 'event_type', None)}",
                flush=True,
            )
            self._anim.finish()
            return
        self._on_event_done()

    def stop_current_event(self):
        """Force-stop the current event and discard any pending recovery."""
        old = self._current
        if old is None:
            return

        print(
            f"[AI-DIAG][EventArbiter] stop_current_event current_event={old.event_type} "
            f"kind={old.kind.value}",
            flush=True,
        )
        self._cancel_current()
        if old.kind == EventKind.STATE:
            self._move.enter_default()
        self._update_stats()

    def recovery_tick(self, dt: float = 1.0):
        """每秒调用一次，逐步恢复 pending 属性"""
        had_legacy_recovery = self._has_pending_recovery()
        has_effect_progressive = self._has_pending_effect_progressive()
        if not had_legacy_recovery and not has_effect_progressive:
            return

        changed = False
        changed = self._step_pending_recovery(dt) or changed
        changed = self._step_pending_effect_progressive(dt) or changed
        if changed:
            self._update_stats()

        # 全部恢复完毕 → 结束循环动画
        all_done = (
            (had_legacy_recovery and not self._has_pending_recovery())
            or (has_effect_progressive and not self._has_pending_effect_progressive())
        )
        if all_done and not self._has_pending_recovery() and not self._has_pending_effect_progressive():
            from ui.event_anim_layer import EventAnimState
            if self._anim.state == EventAnimState.PLAYING_LOOP:
                self._anim.finish()

    def _has_pending_recovery(self) -> bool:
        return any(v > 0 for v in self._pending_recovery.values())

    def _flush_pending_recovery(self):
        for attr in ("hunger", "energy", "cleanliness", "mood"):
            remaining = self._pending_recovery[attr]
            if remaining <= 0:
                self._pending_recovery_rates[attr] = 0.0
                continue
            old_val = getattr(self._pet, attr)
            new_val = min(100.0, old_val + remaining)
            setattr(self._pet, attr, new_val)
            self._pending_recovery[attr] = 0.0
            self._pending_recovery_rates[attr] = 0.0

    def _step_pending_recovery(self, dt: float) -> bool:
        changed = False
        for attr in ("hunger", "energy", "cleanliness", "mood"):
            remaining = self._pending_recovery[attr]
            if remaining <= 0:
                self._pending_recovery_rates[attr] = 0.0
                continue
            rate = float(self._pending_recovery_rates.get(attr, 0) or 0.0)
            if rate <= 0:
                rate = _RECOVERY_RATE.get(attr, 1.0)
            delta = min(rate * dt, remaining)
            old_val = getattr(self._pet, attr)
            new_val = min(100.0, old_val + delta)
            setattr(self._pet, attr, new_val)
            self._pending_recovery[attr] = remaining - delta
            if self._pending_recovery[attr] <= 0:
                self._pending_recovery[attr] = 0.0
                self._pending_recovery_rates[attr] = 0.0
            changed = changed or abs(new_val - old_val) > 1e-6
        return changed

    def _activate_animation_effect(self, req: EventRequest, match) -> None:
        if not _should_apply_animation_effect(req):
            self._active_effect_profile = None
            self._discard_pending_effect_progressive()
            return
        profile = getattr(match, "effect_profile", None)
        if not isinstance(profile, dict):
            return
        self._active_effect_profile = profile
        settlement = profile.get("settlement") or {}
        progressive = settlement.get("progressive") or {}
        for attr in self._pending_effect_progressive:
            self._pending_effect_progressive[attr] = float(progressive.get(attr, 0) or 0.0)
        self._pet.apply_effect_profile(profile, "on_start")
        self._update_stats()

    def _has_pending_effect_progressive(self) -> bool:
        return any(abs(v) > 1e-6 for v in self._pending_effect_progressive.values())

    def _step_pending_effect_progressive(self, dt: float) -> bool:
        changed = False
        for attr in ("hunger", "energy", "cleanliness", "mood"):
            remaining = float(self._pending_effect_progressive[attr])
            if abs(remaining) <= 1e-6:
                continue
            rate = _RECOVERY_RATE.get(attr, 1.0)
            delta_mag = min(rate * dt, abs(remaining))
            delta = delta_mag if remaining > 0 else -delta_mag
            old_val = getattr(self._pet, attr)
            new_val = max(0.0, min(100.0, old_val + delta))
            applied = new_val - old_val
            setattr(self._pet, attr, new_val)
            if abs(applied) <= 1e-6:
                self._pending_effect_progressive[attr] = 0.0
                continue
            self._pending_effect_progressive[attr] = remaining - applied
            if abs(self._pending_effect_progressive[attr]) <= 1e-6:
                self._pending_effect_progressive[attr] = 0.0
            changed = True
        return changed

    def _flush_pending_effect_progressive(self) -> None:
        for attr in ("hunger", "energy", "cleanliness", "mood"):
            remaining = float(self._pending_effect_progressive[attr])
            if abs(remaining) <= 1e-6:
                self._pending_effect_progressive[attr] = 0.0
                continue
            old_val = getattr(self._pet, attr)
            new_val = max(0.0, min(100.0, old_val + remaining))
            setattr(self._pet, attr, new_val)
            self._pending_effect_progressive[attr] = 0.0

    # ── AI 拒绝记录 ─────────────────────────────────

    def _log_rejection(self, req: EventRequest, result: ArbiterResult):
        if req.source == EventSource.AI:
            self._rejection_log.append({
                "event_type": req.event_type,
                "behavior_type": req.behavior_type,
                "anim_tags": list(req.anim_tags),
                "action_desc": req.action_desc,
                "reason": result.reason,
                "timestamp": time.monotonic(),
            })
            self._rejection_log = self._rejection_log[-5:]

    def get_ai_rejection_context(self) -> list[dict]:
        return self._rejection_log.copy()

    # ── ComfyUI 生成 ────────────────────────────────

    def get_pending_generation_id(self) -> str | None:
        return self._pending_generation_id

    def on_generation_done(self, generation_id: str, gif_path: str | None):
        if self._pending_generation_id != generation_id:
            print(
                f"[AI-DIAG][EventArbiter] generation result ignored due to id mismatch "
                f"generation_id={generation_id} pending_generation_id={self._pending_generation_id} "
                f"current_event={getattr(self._current, 'event_type', None)} media={bool(gif_path)}",
                flush=True,
            )
            return

        self._pending_generation_id = None
        if not gif_path:
            print(
                f"[AI-DIAG][EventArbiter] generation finished without media; ending event "
                f"current_event={getattr(self._current, 'event_type', None)}",
                flush=True,
            )
            self._on_event_done()
            return

        current = self._current
        if current and not current.attr_deltas and not self._active_effect_profile:
            generated_id = Path(str(gif_path)).stem
            generated_match = self._lib.get_by_id(generated_id)
            if generated_match:
                self._activate_animation_effect(current, generated_match)
        should_loop = bool(current and (current.anim_loop or self._has_pending_recovery()))
        print(
            f"[AI-DIAG][EventArbiter] generation result attached generation_id={generation_id} "
            f"current_event={getattr(current, 'event_type', None)} should_loop={should_loop} "
            f"gif_path={gif_path}",
            flush=True,
        )
        if should_loop:
            self._anim.play_loop(gif_path, on_done=self._on_event_done)
        else:
            self._anim.play_once(gif_path, on_done=self._on_event_done)

    def _next_generation_id(self) -> str:
        self._generation_seq += 1
        return f"gen-{self._generation_seq}"

    # ── 辅助 ────────────────────────────────────────

    def _show_dialogue(self, text: str):
        if self._on_dialogue:
            self._on_dialogue(text)

    def _update_stats(self):
        if self._on_stats_update:
            self._on_stats_update()
