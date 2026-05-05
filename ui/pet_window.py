import time
import math
from PyQt6.QtWidgets import QApplication, QWidget, QMenu
from PyQt6.QtCore    import Qt, QTimer, QPoint, QSize, QRect
from PyQt6.QtGui     import QCursor
from ui.animation_layer import AnimationLayer
from ui.bubble_widget   import BubbleWidget
from ui.feedback_bar    import FeedbackBar
from core.movement_sm   import MoveState
from config             import (
    TICK_MS, POSITION_LERP, NEAR_MOUSE_PX, WALK_THRESHOLD_PX,
    NEAR_MOUSE_IDLE_S, DRAG_HOLD_MS,
    PET_W, PET_H, WALK_SPEED_PX_S
)
from core.interaction_capabilities import get_interaction_availability


def merge_default_anim_tags(base_tags: list[str], emotion: str | None) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for raw in [*base_tags, emotion]:
        tag = str(raw or "").strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        merged.append(tag)
    return merged


def _compact_dialogue_log(text: str, limit: int = 140) -> str:
    single = " ".join(str(text or "").split())
    if len(single) <= limit:
        return single
    if limit <= 3:
        return single[:limit]
    return single[: limit - 3] + "..."


def _step_wander_towards(current: QPoint, target: QPoint, step: float) -> QPoint:
    dx = target.x() - current.x()
    dy = target.y() - current.y()
    dist = math.sqrt(dx * dx + dy * dy)
    if dist <= step:
        return QPoint(target)

    raw_x = current.x() + dx / dist * step
    raw_y = current.y() + dy / dist * step
    new_x = round(raw_x)
    new_y = round(raw_y)

    if new_x == current.x() and new_y == current.y():
        if abs(dx) >= abs(dy) and dx != 0:
            new_x += 1 if dx > 0 else -1
        elif dy != 0:
            new_y += 1 if dy > 0 else -1

    return QPoint(new_x, new_y)


def _is_mouse_in_interaction_safe_zone(
    pet_pos: QPoint,
    pet_size: QSize,
    mouse_pos: QPoint,
) -> bool:
    # Keep a larger stable area on the right/bottom so the user can reach
    # the context menu and the floating stats panel without the pet chasing away.
    safe_rect = QRect(pet_pos, pet_size).adjusted(-36, -36, 240, 180)
    return safe_rect.contains(mouse_pos)


