"""
make_icon.py - 用 Fluent UI System Icons 的图标生成 Windows .ico

图标来源：microsoft/fluentui-system-icons（MIT 协议）
字形：book_arrow_clockwise_24_filled（书 + 循环箭头 = 转换书籍）

流程：
  1. 用 Edge 无头模式把 SVG 渲染成 256x256 位图（白底黑形）
  2. 把"黑"转成 alpha 通道，得到干净的抗锯齿蒙版
  3. 合成到圆角方形背景上
  4. 导出多尺寸 .ico（16/24/32/48/64/128/256）+ 预览图
"""
import os
import subprocess
import sys

from PIL import Image, ImageDraw

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

SVG_NAME = "book_convert.svg"
SIZES = [256, 128, 64, 48, 32, 24, 16]

# 背景色（渐变起止）
BG_TOP = (37, 99, 235)      # #2563EB
BG_BOTTOM = (29, 78, 216)   # #1D4ED8
GLYPH_INSET = 0.20          # 字形在图标内留白比例（四边）
BG_RADIUS = 0.22            # 圆角半径占边长比例


def render_svg(px: int, out_png: str) -> None:
    """用 Edge 无头模式把 SVG 渲染成 px×px 的 PNG（白底黑形）。"""
    svg = open(os.path.join(HERE, SVG_NAME), encoding="utf-8").read()
    # 强制放大到目标尺寸
    svg = svg.replace('width="24" height="24"',
                      f'width="{px}" height="{px}"')
    html = (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<style>html,body{margin:0;padding:0;background:#fff;'
        'width:' + str(px) + 'px;height:' + str(px) + 'px;overflow:hidden}'
        'svg{display:block;width:' + str(px) + 'px;height:' + str(px) + 'px}'
        '</style></head><body>' + svg + '</body></html>'
    )
    html_path = os.path.join(HERE, "_render.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    for p in (out_png,):
        if os.path.exists(p):
            os.remove(p)

    subprocess.run([
        EDGE, "--headless", "--disable-gpu", "--no-sandbox",
        "--hide-scrollbars", "--force-device-scale-factor=1",
        f"--screenshot={out_png}", f"--window-size={px},{px}",
        "file:///" + html_path.replace("\\", "/"),
    ], capture_output=True, timeout=90)

    if not os.path.exists(out_png):
        raise RuntimeError("Edge 渲染失败")


def build(size: int, glyph_src: Image.Image) -> Image.Image:
    """合成一张 size×size 的图标。"""
    # 圆角方形背景（带轻微竖向渐变）
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / max(1, size - 1)
        grad.putpixel((0, y), (
            int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t),
            int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t),
            int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t),
            255))
    grad = grad.resize((size, size))

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * BG_RADIUS), fill=255)
    bg.paste(grad, (0, 0), mask)

    # 字形：白色 + alpha 蒙版，居中放置
    inset = int(size * GLYPH_INSET)
    gw = size - inset * 2
    g = glyph_src.resize((gw, gw), Image.LANCZOS)

    glyph_white = Image.new("RGBA", (gw, gw), (255, 255, 255, 255))
    bg.paste(glyph_white, (inset, inset), g)   # g 作为 alpha
    return bg


def main():
    tmp = os.path.join(HERE, "_glyph.png")
    print("渲染 SVG（Edge 无头）…")
    render_svg(512, tmp)

    src = Image.open(tmp).convert("L")
    # 白底黑形 -> alpha 蒙版（黑=不透明）
    alpha = src.point(lambda v: 255 - v)
    glyph = alpha

    print(f"字形蒙版尺寸: {glyph.size}")
    non_zero = sum(1 for p in glyph.getdata() if p > 16)
    print(f"有效像素: {non_zero} ({non_zero / (glyph.size[0] * glyph.size[1]) * 100:.1f}%)")

    # 各尺寸图标
    frames = []
    preview_dir = os.path.join(HERE, "preview")
    os.makedirs(preview_dir, exist_ok=True)

    for s in SIZES:
        img = build(s, glyph)
        frames.append(img)
        img.save(os.path.join(preview_dir, f"icon_{s}.png"))
        print(f"  {s}x{s}")

    # 存 .ico（Pillow 会写入多尺寸）
    frames.sort(key=lambda i: i.width, reverse=True)
    ico_path = os.path.join(HERE, "app.ico")
    frames[0].save(ico_path, format="ICO",
                   sizes=[(i.width, i.height) for i in frames])
    print(f"\n已生成: {ico_path}  ({os.path.getsize(ico_path) / 1024:.1f} KB)")

    # 预览条：把各尺寸并排拼一起，方便肉眼检查小尺寸是否还认得出
    pad, maxh = 16, 256
    total_w = sum(i.width for i in frames) + pad * (len(frames) + 1)
    sheet = Image.new("RGB", (total_w, maxh + pad * 2), (245, 245, 247))
    x = pad
    for img in frames:
        sheet.paste(img, (x, pad + (maxh - img.height)), img)
        x += img.width + pad
    sheet.save(os.path.join(HERE, "_preview.png"))
    print(f"预览: {os.path.join(HERE, '_preview.png')}")

    os.remove(tmp)
    h = os.path.join(HERE, "_render.html")
    if os.path.exists(h):
        os.remove(h)


if __name__ == "__main__":
    main()
