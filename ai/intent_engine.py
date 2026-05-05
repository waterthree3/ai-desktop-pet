import json
import threading
import time
import urllib.request
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from animation.effect_profiles import infer_behavior_family
from ai.prompt_templates import (
    CHAT_SYSTEM_PROMPT,
    QUICK_RESPONSE_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    _default_behavior,
    build_autonomous_prompt,
    build_chat_prompt,
    build_quick_reaction_prompt,
    parse_chat_output,
    parse_quick_reaction_output,
    parse_autonomous_output,
    parse_autonomous_output_with_meta,
)

LLM_CONFIG_PATH = Path(__file__).parent.parent / "assets" / "llm_config.json"


def _normalize_behavior_label(value: str | None) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _behavior_family_for_label(value: str | None) -> str:
    label = _normalize_behavior_label(value)
    if not label:
        return ""
    parts = [part for part in label.split("_") if part]
    return infer_behavior_family({
        "behavior_type": label,
        "tags": [label, *parts],
    })


def _build_novelty_retry_context(
    decision: dict,
    recent_behavior_history: list[str] | None,
    manual_trigger: bool,
) -> dict | None:
    history = [
        _normalize_behavior_label(item)
        for item in (recent_behavior_history or [])
        if _normalize_behavior_label(item)
    ]
    if not history:
        return None

    recent_window: list[str] = []
    seen: set[str] = set()
    for item in history:
        if item in seen:
            continue
        seen.add(item)
        recent_window.append(item)
        if len(recent_window) >= 2:
            break

    if not recent_window:
        return None

    repeated_behavior = _normalize_behavior_label(decision.get("behavior_type"))
    if not repeated_behavior:
        return None

    decision_family = _behavior_family_for_label(repeated_behavior)
    recent_families = [
        family
        for family in (_behavior_family_for_label(item) for item in recent_window)
        if family
    ]
    repeated_family = bool(
        decision_family
        and recent_families
        and len(set(recent_families)) == 1
        and decision_family == recent_families[0]
    )
    needs_retry = repeated_behavior in recent_window
    if manual_trigger:
        needs_retry = needs_retry or bool(decision_family and decision_family in recent_families)
    else:
        needs_retry = needs_retry or repeated_family

    if not needs_retry:
        return None

    return {
        "repeated_behavior": repeated_behavior,
        "decision_family": decision_family,
        "recent_behaviors": recent_window,
        "recent_families": recent_families,
        "manual_trigger": bool(manual_trigger),
        "repeated_family": repeated_family,
    }


