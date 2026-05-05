import copy
import json
import mimetypes
import struct
import threading
import uuid
from pathlib import Path
from typing import Callable, Optional
from urllib.error import HTTPError
from urllib import parse as _urllib_parse
from urllib import request as _urllib_request


class ComfyUIClient:
    """
    ComfyUI HTTP API 封装。
    兼容旧的单 prompt 模板，也支持当前桌宠项目的多 prompt API 工作流模板。
    """

    def __init__(
        self,
        base_url: str,
        workflow_template: dict,
        prompt_node_id: str = "",
        ref_image_path: str = "",
        output_dir: str = "",
    ):
        self._base_url = base_url.rstrip("/")
        self._bindings = workflow_template.get("_meta", {}) if workflow_template else {}
        self._workflow = {
            str(node_id): node
            for node_id, node in (workflow_template or {}).items()
            if not str(node_id).startswith("_")
        }
        self._prompt_node_id = str(prompt_node_id or self._bindings.get("legacy_prompt_node_id", ""))
        self._ref_image = Path(ref_image_path) if ref_image_path else None
        self._output_dir = Path(output_dir) if output_dir else Path(".")
        self._busy = False
        self._last_error: str | None = None
        self._lock = threading.Lock()

    def is_busy(self) -> bool:
        return self._busy

    def is_configured(self) -> bool:
        return bool(self._workflow)

    def get_workflow_node_count(self) -> int:
        return len(self._workflow)

    def get_last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def generate(
        self,
        prompt_text,
        ref_image_path: str,
        on_done: Callable[[Optional[str]], None],
    ) -> bool:
        """
        提交生成任务。若已在忙则返回 False。
        on_done 在后台线程调用，参数为生成的媒体路径（失败则为 None）。
        """
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            self._last_error = None
        prompt_data = self._normalize_prompt_data(prompt_text)
        print(
            f"[AI-DIAG][ComfyUIClient] generation queued "
            f"basename={prompt_data.get('output_basename') or '-'} "
            f"image_prompt_len={len(prompt_data.get('image_prompt') or '')} "
            f"video_prompt_len={len(prompt_data.get('video_prompt') or '')}",
            flush=True,
        )

        thread = threading.Thread(
            target=self._run,
            args=(prompt_text, ref_image_path, on_done),
            daemon=True,
        )
        thread.start()
        return True

    def _run(self, prompt_text, ref_image_path: str, on_done: Callable) -> None:
        result = None
        try:
            prompt_data = self._normalize_prompt_data(prompt_text)
            width, height = self._resolve_target_dimensions(Path(ref_image_path), prompt_data)
            prompt_data["target_width"] = width
            prompt_data["target_height"] = height
            uploaded_name = self._upload_reference_image(ref_image_path)
            print(
                f"[AI-DIAG][ComfyUIClient] reference uploaded image={uploaded_name} "
                f"basename={prompt_data.get('output_basename') or '-'} "
                f"target={width}x{height}",
                flush=True,
            )
            payload = self._build_payload(prompt_data, uploaded_name or Path(ref_image_path).name)
            client_id = str(uuid.uuid4())
            body = json.dumps({"prompt": payload, "client_id": client_id}).encode("utf-8")
            req = _urllib_request.Request(
                f"{self._base_url}/prompt",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with _urllib_request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except HTTPError as exc:
                details = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"ComfyUI prompt rejected: HTTP {exc.code} {details}") from exc
            prompt_id = data["prompt_id"]
            print(
                f"[AI-DIAG][ComfyUIClient] prompt submitted prompt_id={prompt_id} "
                f"client_id={client_id} basename={prompt_data.get('output_basename') or '-'}",
                flush=True,
            )
            result = self._poll_result(prompt_id, prompt_data.get("output_basename", ""))
            if result is None:
                raise RuntimeError("ComfyUI finished without a downloadable output.")
            print(
                f"[AI-DIAG][ComfyUIClient] generation downloaded prompt_id={prompt_id} "
                f"path={result}",
                flush=True,
            )
        except Exception as e:
            with self._lock:
                self._last_error = str(e)
            print(f"[AI-DIAG][ComfyUIClient] generation failed error={e}", flush=True)
        finally:
            with self._lock:
                self._busy = False
            on_done(result)

    def _build_payload(self, prompt_text, ref_image_name: str) -> dict:
        payload = copy.deepcopy(self._workflow)
        prompt_data = (
            dict(prompt_text)
            if isinstance(prompt_text, dict)
            else self._normalize_prompt_data(prompt_text)
        )

        if self._bindings:
            self._set_bound_input(
                payload,
                self._bindings.get("reference_image_node_id"),
                self._bindings.get("reference_image_input_name", "image"),
                ref_image_name,
            )
            self._set_bound_input(
                payload,
                self._bindings.get("image_prompt_node_id"),
                self._bindings.get("image_prompt_input_name", "prompt"),
                prompt_data["image_prompt"],
            )
            self._set_bound_input(
                payload,
                self._bindings.get("image_negative_prompt_node_id"),
                self._bindings.get("image_negative_prompt_input_name", "prompt"),
                prompt_data["image_negative_prompt"],
            )
            self._set_bound_input(
                payload,
                self._bindings.get("video_prompt_node_id"),
                self._bindings.get("video_prompt_input_name", "text"),
                prompt_data["video_prompt"],
            )
            self._set_bound_input(
                payload,
                self._bindings.get("video_negative_prompt_node_id"),
                self._bindings.get("video_negative_prompt_input_name", "text"),
                prompt_data["video_negative_prompt"],
            )
            if prompt_data.get("filename_prefix"):
                self._set_bound_input(
                    payload,
                    self._bindings.get("filename_prefix_node_id"),
                    self._bindings.get("filename_prefix_input_name", "filename_prefix"),
                    prompt_data["filename_prefix"],
                )
            if prompt_data.get("target_width"):
                self._set_bound_input(
                    payload,
                    self._bindings.get("resize_image_node_id"),
                    self._bindings.get("resize_image_width_input_name", "width"),
                    int(prompt_data["target_width"]),
                )
            if prompt_data.get("target_height"):
                self._set_bound_input(
                    payload,
                    self._bindings.get("resize_image_node_id"),
                    self._bindings.get("resize_image_height_input_name", "height"),
                    int(prompt_data["target_height"]),
                )
            return payload

        if self._prompt_node_id in payload:
            payload[self._prompt_node_id]["inputs"]["text"] = prompt_data["image_prompt"]
        return payload

    @staticmethod
    def _normalize_prompt_data(prompt_text) -> dict:
        if isinstance(prompt_text, dict):
            return {
                "image_prompt": prompt_text.get("image_prompt", ""),
                "image_negative_prompt": prompt_text.get("image_negative_prompt", ""),
                "video_prompt": prompt_text.get("video_prompt", prompt_text.get("image_prompt", "")),
                "video_negative_prompt": prompt_text.get("video_negative_prompt", ""),
                "filename_prefix": prompt_text.get("filename_prefix", ""),
                "output_basename": prompt_text.get("output_basename", ""),
                "target_width": prompt_text.get("target_width"),
                "target_height": prompt_text.get("target_height"),
            }
        return {
            "image_prompt": str(prompt_text),
            "image_negative_prompt": "",
            "video_prompt": str(prompt_text),
            "video_negative_prompt": "",
            "filename_prefix": "",
            "output_basename": "",
            "target_width": None,
            "target_height": None,
        }

    @staticmethod
    def _set_bound_input(payload: dict, node_id: str | None, input_name: str, value) -> None:
        if not node_id:
            return
        node = payload.get(str(node_id))
        if not node:
            return
        node.setdefault("inputs", {})[input_name] = value

    def _upload_reference_image(self, ref_image_path: str) -> str:
        path = Path(ref_image_path)
        if not path.exists():
            raise FileNotFoundError(f"reference image not found: {path}")

        boundary = f"----CodexComfy{uuid.uuid4().hex}"
        file_name = f"desktop_pet_{uuid.uuid4().hex}{path.suffix.lower() or '.png'}"
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

        body = bytearray()
        body.extend(self._multipart_field(boundary, "type", "input"))
        body.extend(self._multipart_file(boundary, "image", file_name, path.read_bytes(), mime_type))
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))

        req = _urllib_request.Request(
            f"{self._base_url}/upload/image",
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with _urllib_request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["name"]

    @staticmethod
    def _multipart_field(boundary: str, name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8")

    @staticmethod
    def _multipart_file(boundary: str, name: str, filename: str, content: bytes, mime_type: str) -> bytes:
        header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
        return header + content + b"\r\n"

    def _poll_result(self, prompt_id: str, output_basename: str = "", timeout_s: int = 600) -> Optional[str]:
        import time

        deadline = time.monotonic() + timeout_s
        next_progress_log_at = time.monotonic()
        while time.monotonic() < deadline:
            try:
                url = f"{self._base_url}/history/{prompt_id}"
                with _urllib_request.urlopen(url, timeout=10) as resp:
                    history = json.loads(resp.read().decode("utf-8"))
                if prompt_id in history:
                    outputs = history[prompt_id].get("outputs", {})
                    asset = self._extract_output_asset(outputs)
                    if asset:
                        print(
                            f"[AI-DIAG][ComfyUIClient] output asset ready prompt_id={prompt_id} "
                            f"filename={asset.get('filename')} subfolder={asset.get('subfolder', '')}",
                            flush=True,
                        )
                        return self._download_output_asset(asset, output_basename)
            except Exception:
                pass
            now = time.monotonic()
            if now >= next_progress_log_at:
                remaining = max(0, int(deadline - now))
                print(
                    f"[AI-DIAG][ComfyUIClient] waiting for prompt_id={prompt_id} "
                    f"timeout_in_s={remaining}",
                    flush=True,
                )
                next_progress_log_at = now + 10
            time.sleep(2)
        print(
            f"[AI-DIAG][ComfyUIClient] poll timeout prompt_id={prompt_id} "
            f"timeout_s={timeout_s}",
            flush=True,
        )
        return None

    @staticmethod
    def _extract_output_asset(outputs: dict) -> Optional[dict]:
        for node_output in outputs.values():
            for value in node_output.values():
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict) and item.get("filename"):
                            return item
                elif isinstance(value, dict) and value.get("filename"):
                    return value
        return None

    def _download_output_asset(self, asset: dict, output_basename: str = "") -> str:
        query = {"filename": asset["filename"]}
        if asset.get("subfolder"):
            query["subfolder"] = asset["subfolder"]
        if asset.get("type"):
            query["type"] = asset["type"]

        url = f"{self._base_url}/view?{_urllib_parse.urlencode(query)}"
        print(
            f"[AI-DIAG][ComfyUIClient] downloading asset url={url}",
            flush=True,
        )
        with _urllib_request.urlopen(url, timeout=120) as resp:
            data = resp.read()

        self._output_dir.mkdir(parents=True, exist_ok=True)
        out_path = self._build_output_path(asset, output_basename)
        out_path.write_bytes(data)
        print(
            f"[AI-DIAG][ComfyUIClient] asset saved path={out_path}",
            flush=True,
        )
        return str(out_path)

    def _build_output_path(self, asset: dict, output_basename: str) -> Path:
        original_name = Path(asset["filename"]).name
        suffix = Path(original_name).suffix or ".mp4"
        safe_base = self._sanitize_basename(output_basename)
        if not safe_base:
            return self._output_dir / original_name

        candidate = self._output_dir / f"{safe_base}{suffix}"
        if not candidate.exists():
            return candidate

        index = 2
        while True:
            candidate = self._output_dir / f"{safe_base}_{index:02d}{suffix}"
            if not candidate.exists():
                return candidate
            index += 1

    @staticmethod
    def _sanitize_basename(name: str) -> str:
        cleaned = "".join(
            ch if ch.isalnum() or ch in {"_", "-"} else "_"
            for ch in str(name or "").strip().lower()
        )
        while "__" in cleaned:
            cleaned = cleaned.replace("__", "_")
        return cleaned.strip("_")

    @classmethod
    def _resolve_target_dimensions(cls, ref_image_path: Path, prompt_data: dict) -> tuple[int, int]:
        explicit_width = prompt_data.get("target_width")
        explicit_height = prompt_data.get("target_height")
        if explicit_width and explicit_height:
            return int(explicit_width), int(explicit_height)

        original_width, original_height = cls._read_image_size(ref_image_path)
        generation_settings = cls._load_generation_settings(ref_image_path)
        max_side = int(generation_settings.get("max_side") or 768)
        divisible_by = max(2, int(generation_settings.get("divisible_by") or 32))
        preserve_aspect = bool(generation_settings.get("preserve_aspect", True))

        if not preserve_aspect:
            target = cls._round_dimension(max_side, divisible_by)
            return target, target

        long_side = max(original_width, original_height)
        scale = min(1.0, float(max_side) / float(long_side)) if long_side > 0 else 1.0
        scaled_width = max(1, int(round(original_width * scale)))
        scaled_height = max(1, int(round(original_height * scale)))
        width = cls._round_dimension(scaled_width, divisible_by)
        height = cls._round_dimension(scaled_height, divisible_by)

        if original_width >= original_height and width < height:
            width, height = height, width
        if original_height > original_width and height < width:
            width, height = height, width
        return width, height

    @staticmethod
    def _round_dimension(value: int, divisible_by: int) -> int:
        divisible_by = max(1, int(divisible_by or 1))
        rounded = max(divisible_by, int(round(float(value) / divisible_by) * divisible_by))
        return rounded

    @staticmethod
    def _load_generation_settings(ref_image_path: Path) -> dict:
        manifest_path = ref_image_path.parent / "pet_manifest.json"
        if not manifest_path.exists():
            return {}
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        generation = payload.get("generation")
        return generation if isinstance(generation, dict) else {}

    @staticmethod
    def _read_image_size(path: Path) -> tuple[int, int]:
        data = path.read_bytes()
        if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
            return struct.unpack(">II", data[16:24])
        if data[:6] in {b"GIF87a", b"GIF89a"} and len(data) >= 10:
            return struct.unpack("<HH", data[6:10])
        if data.startswith(b"\xff\xd8"):
            offset = 2
            while offset + 9 < len(data):
                while offset < len(data) and data[offset] == 0xFF:
                    offset += 1
                if offset >= len(data):
                    break
                marker = data[offset]
                offset += 1
                if marker in {0xD8, 0xD9}:
                    continue
                if offset + 2 > len(data):
                    break
                segment_length = struct.unpack(">H", data[offset:offset + 2])[0]
                if marker in {
                    0xC0, 0xC1, 0xC2, 0xC3,
                    0xC5, 0xC6, 0xC7,
                    0xC9, 0xCA, 0xCB,
                    0xCD, 0xCE, 0xCF,
                } and offset + 7 < len(data):
                    height, width = struct.unpack(">HH", data[offset + 3:offset + 7])
                    return width, height
                offset += max(0, segment_length)
        raise ValueError(f"unsupported image format for size detection: {path}")
