from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar, QMenu
from PyQt6.QtCore import Qt, QTimer, QPoint, QRect
from PyQt6.QtGui import QPainter, QColor, QPainterPath, QFont


class _GearButton(QWidget):

    def __init__(self, on_click):
        super().__init__()
        self._on_click = on_click
        self._hover = False
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(32, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click()

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg_alpha = 200 if self._hover else 160
        p.setBrush(QColor(40, 40, 40, bg_alpha))
        p.setPen(QColor(255, 255, 255, 60))
        p.drawEllipse(2, 2, 28, 28)
        p.setPen(QColor(220, 220, 220))
        font = QFont()
        font.setPixelSize(18)
        p.setFont(font)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "\u2699")
        p.end()


class _ExpandedPanel(QWidget):

    def __init__(
        self,
        on_interaction,
        on_toggle_follow_mouse,
        on_toggle_llm_mode,
        on_manual_ai_request,
        on_open_chat,
        on_open_gallery,
        on_switch_character=lambda asset_id: None,
        character_options=None,
        current_character_label="unknown",
        current_character_asset_id="",
        on_close=lambda: None,
    ):
        super().__init__()
        self._on_interaction = on_interaction
        self._on_toggle_follow_mouse = on_toggle_follow_mouse
        self._on_toggle_llm_mode = on_toggle_llm_mode
        self._on_manual_ai_request = on_manual_ai_request
        self._on_open_chat = on_open_chat
        self._on_open_gallery = on_open_gallery
        self._on_switch_character = on_switch_character
        self._on_close = on_close
        self._character_options = list(character_options or [])
        self._current_character_label = str(current_character_label or "unknown")
        self._current_character_asset_id = str(current_character_asset_id or "")
        self._interaction_availability = {}
        self._follow_mouse_on = False
        self._llm_mode_on = False
        self._dragging = False
        self._drag_offset = QPoint()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(190)
        self._bars: dict[str, tuple] = {}
        self._interaction_buttons: dict[str, QPushButton] = {}
        self._setup_ui()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_offset = event.pos()

    def mouseMoveEvent(self, event):
        if self._dragging:
            self.move(self.mapToGlobal(event.pos()) - self._drag_offset)

    def mouseReleaseEvent(self, event):
        self._dragging = False

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)

        header = QHBoxLayout()
        title = QLabel("\U0001F43E \u5BA0\u7269\u72B6\u6001")
        title.setStyleSheet("color: white; font-weight: bold; font-size: 13px;")
        header.addWidget(title)
        header.addStretch()
        close_btn = QPushButton("\u2716")
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #999; border: none; font-size: 12px; }"
            "QPushButton:hover { color: #ff6666; }"
        )
        close_btn.clicked.connect(self._on_close)
        header.addWidget(close_btn)
        layout.addLayout(header)

        stats = [
            ("hunger",      "\u997F\u5EA6", "#FF8C00"),
            ("energy",      "\u80FD\u91CF", "#4169E1"),
            ("cleanliness", "\u6E05\u6D01", "#32CD32"),
            ("mood",        "\u5FC3\u60C5", "#FF69B4"),
        ]
        for key, label, color in stats:
            row = QHBoxLayout()
            row.setSpacing(6)
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #ccc; font-size: 11px;")
            lbl.setFixedWidth(32)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(80)
            bar.setTextVisible(False)
            bar.setFixedHeight(10)
            bar.setStyleSheet(
                f"QProgressBar {{ background: rgba(255,255,255,0.15); border-radius: 5px; border: none; }}"
                f"QProgressBar::chunk {{ background: {color}; border-radius: 5px; }}"
            )
            val_lbl = QLabel("80")
            val_lbl.setStyleSheet("color: #aaa; font-size: 10px;")
            val_lbl.setFixedWidth(24)
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(lbl)
            row.addWidget(bar)
            row.addWidget(val_lbl)
            layout.addLayout(row)
            self._bars[key] = (bar, val_lbl)

        self._growth_label = QLabel("成长 Lv1 · EXP 0 · 亲密 0")
        self._growth_label.setWordWrap(True)
        self._growth_label.setStyleSheet(
            "color: #d8e3f7; font-size: 10px; padding: 5px 6px;"
            "background: rgba(120,170,255,0.10); border-radius: 6px;"
        )
        layout.addWidget(self._growth_label)

        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255,255,255,0.25); margin-top: 3px; margin-bottom: 3px;")
        layout.addWidget(sep)

        row1 = QHBoxLayout()
        row1.setSpacing(4)
        for text, itype in [("\u5582\u98DF\U0001F356", "feed"),
                            ("\u73A9\u800D\U0001F3BE", "play"),
                            ("\u6D17\u6FA1\U0001F6C1", "bath")]:
            row1.addWidget(self._make_btn(text, itype))
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(4)
        for text, itype in [("\u629A\u6478\u270B", "stroke"),
                            ("\u6563\u6B65\U0001F9AE", "walk_mode")]:
            row2.addWidget(self._make_btn(text, itype))
        row2.addStretch()
        layout.addLayout(row2)

        self._follow_btn = QPushButton("\u73A9\u95F9\u6A21\u5F0F: \u5173")
        self._follow_btn.setCheckable(True)
        self._follow_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.12); color: #aaa;"
            " border: 1px solid rgba(255,255,255,0.2); border-radius: 5px; padding: 4px; font-size: 11px; }"
            "QPushButton:checked { background: rgba(100,200,100,0.35); color: #7fff7f;"
            " border: 1px solid rgba(100,255,100,0.5); }"
            "QPushButton:hover { background: rgba(255,255,255,0.22); }"
        )
        self._follow_btn.clicked.connect(self._toggle_follow)
        layout.addWidget(self._follow_btn)

        self._llm_btn = QPushButton("自主AI模式: 关")
        self._llm_btn.setCheckable(True)
        self._llm_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.12); color: #aaa;"
            " border: 1px solid rgba(255,255,255,0.2); border-radius: 5px; padding: 4px; font-size: 11px; }"
            "QPushButton:checked { background: rgba(255,180,80,0.28); color: #ffd27f;"
            " border: 1px solid rgba(255,210,127,0.45); }"
            "QPushButton:hover { background: rgba(255,255,255,0.22); }"
        )
        self._llm_btn.clicked.connect(self._toggle_llm_mode)
        layout.addWidget(self._llm_btn)

        self._manual_ai_btn = QPushButton("立即思考一次")
        self._manual_ai_btn.setStyleSheet(
            "QPushButton { background: rgba(80,140,220,0.18); color: #b8d8ff;"
            " border: 1px solid rgba(120,180,255,0.35); border-radius: 5px; padding: 4px; font-size: 11px; }"
            "QPushButton:hover { background: rgba(100,160,240,0.28); }"
            "QPushButton:pressed { background: rgba(60,110,180,0.3); }"
            "QPushButton:disabled { background: rgba(255,255,255,0.08); color: #777;"
            " border: 1px solid rgba(255,255,255,0.12); }"
        )
        self._manual_ai_btn.clicked.connect(self._on_manual_ai_request)
        layout.addWidget(self._manual_ai_btn)

        self._chat_btn = QPushButton("\u804a\u5929\U0001F4AC")
        self._chat_btn.setStyleSheet(
            "QPushButton { background: rgba(255,120,160,0.18); color: #ffd7e3;"
            " border: 1px solid rgba(255,180,205,0.35); border-radius: 5px; padding: 4px; font-size: 11px; }"
            "QPushButton:hover { background: rgba(255,145,178,0.28); }"
            "QPushButton:pressed { background: rgba(210,100,140,0.28); }"
            "QPushButton:disabled { background: rgba(255,255,255,0.08); color: #777;"
            " border: 1px solid rgba(255,255,255,0.12); }"
        )
        self._chat_btn.clicked.connect(self._on_open_chat)
        layout.addWidget(self._chat_btn)

        self._gallery_btn = QPushButton("图鉴 📚")
        self._gallery_btn.setStyleSheet(
            "QPushButton { background: rgba(120,170,255,0.16); color: #dde9ff;"
            " border: 1px solid rgba(150,195,255,0.32); border-radius: 5px; padding: 4px; font-size: 11px; }"
            "QPushButton:hover { background: rgba(135,185,255,0.26); }"
            "QPushButton:pressed { background: rgba(100,145,220,0.28); }"
        )
        self._gallery_btn.clicked.connect(self._on_open_gallery)
        layout.addWidget(self._gallery_btn)

        self._character_btn = QPushButton()
        self._character_btn.setStyleSheet(
            "QPushButton { background: rgba(120,255,190,0.14); color: #dfffee;"
            " border: 1px solid rgba(150,255,210,0.28); border-radius: 5px; padding: 4px; font-size: 11px; }"
            "QPushButton:hover { background: rgba(140,255,205,0.22); }"
            "QPushButton:pressed { background: rgba(90,210,160,0.24); }"
        )
        self._character_menu = QMenu(self)
        self._character_btn.setMenu(self._character_menu)
        layout.addWidget(self._character_btn)
        self.set_character_options(
            self._character_options,
            current_asset_id=self._current_character_asset_id,
            current_label=self._current_character_label,
        )

        self._ai_status_label = QLabel("AI状态: 已关闭")
        self._ai_status_label.setWordWrap(True)
        self._ai_status_label.setStyleSheet(
            "color: #999; font-size: 10px; padding: 4px 6px;"
            "background: rgba(255,255,255,0.08); border-radius: 5px;"
        )
        layout.addWidget(self._ai_status_label)

    def _toggle_follow(self):
        self._follow_mouse_on = self._follow_btn.isChecked()
        self._follow_btn.setText(
            "\u73A9\u95F9\u6A21\u5F0F: \u5F00" if self._follow_mouse_on
            else "\u73A9\u95F9\u6A21\u5F0F: \u5173"
        )
        self._on_toggle_follow_mouse(self._follow_mouse_on)

    def _toggle_llm_mode(self):
        requested = self._llm_btn.isChecked()
        actual = self._on_toggle_llm_mode(requested)
        self.set_llm_mode(bool(actual))

    def set_follow_mode(self, enabled: bool):
        self._follow_mouse_on = bool(enabled)
        self._follow_btn.setChecked(self._follow_mouse_on)
        self._follow_btn.setText(
            "\u73A9\u95F9\u6A21\u5F0F: \u5F00" if self._follow_mouse_on
            else "\u73A9\u95F9\u6A21\u5F0F: \u5173"
        )

    def set_llm_mode(self, enabled: bool):
        self._llm_mode_on = bool(enabled)
        self._llm_btn.setChecked(self._llm_mode_on)
        self._llm_btn.setText(
            "自主AI模式: 开" if self._llm_mode_on
            else "自主AI模式: 关"
        )
        self._manual_ai_btn.setEnabled(self._llm_mode_on)
        self._chat_btn.setEnabled(self._llm_mode_on)
        if not self._llm_mode_on:
            self.set_ai_status("已关闭", "off")

    def set_ai_status(self, text: str, tone: str = "idle"):
        styles = {
            "off": (
                "color: #999; background: rgba(255,255,255,0.08);"
                "border-radius: 5px; padding: 4px 6px; font-size: 10px;"
            ),
            "idle": (
                "color: #b8d8ff; background: rgba(80,140,220,0.18);"
                "border-radius: 5px; padding: 4px 6px; font-size: 10px;"
            ),
            "working": (
                "color: #ffd27f; background: rgba(255,180,80,0.18);"
                "border-radius: 5px; padding: 4px 6px; font-size: 10px;"
            ),
            "success": (
                "color: #98f0a6; background: rgba(80,180,100,0.18);"
                "border-radius: 5px; padding: 4px 6px; font-size: 10px;"
            ),
            "error": (
                "color: #ff9b9b; background: rgba(220,90,90,0.18);"
                "border-radius: 5px; padding: 4px 6px; font-size: 10px;"
            ),
        }
        self._ai_status_label.setText(f"AI状态: {text}")
        self._ai_status_label.setStyleSheet(styles.get(tone, styles["idle"]))

    def set_character_options(self, options: list[dict], current_asset_id: str, current_label: str):
        self._character_options = list(options or [])
        self._current_character_asset_id = str(current_asset_id or "")
        self._current_character_label = str(current_label or self._current_character_asset_id or "unknown")
        self._character_btn.setText(f"角色: {self._current_character_label}")
        self._character_menu.clear()
        for option in self._character_options:
            asset_id = str(option.get("asset_id") or "").strip()
            label = str(option.get("label") or asset_id).strip() or asset_id
            action = self._character_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(asset_id == self._current_character_asset_id)
            action.triggered.connect(lambda _checked=False, a=asset_id: self._on_switch_character(a))

    def _make_btn(self, text, itype):
        btn = QPushButton(text)
        btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.18); color: white;"
            " border: none; border-radius: 5px; padding: 4px 2px; font-size: 11px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.35); }"
            "QPushButton:pressed { background: rgba(255,255,255,0.08); }"
        )
        btn.clicked.connect(lambda _=False, t=itype: self._on_interaction({"type": t}))
        self._interaction_buttons[str(itype)] = btn
        return btn

    def set_interaction_availability(self, availability: dict[str, bool]):
        self._interaction_availability = dict(availability or {})
        for event_type, btn in self._interaction_buttons.items():
            enabled = self._interaction_availability.get(event_type, True)
            btn.setEnabled(enabled)

    def update_bars(self, displayed):
        for key, cur in displayed.items():
            if key in self._bars:
                bar, lbl = self._bars[key]
                bar.setValue(int(cur))
                lbl.setText(f"{cur:.1f}" if cur < 10 else str(int(cur)))

    def update_growth(self, exp: float, intimacy: float, level: int, growth_stage: str):
        stage = str(growth_stage or "newborn").replace("_", " ")
        self._growth_label.setText(
            f"成长 Lv{int(level)} · EXP {int(exp)} · 亲密 {int(intimacy)} · {stage}"
        )

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 12, 12)
        p.fillPath(path, QColor(20, 20, 20, 210))


