"""
make_msix_assets.py - 由 app.ico 生成 MSIX 所需的各尺寸 PNG 资源。

MSIX 磁贴/图标规范要求特定文件名与尺寸：
  Square44x44Logo.png   44x44    （任务栏、开始菜单小图标）
  Square150x150Logo.png 150x150  （开始菜单磁贴）
  Square71x71Logo.png   71x71
  Square310x310Logo.png 310x310
  Wide310x150Logo.png   310x150
  StoreLogo.png         50x50    （商店/应用列表）
"""
import os
import sys

from PIL import Image

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "assets")
SRC = os.path.join(HERE, "preview", "icon_256.png")

SPECS = [
    ("Square44x44Logo.png", 44, 44),
    ("Square71x71Logo.png", 71, 71),
    ("Square150x150Logo.png", 150, 150),
    ("Square310x310Logo.png", 310, 310),
    ("StoreLogo.png", 50, 50),
    ("Wide310x150Logo.png", 310, 150),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    base = Image.open(SRC).convert("RGBA")
    print(f"源图标: {base.size}")

    for name, w, h in SPECS:
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if w == h:
            img = base.resize((w, h), Image.LANCZOS)
            canvas = img
        else:
            # 宽幅磁贴：图标居中，背景用图标底色填充两侧
            side = h
            logo = base.resize((side, side), Image.LANCZOS)
            bg_color = base.getpixel((base.width // 2, int(base.height * 0.06)))
            canvas.paste(Image.new("RGBA", (w, h), bg_color), (0, 0))
            canvas.paste(logo, ((w - side) // 2, 0), logo)
        canvas.save(os.path.join(OUT, name))
        print(f"  {name:<24} {w}x{h}")

    print(f"\n输出目录: {OUT}")


if __name__ == "__main__":
    main()
