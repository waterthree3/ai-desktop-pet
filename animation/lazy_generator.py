from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from config import GENERATION_DAILY_LIMIT, GENERATION_COOLDOWN_MIN, CURRENT_PET_LABEL


class LazyGenerator:
    """
    动画懒生成器：优先用现有库，匹配不到时按需调用 I2V 生成新 GIF。
    内置每日次数上限 + 冷却时间限制，防止过度依赖生成。
    """

    def __init__(
        self,
        generated_dir: str,
        ref_image_path: str,
        daily_limit: int = GENERATION_DAILY_LIMIT,
        cooldown_minutes: int = GENERATION_COOLDOWN_MIN
    ):
        self._gen_dir         = Path(generated_dir)
        self._ref_image       = Path(ref_image_path)
        self._daily_limit     = daily_limit
        self._cooldown_min    = cooldown_minutes
        self._today_count     = 0
        self._today_date      = datetime.now().date()
        self._last_generated_at: datetime | None = None

    def is_allowed(self) -> bool:
        """检查当前是否允许生成（未超每日上限且不在冷却期）。"""
        today = datetime.now().date()
        if today != self._today_date:
            self._today_count = 0
            self._today_date  = today
        if self._today_count >= self._daily_limit:
            return False
        if self._last_generated_at is not None:
            elapsed = datetime.now() - self._last_generated_at
            if elapsed < timedelta(minutes=self._cooldown_min):
                return False
        return True

    def generate(self, tags: list[str]) -> Optional[str]:
        """
        生成一个新 GIF，返回文件路径；不允许或失败返回 None。
        """
        if not self.is_allowed():
            print(f"[LazyGenerator] 已达频率限制，跳过生成 tags={tags}")
            return None
        prompt = self._build_prompt(tags)
        gif_path = self._run_i2v(prompt, tags)
        if gif_path:
            self._today_count += 1
            self._last_generated_at = datetime.now()
        return gif_path

    def _build_prompt(self, tags: list[str]) -> str:
        tag_str = ", ".join(tags)
        return (
            f"a cute cartoon {CURRENT_PET_LABEL} {tag_str}, "
            "chibi style, white background, full body, simple design, "
            "smooth animation, no text"
        )

    def _run_i2v(self, prompt: str, tags: list[str]) -> Optional[str]:
        """
        调用 Wan2.1 I2V 生成 GIF。
        当前为存根实现——集成 Wan2.1 时替换此方法体。

        接入示例（Wan2.1 CLI，实际路径按部署调整）：
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_gif = self._gen_dir / f"gen_{timestamp}.gif"
            subprocess.run([
                "python", "wan2.1/generate.py",
                "--ref_image", str(self._ref_image),
                "--prompt", prompt,
                "--output", str(out_gif),
            ], check=True)
            return str(out_gif) if out_gif.exists() else None
        """
        print(f"[LazyGenerator] stub: would generate tags={tags}")
        return None
