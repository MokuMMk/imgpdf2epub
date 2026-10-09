"""
app.py - 统一入口

- 无参数运行  -> 打开图形界面
- 带参数运行  -> 命令行模式（方便批量/自动化调用）
"""

import multiprocessing
import sys


def _fix_console_encoding() -> None:
    """Windows 控制台默认 GBK，中文/符号输出会崩，这里统一成 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> int:
    _fix_console_encoding()

    if len(sys.argv) > 1:
        from cli import main as cli_main
        return cli_main()

    from gui import main as gui_main
    return gui_main()


if __name__ == "__main__":
    multiprocessing.freeze_support()  # PyInstaller 打包后必需
    sys.exit(main())
