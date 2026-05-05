from collections.abc import Callable

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_SIDE_GAP = 10
_STACK_GAP = 8
_TOP_PADDING = 8


def choose_chat_bubble_position(
    pet_rect: QRect,
    bubble_size: QSize,
    screen_rect: QRect,
    avoid_rects: list[QRect] | None = None,
    preferred_panel_rect: QRect | None = None,
) -> QPoint:
    avoid = [rect for rect in (avoid_rects or []) if rect and rect.isValid()]
    candidates = _build_candidates(
        pet_rect=pet_rect,
        bubble_size=bubble_size,
        screen_rect=screen_rect,
        preferred_panel_rect=preferred_panel_rect,
    )

    best_point = QPoint(screen_rect.x(), screen_rect.y())
    best_overlap = None
    for point in candidates:
        candidate_rect = QRect(point, bubble_size)
        overlap = _total_overlap_area(candidate_rect, [pet_rect, *avoid])
        if overlap == 0:
            return point
        if best_overlap is None or overlap < best_overlap:
            best_overlap = overlap
            best_point = point
    return best_point


def _build_candidates(
    pet_rect: QRect,
    bubble_size: QSize,
    screen_rect: QRect,
    preferred_panel_rect: QRect | None,
) -> list[QPoint]:
    width = bubble_size.width()
    height = bubble_size.height()
    right_x = pet_rect.right() + 1 + _SIDE_GAP
    left_x = pet_rect.left() - width - _SIDE_GAP
    top_y = pet_rect.top() + _TOP_PADDING
    bottom_align_y = pet_rect.bottom() - height + 1

    raw_positions: list[QPoint] = []
    if preferred_panel_rect and preferred_panel_rect.isValid():
        raw_positions.extend(
            [
                QPoint(preferred_panel_rect.left(), preferred_panel_rect.top() - height - _STACK_GAP),
                QPoint(preferred_panel_rect.left(), preferred_panel_rect.bottom() + 1 + _STACK_GAP),
            ]
        )

    raw_positions.extend(
        [
            QPoint(right_x, top_y),
            QPoint(left_x, top_y),
            QPoint(right_x, bottom_align_y),
            QPoint(left_x, bottom_align_y),
        ]
    )
    return [_clamp_point(pos, bubble_size, screen_rect) for pos in raw_positions]


def _clamp_point(point: QPoint, bubble_size: QSize, screen_rect: QRect) -> QPoint:
    max_x = screen_rect.right() - bubble_size.width() + 1
    max_y = screen_rect.bottom() - bubble_size.height() + 1
    x = min(max(point.x(), screen_rect.left()), max_x)
    y = min(max(point.y(), screen_rect.top()), max_y)
    return QPoint(x, y)


def _total_overlap_area(rect: QRect, avoid_rects: list[QRect]) -> int:
    area = 0
    for other in avoid_rects:
        inter = rect.intersected(other)
        if inter.isValid() and inter.width() > 0 and inter.height() > 0:
            area += inter.width() * inter.height()
    return area