class IntentEngine(QThread):
    """
    Autonomous behavior inference worker.
    Runs model inference off the UI thread and emits `decision_ready` with a parsed JSON decision.
    """

    decision_ready = pyqtSignal(dict)
    quick_reply_ready = pyqtSignal(dict)
    chat_ready = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._enabled: bool = False
        self._backend: str = "local"
        self._cfg: dict = {}
        self._llm = None
        self._pending: dict | None = None
        self._request_seq: int = 0
        self._quick_seq: int = 0
        self._chat_seq: int = 0
        self._quick_inflight: bool = False
        self._chat_inflight: bool = False
        self._infer_lock = threading.Lock()
        self._quick_lock = threading.Lock()
        self._chat_lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def has_pending_request(self) -> bool:
        return self._pending is not None

    def is_busy(self) -> bool:
        return self.isRunning() or self.has_pending_request() or self._quick_inflight or self._chat_inflight

    @staticmethod
    def _load_enabled_flag(path: Path) -> bool:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return False
        return bool(raw.get("enabled", False))

    @staticmethod
    def _save_enabled_flag(path: Path, enabled: bool) -> None:
        cfg_path = Path(path)
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        raw["enabled"] = bool(enabled)
        cfg_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_model(self) -> None:
        started_at = time.perf_counter()
        try:
            raw = json.loads(LLM_CONFIG_PATH.read_text(encoding="utf-8"))
            self._enabled = self._load_enabled_flag(LLM_CONFIG_PATH)
            if not self._enabled:
                self._backend = "disabled"
                self._cfg = {}
                self._llm = None
                print("[AI-DIAG][IntentEngine] load_model disabled by config", flush=True)
                return
            self._backend = raw.get("backend", "local")
            self._cfg = raw.get(self._backend, {})
            print(
                f"[AI-DIAG][IntentEngine] load_model start backend={self._backend} "
                f"model={self._cfg.get('model', self._cfg.get('model_path', 'n/a'))}",
                flush=True,
            )
        except Exception as e:
            print(f"[AI-DIAG][IntentEngine] failed to read llm_config.json, falling back: {e}", flush=True)
            self._enabled = False
            self._backend = "disabled"
            return

        if self._backend == "local":
            try:
                from llama_cpp import Llama

                model_path = Path(self._cfg.get("model_path", "models/model.gguf"))
                self._llm = Llama(
                    model_path=str(model_path),
                    n_ctx=self._cfg.get("n_ctx", 2048),
                    verbose=False,
                )
                print(f"[AI-DIAG][IntentEngine] local model loaded path={model_path}", flush=True)
            except Exception as e:
                print(f"[AI-DIAG][IntentEngine] local model load failed, falling back: {e}", flush=True)
                self._enabled = False
                self._backend = "disabled"
        elif self._backend in ("deepseek", "claude"):
            if not self._cfg.get("api_key", "").startswith("YOUR_"):
                print(f"[AI-DIAG][IntentEngine] API mode ready backend={self._backend}", flush=True)
            else:
                print(f"[AI-DIAG][IntentEngine] {self._backend} api_key missing, disabling LLM", flush=True)
                self._enabled = False
                self._backend = "disabled"
        print(
            f"[AI-DIAG][IntentEngine] load_model done enabled={self._enabled} "
            f"backend={self._backend} dt_ms={(time.perf_counter() - started_at) * 1000:.1f}",
            flush=True,
        )

    def set_enabled(self, enabled: bool, persist: bool = True) -> bool:
        started_at = time.perf_counter()
        print(
            f"[AI-DIAG][IntentEngine] set_enabled requested enabled={enabled} "
            f"persist={persist} current_enabled={self._enabled} backend={self._backend}",
            flush=True,
        )
        if persist:
            self._save_enabled_flag(LLM_CONFIG_PATH, enabled)

        if not enabled:
            self._enabled = False
            self._backend = "disabled"
            self._cfg = {}
            self._llm = None
            self._pending = None
            print(
                f"[AI-DIAG][IntentEngine] disabled at runtime dt_ms={(time.perf_counter() - started_at) * 1000:.1f}",
                flush=True,
            )
            return False

        self.load_model()
        print(
            f"[AI-DIAG][IntentEngine] set_enabled completed actual={self._enabled} "
            f"backend={self._backend} dt_ms={(time.perf_counter() - started_at) * 1000:.1f}",
            flush=True,
        )
        return self._enabled

    def request(
        self,
        pet_state: dict,
        memories: list,
        available_tags: list,
        suggested_behaviors: list[dict] | None = None,
        rejection_context: list | None = None,
        last_behavior_hint: str | None = None,
        interaction_summary: str | None = None,
        interaction_stats: str | None = None,
        emotion_history: str | None = None,
        recent_behavior_history: list[str] | None = None,
        manual_trigger: bool = False,
    ) -> None:
        if not self._enabled:
            print("[AI-DIAG][IntentEngine] request skipped because engine is disabled", flush=True)
            return
        self._request_seq += 1
        req_id = self._request_seq
        had_pending = self._pending is not None
        self._pending = {
            "req_id": req_id,
            "pet_state": pet_state,
            "memories": memories,
            "available_tags": available_tags,
            "suggested_behaviors": suggested_behaviors or [],
            "rejection_context": rejection_context or [],
            "last_behavior_hint": last_behavior_hint,
            "interaction_summary": interaction_summary,
            "interaction_stats": interaction_stats,
            "emotion_history": emotion_history,
            "recent_behavior_history": recent_behavior_history or [],
            "manual_trigger": manual_trigger,
        }
        print(
            f"[AI-DIAG][IntentEngine] queued autonomous req_id={req_id} running={self.isRunning()} "
            f"replaced_pending={had_pending} memories={len(memories)} tags={len(available_tags)} "
            f"rejections={len(rejection_context or [])} manual_trigger={manual_trigger}",
            flush=True,
        )
        if not self.isRunning():
            self.start()

    def run(self) -> None:
        if not self._enabled:
            return
        if not self._pending:
            self.decision_ready.emit(_default_behavior())
            return

        pending = self._pending
        self._pending = None
        req_id = pending.get("req_id", -1)
        started_at = time.perf_counter()
        prompt = build_autonomous_prompt(
            pending["pet_state"],
            pending["memories"],
            pending["available_tags"],
            suggested_behaviors=pending.get("suggested_behaviors", []),
            rejection_context=pending.get("rejection_context", []),
            last_behavior_hint=pending.get("last_behavior_hint"),
            interaction_summary=pending.get("interaction_summary"),
            interaction_stats=pending.get("interaction_stats"),
            emotion_history=pending.get("emotion_history"),
            recent_behavior_history=pending.get("recent_behavior_history", []),
            manual_trigger=bool(pending.get("manual_trigger")),
        )
        print(
            f"[AI-DIAG][IntentEngine] run start req_id={req_id} backend={self._backend} "
            f"prompt_chars={len(prompt)}",
            flush=True,
        )
        print(
            f"[AI-DIAG][IntentEngine] run context req_id={req_id} "
            f"hunger={pending['pet_state'].get('hunger', 0):.1f} "
            f"mood={pending['pet_state'].get('mood', 0):.1f} "
            f"energy={pending['pet_state'].get('energy', 0):.1f} "
            f"cleanliness={pending['pet_state'].get('cleanliness', 0):.1f} "
            f"last_behavior={pending.get('last_behavior_hint') or '-'} "
            f"recent_history={pending.get('recent_behavior_history', [])} "
            f"manual_trigger={bool(pending.get('manual_trigger'))}",
            flush=True,
        )
        print(
            f"[AI-DIAG][IntentEngine] run interaction_summary req_id={req_id} "
            f"{self._compact_text(str(pending.get('interaction_summary') or '(none)'), 220)}",
            flush=True,
        )
        print(
            f"[AI-DIAG][IntentEngine] run interaction_stats req_id={req_id} "
            f"{self._compact_text(str(pending.get('interaction_stats') or '(none)'), 220)}",
            flush=True,
        )
        print(
            f"[AI-DIAG][IntentEngine] run emotion_history req_id={req_id} "
            f"{self._compact_text(str(pending.get('emotion_history') or '(none)'), 220)}",
            flush=True,
        )
        if pending.get("rejection_context"):
            print(
                f"[AI-DIAG][IntentEngine] run rejection_context req_id={req_id} "
                f"{self._compact_text(json.dumps(pending.get('rejection_context', []), ensure_ascii=False), 220)}",
                flush=True,
            )

        try:
            decision = self._infer_autonomous(prompt)
            decision = self._retry_if_repetitive(
                req_id=req_id,
                prompt=prompt,
                pending=pending,
                decision=decision,
            )
        except Exception as e:
            print(f"[AI-DIAG][IntentEngine] inference failed req_id={req_id} error={e}", flush=True)
            decision = _default_behavior()

        print(
            f"[AI-DIAG][IntentEngine] run done req_id={req_id} behavior={decision.get('behavior_type')} "
            f"movement={decision.get('movement')} dialogue_len={len(str(decision.get('dialogue') or ''))} "
            f"dt_ms={(time.perf_counter() - started_at) * 1000:.1f}",
            flush=True,
        )
        self.decision_ready.emit(decision)

    def request_quick_reply_async(
        self,
        pet_state: dict,
        event_type: str,
        event_desc: str,
        recent_memories: list | None = None,
        meta: dict | None = None,
    ) -> bool:
        if not self._enabled:
            print(f"[AI-DIAG][IntentEngine] quick_infer skipped disabled event={event_type}", flush=True)
            return False
        if self.isRunning() or self.has_pending_request():
            print(
                f"[AI-DIAG][IntentEngine] quick_infer skipped because autonomous run is active "
                f"event={event_type}",
                flush=True,
            )
            return False
        if self._chat_inflight:
            print(f"[AI-DIAG][IntentEngine] quick_infer skipped because chat is inflight event={event_type}", flush=True)
            return False

        with self._quick_lock:
            if self._quick_inflight:
                print(
                    f"[AI-DIAG][IntentEngine] quick_infer skipped because another quick request is inflight "
                    f"event={event_type}",
                    flush=True,
                )
                return False
            self._quick_inflight = True
            self._quick_seq += 1
            req_id = self._quick_seq

        print(
            f"[AI-DIAG][IntentEngine] quick_infer queued req_id={req_id} event={event_type} backend={self._backend}",
            flush=True,
        )
        thread = threading.Thread(
            target=self._run_quick_reply,
            args=(
                req_id,
                dict(pet_state),
                event_type,
                event_desc,
                list(recent_memories or []),
                dict(meta or {}),
            ),
            daemon=True,
        )
        thread.start()
        return True

    def request_chat_async(
        self,
        pet_state: dict,
        user_text: str,
        recent_memories: list | None = None,
        interaction_summary: str | None = None,
        meta: dict | None = None,
    ) -> bool:
        user_text = str(user_text or "").strip()
        if not self._enabled:
            print("[AI-DIAG][IntentEngine] chat skipped because engine is disabled", flush=True)
            return False
        if not user_text:
            print("[AI-DIAG][IntentEngine] chat skipped because user_text is empty", flush=True)
            return False
        if self.isRunning() or self.has_pending_request():
            print("[AI-DIAG][IntentEngine] chat skipped because autonomous run is active", flush=True)
            return False
        if self._quick_inflight:
            print("[AI-DIAG][IntentEngine] chat skipped because quick reply is inflight", flush=True)
            return False

        with self._chat_lock:
            if self._chat_inflight:
                print("[AI-DIAG][IntentEngine] chat skipped because another chat request is inflight", flush=True)
                return False
            self._chat_inflight = True
            self._chat_seq += 1
            req_id = self._chat_seq

        print(
            f"[AI-DIAG][IntentEngine] chat queued req_id={req_id} backend={self._backend} "
            f"user_text_len={len(user_text)}",
            flush=True,
        )
        thread = threading.Thread(
            target=self._run_chat_reply,
            args=(
                req_id,
                dict(pet_state),
                user_text,
                list(recent_memories or []),
                str(interaction_summary or ""),
                dict(meta or {}),
            ),
            daemon=True,
        )
        thread.start()
        return True

    def _run_quick_reply(
        self,
        req_id: int,
        pet_state: dict,
        event_type: str,
        event_desc: str,
        recent_memories: list,
        meta: dict,
    ) -> None:
        started_at = time.perf_counter()
        prompt = build_quick_reaction_prompt(
            pet_state=pet_state,
            event_type=event_type,
            event_desc=event_desc,
            recent_memories=recent_memories,
        )
        print(
            f"[AI-DIAG][IntentEngine] quick_infer start req_id={req_id} event={event_type} "
            f"backend={self._backend} prompt_chars={len(prompt)} memories={len(recent_memories)}",
            flush=True,
        )

        result = None
        error_text = ""
        try:
            result = self._infer_quick_response(prompt)
        except Exception as e:
            error_text = str(e)
            print(
                f"[AI-DIAG][IntentEngine] quick inference failed req_id={req_id} event={event_type} "
                f"dt_ms={(time.perf_counter() - started_at) * 1000:.1f} error={e}",
                flush=True,
            )
        finally:
            with self._quick_lock:
                self._quick_inflight = False

        print(
            f"[AI-DIAG][IntentEngine] quick_infer done req_id={req_id} event={event_type} "
            f"ok={bool(result)} dialogue_len={len(str((result or {}).get('dialogue') or ''))} "
            f"dt_ms={(time.perf_counter() - started_at) * 1000:.1f}",
            flush=True,
        )
        self.quick_reply_ready.emit(
            {
                "req_id": req_id,
                "event_type": event_type,
                "ok": bool(result),
                "result": result or {},
                "meta": meta,
                "error": error_text,
                "dt_ms": (time.perf_counter() - started_at) * 1000,
            }
        )

    def _run_chat_reply(
        self,
        req_id: int,
        pet_state: dict,
        user_text: str,
        recent_memories: list,
        interaction_summary: str,
        meta: dict,
    ) -> None:
        started_at = time.perf_counter()
        prompt = build_chat_prompt(
            pet_state=pet_state,
            user_input=user_text,
            recent_memories=recent_memories,
            interaction_summary=interaction_summary,
        )
        print(
            f"[AI-DIAG][IntentEngine] chat start req_id={req_id} backend={self._backend} "
            f"prompt_chars={len(prompt)} memories={len(recent_memories)}",
            flush=True,
        )

        result = None
        error_text = ""
        try:
            result = self._infer_chat_response(prompt)
        except Exception as e:
            error_text = str(e)
            print(
                f"[AI-DIAG][IntentEngine] chat failed req_id={req_id} "
                f"dt_ms={(time.perf_counter() - started_at) * 1000:.1f} error={e}",
                flush=True,
            )
        finally:
            with self._chat_lock:
                self._chat_inflight = False

        print(
            f"[AI-DIAG][IntentEngine] chat done req_id={req_id} ok={bool(result)} "
            f"dialogue_len={len(str((result or {}).get('dialogue') or ''))} "
            f"dt_ms={(time.perf_counter() - started_at) * 1000:.1f}",
            flush=True,
        )
        self.chat_ready.emit(
            {
                "req_id": req_id,
                "user_text": user_text,
                "ok": bool(result),
                "result": result or {},
                "meta": meta,
                "error": error_text,
                "dt_ms": (time.perf_counter() - started_at) * 1000,
            }
        )

    def _infer_autonomous(self, prompt: str) -> dict:
        raw = self._infer_raw(
            system_prompt=SYSTEM_PROMPT,
            prompt=prompt,
            max_tokens=self._cfg.get("max_tokens", 256),
            temperature=self._cfg.get("temperature", 0.7),
            timeout=30,
        )
        decision, meta = parse_autonomous_output_with_meta(raw)
        print(
            f"[AI-DIAG][IntentEngine] autonomous_raw parsed_ok={not meta['used_fallback']} "
            f"reason={meta['reason']} raw={self._compact_text(raw, 280)}",
            flush=True,
        )
        return decision

    def _retry_if_repetitive(
        self,
        req_id: int,
        prompt: str,
        pending: dict,
        decision: dict,
    ) -> dict:
        retry_context = _build_novelty_retry_context(
            decision=decision,
            recent_behavior_history=pending.get("recent_behavior_history", []),
            manual_trigger=bool(pending.get("manual_trigger")),
        )
        if not retry_context:
            return decision

        repeated_behavior = retry_context["repeated_behavior"]
        recent_window = retry_context["recent_behaviors"]
        recent_families = retry_context["recent_families"]
        decision_family = retry_context["decision_family"]
        repeated_family = bool(retry_context["repeated_family"])
        retry_kind = "manual" if retry_context["manual_trigger"] else "autonomous"
        avoid_text = ", ".join(recent_window)
        family_text = ", ".join(recent_families) or "(none)"
        print(
            f"[AI-DIAG][IntentEngine] {retry_kind} novelty retry req_id={req_id} "
            f"repeated_behavior={repeated_behavior} repeated_family={decision_family if repeated_family else '-'} "
            f"avoid={avoid_text} avoid_families={family_text}",
            flush=True,
        )
        retry_prompt = (
            f"{prompt}\n\n"
            "Novelty retry instruction:\n"
            f"- Your previous answer repeated behavior_type={repeated_behavior}.\n"
            f"- Do not choose any of these recent behaviors this time: {avoid_text}.\n"
            f"- Avoid repeating the same recent behavior family when possible: {family_text}.\n"
            "- Pick a different believable behavior_type and keep the JSON schema unchanged."
        )
        retried = self._infer_autonomous(retry_prompt)
        print(
            f"[AI-DIAG][IntentEngine] {retry_kind} novelty retry result req_id={req_id} "
            f"behavior={retried.get('behavior_type')}",
            flush=True,
        )
        return retried

    def _infer_quick_response(self, prompt: str) -> dict | None:
        raw = self._infer_raw(
            system_prompt=QUICK_RESPONSE_SYSTEM_PROMPT,
            prompt=prompt,
            max_tokens=min(self._cfg.get("max_tokens", 256), 160),
            temperature=min(self._cfg.get("temperature", 0.7), 0.9),
            timeout=5,
        )
        return parse_quick_reaction_output(raw)

    def _infer_chat_response(self, prompt: str) -> dict | None:
        raw = self._infer_raw(
            system_prompt=CHAT_SYSTEM_PROMPT,
            prompt=prompt,
            max_tokens=min(self._cfg.get("max_tokens", 256), 200),
            temperature=min(self._cfg.get("temperature", 0.7), 0.9),
            timeout=8,
        )
        return parse_chat_output(raw)

    def _infer_raw(
        self,
        system_prompt: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> str:
        with self._infer_lock:
            if self._backend == "local" and self._llm:
                return self._infer_local_raw(system_prompt, prompt, max_tokens, temperature)
            if self._backend == "deepseek":
                return self._infer_openai_compat_raw(system_prompt, prompt, max_tokens, temperature, timeout)
            if self._backend == "claude":
                return self._infer_claude_raw(system_prompt, prompt, max_tokens, timeout)
            return json.dumps(_default_behavior(), ensure_ascii=False)

    @staticmethod
    def _compact_text(text: str, limit: int = 200) -> str:
        single_line = " ".join(str(text).split())
        if len(single_line) <= limit:
            return single_line
        return single_line[:limit] + "..."

    def _infer_local_raw(self, system_prompt: str, prompt: str, max_tokens: int, temperature: float) -> str:
        resp = self._llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return resp["choices"][0]["message"]["content"]

    def _infer_openai_compat_raw(
        self,
        system_prompt: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> str:
        payload = json.dumps(
            {
                "model": self._cfg.get("model", "deepseek-chat"),
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self._cfg['base_url']}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._cfg['api_key']}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]

    def _infer_claude_raw(
        self,
        system_prompt: str,
        prompt: str,
        max_tokens: int,
        timeout: int,
    ) -> str:
        payload = json.dumps(
            {
                "model": self._cfg.get("model", "claude-haiku-4-5-20251001"),
                "system": system_prompt,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self._cfg["api_key"],
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["content"][0]["text"]
