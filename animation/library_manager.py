import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from animation.effect_profiles import (
    build_effect_profile,
    infer_behavior_family,
    normalize_effect_profile,
    summarize_effect_profile,
)
from animation.tag_matcher import TagMatcher


@dataclass
class AnimationMatch:
    animation_id: str
    file_path: str
    score: float
    loop: bool
    fps: int
    source: str
    tags: list[str]
    effect_profile: dict | None = None
    behavior_type: str = ""
    behavior_family: str = ""
    reuse_scope: str = "any"


class LibraryManager:
    def __init__(self, index_path: str):
        self._path = Path(index_path)
        self._pet_id = self._manifest_slug("asset_id", fallback=self._normalize_tag(self._path.parent.name) or "pet", aliases=("pet_id",))
        self._pet_profile = self._manifest_slug("prompt_profile", fallback=self._pet_id, aliases=("profile_id", "pet_profile"))
        self._data = json.loads(self._path.read_text(encoding="utf-8"))
        self._matcher = TagMatcher(self._data.get("tag_synonyms", {}))

    @property
    def animations(self) -> list[dict]:
        return [a for a in self._data["animations"] if not a.get("blocked", False)]

    def get_by_id(self, animation_id: str) -> Optional[AnimationMatch]:
        index = self._find_animation_index(animation_id)
        if index is None:
            return None
        anim = self._data["animations"][index]
        return self._to_match(anim, 1.0)

    def find(self, tags: list[str], threshold: float = 0.7) -> Optional[AnimationMatch]:
        best = self._matcher.find_best(tags, self.animations)
        if best is None:
            return None
        score = self._matcher.score(tags, best.get("tags", []))
        return self._to_match(best, score) if score >= threshold else None

    def find_or_fallback(self, tags: list[str]) -> Optional[AnimationMatch]:
        best = self._matcher.find_best(tags, self.animations)
        if best is None:
            return None
        return self._to_match(best, self._matcher.score(tags, best.get("tags", [])))

    def find_matching(
        self,
        tags: list[str],
        required_tags: list[str] | None = None,
        excluded_tags: list[str] | None = None,
        threshold: float = 0.1,
    ) -> Optional[AnimationMatch]:
        required = {self._normalize_tag(tag) for tag in (required_tags or []) if self._normalize_tag(tag)}
        excluded = {self._normalize_tag(tag) for tag in (excluded_tags or []) if self._normalize_tag(tag)}
        candidates: list[dict] = []
        for anim in self.animations:
            normalized_anim_tags = {self._normalize_tag(tag) for tag in anim.get("tags", []) if self._normalize_tag(tag)}
            if required and not required.issubset(normalized_anim_tags):
                continue
            if excluded and normalized_anim_tags & excluded:
                continue
            candidates.append(anim)

        if not candidates:
            return None

        best = self._matcher.find_best(tags, candidates)
        if best is None:
            return None
        score = self._matcher.score(tags, best.get("tags", []))
        return self._to_match(best, score) if score >= threshold else None

    def find_generated_equivalent(
        self,
        behavior_type: str | None,
        tags: list[str] | None,
    ) -> Optional[AnimationMatch]:
        target_key = self._generated_semantic_key_for_request(behavior_type, tags)
        if not target_key:
            return None

        for anim in self.animations:
            if str(anim.get("source", "")).strip().lower() != "generated":
                continue
            if self._generated_semantic_key(anim) != target_key:
                continue
            return self._to_match(anim, 1.0)
        return None

    def add(self, animation: dict) -> dict:
        record = dict(animation)
        self._normalize_animation_record(record, assign_discovered=False)

        existing_index = self._find_animation_index(record["id"])
        if existing_index is None:
            self._data["animations"].append(record)
        else:
            self._data["animations"][existing_index] = record

        self._save()
        return dict(record)

    def mark_discovered(self, animation_id: str, discovered_at: str | None = None) -> bool:
        for anim in self._data["animations"]:
            if anim["id"] != animation_id:
                continue

            changed = False
            if not anim.get("discovered_at"):
                anim["discovered_at"] = discovered_at or self._now_iso()
                changed = True
            if "rarity" not in anim:
                anim["rarity"] = self._infer_rarity(anim)
                changed = True
            if "behavior_type" not in anim:
                anim["behavior_type"] = self._infer_behavior_type(anim)
                changed = True

            if changed:
                self._save()
            return changed
        return False

    def set_blocked(self, animation_id: str, blocked: bool) -> None:
        for a in self._data["animations"]:
            if a["id"] == animation_id:
                a["blocked"] = blocked
                break
        self._save()

    def set_rating(self, animation_id: str, rating: int) -> None:
        for a in self._data["animations"]:
            if a["id"] == animation_id:
                a["rating"] = rating
                break
        self._save()

    def update_tags(self, animation_id: str, tags: list[str]) -> None:
        for a in self._data["animations"]:
            if a["id"] == animation_id:
                a["tags"] = tags
                if "behavior_type" not in a or not str(a.get("behavior_type") or "").strip():
                    a["behavior_type"] = self._infer_behavior_type(a)
                if str((a.get("effect_profile") or {}).get("effect_source") or "").strip().lower() != "manual_override":
                    a["effect_profile"] = build_effect_profile(a)
                break
        self._save()
        self._matcher = TagMatcher(self._data.get("tag_synonyms", {}))

    def all_tags(self) -> list[str]:
        tags: set[str] = set()
        for a in self.animations:
            tags.update(a.get("tags", []))
        return sorted(tags)

    def get_collection_entries(self, include_blocked: bool = False) -> list[dict]:
        raw_items = self._data.get("animations", [])
        entries: list[dict] = []
        for anim in raw_items:
            if anim.get("blocked", False) and not include_blocked:
                continue

            rarity = str(anim.get("rarity") or self._infer_rarity(anim))
            behavior_type = self._infer_behavior_type(anim)
            discovered_at = anim.get("discovered_at")
            discovered = self._is_discovered(anim)
            effect_profile = self._runtime_effect_profile(anim)
            entries.append(
                {
                    "id": anim["id"],
                    "display_name": self._build_display_name(anim),
                    "file": anim.get("file", ""),
                    "tags": list(anim.get("tags", [])),
                    "loop": bool(anim.get("loop", True)),
                    "fps": int(anim.get("fps", 12)),
                    "source": anim.get("source", "user_provided"),
                    "pet_id": self._effective_pet_id(anim),
                    "pet_profile": self._effective_pet_profile(anim),
                    "rarity": rarity,
                    "rating": int(anim.get("rating", 0) or 0),
                    "blocked": bool(anim.get("blocked", False)),
                    "discovered": discovered,
                    "discovered_at": discovered_at,
                    "behavior_type": behavior_type,
                    "behavior_family": self._effect_behavior_family(anim, effect_profile),
                    "reuse_scope": self._reuse_scope(anim),
                    "effect_profile": effect_profile,
                    "effect_summary": self._build_effect_summary(anim, effect_profile),
                }
            )

        return sorted(
            entries,
            key=lambda item: (
                0 if item["rarity"] == "common" else 1,
                item["display_name"],
            ),
        )

    def get_collection_stats(self, include_blocked: bool = False) -> dict:
        entries = self.get_collection_entries(include_blocked=include_blocked)
        groups: dict[str, dict] = {}
        common_total = 0
        common_discovered = 0
        rare_total = 0
        rare_discovered = 0

        for entry in entries:
            behavior_type = entry["behavior_type"]
            group = groups.setdefault(
                behavior_type,
                {
                    "behavior_type": behavior_type,
                    "title": behavior_type.replace("_", " "),
                    "discovered": 0,
                    "total": 0,
                    "rare_discovered": 0,
                    "rare_total": 0,
                    "entries": [],
                },
            )
            group["entries"].append(entry)
            group["total"] += 1
            if entry["discovered"]:
                group["discovered"] += 1
            if entry["rarity"] == "common":
                common_total += 1
                if entry["discovered"]:
                    common_discovered += 1
            if entry["rarity"] == "rare":
                group["rare_total"] += 1
                rare_total += 1
                if entry["discovered"]:
                    group["rare_discovered"] += 1
                    rare_discovered += 1

        discovered_total = sum(1 for entry in entries if entry["discovered"])
        total = len(entries)
        progress = 1.0 if common_total == 0 else common_discovered / common_total
        ordered_groups = sorted(groups.values(), key=lambda item: item["title"])
        return {
            "overall": {
                "discovered": discovered_total,
                "total": total,
                "progress": progress,
                "common_discovered": common_discovered,
                "common_total": common_total,
                "rare_discovered": rare_discovered,
                "rare_total": rare_total,
            },
            "groups": ordered_groups,
        }

    def sync_generated_assets(self, generated_dir: str | Path) -> list[dict]:
        base = Path(generated_dir)
        if not base.exists():
            return []
        generated_pet_id = self._normalize_tag(base.name) or self._pet_id
        generated_pet_profile = self._pet_profile

        indexed_files = {
            self._normalize_path(anim.get("file", ""))
            for anim in self._data.get("animations", [])
        }
        existing_keys: set[str] = set()
        for anim in self._data.get("animations", []):
            anim_id = self._sanitize_id(anim.get("id", ""))
            behavior = self._sanitize_id(anim.get("behavior_type", ""))
            file_stem = self._sanitize_id(Path(str(anim.get("file", ""))).stem)
            pet_id = self._effective_pet_id(anim)
            for key in {
                anim_id,
                behavior,
                self._infer_generated_slug(file_stem, pet_id=pet_id),
                file_stem,
            }:
                if key:
                    existing_keys.add(key)

        created: list[dict] = []
        for path in sorted(base.glob("*")):
            if not path.is_file():
                continue
            normalized = self._normalize_path(path)
            if normalized in indexed_files:
                continue

            stem = path.stem.lower()
            slug = self._infer_generated_slug(stem, pet_id=generated_pet_id)
            if self._should_skip_generated_asset(stem, slug):
                continue
            if self._sanitize_id(slug) in existing_keys or self._sanitize_id(stem) in existing_keys:
                continue
            try:
                stored_path = path.resolve().relative_to(Path.cwd().resolve()).as_posix()
            except ValueError:
                stored_path = path.as_posix()
            record = {
                "id": self._sanitize_id(stem),
                "file": stored_path,
                "tags": self._infer_tags_from_slug(slug),
                "loop": False,
                "fps": 16,
                "source": "generated",
                "blocked": False,
                "rating": 0,
                "rarity": "rare",
                "behavior_type": self._infer_generated_behavior_type(slug),
                "pet_id": generated_pet_id,
                "pet_profile": generated_pet_profile,
                "discovered_at": self._now_iso(),
            }
            saved = self.add(record)
            indexed_files.add(normalized)
            existing_keys.update({self._sanitize_id(stem), self._sanitize_id(slug)})
            created.append(saved)
        return created

    def _find_animation_index(self, animation_id: str) -> int | None:
        for index, anim in enumerate(self._data.get("animations", [])):
            if anim.get("id") == animation_id:
                return index
        return None

    def _normalize_animation_record(self, animation: dict, assign_discovered: bool) -> None:
        animation.setdefault("blocked", False)
        animation.setdefault("rating", 0)
        animation.setdefault("loop", True)
        animation.setdefault("fps", 12)
        animation.setdefault("source", "user_provided")
        animation.setdefault("tags", [])
        animation["pet_id"] = self._effective_pet_id(animation)
        animation["pet_profile"] = self._effective_pet_profile(animation)
        animation["rarity"] = str(animation.get("rarity") or self._infer_rarity(animation))
        animation["behavior_type"] = str(animation.get("behavior_type") or self._infer_behavior_type(animation))
        animation["reuse_scope"] = str(animation.get("reuse_scope") or self._infer_reuse_scope(animation))
        if animation.get("effect_profile"):
            animation["effect_profile"] = normalize_effect_profile(animation)
        else:
            animation["effect_profile"] = build_effect_profile(animation)
        if assign_discovered and not animation.get("discovered_at"):
            animation["discovered_at"] = self._now_iso()

    def _is_discovered(self, animation: dict) -> bool:
        if animation.get("discovered_at"):
            return True
        source = str(animation.get("source", "user_provided")).strip().lower()
        return source != "generated"

    @staticmethod
    def _build_display_name(animation: dict) -> str:
        behavior_type = str(animation.get("behavior_type") or "").strip()
        if behavior_type:
            return behavior_type.replace("_", " ").title()
        for tag in animation.get("tags", []):
            text = str(tag or "").strip()
            if text:
                return text.replace("_", " ").title()
        return str(animation.get("id") or "Unknown").replace("_", " ").title()

    def _effective_pet_id(self, animation: dict) -> str:
        explicit = self._normalize_tag(animation.get("pet_id", ""))
        if explicit:
            return explicit

        file_path = self._normalize_path(animation.get("file", ""))
        parts = [part for part in file_path.split("/") if part]
        for marker in ("generated", "base"):
            if marker not in parts:
                continue
            marker_index = parts.index(marker)
            if marker_index + 1 >= len(parts):
                continue
            candidate = self._normalize_tag(parts[marker_index + 1])
            if candidate and "." not in candidate:
                return candidate
        generated_prompt = animation.get("generated_prompt") or {}
        if isinstance(generated_prompt, dict):
            prompt_request = generated_prompt.get("prompt_request") or {}
            candidate = self._normalize_tag(prompt_request.get("pet_id", ""))
            if candidate:
                return candidate
        return self._pet_id

    def _effective_pet_profile(self, animation: dict) -> str:
        explicit = self._normalize_tag(animation.get("pet_profile", ""))
        if explicit:
            return explicit

        generated_prompt = animation.get("generated_prompt") or {}
        if isinstance(generated_prompt, dict):
            prompt_request = generated_prompt.get("prompt_request") or {}
            candidate = self._normalize_tag(prompt_request.get("pet_profile", ""))
            if candidate:
                return candidate
        return self._pet_profile

    @staticmethod
    def _infer_rarity(animation: dict) -> str:
        source = str(animation.get("source", "user_provided")).strip().lower()
        return "rare" if source == "generated" else "common"

    @staticmethod
    def _infer_behavior_type(animation: dict) -> str:
        explicit = str(animation.get("behavior_type") or "").strip()
        if explicit:
            return explicit.lower().replace(" ", "_").replace("-", "_")

        generated_prompt = animation.get("generated_prompt") or {}
        if isinstance(generated_prompt, dict):
            prompt_request = generated_prompt.get("prompt_request") or {}
            behavior_type = str(prompt_request.get("behavior_type") or "").strip()
            if behavior_type:
                return behavior_type.lower().replace(" ", "_").replace("-", "_")

        tags = animation.get("tags") or []
        for tag in tags:
            text = str(tag or "").strip()
            if text:
                return text.lower().replace(" ", "_").replace("-", "_")
        return "misc"

    @staticmethod
    def _effect_behavior_family(animation: dict, effect_profile: dict | None = None) -> str:
        profile = effect_profile if isinstance(effect_profile, dict) else animation.get("effect_profile")
        if isinstance(profile, dict):
            family = str(profile.get("behavior_family") or "").strip()
            if family:
                return family
        return infer_behavior_family(animation)

    @classmethod
    def _build_effect_summary(cls, animation: dict, effect_profile: dict | None = None) -> dict:
        profile = effect_profile if isinstance(effect_profile, dict) else cls._runtime_effect_profile(animation)
        summary = summarize_effect_profile(profile)
        return {
            "stats": summary["stats"],
            "growth": summary["growth"],
            "source": str(profile.get("effect_source") or "rule"),
        }

    @classmethod
    def _reuse_scope(cls, animation: dict) -> str:
        return str(animation.get("reuse_scope") or cls._infer_reuse_scope(animation))

    @staticmethod
    def _normalize_tag(tag: str) -> str:
        return str(tag or "").strip().lower().replace(" ", "_").replace("-", "_")

    @staticmethod
    def _normalize_path(path: str | Path) -> str:
        return str(path or "").replace("\\", "/").strip().lower()

    @staticmethod
    def _sanitize_id(text: str) -> str:
        safe = re.sub(r"[^a-z0-9_-]+", "_", str(text or "").strip().lower())
        safe = re.sub(r"_+", "_", safe).strip("_")
        return safe or f"generated_{int(datetime.now().timestamp())}"

    @staticmethod
    def _infer_generated_slug(stem: str, pet_id: str | None = None) -> str:
        parts = [part for part in stem.split("_") if part]
        prefix_parts = [
            part
            for part in LibraryManager._normalize_tag(pet_id or "").split("_")
            if part
        ]
        if prefix_parts and parts[:len(prefix_parts)] == prefix_parts:
            parts = parts[len(prefix_parts):]
        return "_".join(parts) or stem

    def _manifest_slug(self, primary_key: str, fallback: str, aliases: tuple[str, ...] = ()) -> str:
        manifest_path = self._path.parent / "pet_manifest.json"
        if not manifest_path.exists():
            return fallback
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return fallback
        for key in (primary_key, *aliases):
            candidate = self._normalize_tag(payload.get(key, ""))
            if candidate:
                return candidate
        return fallback

    @staticmethod
    def _infer_tags_from_slug(slug: str) -> list[str]:
        parts = [part for part in slug.split("_") if part]
        tags: list[str] = []
        seen: set[str] = set()
        for candidate in [slug, *parts]:
            if candidate and candidate not in seen:
                seen.add(candidate)
                tags.append(candidate)
        return tags

    @staticmethod
    def _infer_generated_behavior_type(slug: str) -> str:
        parts = [part for part in slug.split("_") if part]
        compounds = {
            ("beg", "food"): "beg_food",
            ("show", "off"): "show_off",
            ("self", "care"): "self_care",
            ("play", "ball"): "play_ball",
            ("paw", "bounce"): "paw_bounce",
            ("sniff", "floor"): "sniff_floor",
            ("peek", "window"): "peek_window",
            ("inspect", "corner"): "inspect_corner",
            ("chase", "tail"): "chase_tail",
            ("look", "around"): "look_around",
        }
        if len(parts) >= 2 and tuple(parts[:2]) in compounds:
            return compounds[tuple(parts[:2])]
        if "self" in parts and "care" in parts:
            return "self_care"
        if "show" in parts and "off" in parts:
            return "show_off"
        if parts:
            return parts[0]
        return slug or "misc"

    @staticmethod
    def _should_skip_generated_asset(stem: str, slug: str) -> bool:
        normalized_stem = LibraryManager._sanitize_id(stem)
        normalized_slug = LibraryManager._sanitize_id(slug)
        blocked = {
            "smoke_test",
            "demo",
            "tmp",
        }
        return (
            normalized_stem in blocked
            or normalized_slug in blocked
            or normalized_stem.endswith("_smoke_test")
            or normalized_slug.endswith("_smoke_test")
        )

    @classmethod
    def _infer_reuse_scope(cls, animation: dict) -> str:
        explicit = str(animation.get("reuse_scope") or "").strip().lower()
        if explicit:
            return explicit

        source = str(animation.get("source", "user_provided")).strip().lower()
        behavior_type = cls._infer_behavior_type(animation)
        tags = {
            cls._normalize_tag(tag)
            for tag in animation.get("tags", []) or []
            if cls._normalize_tag(tag)
        }
        if source == "generated":
            return "ai_exact_only"
        if behavior_type in {"force_hungry", "force_sleep", "drowsy_idle"}:
            return "threshold_only"
        if tags & {"starving", "hungry", "beg_food", "sleep", "sleeping", "exhausted", "drowsy_idle", "sleepy", "yawning"}:
            return "threshold_only"
        if behavior_type in {"idle_neutral", "idle_normal"} or tags & {"idle", "neutral", "calm"}:
            return "idle_only"
        if behavior_type in {"play_ball"}:
            return "ai_exact_only"
        return "direct_only"

    @classmethod
    def _generated_semantic_key_for_request(
        cls,
        behavior_type: str | None,
        tags: list[str] | None,
    ) -> str:
        normalized_behavior = cls._normalize_tag(behavior_type or "")
        layered_tags: list[str] = []
        seen: set[str] = set()

        if normalized_behavior:
            seen.add(normalized_behavior)
            layered_tags.append(normalized_behavior)

        for raw in tags or []:
            normalized = cls._normalize_tag(raw)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            layered_tags.append(normalized)

        if not layered_tags:
            return ""
        if not normalized_behavior:
            normalized_behavior = layered_tags[0]
        return "|".join([normalized_behavior, *layered_tags])

    @classmethod
    def _generated_semantic_key(cls, animation: dict) -> str:
        return cls._generated_semantic_key_for_request(
            cls._infer_behavior_type(animation),
            animation.get("tags") or [],
        )

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _save(self) -> None:
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _to_match(anim: dict, score: float) -> AnimationMatch:
        effect_profile = LibraryManager._runtime_effect_profile(anim)
        behavior_family = str(effect_profile.get("behavior_family") or "")
        return AnimationMatch(
            animation_id=anim["id"],
            file_path=anim["file"],
            score=score,
            loop=anim.get("loop", True),
            fps=anim.get("fps", 12),
            source=anim.get("source", "user_provided"),
            tags=list(anim.get("tags", [])),
            effect_profile=effect_profile,
            behavior_type=str(anim.get("behavior_type") or ""),
            behavior_family=behavior_family,
            reuse_scope=LibraryManager._reuse_scope(anim),
        )
    @staticmethod
    def _runtime_effect_profile(animation: dict) -> dict:
        profile = animation.get("effect_profile")
        if isinstance(profile, dict):
            return normalize_effect_profile(animation)
        return build_effect_profile(animation)
