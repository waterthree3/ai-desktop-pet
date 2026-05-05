# AI Desktop Pet (AI 桌宠)

一个 AI 驱动的 Windows 桌面宠物。宠物拥有自主行为、情感状态和对话能力，不只是被动的动画播放器，而是一个有"性格"和"记忆"的桌面伙伴。

## 功能特性

### 自主行为
- 宠物在无人操作时会自主产生行为（散步、打瞌睡、玩耍等）
- 行为由本地 LLM（Qwen2.5-1.5B）推理决定，无需联网
- 没有 LLM 模型时自动降级为固定行为模式，不影响基础体验

### 属性系统
- **能量**：随时间消耗，过低会困倦/强制入睡
- **饥饿**：随时间增长，过饿会主动讨食
- **清洁**：逐渐变脏，过低会撒娇求洗澡
- **心情**：受互动影响，低落时会求安慰

### 交互方式
| 操作 | 效果 |
|------|------|
| 单击 | 抚摸 |
| 双击 | 兴奋反应 |
| 拖拽 | 抱起宠物 |
| 右键菜单 | 喂食、洗澡、玩耍、散步等 |

### 对话
- 右键菜单点击"聊天"可以和宠物自然语言对话
- 宠物会根据当前心情和状态回复（需要 LLM 模型）

### 动画生成（可选）
- 接入本地 ComfyUI 后，AI 发现新行为时可自动生成对应动画
- 不使用 ComfyUI 也完全正常运行

### 多角色支持
- 内置两个角色包：史莱姆酱（默认）、小狗
- 支持自定义角色（见下方"添加自定义角色"）

## 快速开始

### 环境要求
- Windows 10/11
- Python >= 3.10

### 安装

```bash
pip install -r requirements.txt
```

### 启动

```bash
python run.py
```

或双击 `start.bat`。

首次启动会自动检查依赖和资源，给出明确提示。

### 可选：安装 LLM 模型

下载 [Qwen2.5-1.5B-Q4 GGUF](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF) 模型文件，放到 `models/qwen2.5-1.5b-q4.gguf`。

没有模型文件时，程序仍可正常运行，只是自主行为和对话功能不可用。

## 切换角色

### 方式一：托盘菜单
运行后右下角托盘图标 → 切换角色

### 方式二：环境变量
```bash
set DESKTOP_PET_ASSET_ID=dog
python run.py
```

可用角色：`slime_chan`（默认）、`dog`

## 添加自定义角色

提供两条路径，最终效果完全一致：

### 路径一：自行准备动作视频

适合手上已有动画素材的场景。

**1. 准备素材文件夹：**

```
my_character/
  reference.png       # 角色全身参考图
  idle.mp4            # 待机动画（必需）
  walk.mp4            # 行走（推荐）
  sleep.mp4           # 睡觉（推荐）
  carried.mp4         # 被抱起（推荐）
```

**2. 运行导入命令：**

```bash
python scripts/quick_import_character.py \
  --source-dir D:\my_character \
  --asset-id my_character \
  --label "我的角色" \
  --set-current
```

**3. 启动即可体验。**

### 路径二：用 ComfyUI 从参考图生成动作

适合只有一张角色图的场景。需要本地运行 ComfyUI。

**1. 生成角色包骨架：**

```bash
python scripts/scaffold_character_package.py \
  --asset-id my_character \
  --label "我的角色" \
  --subject-kind "cute cartoon character"
```

**2. 把参考图放到** `assets/base/my_character/my_character.png`

**3. 逐个生成动作视频：**

```bash
python scripts/generate_character_motion.py --asset-id my_character --slot IDLE_NEUTRAL
python scripts/generate_character_motion.py --asset-id my_character --slot WALK
python scripts/generate_character_motion.py --asset-id my_character --slot FORCE_SLEEP
python scripts/generate_character_motion.py --asset-id my_character --slot CARRIED
```

**4. 启动程序即可。**

### 动作槽位表

| 文件名 | 动作 | 是否必需 |
|--------|------|----------|
| `idle.mp4` | 待机 | 必需 |
| `walk.mp4` | 行走 | 推荐 |
| `sleep.mp4` | 睡觉 | 推荐 |
| `carried.mp4` | 被抱起 | 推荐 |
| `stroke.mp4` | 抚摸 | 可选 |
| `play.mp4` | 玩耍 | 可选 |
| `eat.mp4` | 喂食 | 可选 |
| `bath.mp4` | 洗澡 | 可选 |
| `double_click.mp4` | 双击反应 | 可选 |
| `drowsy.mp4` | 困倦 | 可选 |
| `hungry.mp4` | 饥饿提醒 | 可选 |
| `dirty.mp4` | 嫌脏 | 可选 |
| `sad.mp4` | 难过 | 可选 |

缺少的动作不会报错，对应功能会自动禁用。

### 视频制作建议

- 分辨率：512x768 或保持与参考图相同比例
- 背景：纯白或透明
- 构图：全身居中，四周留白
- 循环动画（idle/walk/sleep）：首尾帧尽量一致
- 支持格式：`.mp4` `.gif` `.mov` `.webm` `.avi`

## 项目结构

```
├── main.py              # 主程序入口
├── config.py            # 配置参数
├── run.py               # 带预检的启动脚本
├── requirements.txt     # 依赖
├── core/                # 核心逻辑（状态机、事件、属性）
├── ai/                  # AI 行为推理与对话
├── animation/           # 动画管理与匹配
├── ui/                  # 界面窗口与组件
├── data/                # 数据持久化
├── scripts/             # 角色导入/生成工具
├── assets/
│   ├── base/            # 角色包（动画+配置）
│   ├── generated/       # AI 生成的动画（运行时产生）
│   ├── templates/       # 角色导入模板
│   └── workflows/       # ComfyUI 工作流
└── models/              # LLM 模型文件（需自行下载）
```

## 许可证

MIT
