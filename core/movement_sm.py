"""
Layer A: 移动状态机 — 只管位置，不管动画内容。

状态: IDLE / FOLLOW_MOUSE / WANDER / CARRIED / SLEEP
提供 get_default_anim_tags() 供 Layer B 为 NONE 时使用。
"""
import math
from enum import Enum
from PyQt6.QtCore import QPoint
from config import (
    TICK_MS, NEAR_MOUSE_PX, WALK_THRESHOLD_PX,
    WALK_SPEED_PX_S, AUTONOMOUS_INTERVAL_S,
)


class MoveState(Enum):
    IDLE = "idle"
    FOLLOW_MOUSE = "follow_mouse"
    WANDER = "wander"
    CARRIED = "carried"
    SLEEP = "sleep"


class MovementStateMachine:
    def __init__(self, follow_mouse_enabled: bool = False):
        self.state = MoveState.IDLE
        self._follow_mouse_enabled = follow_mouse_enabled
        self._target = QPoint(0, 0)
        self._auto_ticks_remaining = 0
        self._ticks_per_interval = int(AUTONOMOUS_INTERVAL_S * 1000 / TICK_MS)

    def _default_state(self) -> MoveState:
        return MoveState.FOLLOW_MOUSE if self._follow_mouse_enabled else MoveState.IDLE

    # ── 状态转换（仅由 EventArbiter 调用）──────────────

    def enter_carried(self):
        self.state = MoveState.CARRIED

    def enter_sleep(self):
        if self.state != MoveState.CARRIED:
            self.state = MoveState.SLEEP
            self._reset_autonomous_timer()

    def enter_wander(self, target: QPoint):
        self._target = target
        self.state = MoveState.WANDER
        print(
            f"[AI-DIAG][MoveSM] enter_wander target=({target.x()},{target.y()})",
            flush=True,
        )

    def enter_default(self):
        self.state = self._default_state()
        self._reset_autonomous_timer()

    def on_wander_arrived(self):
        print("[AI-DIAG][MoveSM] wander_arrived -> default", flush=True)
        self.enter_default()

    # ── 玩闹模式 ──────────────────────────────────────

    def set_follow_mouse(self, enabled: bool):
        self._follow_mouse_enabled = enabled
        if self.state in (MoveState.IDLE, MoveState.FOLLOW_MOUSE):
            self.state = self._default_state()

    @property
    def follow_mouse_enabled(self) -> bool:
        return self._follow_mouse_enabled

    def update_mouse_target(self, mouse_pos: QPoint):
        if self._follow_mouse_enabled and self.state == MoveState.FOLLOW_MOUSE:
            self._target = mouse_pos

    # ── 自主行为计时 ──────────────────────────────────

    def tick_autonomous_timer(self) -> bool:
        if self.state in (MoveState.CARRIED, MoveState.SLEEP, MoveState.IDLE):
            return False
        if self._auto_ticks_remaining > 0:
            self._auto_ticks_remaining -= 1
            return False
        return True

    def _reset_autonomous_timer(self):
        self._auto_ticks_remaining = self._ticks_per_interval

    # ── 查询 ──────────────────────────────────────────

    def get_target_pos(self) -> QPoint:
        return self._target

    def should_mirror(self, current: QPoint) -> bool:
        return self._target.x() < current.x()

    def is_near_target(self, current: QPoint) -> bool:
        return self._dist(current, self._target) <= NEAR_MOUSE_PX

    def is_walking(self, current: QPoint) -> bool:
        return self._dist(current, self._target) > WALK_THRESHOLD_PX

    def get_default_anim_tags(self) -> list[str]:
        return {
            MoveState.IDLE:         ["idle", "neutral"],
            MoveState.FOLLOW_MOUSE: ["idle", "neutral"],
            MoveState.WANDER:       ["walking", "wander"],
            MoveState.CARRIED:      ["carried", "scared"],
            MoveState.SLEEP:        ["sleeping", "sleep"],
        }.get(self.state, ["idle"])

    @staticmethod
    def _dist(a: QPoint, b: QPoint) -> float:
        return math.sqrt((a.x() - b.x()) ** 2 + (a.y() - b.y()) ** 2)
