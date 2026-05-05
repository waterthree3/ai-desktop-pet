"""
桌宠启动脚本
=============
启动前自动检查依赖和资源，给出明确的错误提示。
"""

import sys
import json
from pathlib import Path

ROOT = Path(__file__).parent


def _ok(msg: str):  print(f"  [OK]  {msg}")
def _warn(msg: str): print(f"  [!]   {msg}")
def _fail(msg: str): print(f"  [ERR] {msg}")


def check_python():
    if sys.version_info < (3, 10):
        _fail(f"需要 Python >= 3.10，当前版本: {sys.version}")
        sys.exit(1)
    _ok(f"Python {sys.version.split()[0]}")


def check_packages():
    failed = []
    packages = {
        "PyQt6":                  "pip install PyQt6",
        "PyQt6.QtMultimedia":     "pip install PyQt6 PyQt6-Qt6 PyQt6-sip",
    }
    for pkg, install_hint in packages.items():
        try:
            __import__(pkg)
        except ImportError:
            _fail(f"缺少依赖包: {pkg}")
            _fail(f"  → {install_hint}")
            failed.append(pkg)
    if not failed:
        _ok("PyQt6 + QtMultimedia 已安装")
    return not failed


def check_dirs():
    from config import CURRENT_PET
    dirs = [
        ROOT / "assets" / "generated" / CURRENT_PET,
        ROOT / "assets" / "base" / CURRENT_PET,
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    _ok("资源目录已就绪")


def check_anim_index():
    from config import CURRENT_PET
    index_path = ROOT / "assets" / "base" / CURRENT_PET / "animation_index.json"
    if not index_path.exists():
        _fail("缺少 assets/animation_index.json")
        return False
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception as e:
        _fail(f"animation_index.json 格式错误: {e}")
        return False

    anims = data.get("animations", [])
    if not anims:
        _warn("animation_index.json 中没有动画条目 — 宠物将无动画可播")
        _warn("请按格式添加条目: {\"id\": \"xxx\", \"file\": \"assets/xxx.gif\", \"tags\": [...], \"loop\": false, \"fps\": 12, \"source\": \"user_provided\"}")
        return True

    # 检查文件是否存在
    missing = [a["file"] for a in anims if not (ROOT / a["file"]).exists()]
    if missing:
        _warn(f"以下动画文件不存在（{len(missing)} 个）:")
        for f in missing[:5]:
            _warn(f"  {f}")
        if len(missing) > 5:
            _warn(f"  ...以及另外 {len(missing)-5} 个")
    else:
        _ok(f"动画库已就绪，共 {len(anims)} 个动画")
    return True


def check_ref_image():
    from config import REF_IMAGE_PATH
    ref = REF_IMAGE_PATH
    if not ref.exists():
        _warn(f"缺少角色参考图: {ref}")
    else:
        _ok(f"角色参考图已就绪: {ref}")


def check_llm_model():
    model_path = ROOT / "models" / "qwen2.5-1.5b-q4.gguf"
    if not model_path.exists():
        _warn("未找到 LLM 模型文件（models/qwen2.5-1.5b-q4.gguf）")
        _warn("自主行为将使用 fallback 模式（固定 idle 动作，无 AI 对话）")
    else:
        _ok("LLM 模型文件已就绪")


def main():
    from core.runtime_logging import install_session_logging

    install_session_logging(ROOT)
    print("=" * 50)
    print("  桌宠 2.0 - 启动检查")
    print("=" * 50)

    check_python()
    ok = check_packages()
    if not ok:
        sys.exit(1)
    check_dirs()
    check_anim_index()
    check_ref_image()
    check_llm_model()

    print("=" * 50)
    print("  正在启动应用...")
    print("=" * 50)

    # 把项目根目录加入路径，再导入 main
    sys.path.insert(0, str(ROOT))
    from main import main as app_main
    app_main()


if __name__ == "__main__":
    main()
