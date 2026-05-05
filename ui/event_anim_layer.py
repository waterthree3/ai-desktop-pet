"""
Layer B: 事件动画层 — 独立管理事件动画的播放/取消/回调。

状态: NONE / PLAYING_ONCE / PLAYING_LOOP
每次 play/stop 前必须 _disconnect_old_callback()，防止旧回调干扰新事件。
"""
from enum import Enum


class EventAnimState(Enum):
    NONE = "none"
    PLAYING_ONCE = "once"
    PLAYING_LOOP = "loop"


class EventAnimLayer:
    def __init__(self, animation_widget):
        self._widget = animation_widget
        self.state = EventAnimState.NONE
        self._on_done_callback = None
        self._current_gif: str | None = None

    @property
    def current_gif(self) -> str | None:
        return self._current_gif

    def play_once(self, gif_path: str, on_done=None, mirror=False):
        self._disconnect_old_callback()
        self.state = EventAnimState.PLAYING_ONCE
        self._on_done_callback = on_done
        self._current_gif = gif_path
        self._widget.play_once(gif_path, on_finished=self._on_finished, mirror=mirror)

    def play_loop(self, gif_path: str, on_done=None, mirror=False):
        self._disconnect_old_callback()
        self.state = EventAnimState.PLAYING_LOOP
        self._on_done_callback = on_done
        self._current_gif = gif_path
        self._widget.play_loop(gif_path, mirror=mirror)

    def stop(self):
        self._disconnect_old_callback()
        self._widget.stop()
        self.state = EventAnimState.NONE
        self._current_gif = None

    def finish(self):
        """recovery 完毕后主动结束循环动画"""
        self._widget.stop()
        self._on_finished()

    def _on_finished(self):
        self.state = EventAnimState.NONE
        self._current_gif = None
        callback = self._on_done_callback
        self._on_done_callback = None
        if callback:
            callback()

    def _disconnect_old_callback(self):
        self._on_done_callback = None
