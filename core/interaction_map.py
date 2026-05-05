from enum import Enum, auto
from typing import Optional


class InteractionEvent(Enum):
    CLICK         = auto()   # 单击身体
    DOUBLE_CLICK  = auto()   # 双击身体
    FEED          = auto()   # 喂食（右键菜单）
    PLAY          = auto()   # 玩耍（右键菜单）
    BATH          = auto()   # 洗澡（右键菜单）
    STROKE        = auto()   # 抚摸（右键菜单）
    WALK_MODE     = auto()   # 散步（右键菜单）
    FORCE_HUNGRY  = auto()   # 阈值：极度饥饿（也用于 hunger≤10 时手动喂食的强制动画）
    FORCE_SLEEP   = auto()   # 阈值：极度疲劳
    DROWSY        = auto()   # 阈值：低能量
    FORCE_DIRTY   = auto()   # 临界：cleanliness≤10 时手动洗澡触发的强制动画
    FORCE_SAD     = auto()   # 临界：mood≤10 时手动抚摸/玩耍触发的强制动画


# 每条记录：
#   tags        → 用于动画库匹配的 tag 列表
#   loop        → GIF 是否循环播放（False = play_once）
#   dialogue    → 可选对话文本（空字符串 = 不显示）
#   cooldown_s  → 冷却时间秒数（0 = 无冷却）
#
# 属性变化统一由匹配到的动画的 effect_profile 提供，此处不再硬编码 delta。

_MAP: dict = {
    InteractionEvent.CLICK: {
        "tags": ["pet_stroke", "happy", "wagging"],
        "loop": False,
        "dialogue": "嗯哼～", "cooldown_s": 0
    },
    InteractionEvent.DOUBLE_CLICK: {
        "tags": ["excited_spin", "excited", "jumping"],
        "loop": False,
        "dialogue": "汪汪！", "cooldown_s": 0
    },
    InteractionEvent.FEED: {
        "tags": ["eat", "eating", "fed"],
        "loop": False,
        "dialogue": "好好吃！", "cooldown_s": 0,
        "wakes_from_sleep": True   # 吃饭能唤醒睡眠
    },
    InteractionEvent.PLAY: {
        "tags": ["play_ball", "playful", "excited"],
        "loop": False,
        "dialogue": "太好玩了！", "cooldown_s": 600,
        "blocked_while_sleep": True  # 睡觉时禁止玩耍
    },
    InteractionEvent.BATH: {
        "tags": ["bath", "cleanliness", "shaking"],
        "loop": False,
        "dialogue": "呜……湿了……", "cooldown_s": 3600
    },
    InteractionEvent.STROKE: {
        "tags": ["pet_stroke", "happy", "calm"],
        "loop": False,
        "dialogue": "舒服～", "cooldown_s": 0
    },
    InteractionEvent.WALK_MODE: {
        "tags": ["walking", "wander", "explore"],
        "loop": True,
        "dialogue": "出去玩咯！", "cooldown_s": 0,
        "blocked_low_energy": True  # BUG-17: 能量不足时禁止散步
    },
    InteractionEvent.FORCE_HUNGRY: {
        "tags": ["starving", "hungry", "beg_food"],
        "loop": False,
        "dialogue": "好……好饿……", "cooldown_s": 0
    },
    InteractionEvent.FORCE_SLEEP: {
        "tags": ["sleep", "sleeping", "exhausted"],
        "loop": True,
        "dialogue": "zzz…", "cooldown_s": 0
    },
    InteractionEvent.DROWSY: {
        "tags": ["drowsy_idle", "sleepy", "yawning"],
        "loop": True,
        "dialogue": "好困哦…", "cooldown_s": 0
    },
    InteractionEvent.FORCE_DIRTY: {
        # cleanliness ≤ 10 时手动洗澡触发，比普通洗澡更夸张
        "tags": ["filthy", "dirty_shake", "miserable"],
        "loop": False,
        "dialogue": "太脏了！终于洗了……", "cooldown_s": 3600
    },
    InteractionEvent.FORCE_SAD: {
        # mood ≤ 10 时手动抚摸/玩耍触发，比普通抚摸更治愈
        "tags": ["depressed", "comforting", "sad_pet"],
        "loop": False,
        "dialogue": "呜……谢谢你……", "cooldown_s": 0
    },
}


class InteractionMap:
    def get(self, event) -> Optional[dict]:
        return _MAP.get(event)
