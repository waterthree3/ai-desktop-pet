"""
统一事件请求数据结构 — 三源两层架构的核心类型定义。

三个事件源（USER / THRESHOLD / AI）构造 EventRequest，
交给 EventArbiter 做优先级 x EventKind 二维仲裁。
"""
from enum import Enum, IntEnum
from dataclasses import dataclass, field


class EventSource(Enum):
    USER = "user"
    THRESHOLD = "threshold"
    AI = "ai"


class EventPriority(IntEnum):
    DEFAULT = 0
    AI_DECISION = 1
    THRESHOLD_WARNING = 2
    THRESHOLD_CRITICAL = 3
    USER_INTERACTION = 4
    USER_PHYSICAL = 5


class EventKind(Enum):
    STATE = "state"
    ACTION = "action"
    NOTIFY = "notify"


class MovementIntent(Enum):
    STAY = "stay"
    WANDER = "wander"
    CARRIED = "carried"
    SLEEP = "sleep"
    RETURN_DEFAULT = "default"


@dataclass
class EventRequest:
    source: EventSource
    priority: EventPriority
    kind: EventKind
    event_type: str

    # Layer A
    movement: MovementIntent = MovementIntent.STAY
    target_pos: object = None  # QPoint | None — 用 object 避免顶层 import Qt

    # Layer B
    anim_id: str | None = None
    anim_tags: list[str] = field(default_factory=list)
    anim_loop: bool = False
    anim_fallback: str | None = None

    # 属性变化
    attr_deltas: dict = field(default_factory=dict)
    recovery_mode: str = "progressive"  # "immediate" | "progressive" | "none"
    recovery_rates: dict = field(default_factory=dict)

    # 展示
    dialogue: str = ""
    emotion: dict = field(default_factory=dict)

    # AI 专属
    generate_if_missing: bool = False
    action_desc: str = ""
    behavior_type: str = ""
    prompt_request: dict = field(default_factory=dict)

    # 睡眠唤醒标记
    wakes_from_sleep: bool = False
    blocked_while_sleep: bool = False
    blocked_low_energy: bool = False


@dataclass
class ArbiterResult:
    accepted: bool
    reason: str = ""
    downgraded: bool = False
