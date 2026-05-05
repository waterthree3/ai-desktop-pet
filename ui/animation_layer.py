import time
from pathlib import Path
from PyQt6.QtWidgets import QLabel
from PyQt6.QtCore    import QSize, Qt, QUrl
from PyQt6.QtGui     import QMovie, QTransform, QPixmap


_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".avi"}


class AnimationLayer(QLabel):
    """
    GIF + MP4 统一播放层。
    - play_loop(path, mirror)  : 循环播放
    - play_once(path, callback): 播一次后执行回调
    - stop()                   : 停止并清空

    GIF 通过 QMovie 解帧后手动设 pixmap（支持镜像）。
    MP4 通过 QMediaPlayer + QVideoSink 捕获帧后手动设 pixmap（支持镜像）。
    """

    def __init__(self, parent=None, size: QSize = QSize(150, 150)):
        super().__init__(parent)
        self._size           = size
        self._mirror         = False
        self._loop_mode      = False
        self._once_callback  = None

        # GIF
        self._movie: QMovie | None = None
        # MP4
        self._player = None   # QMediaPlayer，延迟初始化（需要 QtMultimedia）
        self._sink   = None   # QVideoSink

        self._play_start_t: float = 0.0   # 播放开始时间
        self._play_path: str = ""         # 当前播放文件路径

        self.setFixedSize(size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    @property
    def current_path(self) -> str:
        return self._play_path

    # ── 公开接口 ────────────────────────────────────────────

    def play_loop(self, path: str, mirror: bool = False) -> None:
        print(f"[DBG][AnimLayer] play_loop  path={path}  is_video={self._is_video(path)}", flush=True)
        self._stop_current()
        self._mirror    = mirror
        self._loop_mode = True
        self._once_callback = None
        self._frame_count = 0  # 帧计数，用于诊断
        if self._is_video(path):
            self._start_video(path)
        else:
            self._start_gif(path)

    def play_once(self, path: str, on_finished=None, mirror: bool = False) -> None:
        self._stop_current()
        self._mirror        = mirror
        self._loop_mode     = False
        self._once_callback = on_finished
        self._frame_count = 0
        if self._is_video(path):
            self._start_video(path)
        else:
            self._start_gif(path)

    def stop(self) -> None:
        self._stop_current()
        self.clear()

    def set_mirror(self, mirror: bool) -> None:
        self._mirror = mirror  # 下一帧自动生效

    # ── 鼠标事件透传 + 埋点 ────────────────────────────────
    def mousePressEvent(self, event) -> None:
        print(f"[DBG][AnimLayer] mousePressEvent btn={event.button()}  → ignore & propagate", flush=True)
        event.ignore()   # 显式 ignore，确保事件传到父窗口 PetWindow

    def mouseReleaseEvent(self, event) -> None:
        print(f"[DBG][AnimLayer] mouseReleaseEvent btn={event.button()}  → ignore & propagate", flush=True)
        event.ignore()

    def mouseDoubleClickEvent(self, event) -> None:
        print(f"[DBG][AnimLayer] mouseDoubleClickEvent  → ignore & propagate", flush=True)
        event.ignore()

    # ── GIF ────────────────────────────────────────────────

    def _start_gif(self, path: str) -> None:
        self._play_start_t = time.monotonic()
        self._play_path = path
        self._movie = QMovie(path)
        self._movie.setCacheMode(QMovie.CacheMode.CacheAll)
        self._movie.frameChanged.connect(self._on_gif_frame)
        self._movie.start()

    def _on_gif_frame(self, frame_num: int) -> None:
        if self._movie is None:
            return
        pix = self._movie.currentPixmap()
        if not pix.isNull():
            pix = pix.scaled(
                self._size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        if self._mirror:
            pix = pix.transformed(QTransform().scale(-1, 1))
        self.setPixmap(pix)

        if not self._loop_mode and frame_num == self._movie.frameCount() - 1:
            elapsed = time.monotonic() - self._play_start_t
            print(f"[DBG][AnimLayer] GIF finished  duration={elapsed:.2f}s  frames={frame_num+1}  file={self._play_path}", flush=True)
            self._movie.stop()
            self._fire_callback()

    # ── MP4 ────────────────────────────────────────────────

    def _ensure_player(self) -> None:
        if self._player is not None:
            return
        from PyQt6.QtMultimedia import QMediaPlayer, QVideoSink
        self._sink = QVideoSink(self)
        self._sink.videoFrameChanged.connect(self._on_video_frame)
        self._player = QMediaPlayer(self)
        self._player.setVideoSink(self._sink)
        self._player.mediaStatusChanged.connect(self._on_media_status)

    def _start_video(self, path: str) -> None:
        self._play_start_t = time.monotonic()
        self._play_path = path
        # Recreate the multimedia backend when switching clips so stale status/frame
        # callbacks from the previous source cannot interfere with the new playback.
        self._dispose_player()
        self._ensure_player()
        self._player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
        self._player.play()

    def _on_video_frame(self, frame) -> None:
        if not frame.isValid():
            return
        img = frame.toImage()
        if img.isNull():
            return
        if not hasattr(self, '_frame_count'):
            self._frame_count = 0
        self._frame_count += 1
        if self._frame_count <= 3 or self._frame_count % 50 == 0:
            print(f"[DBG][AnimLayer] video frame #{self._frame_count}  size={img.width()}x{img.height()}  "
                  f"loop={self._loop_mode}", flush=True)
        img = img.scaled(
            self._size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        pix = QPixmap.fromImage(img)
        if self._mirror:
            pix = pix.transformed(QTransform().scale(-1, 1))
        self.setPixmap(pix)

    def _on_media_status(self, status) -> None:
        from PyQt6.QtMultimedia import QMediaPlayer
        elapsed = time.monotonic() - self._play_start_t
        print(f"[DBG][AnimLayer] media status: {status.name}  loop={self._loop_mode}  "
              f"frames_so_far={getattr(self, '_frame_count', '?')}  elapsed={elapsed:.2f}s  "
              f"file={self._play_path}", flush=True)
        if status != QMediaPlayer.MediaStatus.EndOfMedia:
            return
        if self._loop_mode:
            self._player.setPosition(0)
            self._player.play()
            print(f"[DBG][AnimLayer] video loop restart  duration_this_loop={elapsed:.2f}s  file={self._play_path}", flush=True)
            self._play_start_t = time.monotonic()  # 重置下一轮计时
        else:
            print(f"[DBG][AnimLayer] video finished  total_duration={elapsed:.2f}s  "
                  f"total_frames={getattr(self, '_frame_count', '?')}  file={self._play_path}", flush=True)
            self._fire_callback()

    # ── 内部工具 ────────────────────────────────────────────

    @staticmethod
    def _is_video(path: str) -> bool:
        return Path(path).suffix.lower() in _VIDEO_EXTS

    def _fire_callback(self) -> None:
        cb = self._once_callback
        self._once_callback = None
        if cb:
            cb()

    def _stop_current(self) -> None:
        if self._movie:
            try:
                self._movie.frameChanged.disconnect(self._on_gif_frame)
            except RuntimeError:
                pass
            self._movie.stop()
            self._movie = None
        self._dispose_player()
        self._play_path = ""
        self._once_callback = None

    def _dispose_player(self) -> None:
        player = self._player
        sink = self._sink
        self._player = None
        self._sink = None

        if sink is not None:
            try:
                sink.videoFrameChanged.disconnect(self._on_video_frame)
            except (RuntimeError, TypeError):
                pass

        if player is not None:
            try:
                player.mediaStatusChanged.disconnect(self._on_media_status)
            except (RuntimeError, TypeError):
                pass
            try:
                player.stop()
            except RuntimeError:
                pass
            try:
                player.setVideoSink(None)
            except RuntimeError:
                pass
            player.deleteLater()

        if sink is not None:
            sink.deleteLater()