class PetWindow(QWidget):
    PET_SIZE = QSize(PET_W, PET_H)

    def __init__(self, move_sm, event_bus, library_manager):
        super().__init__()
        self._move_sm = move_sm
        self._bus     = event_bus
        self._lib     = library_manager
        self._event_anim = None          # EventAnimLayer, set via set_event_anim_layer()
        self._current_default_anim: str | None = None
        self._expression_emotion: str = "neutral"
        self._interaction_availability = get_interaction_availability(library_manager)
        # 鼠标速度追踪
        self._last_mouse: QPoint | None = None
        self._last_mouse_t: float = 0.0
        self._mouse_speed: float  = 0.0
        # 靠近计时
        self._near_mouse_ticks = 0
        self._near_mouse_threshold = int(NEAR_MOUSE_IDLE_S * 1000 / TICK_MS)
        # 拖拽检测
        self._press_pos: QPoint | None = None
        self._press_time: float = 0.0
        self._dragging = False
        self._move_callbacks = []
        # 延迟单击：等待双击窗口期过后再发送 click
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(
            lambda: self._bus.emit("interaction", {"type": "click"})
        )
        self._expression_timer = QTimer(self)
        self._expression_timer.setSingleShot(True)
        self._expression_timer.timeout.connect(self.clear_expression_emotion)
        self._setup_window()
        self._setup_widgets()
        self._setup_timer()

    def set_event_anim_layer(self, layer):
        """构造后设置 EventAnimLayer（需要 self._body 先存在）。"""
        self._event_anim = layer

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self.PET_SIZE)
        self.move(400, 400)

    def _setup_widgets(self) -> None:
        self._body   = AnimationLayer(self, size=self.PET_SIZE)
        self._body.move(0, 0)
        self._bubble = BubbleWidget(self)
        self._feedback = FeedbackBar(self)
        self._feedback.move(0, self.PET_SIZE.height() + 4)

    def _setup_timer(self) -> None:
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(TICK_MS)

    def _tick(self) -> None:
        now       = time.monotonic()
        mouse_pos = QCursor.pos()
        current   = self.pos()
        interaction_safe = _is_mouse_in_interaction_safe_zone(current, self.PET_SIZE, mouse_pos)

        # 鼠标速度计算
        if self._last_mouse is not None:
            dt = now - self._last_mouse_t
            if dt > 0:
                dx = mouse_pos.x() - self._last_mouse.x()
                dy = mouse_pos.y() - self._last_mouse.y()
                self._mouse_speed = math.sqrt(dx*dx + dy*dy) / dt
        self._last_mouse   = mouse_pos
        self._last_mouse_t = now

        # 更新鼠标追随目标
        if not interaction_safe:
            self._move_sm.update_mouse_target(mouse_pos)

        # 检查事件动画是否正在播放
        from ui.event_anim_layer import EventAnimState
        event_playing = (self._event_anim is not None
                         and self._event_anim.state != EventAnimState.NONE)

        # 靠近鼠标后的注视（仅在无事件动画时）
        if not event_playing and self._move_sm.state == MoveState.FOLLOW_MOUSE:
            if interaction_safe:
                self._near_mouse_ticks = 0
            elif self._move_sm.is_near_target(current) and self._mouse_speed < 5:
                self._near_mouse_ticks += 1
                if self._near_mouse_ticks >= self._near_mouse_threshold:
                    look_gif = self._lib.find_or_fallback(["look_at_cursor", "curious"])
                    if (look_gif and look_gif.score > 0
                            and look_gif.file_path != self._current_default_anim):
                        self._body.play_loop(look_gif.file_path)
                        self._current_default_anim = look_gif.file_path
            else:
                self._near_mouse_ticks = 0

        # 位移插值（IDLE / CARRIED / SLEEP 不移动）
        skip_move = (
            self._move_sm.state in (MoveState.IDLE, MoveState.CARRIED, MoveState.SLEEP)
            or self._dragging
            or (self._move_sm.state == MoveState.FOLLOW_MOUSE and interaction_safe)
            or (not self._move_sm.follow_mouse_enabled
                and self._move_sm.state != MoveState.WANDER)
        )
        if not skip_move:
            target = self._move_sm.get_target_pos()
            if self._move_sm.state == MoveState.WANDER:
                # 散步使用匀速移动
                step = WALK_SPEED_PX_S * TICK_MS / 1000.0
                next_pos = _step_wander_towards(current, target, step)
                if next_pos != current:
                    self.move(next_pos)
                current_after_move = next_pos
            else:
                # 追鼠标用 lerp
                new_x = int(current.x() + (target.x() - current.x()) * POSITION_LERP)
                new_y = int(current.y() + (target.y() - current.y()) * POSITION_LERP)
                self.move(new_x, new_y)
                current_after_move = QPoint(new_x, new_y)
        else:
            current_after_move = current

        # 默认动画（无事件动画时，根据移动状态选择）
        if not event_playing:
            match = self._find_default_animation_match()
            if match and self._should_play_default_animation(match.file_path):
                self._body.play_loop(match.file_path)
                self._current_default_anim = match.file_path
        else:
            # 事件动画播放中，清除默认动画追踪（事件结束后重新匹配）
            self._current_default_anim = None

        # 走路时每帧更新镜像
        if self._move_sm.is_walking(current_after_move):
            self._body.set_mirror(self._move_sm.should_mirror(current_after_move))

        # 散步到达目标点
        if self._move_sm.state == MoveState.WANDER and self._move_sm.is_near_target(current_after_move):
            target = self._move_sm.get_target_pos()
            print(
                f"[DBG][tick] WANDER reached target  pos=({current_after_move.x()},{current_after_move.y()}) "
                f"target=({target.x()},{target.y()})",
                flush=True,
            )
            self._move_sm.on_wander_arrived()

    # ── 鼠标事件 ──────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos  = event.pos()
            self._press_time = time.monotonic()
            self._dragging   = False

    def mouseMoveEvent(self, event) -> None:
        if self._press_pos is not None:
            elapsed_ms = (time.monotonic() - self._press_time) * 1000
            if elapsed_ms >= DRAG_HOLD_MS and not self._dragging:
                self._dragging = True
                self._bus.emit("interaction", {"type": "drag_start"})
            if self._dragging:
                self.move(event.globalPosition().toPoint() - self._press_pos)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dragging:
                self._bus.emit("interaction", {"type": "drag_end"})
            elif self._press_pos is not None:
                elapsed_ms = (time.monotonic() - self._press_time) * 1000
                if elapsed_ms < 300:
                    self._click_timer.start(QApplication.doubleClickInterval())
            self._press_pos = None
            self._dragging  = False

    def mouseDoubleClickEvent(self, event) -> None:
        self._click_timer.stop()
        self._bus.emit("interaction", {"type": "double_click"})

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.addAction("喂食 🍖").triggered.connect(lambda: self._bus.emit("interaction", {"type": "feed"}))
        menu.addAction("玩耍 🎾").triggered.connect(lambda: self._bus.emit("interaction", {"type": "play"}))
        menu.addAction("洗澡 🛁").triggered.connect(lambda: self._bus.emit("interaction", {"type": "bath"}))
        menu.addAction("抚摸 ✋").triggered.connect(lambda: self._bus.emit("interaction", {"type": "stroke"}))
        menu.addAction("聊天 💬").triggered.connect(lambda: self._bus.emit("chat", {"source": "context_menu"}))
        menu.addAction("图鉴 📚").triggered.connect(lambda: self._bus.emit("gallery", {"source": "context_menu"}))
        menu.addSeparator()
        menu.addAction("散步一下 🦮").triggered.connect(lambda: self._bus.emit("interaction", {"type": "walk_mode"}))
        for action, event_type in zip(menu.actions(), ("feed", "play", "bath", "stroke", None, None, None, "walk_mode")):
            if event_type is None:
                continue
            action.setEnabled(self._interaction_availability.get(event_type, True))
        follow_enabled = self._move_sm.follow_mouse_enabled
        menu.addAction("关闭玩闹模式" if follow_enabled else "开启玩闹模式").triggered.connect(
            lambda: self._bus.emit(
                "follow_mode",
                {"enabled": not follow_enabled, "source": "context_menu"},
            )
        )
        menu.exec(event.globalPos())

    # ── 移动事件绑定 ─────────────────────────────────────

    def on_move(self, callback) -> None:
        """注册窗口移动回调，callback(QPoint)。"""
        self._move_callbacks.append(callback)

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        for cb in self._move_callbacks:
            cb(self.pos())
        if self._bubble.isVisible():
            self._bubble._update_position()

    # ── 外部接口 ──────────────────────────────────────────

    def show_dialogue(self, text: str, duration_ms: int = 3000, emotion: str | None = None) -> None:
        if emotion:
            self.set_expression_emotion(emotion, duration_ms=max(duration_ms + 1200, 3500))
        print(
            f"[PET][Dialogue] text={_compact_dialogue_log(text)!r} "
            f"duration_ms={duration_ms} emotion={emotion or self._expression_emotion}",
            flush=True,
        )
        self._bubble.show_text(text, duration_ms, emotion=emotion or self._expression_emotion)

    def get_dialogue_overlay_rect(self) -> QRect | None:
        return self._bubble.visible_rect()

    def set_expression_emotion(self, emotion: str | None, duration_ms: int = 4200) -> None:
        next_emotion = str(emotion or "").strip().lower() or "neutral"
        self._expression_emotion = next_emotion
        self._current_default_anim = None
        self._expression_timer.stop()
        if next_emotion != "neutral":
            self._expression_timer.start(max(1200, int(duration_ms)))

    def clear_expression_emotion(self) -> None:
        self._expression_emotion = "neutral"
        self._current_default_anim = None

    def show_feedback(self, gif_path: str) -> None:
        """为生成的动画显示反馈栏。"""
        self._feedback.show_for(
            gif_path,
            on_block=lambda aid: self._bus.emit("feedback", {"type": "block", "id": aid}),
            on_like=lambda aid:  self._bus.emit("feedback", {"type": "like",  "id": aid}),
            on_retag=lambda aid, tags: self._bus.emit("feedback", {"type": "retag", "id": aid, "tags": tags})
        )

    def _find_default_animation_match(self):
        tags = merge_default_anim_tags(
            self._move_sm.get_default_anim_tags(),
            None if self._expression_emotion == "neutral" else self._expression_emotion,
        )
        match = self._lib.find_or_fallback(tags)
        if match and match.score > 0:
            return match

        fallback_tags = self._move_sm.get_default_anim_tags()
        fallback_match = self._lib.find_or_fallback(fallback_tags)
        if fallback_match and fallback_match.score > 0:
            return fallback_match

        idle_match = self._lib.find_or_fallback(["idle", "neutral"])
        if idle_match and idle_match.score > 0:
            return idle_match
        return None

    def _should_play_default_animation(self, file_path: str) -> bool:
        if file_path != self._current_default_anim:
            return True
        return self._body.current_path != file_path
