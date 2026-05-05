from enum import Enum, auto


class AnimState(Enum):
    IDLE         = auto()  # 默认待机：玩闹模式关闭时，宠物停在原地
    FOLLOW_MOUSE = auto()  # 玩闹模式开启：追随鼠标，走路或在鼠标旁待机
    AUTONOMOUS   = auto()  # 自主行为（wander/sleep/beg 等，有自己的目标点）
    EVENT        = auto()  # 交互事件动画播放中（最高优先级，不可打断）
    STARTLED     = auto()  # 受惊（鼠标快速冲来）
    CARRIED      = auto()  # 被拖拽（跟着鼠标）
    SLEEP        = auto()  # 强制睡觉（energy < 5，持续直到 energy > 30）
