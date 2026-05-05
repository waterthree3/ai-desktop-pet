from enum import Enum, auto
from datetime import datetime
import json
from config import (
    HUNGER_DECAY, CLEANLINESS_DECAY, ENERGY_DECAY, ENERGY_SLEEP_RECOVERY,
    HUNGER_FORCE_THRESHOLD, ENERGY_FORCE_THRESHOLD, ENERGY_DROWSY_THRESHOLD,
    CURRENT_PET_ID, CURRENT_PET_LABEL, CURRENT_PET_PROFILE,
)


class ThresholdEvent(Enum):
    FORCE_HUNGRY = auto()   # hunger < 10 → 强制打断，播 starving
    FORCE_SLEEP  = auto()   # energy < 5  → 强制打断，播 sleep
    DROWSY       = auto()   # energy < 20 → 切慵懒待机（不强制打断 EVENT）


class PetData:
    def __init__(self, db, asset_id: str | None = None):
        self._db = db
        self.asset_id = str(asset_id or CURRENT_PET_ID).strip().lower().replace(" ", "_").replace("-", "_") or CURRENT_PET_ID
        state = db.get_pet_state(asset_id=self.asset_id)
        self.hunger                = state["hunger"]
        self.cleanliness           = state["cleanliness"]
        self.mood                  = state["mood"]
        self.energy                = state["energy"]
        self.exp                   = float(state.get("exp", 0.0) or 0.0)
        self.intimacy              = float(state.get("intimacy", 0.0) or 0.0)
        self.growth_stage          = str(state.get("growth_stage") or "newborn")
        self.derived_status_tags   = self._parse_tags(state.get("derived_status_tags"))
        self.personality_extrovert = state["personality_extrovert"]
        self.personality_obedient  = state["personality_obedient"]
        self.personality_curious   = state["personality_curious"]
        last_active = state.get("last_active_at")
        if last_active:
            try:
                dt = datetime.now() - datetime.fromisoformat(last_active)
                self.apply_decay(min(dt.total_seconds(), 86400))
            except ValueError:
                pass
        self.refresh_derived_status_tags()
        self.refresh_growth_stage()

    def apply_decay(self, seconds: float) -> None:
        self.hunger      = max(0.0, self.hunger      - HUNGER_DECAY      * seconds)
        self.cleanliness = max(0.0, self.cleanliness - CLEANLINESS_DECAY * seconds)
        self.energy      = max(0.0, self.energy      - ENERGY_DECAY      * seconds)
        # 心情：由三项属性亏缺共同决定，最快不超过能量速率
        shortage = (
            max(0.0, 50 - self.energy)      / 50 * 0.4
          + max(0.0, 50 - self.hunger)      / 50 * 0.4
          + max(0.0, 50 - self.cleanliness) / 50 * 0.2
        )
        mood_factor = 0.1 + shortage * 0.9   # 0.1 基础衰减 → 最高 1.0
        self.mood = max(0.0, self.mood - ENERGY_DECAY * mood_factor * seconds)
        self.refresh_derived_status_tags()

    def apply_sleep_recovery(self, seconds: float) -> None:
        """睡眠时：能量快速恢复，饥饿/清洁仍正常衰减，心情缓慢恢复。"""
        self.hunger      = max(0.0,   self.hunger      - HUNGER_DECAY          * seconds)
        self.cleanliness = max(0.0,   self.cleanliness - CLEANLINESS_DECAY     * seconds)
        self.energy      = min(100.0, self.energy      + ENERGY_SLEEP_RECOVERY * seconds)
        self.mood        = min(100.0, self.mood        + HUNGER_DECAY * 0.3    * seconds)
        self.refresh_derived_status_tags()

    def update_mood(self) -> None:
        shortage = (
            max(0.0, 30 - self.hunger) / 30 * 18
            + max(0.0, 30 - self.cleanliness) / 30 * 12
            + max(0.0, 30 - self.energy) / 30 * 16
        )
        target = max(0.0, min(100.0, 80.0 - shortage))
        if self.mood > target:
            # Let event-driven positive feedback linger briefly instead of
            # snapping straight down to the low-energy mood ceiling.
            self.mood = max(target, self.mood - 4.0)
        self.refresh_derived_status_tags()

    def check_thresholds(self) -> list:
        """返回当前需要强制触发的阈值事件列表（按优先级排序）。"""
        events = []
        if self.hunger <= HUNGER_FORCE_THRESHOLD:
            events.append(ThresholdEvent.FORCE_HUNGRY)
        if self.energy <= ENERGY_FORCE_THRESHOLD:
            events.append(ThresholdEvent.FORCE_SLEEP)
        elif self.energy <= ENERGY_DROWSY_THRESHOLD:
            events.append(ThresholdEvent.DROWSY)
        return events

    def persist(self) -> None:
        self._db.update_pet_state({
            "hunger": self.hunger, "cleanliness": self.cleanliness,
            "mood": self.mood, "energy": self.energy,
            "exp": self.exp, "intimacy": self.intimacy,
            "growth_stage": self.growth_stage,
            "derived_status_tags": json.dumps(self.derived_status_tags, ensure_ascii=False),
            "last_active_at": datetime.now().isoformat()
        }, asset_id=self.asset_id)

    def to_context_dict(self) -> dict:
        return {
            "hunger": round(self.hunger, 1), "cleanliness": round(self.cleanliness, 1),
            "mood": round(self.mood, 1), "energy": round(self.energy, 1),
            "exp": round(self.exp, 1), "intimacy": round(self.intimacy, 1),
            "level": self.level, "growth_stage": self.growth_stage,
            "pet_id": self.asset_id,
            "pet_profile": CURRENT_PET_PROFILE,
            "pet_label": CURRENT_PET_LABEL,
            "derived_status_tags": list(self.derived_status_tags),
            "personality": {
                "extrovert": self.personality_extrovert,
                "obedient":  self.personality_obedient,
                "curious":   self.personality_curious,
            }
        }

    @property
    def level(self) -> int:
        if self.exp >= 600:
            return 10
        return min(10, int(self.exp // 75) + 1)

    def apply_effect_phase(self, phase_values: dict | None) -> None:
        if not phase_values:
            return
        for attr in ("hunger", "cleanliness", "energy", "mood"):
            if attr not in phase_values:
                continue
            old = getattr(self, attr)
            setattr(self, attr, max(0.0, min(100.0, old + float(phase_values.get(attr, 0) or 0))))
        self.exp = max(0.0, self.exp + float(phase_values.get("exp", 0) or 0))
        self.intimacy = max(0.0, self.intimacy + float(phase_values.get("intimacy", 0) or 0))
        self.refresh_derived_status_tags(extra_tags=[])
        self.refresh_growth_stage()

    def apply_effect_profile(self, effect_profile: dict | None, phase: str) -> None:
        if not effect_profile:
            return
        settlement = effect_profile.get("settlement") or {}
        self.apply_effect_phase(settlement.get(phase))
        if phase == "on_finish":
            self.refresh_derived_status_tags(extra_tags=effect_profile.get("derived_tags_on_apply") or [])
        else:
            self.refresh_derived_status_tags()

    def refresh_growth_stage(self) -> None:
        if self.level >= 7 or self.intimacy >= 300:
            self.growth_stage = "bonded"
        elif self.level >= 4 or self.intimacy >= 100:
            self.growth_stage = "familiar"
        else:
            self.growth_stage = "newborn"

    def refresh_derived_status_tags(self, extra_tags: list[str] | None = None) -> None:
        tags: list[str] = []
        if self.hunger >= 80:
            tags.append("well_fed")
        if self.hunger <= 30:
            tags.append("hungry")
        if self.energy <= 20:
            tags.append("sleepy")
        if self.cleanliness <= 30:
            tags.append("dirty")
        if self.mood <= 20:
            tags.append("low_spirited")
        for tag in extra_tags or []:
            text = str(tag or "").strip().lower().replace(" ", "_").replace("-", "_")
            if text and text not in tags:
                tags.append(text)
        self.derived_status_tags = tags

    @staticmethod
    def _parse_tags(raw) -> list[str]:
        if isinstance(raw, list):
            return [str(tag) for tag in raw if str(tag or "").strip()]
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = []
            if isinstance(parsed, list):
                return [str(tag) for tag in parsed if str(tag or "").strip()]
        return []
