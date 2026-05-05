import re
from collections import Counter
from config import CURRENT_PET_ID


class MemoryManager:
    def __init__(self, db, asset_id: str | None = None):
        self._db = db
        self._asset_id = str(asset_id or CURRENT_PET_ID).strip().lower().replace(" ", "_").replace("-", "_") or CURRENT_PET_ID

    def add(self, summary: str, importance: float = 0.5) -> None:
        self._db.add_memory(summary, importance, asset_id=self._asset_id)

    def get_recent(self, limit: int = 5) -> list[dict]:
        return self._db.get_recent_memories(limit, asset_id=self._asset_id)

    def get_latest(self, limit: int = 5) -> list[dict]:
        if hasattr(self._db, "get_latest_memories"):
            return self._db.get_latest_memories(limit, asset_id=self._asset_id)
        return self.get_recent(limit)

    def log_interaction(
        self,
        event_type: str,
        dialogue: str = "",
        source: str = "user",
        emotion: str = "",
        action_trigger: str | None = None,
    ) -> None:
        summary = f"Interaction source={source} event={event_type}"
        if emotion:
            summary += f" emotion={emotion}"
        if action_trigger:
            summary += f" trigger={action_trigger}"
        if dialogue:
            summary += f", reply={dialogue}"
        self._db.add_memory(summary, 0.4, asset_id=self._asset_id)

    def log_quick_reply(
        self,
        event_type: str,
        dialogue: str = "",
        emotion: str = "",
        action_trigger: str | None = None,
    ) -> None:
        summary = f"QuickReply event={event_type}"
        if emotion:
            summary += f" emotion={emotion}"
        if action_trigger:
            summary += f" trigger={action_trigger}"
        if dialogue:
            summary += f", reply={dialogue}"
        self._db.add_memory(summary, 0.45, asset_id=self._asset_id)

    def maybe_store_chat_memory(
        self,
        user_text: str,
        reply_text: str = "",
        emotion: str = "",
        action_trigger: str | None = None,
    ) -> bool:
        if not self._should_store_chat_memory(user_text, action_trigger=action_trigger):
            return False

        summary = self._build_chat_memory_summary(
            user_text=user_text,
            reply_text=reply_text,
            emotion=emotion,
            action_trigger=action_trigger,
        )
        importance = 0.72 if action_trigger else 0.62
        self._db.add_memory(summary, importance, asset_id=self._asset_id)
        return True

    def get_recent_interaction_summary(self, limit: int = 3) -> str:
        recent = self.get_latest(limit=max(10, limit * 5))
        lines = [str(item.get("summary") or "").strip() for item in recent]
        lines = [line for line in lines if line]
        summary_lines = self._summarize_interaction_lines(lines, limit=limit, allowed_sources={"user"})
        if not summary_lines:
            return "(none)"
        return "\n".join(f"- {line}" for line in summary_lines)

    def get_recent_interaction_stats(self, limit: int = 12) -> str:
        recent = self.get_latest(limit=max(20, limit * 4))
        parsed = [
            item
            for item in (
                self._parse_interaction_details(str(entry.get("summary") or "").strip())
                for entry in recent
            )
            if item
        ]
        if not parsed:
            return "(none)"

        total = len(parsed[:limit])
        window = parsed[:limit]
        by_source = Counter(item["source"] for item in window)
        by_event = Counter(item["event_type"] for item in window)
        top_events = ", ".join(
            f"{event}x{count}" for event, count in by_event.most_common(3)
        ) or "(none)"
        return "\n".join([
            f"- total_interactions={total}",
            f"- user_interactions={by_source.get('user', 0)}",
            f"- threshold_interactions={by_source.get('threshold', 0)}",
            f"- top_events={top_events}",
        ])

    def get_recent_emotion_history(self, limit: int = 4) -> str:
        recent = self.get_latest(limit=max(12, limit * 4))
        items: list[str] = []
        seen: set[tuple[str, str]] = set()
        for entry in recent:
            parsed = self._parse_emotion_entry(str(entry.get("summary") or "").strip())
            if not parsed:
                continue
            key = (parsed["event_type"], parsed["emotion"])
            if key in seen:
                continue
            seen.add(key)
            items.append(f"- {parsed['event_type']} -> {parsed['emotion']}")
            if len(items) >= limit:
                break
        return "\n".join(items) if items else "(none)"

    def get_last_ai_behavior(self) -> str | None:
        for item in self.get_latest(limit=10):
            summary = str(item.get("summary") or "")
            if "AI accepted behavior=" not in summary:
                continue
            match = re.search(r"behavior=([a-z_]+)", summary)
            if match:
                return match.group(1)
        return None

    def get_recent_ai_behaviors(self, limit: int = 3) -> list[str]:
        behaviors: list[str] = []
        for item in self.get_latest(limit=max(10, limit * 4)):
            summary = str(item.get("summary") or "")
            if "AI accepted behavior=" not in summary:
                continue
            match = re.search(r"behavior=([a-z_]+)", summary)
            if match:
                behaviors.append(match.group(1))
            if len(behaviors) >= limit:
                break
        return behaviors

    def log_ai_decision(self, decision: dict, accepted: bool, reason: str = "") -> None:
        behavior = str(decision.get("behavior_type") or "idle_normal")
        action_desc = str(decision.get("action_desc") or "").strip()
        anim_tags = decision.get("anim_tags") or []
        prompt_request = decision.get("prompt_request") or {}

        summary = self._build_ai_decision_summary(
            behavior=behavior,
            action_desc=action_desc,
            anim_tags=anim_tags,
            accepted=accepted,
            reason=reason,
            prompt_request=prompt_request,
        )
        importance = 0.75 if not accepted else 0.55
        self._db.add_memory(summary, importance, asset_id=self._asset_id)

    @staticmethod
    def _build_ai_decision_summary(
        behavior: str,
        action_desc: str,
        anim_tags: list,
        accepted: bool,
        reason: str,
        prompt_request: dict,
    ) -> str:
        tags_text = ", ".join(str(tag) for tag in anim_tags[:4]) or "no tags"
        status = "accepted" if accepted else "rejected"
        summary = f"AI {status} behavior={behavior} with tags={tags_text}"
        if action_desc:
            summary += f", action={action_desc}"

        if accepted:
            return f"{summary}."

        motion_intensity = str(prompt_request.get('motion_intensity') or "").strip()
        extra = f" Prompt hint motion_intensity={motion_intensity}." if motion_intensity else ""
        return f"{summary} because {reason}.{extra}"

    @staticmethod
    def _summarize_interaction_lines(
        lines: list[str],
        limit: int,
        allowed_sources: set[str] | None = None,
    ) -> list[str]:
        summarized: list[str] = []
        seen: set[tuple[str, str]] = set()
        for line in lines:
            parsed = MemoryManager._parse_interaction_line(line)
            if not parsed:
                continue
            source, event_type = parsed
            if allowed_sources and source not in allowed_sources:
                continue
            key = (source, event_type)
            if key in seen:
                continue
            seen.add(key)
            summarized.append(f"Interaction source={source} event={event_type}")
            if len(summarized) >= limit:
                break
        return summarized

    @staticmethod
    def _parse_interaction_line(line: str) -> tuple[str, str] | None:
        match = re.search(r"^Interaction source=([a-z_]+) event=([A-Z_a-z0-9]+)", line)
        if not match:
            return None
        return match.group(1), match.group(2)

    @staticmethod
    def _parse_interaction_details(line: str) -> dict | None:
        match = re.search(
            r"^Interaction source=([a-z_]+) event=([A-Z_a-z0-9]+)(?: emotion=([a-z_]+))?(?: trigger=([a-z_]+))?",
            line,
        )
        if not match:
            return None
        return {
            "source": match.group(1),
            "event_type": match.group(2),
            "emotion": match.group(3) or "",
            "trigger": match.group(4) or "",
        }

    @staticmethod
    def _parse_emotion_entry(line: str) -> dict | None:
        quick = re.search(r"^QuickReply event=([A-Z_a-z0-9]+) emotion=([a-z_]+)", line)
        if quick:
            return {
                "kind": "quick",
                "event_type": quick.group(1),
                "emotion": quick.group(2),
            }

        interaction = re.search(
            r"^Interaction source=([a-z_]+) event=([A-Z_a-z0-9]+) emotion=([a-z_]+)",
            line,
        )
        if interaction:
            return {
                "kind": interaction.group(1),
                "event_type": interaction.group(2),
                "emotion": interaction.group(3),
            }
        return None

    @staticmethod
    def _should_store_chat_memory(user_text: str, action_trigger: str | None = None) -> bool:
        text = " ".join(str(user_text or "").split())
        if not text:
            return False
        if action_trigger:
            return True

        lowered = text.lower()
        patterns = (
            r"我叫",
            r"我是",
            r"喜欢",
            r"不喜欢",
            r"讨厌",
            r"想要",
            r"不要",
            r"记住",
            r"别忘",
            r"明天",
            r"今晚",
            r"下次",
            r"以后",
            r"生日",
            r"上班",
            r"开会",
            r"考试",
            r"旅行",
            r"my name",
            r"i am",
            r"i like",
            r"i don't like",
            r"tomorrow",
            r"later",
        )
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
            return True
        return len(text) >= 24 and ("我" in text or "my" in lowered or "i " in lowered)

    @staticmethod
    def _build_chat_memory_summary(
        user_text: str,
        reply_text: str = "",
        emotion: str = "",
        action_trigger: str | None = None,
    ) -> str:
        user_clip = MemoryManager._clip_text(user_text, 48)
        reply_clip = MemoryManager._clip_text(reply_text, 48)
        summary = f'Chat memory user="{user_clip}"'
        if reply_clip:
            summary += f' pet_reply="{reply_clip}"'
        if emotion:
            summary += f" emotion={emotion}"
        if action_trigger:
            summary += f" trigger={action_trigger}"
        return summary

    @staticmethod
    def _clip_text(text: str, limit: int) -> str:
        single = " ".join(str(text or "").split())
        if len(single) <= limit:
            return single
        if limit <= 3:
            return single[:limit]
        return single[: limit - 3] + "..."
