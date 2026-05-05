from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QInputDialog
from PyQt6.QtCore    import Qt


class FeedbackBar(QWidget):
    """
    显示在宠物上方，仅当 source=generated 的 GIF 播放时可见。
    on_block(anim_id)       → 调用方处理 blocked=True
    on_like(anim_id)        → 调用方处理 rating=5
    on_retag(anim_id, tags) → 调用方处理 tags 更新
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._anim_id  = None
        self._on_block = None
        self._on_like  = None
        self._on_retag = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)
        for label, slot in [("🗑", self._block), ("❤", self._like), ("✏", self._retag)]:
            btn = QPushButton(label)
            btn.setFixedSize(28, 28)
            btn.setStyleSheet(
                "QPushButton{background:rgba(255,255,255,200);border-radius:6px;font-size:14px;}"
                "QPushButton:hover{background:rgba(220,220,220,220);}"
            )
            btn.clicked.connect(slot)
            layout.addWidget(btn)
        self.hide()

    def show_for(self, anim_id: str, on_block, on_like, on_retag) -> None:
        self._anim_id  = anim_id
        self._on_block = on_block
        self._on_like  = on_like
        self._on_retag = on_retag
        self.show()

    def hide_bar(self) -> None:
        self._anim_id = None
        self.hide()

    def _block(self) -> None:
        if self._anim_id and self._on_block:
            self._on_block(self._anim_id)
            self.hide_bar()

    def _like(self) -> None:
        if self._anim_id and self._on_like:
            self._on_like(self._anim_id)
            self.hide_bar()

    def _retag(self) -> None:
        if not self._anim_id:
            return
        text, ok = QInputDialog.getText(self, "修正标签", "输入新标签（逗号分隔）：")
        if ok and text.strip() and self._on_retag:
            tags = [t.strip() for t in text.split(",") if t.strip()]
            self._on_retag(self._anim_id, tags)
            self.hide_bar()
