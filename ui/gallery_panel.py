from pathlib import Path

from PyQt6.QtCore import QEventLoop, QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImageReader, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.animation_layer import AnimationLayer


CARD_W = 340
CARD_H = 580
THUMB_W = 308
THUMB_H = 196


class _DragHandle(QFrame):
    def __init__(self, target: QWidget):
        super().__init__()
        self._target = target
        self._dragging = False
        self._offset = QPoint()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._offset = event.globalPosition().toPoint() - self._target.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self._target.move(event.globalPosition().toPoint() - self._offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._dragging = False
        event.accept()


def _make_mouse_transparent(widget: QWidget) -> QWidget:
    widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    return widget


def _load_thumbnail(file_path: str, size: QSize) -> QPixmap | None:
    path = Path(str(file_path or ""))
    if not path.exists():
        return None

    if path.suffix.lower() in {".gif", ".png", ".jpg", ".jpeg", ".webp"}:
        reader = QImageReader(str(path))
        image = reader.read()
        if image.isNull():
            pixmap = QPixmap(str(path))
        else:
            pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return None
        return pixmap.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )

    if path.suffix.lower() not in {".mp4", ".mov", ".webm", ".avi"}:
        return None

    try:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtMultimedia import QMediaPlayer, QVideoSink
    except Exception:
        return None

    player = QMediaPlayer()
    sink = QVideoSink()
    player.setVideoSink(sink)
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    result = {"pixmap": None}

    def on_frame(frame) -> None:
        if result["pixmap"] is not None or not frame.isValid():
            return
        image = frame.toImage()
        if image.isNull():
            return
        result["pixmap"] = QPixmap.fromImage(
            image.scaled(
                size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        player.stop()
        loop.quit()

    def on_status_changed(status) -> None:
        if status.name in {"InvalidMedia", "NoMedia"}:
            loop.quit()

    sink.videoFrameChanged.connect(on_frame)
    player.mediaStatusChanged.connect(on_status_changed)
    player.setSource(QUrl.fromLocalFile(str(path.resolve())))
    timer.start(1600)
    player.play()
    loop.exec()
    timer.stop()
    player.stop()
    return result["pixmap"]


def _build_placeholder(title: str, rarity: str, size: QSize) -> QPixmap:
    pixmap = QPixmap(size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    path = QPainterPath()
    path.addRoundedRect(0, 0, size.width(), size.height(), 16, 16)
    fill = QColor(41, 52, 74) if rarity == "common" else QColor(72, 52, 22)
    stroke = QColor(126, 194, 255) if rarity == "common" else QColor(255, 214, 138)
    painter.fillPath(path, fill)
    painter.setPen(QPen(stroke, 2))
    painter.drawPath(path)

    painter.setPen(QColor(245, 247, 251))
    title_font = painter.font()
    title_font.setPointSize(13)
    title_font.setBold(True)
    painter.setFont(title_font)
    painter.drawText(
        pixmap.rect().adjusted(16, 16, -16, -44),
        Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
        title,
    )

    meta_font = painter.font()
    meta_font.setPointSize(9)
    meta_font.setBold(False)
    painter.setFont(meta_font)
    painter.setPen(QColor(199, 208, 226))
    painter.drawText(
        pixmap.rect().adjusted(12, size.height() - 32, -12, -10),
        Qt.AlignmentFlag.AlignCenter,
        "点击查看预览",
    )
    painter.end()
    return pixmap


def _format_effect_summary(entry: dict) -> str:
    summary = entry.get("effect_summary") or {}
    stats = summary.get("stats") or {}
    growth = summary.get("growth") or {}
    parts: list[str] = []
    labels = [
        ("hunger", "Hunger"),
        ("cleanliness", "Clean"),
        ("energy", "Energy"),
        ("mood", "Mood"),
        ("exp", "EXP"),
        ("intimacy", "Bond"),
    ]
    for key, label in labels:
        value = growth.get(key) if key in {"exp", "intimacy"} else stats.get(key)
        amount = int(value or 0)
        if amount == 0:
            continue
        sign = "+" if amount > 0 else ""
        parts.append(f"{label} {sign}{amount}")
    return " · ".join(parts) if parts else "No stat change"


def _format_card_title(entry: dict) -> str:
    raw = str(entry.get("behavior_type") or entry.get("display_name") or "").strip().lower()
    normalized = raw.replace("-", "_")
    parts = [part for part in normalized.split("_") if part]
    trim_tokens = {
        "excited", "happy", "calm", "sad", "neutral", "curious",
        "playful", "sleepy", "request", "showcase", "idle",
    }
    while len(parts) > 2 and parts[-1] in trim_tokens:
        parts.pop()
    if ("beg" in parts and "food" in parts):
        return "Beg Food"
    if ("show" in parts and "off" in parts):
        return "Show Off"
    if ("self" in parts and "care" in parts):
        return "Self Care"
    if len(parts) >= 2:
        return " ".join(parts[:2]).title()
    if parts:
        return parts[0].title()
    return str(entry.get("display_name") or "Unknown Action").replace("_", " ").title()


def _format_family_label(entry: dict) -> str:
    raw = str(entry.get("behavior_family") or entry.get("behavior_type") or "misc").strip()
    if not raw:
        return "Misc"
    return raw.replace("_", " ").replace("/", " / ").title()


def _format_effect_box_text(entry: dict) -> str:
    summary = entry.get("effect_summary") or {}
    stats = summary.get("stats") or {}
    growth = summary.get("growth") or {}
    items: list[str] = []
    labels = [
        ("hunger", "Hunger"),
        ("cleanliness", "Clean"),
        ("energy", "Energy"),
        ("mood", "Mood"),
        ("exp", "EXP"),
        ("intimacy", "Bond"),
    ]
    for key, label in labels:
        value = growth.get(key) if key in {"exp", "intimacy"} else stats.get(key)
        amount = int(value or 0)
        if amount == 0:
            continue
        sign = "+" if amount > 0 else ""
        items.append(f"{label} {sign}{amount}")
    if not items:
        return "No stat change"
    lines = ["   ".join(items[index:index + 2]) for index in range(0, len(items), 2)]
    return "\n".join(lines)


def _iter_effect_rows(entry: dict) -> list[tuple[str, str, str]]:
    summary = entry.get("effect_summary") or {}
    stats = summary.get("stats") or {}
    growth = summary.get("growth") or {}
    palette = {
        "hunger": ("Hunger", "#ffb04a"),
        "cleanliness": ("Clean", "#69d88f"),
        "energy": ("Energy", "#7bb7ff"),
        "mood": ("Mood", "#ff8dc0"),
        "exp": ("EXP", "#ffe08a"),
        "intimacy": ("Bond", "#caa8ff"),
    }
    rows: list[tuple[str, str, str]] = []
    primary_keys = ("hunger", "cleanliness", "energy", "mood")
    growth_keys = ("exp", "intimacy")

    for key in primary_keys:
        amount = int(stats.get(key) or 0)
        label, color = palette[key]
        sign = "+" if amount > 0 else ""
        rows.append((label, color, f"{sign}{amount}" if amount != 0 else "0"))

    for key in growth_keys:
        amount = int(growth.get(key) or 0)
        if amount == 0:
            continue
        label, color = palette[key]
        sign = "+" if amount > 0 else ""
        rows.append((label, color, f"{sign}{amount}"))
    return rows


def _source_badge_text(entry: dict) -> str:
    source = str(entry.get("source") or "").strip().lower()
    if source == "generated":
        return "AI Generated"
    return "Base Move"


def _format_rarity_text(entry: dict) -> str:
    rarity = str(entry.get("rarity") or "").strip().lower()
    return "RARE" if rarity == "rare" else "BASE"


def _format_rating_text(entry: dict) -> str:
    rating = int(entry.get("rating") or 0)
    return f"Rating {rating}/5"


class _ThumbnailFrame(QFrame):
    def __init__(self, entry: dict):
        super().__init__()
        self.setFixedSize(THUMB_W, THUMB_H)
        self.setStyleSheet(
            "background: rgba(7, 10, 16, 0.92);"
            "border: 1px solid rgba(255,255,255,0.06);"
            "border-radius: 14px;"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rarity = str(entry.get("rarity") or "common").strip().lower()
        pixmap = _load_thumbnail(entry.get("file", ""), QSize(THUMB_W, THUMB_H))
        if pixmap is None or pixmap.isNull():
            pixmap = _build_placeholder(str(entry.get("display_name") or "未知动作"), rarity, QSize(THUMB_W, THUMB_H))
        label.setPixmap(pixmap)
        layout.addWidget(label)


class _PreviewDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._entry = None
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(420, 430)
        self._setup_ui()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        shell = QFrame()
        shell.setStyleSheet(
            "background: rgba(15, 19, 28, 0.97);"
            "border: 1px solid rgba(255,255,255,0.10);"
            "border-radius: 18px;"
        )
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(18, 18, 18, 18)
        shell_layout.setSpacing(12)

        header = QHBoxLayout()
        title_box = _DragHandle(self)
        title_layout = QVBoxLayout(title_box)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(2)
        self._title = _make_mouse_transparent(QLabel("动作预览"))
        self._title.setStyleSheet("color: #f7f9fd; font-size: 18px; font-weight: 700;")
        self._meta = _make_mouse_transparent(QLabel(""))
        self._meta.setStyleSheet("color: #9aa6bf; font-size: 11px;")
        title_layout.addWidget(self._title)
        title_layout.addWidget(self._meta)
        header.addWidget(title_box, 1)
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.08); color: #dde4f2; border: none; border-radius: 14px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.16); }"
        )
        close_btn.clicked.connect(self.close)
        header.addWidget(close_btn)
        shell_layout.addLayout(header)

        self._player = AnimationLayer(shell, size=QSize(320, 320))
        self._player.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        shell_layout.addWidget(self._player, alignment=Qt.AlignmentFlag.AlignCenter)

        actions = QHBoxLayout()
        replay_btn = QPushButton("重新播放")
        replay_btn.setStyleSheet(
            "QPushButton { background: rgba(126, 200, 255, 0.18); color: #e4f7ff; border-radius: 10px; padding: 8px 14px; border: 1px solid rgba(126, 200, 255, 0.35); }"
            "QPushButton:hover { background: rgba(126, 200, 255, 0.26); }"
        )
        replay_btn.clicked.connect(self._play_current)
        actions.addWidget(replay_btn)
        actions.addStretch()
        shell_layout.addLayout(actions)

        root.addWidget(shell)

    def open_for(self, entry: dict) -> None:
        self._entry = dict(entry)
        rarity = "稀有" if str(entry.get("rarity") or "").lower() == "rare" else "基础"
        self._title.setText(str(entry.get("display_name") or "动作预览"))
        self._meta.setText(
            f"{entry.get('behavior_type', 'misc')} · {rarity} · 评分 {int(entry.get('rating') or 0)}/5"
        )
        self._play_current()
        self.show()
        self.raise_()
        self.activateWindow()

    def _play_current(self) -> None:
        if not self._entry:
            return
        path = str(self._entry.get("file") or "")
        if not path:
            return
        if self._entry.get("loop"):
            self._player.play_loop(path)
        else:
            self._player.play_once(path)

    def closeEvent(self, event) -> None:
        self._player.stop()
        super().closeEvent(event)


class _GalleryCard(QFrame):
    activated = pyqtSignal(dict)

    def __init__(self, entry: dict):
        super().__init__()
        self._entry = dict(entry)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(CARD_W, CARD_H)
        self._setup_ui()

    def _setup_ui(self) -> None:
        rarity = str(self._entry.get("rarity") or "common").strip().lower()
        title = str(self._entry.get("display_name") or "未知动作")
        behavior_type = str(self._entry.get("behavior_type") or "misc")
        discovered_at = str(self._entry.get("discovered_at") or "").strip()
        title = _format_card_title(self._entry)
        effect_rows = _iter_effect_rows(self._entry)

        border = "rgba(232, 204, 132, 0.66)" if rarity == "rare" else "rgba(139, 201, 255, 0.50)"
        accent = "#f5d68d" if rarity == "rare" else "#d6efff"
        frame_bg = (
            "qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            "stop:0 rgba(47,34,18,0.98), stop:0.52 rgba(88,62,28,0.98), stop:1 rgba(38,26,14,0.98))"
            if rarity == "rare"
            else
            "qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            "stop:0 rgba(19,28,44,0.98), stop:0.52 rgba(31,49,76,0.98), stop:1 rgba(16,24,38,0.98))"
        )
        self.setStyleSheet(
            "QFrame {"
            f"background: {frame_bg};"
            f"border: 1px solid {border};"
            "border-radius: 18px;"
            "}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        layout.addWidget(_ThumbnailFrame(self._entry), alignment=Qt.AlignmentFlag.AlignCenter)

        badge_row = QHBoxLayout()
        badge_row.setSpacing(8)
        badge = QLabel(_source_badge_text(self._entry))
        badge.setStyleSheet(
            f"background: rgba(10,12,18,0.30); color: {accent};"
            "padding: 5px 10px; border-radius: 10px; font-size: 11px; font-weight: 700;"
        )
        badge_row.addWidget(badge)
        rarity_badge = QLabel(_format_rarity_text(self._entry))
        rarity_badge.setStyleSheet(
            "background: rgba(255,255,255,0.05); color: #dfe6f2;"
            "padding: 5px 10px; border-radius: 10px; font-size: 11px; font-weight: 700;"
        )
        badge_row.addWidget(rarity_badge)
        badge_row.addStretch()
        rating_label = QLabel(_format_rating_text(self._entry))
        rating_label.setStyleSheet("color: #dfe6f2; font-size: 11px; font-weight: 700;")
        badge_row.addWidget(rating_label)
        layout.addLayout(badge_row)

        title_frame = QFrame()
        title_frame.setStyleSheet(
            "background: rgba(8,10,16,0.42);"
            "border-radius: 12px;"
            "border: 1px solid rgba(255,255,255,0.07);"
        )
        title_layout = QVBoxLayout(title_frame)
        title_layout.setContentsMargins(12, 10, 12, 10)
        title_layout.setSpacing(4)

        title_label = QLabel(title)
        title_label.setWordWrap(True)
        title_label.setMaximumHeight(44)
        title_label.setStyleSheet("color: #f7f9fd; font-size: 18px; font-weight: 800;")
        title_layout.addWidget(title_label)

        meta_label = QLabel(_format_family_label(self._entry))
        meta_label.setWordWrap(True)
        meta_label.setStyleSheet(f"color: {accent}; font-size: 12px; font-weight: 600;")
        title_layout.addWidget(meta_label)
        layout.addWidget(title_frame)
        effect_frame = QFrame()
        effect_frame.setStyleSheet(
            "background: rgba(6,9,14,0.52);"
            "border: 1px solid rgba(255,255,255,0.08);"
            "border-radius: 14px;"
        )
        effect_layout = QVBoxLayout(effect_frame)
        effect_layout.setContentsMargins(14, 12, 14, 14)
        effect_layout.setSpacing(10)
        effect_head = QLabel("Attribute Changes")
        effect_head.setStyleSheet(
            f"color: {accent}; font-size: 12px; font-weight: 800;"
            "background: transparent; border: none;"
        )
        effect_layout.addWidget(effect_head)
        for label_text, color, value_text in effect_rows:
            row_frame = QFrame()
            row_frame.setStyleSheet(
                "background: transparent;"
                "border: none;"
            )
            row_frame.setFixedHeight(44)
            row = QHBoxLayout(row_frame)
            row.setContentsMargins(2, 2, 2, 2)
            row.setSpacing(12)

            accent_bar = QFrame()
            accent_bar.setFixedWidth(5)
            accent_bar.setFixedHeight(20)
            accent_bar.setStyleSheet(f"background: {color}; border: none; border-radius: 2px;")
            row.addWidget(accent_bar, alignment=Qt.AlignmentFlag.AlignVCenter)

            name = QLabel(label_text)
            name.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            name.setFixedWidth(96)
            name.setStyleSheet(
                f"color: {color}; font-size: 14px; font-weight: 700; border: none; background: transparent;"
            )
            row.addWidget(name)
            row.addStretch(1)

            value = QLabel(value_text)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value.setStyleSheet(
                "color: #f7f9fd; font-size: 18px; font-weight: 900; border: none; background: transparent;"
            )
            value.setFixedWidth(74)
            row.addWidget(value)

            effect_layout.addWidget(row_frame)
        layout.addWidget(effect_frame, 1)

        footer = QLabel(f"首次发现 {discovered_at[:10]}" if discovered_at else "点击查看预览")
        footer.setStyleSheet("color: #d7deea; font-size: 10px;")
        layout.addWidget(footer)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(dict(self._entry))
            event.accept()
            return
        super().mousePressEvent(event)


class GalleryPanel(QWidget):
    def __init__(self, library_manager, generated_dir: str | Path):
        super().__init__()
        self._lib = library_manager
        self._generated_dir = Path(generated_dir)
        self._built_once = False
        self._preview = _PreviewDialog(self)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(1160, 700)
        self.resize(1240, 790)
        self._setup_ui()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        shell = QFrame()
        shell.setStyleSheet(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            "stop:0 rgba(11,15,24,0.98), stop:1 rgba(21,29,44,0.98));"
            "border: 1px solid rgba(255,255,255,0.10);"
            "border-radius: 22px;"
        )
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(20, 20, 20, 20)
        shell_layout.setSpacing(14)

        header_row = QHBoxLayout()
        drag_handle = _DragHandle(self)
        drag_layout = QVBoxLayout(drag_handle)
        drag_layout.setContentsMargins(0, 0, 0, 0)
        drag_layout.setSpacing(3)
        drag_handle.setStyleSheet("background: transparent;")
        title = _make_mouse_transparent(QLabel("动画图鉴"))
        title.setStyleSheet("color: #f7f9fd; font-size: 22px; font-weight: 800;")
        subtitle = _make_mouse_transparent(QLabel("基础动作有完成度，稀有生成会持续扩展。点击卡牌可查看预览。"))
        subtitle.setStyleSheet("color: #98a6c2; font-size: 11px;")
        drag_layout.addWidget(title)
        drag_layout.addWidget(subtitle)
        header_row.addWidget(drag_handle, 1)
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(30, 30)
        close_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.08); color: #dbe2f2; border: none; border-radius: 15px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.16); }"
        )
        close_btn.clicked.connect(self.hide)
        header_row.addWidget(close_btn)
        shell_layout.addLayout(header_row)

        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        self._base_summary = QLabel("")
        self._base_summary.setStyleSheet(
            "background: rgba(114, 204, 255, 0.12); color: #e8f8ff; border-radius: 14px; padding: 12px 14px; font-size: 12px; font-weight: 700;"
        )
        self._rare_summary = QLabel("")
        self._rare_summary.setStyleSheet(
            "background: rgba(255, 214, 138, 0.12); color: #fff2cf; border-radius: 14px; padding: 12px 14px; font-size: 12px; font-weight: 700;"
        )
        self._total_summary = QLabel("")
        self._total_summary.setStyleSheet(
            "background: rgba(255,255,255,0.06); color: #dbe4f5; border-radius: 14px; padding: 12px 14px; font-size: 12px; font-weight: 700;"
        )
        metrics.addWidget(self._base_summary, 1)
        metrics.addWidget(self._rare_summary, 1)
        metrics.addWidget(self._total_summary, 1)
        shell_layout.addLayout(metrics)

        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet("color: #a7b3ca; font-size: 11px;")
        shell_layout.addWidget(self._summary_label)

        self._empty_label = QLabel("还没有可展示的动画，先多和宠物互动试试看。")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: #8f99ad; font-size: 12px; padding: 30px 0;")
        shell_layout.addWidget(self._empty_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; }"
            "QScrollBar:vertical { background: rgba(255,255,255,0.05); width: 10px; margin: 4px; border-radius: 5px; }"
            "QScrollBar::handle:vertical { background: rgba(255,255,255,0.18); border-radius: 5px; min-height: 24px; }"
        )
        self._content = QWidget()
        self._grid = QGridLayout(self._content)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(16)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        scroll.setWidget(self._content)
        self._scroll = scroll
        shell_layout.addWidget(scroll, 1)

        root.addWidget(shell)

    def refresh(self) -> None:
        entries = self._lib.get_collection_entries()
        stats = self._lib.get_collection_stats()
        overall = stats["overall"]

        common_discovered = int(overall["common_discovered"])
        common_total = int(overall["common_total"])
        rare_discovered = int(overall["rare_discovered"])
        total_discovered = int(overall["discovered"])
        progress = int(round(float(overall["progress"]) * 100))

        self._base_summary.setText(f"基础动作\n{common_discovered}/{common_total} · 完成度 {progress}%")
        self._rare_summary.setText(f"稀有发现\n已收录 {rare_discovered} 张")
        self._total_summary.setText(f"总卡牌数\n当前 {total_discovered} 张")
        self._summary_label.setText("基础动作来自种子库；稀有卡来自生成动画，不设置理论上限。")

        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        has_content = bool(entries)
        self._empty_label.setVisible(not has_content)
        self._scroll.setVisible(has_content)

        for index, entry in enumerate(entries):
            row = index // 3
            col = index % 3
            card = _GalleryCard(entry)
            card.activated.connect(self._preview.open_for)
            self._grid.addWidget(card, row, col)

    def show_panel(self) -> None:
        imported = self._lib.sync_generated_assets(self._generated_dir)
        if imported:
            print(f"[AI-DIAG][Gallery] imported loose generated assets count={len(imported)}", flush=True)
        self.refresh()
        self.show()
        self.raise_()
        self.activateWindow()

        if self._built_once:
            return

        self._built_once = True
        screen = self.screen()
        if screen is None:
            return
        rect = screen.availableGeometry()
        x = rect.x() + max(20, (rect.width() - self.width()) // 2)
        y = rect.y() + max(20, (rect.height() - self.height()) // 2)
        self.move(x, y)