class StatsPanel:
    """齿轮按钮（收起）+ 展开面板，绑定宠物窗口位置。"""

    def __init__(
        self,
        on_interaction,
        on_toggle_follow_mouse,
        on_toggle_llm_mode,
        on_manual_ai_request,
        on_open_chat,
        on_open_gallery,
        on_switch_character=lambda asset_id: None,
        character_options=None,
        current_character_asset_id="",
        current_character_label="unknown",
        on_layout_changed=None,
        llm_mode_enabled=False,
    ):
        self._expanded = False
        self._pet_window = None
        self._on_layout_changed = on_layout_changed

        self._displayed: dict[str, float] = {k: 80.0 for k in ("hunger", "energy", "cleanliness", "mood")}
        self._targets:   dict[str, float] = dict(self._displayed)
        self._growth = {"exp": 0.0, "intimacy": 0.0, "level": 1, "growth_stage": "newborn"}

        self._gear = _GearButton(on_click=self._toggle)
        self._panel = _ExpandedPanel(
            on_interaction,
            on_toggle_follow_mouse,
            on_toggle_llm_mode,
            on_manual_ai_request,
            on_open_chat,
            on_open_gallery,
            on_switch_character,
            character_options,
            current_character_label,
            current_character_asset_id,
            on_close=self._toggle,
        )
        self._panel.set_llm_mode(llm_mode_enabled)
        self._panel.set_ai_status("空闲" if llm_mode_enabled else "已关闭",
                                  tone="idle" if llm_mode_enabled else "off")

        # 属性条平滑动画定时器（仅做数值 lerp，不做位置轮询）
        self._anim_timer = QTimer()
        self._anim_timer.timeout.connect(self._anim_tick)
        self._anim_timer.start(30)

    def set_pet_window(self, pet_window):
        """绑定宠物窗口 moveEvent，齿轮跟随移动。"""
        self._pet_window = pet_window
        pet_window.on_move(self._on_pet_moved)
        print(f"[DBG][StatsPanel] bound to pet window  pet_pos=({pet_window.pos().x()},{pet_window.pos().y()})", flush=True)
        self._on_pet_moved(pet_window.pos())

    def show(self):
        self._gear.show()
        self._notify_layout_changed()

    def set_ai_status(self, text: str, tone: str = "idle"):
        self._panel.set_ai_status(text, tone)

    def set_current_character(self, asset_id: str, label: str):
        self._panel.set_character_options(
            self._panel._character_options,
            current_asset_id=asset_id,
            current_label=label,
        )

    def set_interaction_availability(self, availability: dict[str, bool]):
        self._panel.set_interaction_availability(availability)

    def get_overlay_rects(self) -> list[QRect]:
        rects: list[QRect] = []
        if self._expanded and self._panel.isVisible():
            rects.append(self._panel.geometry())
        elif self._gear.isVisible():
            rects.append(self._gear.geometry())
        return rects

    def get_expanded_panel_rect(self) -> QRect | None:
        if self._expanded and self._panel.isVisible():
            return self._panel.geometry()
        return None

    def _on_pet_moved(self, pet_pos):
        """宠物窗口移动时更新齿轮/面板位置。"""
        pet_w = self._pet_window.width() if self._pet_window else 150
        anchor_x = pet_pos.x() + pet_w + 2
        anchor_y = pet_pos.y()
        if self._expanded:
            if not self._panel._dragging:
                print(f"[DBG][StatsPanel] pet moved, panel follow  pet=({pet_pos.x()},{pet_pos.y()})  panel->({anchor_x},{anchor_y})", flush=True)
                self._panel.move(anchor_x, anchor_y)
        else:
            self._gear.move(anchor_x, anchor_y)
        self._notify_layout_changed()

    def _toggle(self):
        self._expanded = not self._expanded
        print(f"[DBG][StatsPanel] toggle  expanded={self._expanded}", flush=True)
        if self._expanded:
            self._gear.hide()
            gear_pos = self._gear.pos()
            self._panel.adjustSize()
            self._panel.move(gear_pos.x(), gear_pos.y())
            self._panel.show()
            print(f"[DBG][StatsPanel] panel shown at ({gear_pos.x()},{gear_pos.y()})", flush=True)
        else:
            self._panel.hide()
            self._gear.show()
            if self._pet_window:
                self._on_pet_moved(self._pet_window.pos())
        self._notify_layout_changed()

    def _anim_tick(self):
        """仅做属性数值 lerp，不做位置更新。"""
        for key in self._displayed:
            cur = self._displayed[key]
            tgt = self._targets[key]
            if abs(cur - tgt) < 0.3:
                cur = tgt
            else:
                cur = cur + (tgt - cur) * 0.12
            self._displayed[key] = cur

        if self._expanded:
            self._panel.update_bars(self._displayed)
            self._panel.update_growth(
                self._growth["exp"],
                self._growth["intimacy"],
                int(self._growth["level"]),
                str(self._growth["growth_stage"]),
            )

    def update_stats(self, hunger, energy, cleanliness, mood, *, exp=None, intimacy=None, level=None, growth_stage=None):
        self._targets["hunger"]      = hunger
        self._targets["energy"]      = energy
        self._targets["cleanliness"] = cleanliness
        self._targets["mood"]        = mood
        if exp is not None:
            self._growth["exp"] = float(exp)
        if intimacy is not None:
            self._growth["intimacy"] = float(intimacy)
        if level is not None:
            self._growth["level"] = int(level)
        if growth_stage is not None:
            self._growth["growth_stage"] = str(growth_stage)

    def _notify_layout_changed(self):
        if self._on_layout_changed:
            self._on_layout_changed()