class ChatBubble(QWidget):
    def __init__(
        self,
        pet_window,
        on_submit,
        on_visibility_changed=None,
        avoid_rects_provider: Callable[[], list[QRect]] | None = None,
        preferred_panel_rect_provider: Callable[[], QRect | None] | None = None,
    ):
        super().__init__(None)
        self._pet_window = pet_window
        self._on_submit = on_submit
        self._on_visibility_changed = on_visibility_changed
        self._avoid_rects_provider = avoid_rects_provider
        self._preferred_panel_rect_provider = preferred_panel_rect_provider
        self._busy = False
        self._bg_color = QColor(28, 18, 24, 248)
        self._border_color = QColor(244, 182, 200, 235)
        self._border_width = 2
        self._radius = 16

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(272)
        self._setup_ui()
        self._apply_visual_effects()
        self.hide()
        self._pet_window.on_move(lambda _pos: self._update_position())

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(9)

        header = QHBoxLayout()
        title = QLabel("和我说话")
        title.setStyleSheet("color: #fff8fb; font-weight: 700; font-size: 14px;")
        header.addWidget(title)
        header.addStretch()

        close_btn = QPushButton("×")
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #ffd9e4; border: none; font-size: 15px; }"
            "QPushButton:hover { color: white; }"
        )
        close_btn.clicked.connect(self.hide)
        header.addWidget(close_btn)
        layout.addLayout(header)

        self._status = QLabel("想到什么就跟我说吧")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: #ffe5ed; font-size: 12px; line-height: 1.35;")
        layout.addWidget(self._status)

        input_row = QHBoxLayout()
        input_row.setSpacing(6)

        self._input = QLineEdit()
        self._input.setPlaceholderText("输入你想说的话...")
        self._input.returnPressed.connect(self._submit)
        self._input.setStyleSheet(
            "QLineEdit { background: rgba(255,255,255,0.96); color: #22141a; "
            "border: 1px solid rgba(255,228,236,0.96); border-radius: 10px; padding: 9px 10px; "
            "selection-background-color: #f09bb7; selection-color: #231419; }"
            "QLineEdit:focus { border: 1px solid #ffb8ce; background: rgba(255,255,255,0.99); }"
            "QLineEdit:disabled { color: #666; background: rgba(255,255,255,0.78); }"
        )
        input_row.addWidget(self._input, 1)

        self._send_btn = QPushButton("发送")
        self._send_btn.setFixedWidth(62)
        self._send_btn.clicked.connect(self._submit)
        self._send_btn.setStyleSheet(
            "QPushButton { background: #f38aac; color: #fffafc; font-weight: 700; "
            "border: 1px solid #ffc5d8; border-radius: 10px; padding: 8px 10px; }"
            "QPushButton:hover { background: #ff9cbc; }"
            "QPushButton:pressed { background: #df7899; }"
            "QPushButton:disabled { background: #7d5965; color: #dec4cb; border: 1px solid #9c7883; }"
        )
        input_row.addWidget(self._send_btn)

        layout.addLayout(input_row)

    def _apply_visual_effects(self) -> None:
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 140))
        self.setGraphicsEffect(shadow)

    def show_input(self, prefill: str = "", status_text: str = "") -> None:
        if status_text:
            self._status.setText(status_text)
        self._input.setText(str(prefill or ""))
        self._input.setCursorPosition(len(self._input.text()))
        self.adjustSize()
        self._update_position()
        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus()
        if self._on_visibility_changed:
            self._on_visibility_changed(True)

    def set_busy(self, busy: bool, status_text: str = "") -> None:
        self._busy = bool(busy)
        self._input.setEnabled(not self._busy)
        self._send_btn.setEnabled(not self._busy)
        if status_text:
            self._status.setText(status_text)

    def current_text(self) -> str:
        return self._input.text()

    def clear_input(self) -> None:
        self._input.clear()

    def focus_input(self) -> None:
        self._input.setFocus()

    def refresh_position(self) -> None:
        if not self.isVisible():
            return
        self.adjustSize()
        self._update_position()

    def _submit(self) -> None:
        if self._busy:
            return
        self._on_submit(self._input.text())

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(
            self._border_width,
            self._border_width,
            -self._border_width,
            -self._border_width,
        )
        path = QPainterPath()
        path.addRoundedRect(rect.toRectF(), self._radius, self._radius)
        painter.fillPath(path, self._bg_color)
        painter.setPen(QPen(self._border_color, self._border_width))
        painter.drawPath(path)
        painter.end()
        super().paintEvent(event)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if self._on_visibility_changed:
            self._on_visibility_changed(False)

    def _update_position(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return

        pet_rect = QRect(self._pet_window.pos(), self._pet_window.size())
        avoid_rects = self._collect_avoid_rects()
        preferred_panel_rect = None
        if self._preferred_panel_rect_provider:
            preferred_panel_rect = self._preferred_panel_rect_provider()

        point = choose_chat_bubble_position(
            pet_rect=pet_rect,
            bubble_size=self.size(),
            screen_rect=screen.availableGeometry(),
            avoid_rects=avoid_rects,
            preferred_panel_rect=preferred_panel_rect,
        )
        self.move(point)

    def _collect_avoid_rects(self) -> list[QRect]:
        if not self._avoid_rects_provider:
            return []
        return [rect for rect in self._avoid_rects_provider() if rect and rect.isValid()]
