from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
from PyQt6.QtGui     import QIcon, QAction
from PyQt6.QtCore    import QObject
from pathlib import Path


class TrayIcon(QObject):
    def __init__(self, app, on_quit, character_options=None, current_character_asset_id: str = "", on_switch_character=None):
        super().__init__()
        self._tray = QSystemTrayIcon()
        icon_path = Path(__file__).parent.parent / "assets" / "tray_icon.png"
        if icon_path.exists():
            self._tray.setIcon(QIcon(str(icon_path)))
        menu = QMenu()
        options = list(character_options or [])
        current_asset_id = str(current_character_asset_id or "")
        if options and on_switch_character:
            char_menu = menu.addMenu("切换角色")
            for option in options:
                asset_id = str(option.get("asset_id") or "").strip()
                label = str(option.get("label") or asset_id).strip() or asset_id
                action = QAction(label)
                action.setCheckable(True)
                action.setChecked(asset_id == current_asset_id)
                action.triggered.connect(lambda _checked=False, a=asset_id: on_switch_character(a))
                char_menu.addAction(action)
            menu.addSeparator()
        quit_action = QAction("退出")
        quit_action.triggered.connect(on_quit)
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)
        self._tray.show()
