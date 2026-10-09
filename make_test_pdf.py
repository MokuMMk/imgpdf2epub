"""make_test_pdf.py - 生成一个纯图片型 PDF 测试样本（无文字层）。"""
import os
import sys

import fitz
from PIL import Image, ImageDraw, ImageFont

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.join(OUT_DIR, "_tmp_imgs")
os.makedirs(TMP, exist_ok=True)

W, H = 1240, 1754  # A4 @150dpi


def font(size):
    for name in ("msyh.ttc", "simhei.ttf", "arial.ttf"):
        p = os.path.join(r"C:\Windows\Fonts", name)
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def make_page(n):
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    f_big = font(64)
    f_mid = font(36)
    f_sm = font(28)

    d.rectangle([60, 60, W - 60, 200], outline="black", width=4)
    d.text((100, 100), f"扫描页 SCAN PAGE {n}", font=f_big, fill="black")

    y = 280
    for i in range(14):
        d.text((100, y), f"{n}.{i + 1}  这是一行用于测试的文本内容，模拟扫描书页。",
               font=f_mid, fill="black")
        y += 62

    d.ellipse([200, y + 30, 500, y + 330], outline="black", width=5)
    d.rectangle([600, y + 30, 1000, y + 330], outline="black", width=5)
    d.text((620, y + 160), "图 " + str(n), font=f_big, fill="black")

    d.text((100, H - 90), f"- 第 {n} 页 -", font=f_sm, fill="black")
    return img


def main():
    pages = 5
    doc = fitz.open()
    for n in range(1, pages + 1):
        p = os.path.join(TMP, f"page{n}.png")
        make_page(n).save(p)
        jb = os.path.splitext(p)[0] + ".jpg"
        Image.open(p).convert("RGB").save(jb, quality=88)
        page = doc.new_page(width=595, height=842)  # A4 pt
        page.insert_image(fitz.Rect(0, 0, 595, 842), filename=jb)

    pdf_path = os.path.join(OUT_DIR, "test_scan.pdf")
    doc.save(pdf_path, deflate=True, garbage=4)
    doc.close()

    # 校验：确认没有文字层
    chk = fitz.open(pdf_path)
    txt = "".join(chk[i].get_text() for i in range(chk.page_count)).strip()
    nimg = sum(len(chk[i].get_images(full=True)) for i in range(chk.page_count))
    chk.close()

    print(f"生成: {pdf_path}")
    print(f"  页数: {pages}  大小: {os.path.getsize(pdf_path)/1024:.0f} KB")
    print(f"  图片数: {nimg}  文字层字符数: {len(txt)}  (应为 0)")


if __name__ == "__main__":
    main()
