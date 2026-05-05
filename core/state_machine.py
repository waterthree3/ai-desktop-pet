import math
from PyQt6.QtCore import QPoint
from core.anim_state import AnimState
from config import WALK_THRESHOLD_PX, NEAR_MOUSE_PX, AUTONOMOUS_INTERVAL_S, TICK_MS


class StateMachine:
    def __init__(self, library_manager):
        self._lib             = library_manager
        self.state            = AnimState.IDLE
        self._target          = QPoint(0, 0)
        self._event_gif: str | None = None
        self._auto_tags: list = []
        # 自主行为 5min 计时（帧数计数）
        self._auto_ticks_remaining = 0   # 0 = 立即可触发
        self._ticks_per_interval   = int(AUTONOMOUS_INTERVAL_S * 1000 / TICK_MS)
        # 玩闹模式：关闭时宠物停在原地，不追鼠标
        self._follow_mouse_enabled = False

    def _default_state(self) -> AnimState:
        """玩闹模式开启 → FOLLOW_MOUSE，关闭 → IDLE"""
        return AnimState.FOLLOW_MOUSE if self._follow_mouse_enabled else AnimState.IDLE

    # ── 外部驱动 ──────────────────────────────────────────

    def trigger_event(self, gif_path: str) -> None:
        """最高优先级，打断所有状态。"""
        self._event_gif = gif_path
        self.state = AnimState.EVENT

    def finish_event(self) -> None:
        self._event_gif = None
        self.state = self._default_state()
        self._reset_autonomous_timer()

    def set_autonomous(self, tags: list, target: QPoint) -> None:
        """进入自主行为，忽略鼠标追随。"""
        if self.state == AnimState.EVENT:
            return   # EVENT 不可被打断
        self._auto_tags = tags
        self._target    = target
        self.state      = AnimState.AUTONOMOUS

    def finish_autonomous(self) -> None:
        self.state = self._default_state()
        self._reset_autonomous_timer()

    def force_interrupt(self, gif_path: str) -> None:
        """属性阈值强制打断（优先级低于 EVENT，用于 FORCE_HUNGRY / DROWSY 等一次性动画）。"""
        if self.state == AnimState.EVENT:
            return
        self._event_gif = gif_path
        self.state = AnimState.EVENT
        self._reset_autonomous_timer()

    def force_sleep(self) -> None:
        """进入睡眠状态（循环播放，不用 EVENT 机制，能量恢复后自动唤醒）。
        阈值级别：无条件覆盖除 CARRIED 外的所有状态。"""
        if self.state == AnimState.CARRIED:
            return
        self._event_gif = None
        self.state = AnimState.SLEEP
        self._reset_autonomous_timer()

    def wake_up(self) -> None:
        """从睡眠中唤醒，回到正常状态。"""
        if self.state == AnimState.SLEEP:
            self.state = self._default_state()
            self._reset_autonomous_timer()

    def start_drag(self) -> None:
        # 拖拽是最高优先级的用户行为，无条件覆盖所有状态（包括 EVENT）
        self._event_gif = None
        self.state = AnimState.CARRIED

    def stop_drag(self) -> None:
        if self.state == AnimState.CARRIED:
            self.state = self._default_state()
            self._reset_autonomous_timer()   # 防止 drag 结束后立即触发自主漂移

    def trigger_startled(self, gif_path: str) -> None:
        if self.state in (AnimState.EVENT, AnimState.CARRIED):
            return
        self._event_gif = gif_path
        self.state = AnimState.STARTLED

    def finish_startled(self) -> None:
        if self.state == AnimState.STARTLED:
            self._event_gif = None
            self.state = self._default_state()

    def set_follow_mouse(self, enabled: bool) -> None:
        """开启/关闭玩闹模式（追鼠标）。"""
        self._follow_mouse_enabled = enabled
        # 在 IDLE / FOLLOW_MOUSE 之间切换（不打断 EVENT 等高优先级状态）
        if self.state in (AnimState.IDLE, AnimState.FOLLOW_MOUSE):
            self.state = self._default_state()

    @property
    def follow_mouse_enabled(self) -> bool:
        return self._follow_mouse_enabled

    def update_mouse_target(self, mouse_pos: QPoint) -> None:
        """玩闹模式开启时，FOLLOW_MOUSE 状态每帧更新目标为鼠标位置。"""
        if self._follow_mouse_enabled and self.state == AnimState.FOLLOW_MOUSE:
            self._target = mouse_pos

    def tick_autonomous_timer(self) -> bool:
        """每帧调用，返回 True 表示本帧应触发自主行为决策。"""
        if self.state in (AnimState.EVENT, AnimState.STARTLED, AnimState.CARRIED, AnimState.SLEEP, AnimState.IDLE):
            return False
        if self._auto_ticks_remaining > 0:
            self._auto_ticks_remaining -= 1
            return False
        return True   # 计时结束，可触发

    # ── 查询 ──────────────────────────────────────────────

    def get_target_pos(self) -> QPoint:
        return self._target

    def should_mirror(self, current: QPoint) -> bool:
        return self._target.x() < current.x()

    def get_current_gif_path(self) -> str | None:
        if self.state in (AnimState.EVENT, AnimState.STARTLED):
            return self._event_gif
        tag_map = {
            AnimState.IDLE:         ["idle", "neutral"],
            AnimState.FOLLOW_MOUSE: ["idle", "neutral"],
            AnimState.AUTONOMOUS:   self._auto_tags or ["idle"],
            AnimState.CARRIED:      ["carried", "scared"],
            AnimState.SLEEP:        ["sleeping", "sleep"],
        }
        tags = tag_map.get(self.state, ["idle"])
        match = self._lib.find_or_fallback(tags)
        if match is None:
            return None
        # BUG-14: if no animation matches these tags (score==0), don't play a random wrong animation
        if self.state in (AnimState.IDLE, AnimState.FOLLOW_MOUSE, AnimState.AUTONOMOUS) and match.score == 0.0:
            return None
        return match.file_path

    def is_near_target(self, current: QPoint) -> bool:
        return self._dist(current, self._target) <= NEAR_MOUSE_PX

    def is_walking(self, current: QPoint) -> bool:
        return self._dist(current, self._target) > WALK_THRESHOLD_PX

    # ── 内部 ──────────────────────────────────────────────

    def _reset_autonomous_timer(self) -> None:
        self._auto_ticks_remaining = self._ticks_per_interval

    @staticmethod
    def _dist(a: QPoint, b: QPoint) -> float:
        return math.sqrt((a.x() - b.x()) ** 2 + (a.y() - b.y()) ** 2)
