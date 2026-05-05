from PyQt6.QtWidgets import QLabel
from PyQt6.QtCore    import Qt, QTimer, QRect
from PyQt6.QtGui     import QPainter, QColor, QPen, QPainterPath


_EMOTION_THEMES = {
    "happy": {
        "bg": QColor(255, 236, 192, 245),
        "border": QColor(244, 171, 54),
        "text": "#5f3b00",
    },
    "excited": {
        "bg": QColor(255, 220, 198, 245),
        "border": QColor(245, 129, 70),
        "text": "#5a2200",
    },
    "sad": {
        "bg": QColor(214, 231, 255, 245),
        "border": QColor(110, 155, 232),
        "text": "#20385d",
    },
    "sleepy": {
        "bg": QColor(226, 220, 255, 245),
        "border": QColor(150, 130, 235),
        "text": "#31265c",
    },
    "scared": {
        "bg": QColor(255, 230, 218, 245),
        "border": QColor(230, 122, 92),
        "text": "#5b2717",
    },
    "surprised": {
        "bg": QColor(255, 242, 207, 245),
        "border": QColor(238, 183, 57),
        "text": "#5b4200",
    },
    "angry": {
        "bg": QColor(255, 221, 221, 245),
        "border": QColor(230, 102, 102),
        "text": "#5a1d1d",
    },
    "curious": {
        "bg": QColor(226, 247, 236, 245),
        "border": QColor(86, 183, 126),
        "text": "#173d2a",
    },
    "shy": {
        "bg": QColor(255, 228, 240, 245),
        "border": QColor(233, 132, 176),
        "text": "#5a2440",
    },
    "neutral": {
        "bg": QColor(255, 228, 235, 240),
        "border": QColor(0xE8, 0x70, 0x8A),
        "text": "#4a2030",
    },
}


def resolve_bubble_theme(emotion: str | None) -> dict:
    key = str(emotion or "").strip().lower()
    return dict(_EMOTION_THEMES.get(key, _EMOTION_THEMES["neutral"]))


class BubbleWidget(QLabel):
    """
    宠物对话气泡，作为独立顶层窗口显示在宠物下方，duration_ms 后自动隐藏。
    用法：bubble.show_text("汪！", duration_ms=3000)
    """

    def __init__(self, pet_window):
        # 作为独立顶层窗口，不作为 pet_window 的子控件（避免被 150x150 窗口裁切）
        super().__init__(None)
        self._pet_window = pet_window
        self.setWordWrap(True)
        self.setMaximumWidth(200)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        theme = resolve_bubble_theme("neutral")
        self._bg_color = theme["bg"]
        self._border_color = theme["border"]
        self._border_width = 3
        self._radius = 12
        self._apply_theme("neutral")
        self.hide()
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def show_text(self, text: str, duration_ms: int = 3000, emotion: str | None = None) -> None:
        self._apply_theme(emotion)
        self.setText(text)
        self.adjustSize()
        self._update_position()
        self.show()
        self._hide_timer.stop()
        self._hide_timer.start(duration_ms)

    def _apply_theme(self, emotion: str | None) -> None:
        theme = resolve_bubble_theme(emotion)
        self._bg_color = theme["bg"]
        self._border_color = theme["border"]
        self.setStyleSheet(
            f"""
            QLabel {{
                background: transparent;
                border: none;
                padding: 8px 14px;
                font-size: 14px;
                font-weight: bold;
                color: {theme['text']};
            }}
            """
        )

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(
            self._border_width // 2, self._border_width // 2,
            -self._border_width // 2, -self._border_width // 2,
        )
        path = QPainterPath()
        path.addRoundedRect(rect.toRectF(), self._radius, self._radius)
        p.fillPath(path, self._bg_color)
        pen = QPen(self._border_color, self._border_width)
        p.setPen(pen)
        p.drawPath(path)
        p.end()
        super().paintEvent(event)

    def _update_position(self) -> None:
        """将气泡定位到宠物窗口正下方居中。"""
        pet_pos = self._pet_window.pos()
        pet_w = self._pet_window.width()
        bubble_w = self.width()
        x = pet_pos.x() + (pet_w - bubble_w) // 2
        y = pet_pos.y() + self._pet_window.height() + 4
        self.move(x, y)

    def visible_rect(self) -> QRect | None:
        if not self.isVisible():
            return None
        return self.geometry()
