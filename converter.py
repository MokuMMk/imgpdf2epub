"""
converter.py - 纯图片 PDF -> EPUB 转换引擎

适用于扫描版 / 图片型 PDF：每一页是一张图，直接抽取图片并封装成 EPUB，
不做 OCR、不做文字识别，保留原始图像质量。

输出结构（单章节）：
    OEBPS/
      text/cover.xhtml     <- 封面页（用第一页）
      text/content.xhtml   <- 所有页面图片都在这里，spine 只有一项
      images/cover.jpg ...
      images/p00001.jpg ...
      nav.xhtml / toc.ncx  <- 目录指向同一文档内的 #page-N 锚点
      content.opf
      style.css

之所以把所有图片塞进一个章节，是因为不少阅读器每次进入新章节都要重新解析
文档，页数一多就明显卡顿；单章节只加载一次，翻页体验更顺。
页与页之间用 CSS 分页（page-break-after），显示上仍是一页一屏。

依赖: PyMuPDF (fitz), Pillow
"""

from __future__ import annotations

import html
import os
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO as _BytesIO
from typing import Callable, Optional

import fitz  # PyMuPDF
from PIL import Image

try:
    from PIL import Image as _PILImage
    _BILINEAR = _PILImage.Resampling.BILINEAR
    _LANCZOS = _PILImage.Resampling.LANCZOS
except AttributeError:  # Pillow < 9.1
    _BILINEAR = Image.BILINEAR
    _LANCZOS = Image.LANCZOS

# 默认缩放算法：双线性。
# 双线性比 Lanczos 更"软"一些，不会在文字边缘产生 Lanczos 特有的振铃/过冲，
# 在墨水屏上一页一页看反而更干净。需要更锐时可用 resample 参数改回 LANCZOS。
_BILINEAR_DEFAULT = _BILINEAR
_RESAMPLE_MAP = {"bilinear": _BILINEAR, "lanczos": _LANCZOS}


# --------------------------------------------------------------------------
# 数据模型
# --------------------------------------------------------------------------

@dataclass
class PageImage:
    """一页抽出来的图片数据。"""
    index: int              # 0 基页序
    data: bytes             # 编码后的图片字节
    ext: str                # 'jpg' / 'png' / 'webp'
    width: int
    height: int


@dataclass
class ConvertOptions:
    """转换选项。"""
    # 最大边像素限制；None 表示保持原始分辨率
    max_dimension: Optional[int] = None
    # JPEG 质量 (1-100)，仅对 jpg 输出生效
    jpeg_quality: int = 90
    # 强制统一输出格式: 'auto' | 'jpg' | 'png'
    image_format: str = "auto"
    # 是否尝试裁掉扫描白边
    trim_borders: bool = False
    # 裁边阈值 (0-255)，越大裁得越多
    trim_threshold: int = 240
    # 灰度化
    grayscale: bool = False
    # 书名 / 作者
    title: Optional[str] = None
    author: str = "未知"
    language: str = "zh"
    # 每页在 EPUB 中的显示方式
    # 'fit'    : 按页面宽度自适应（推荐，正文阅读器体验最好）
    # 'native' : 按图片原始像素尺寸显示（适合看大图细节）
    display_mode: str = "fit"
    # 是否把第一页设为封面
    use_cover: bool = True
    # 缩放时只按宽度适配（竖屏设备用），而不是限制最长边
    fit_width_only: bool = False
    # 是否允许放大小图。默认 False：只缩不放
    allow_upscale: bool = False
    # 缩放算法: 'bilinear'（默认）| 'lanczos'
    resample: str = "bilinear"


# --------------------------------------------------------------------------
# 设备预设
# --------------------------------------------------------------------------
# 每项: (显示名, 屏幕宽, 屏幕高)
# 注意内部一律用「宽 x 高」存储，界面上才按设备的习惯标注。
DEVICES: list[tuple[str, int, int]] = [
    ("EEGO A4", 552, 768),      # 552x768 竖向
    ("Pico", 684, 1216),        # 1216(高) x 684(宽) 竖向
]

# 兼容旧名字
EEGO_A4_WIDTH = 552
EEGO_A4_HEIGHT = 768
PICO_WIDTH = 684
PICO_HEIGHT = 1216


def device_options(
    title: Optional[str] = None,
    width: int = EEGO_A4_WIDTH,
    height: int = EEGO_A4_HEIGHT,
    headroom: float = 1.0,
    resample: str = "bilinear",
) -> ConvertOptions:
    """
    小屏设备预设：**只按屏幕宽度缩放，不做任何其它处理**。

    - 输出宽度 = width × headroom（默认 ×1.0，与屏幕 1:1）。
      余量 1.0 最清晰（设备端无需再缩放）；调大则输出更宽的图，留缩放余量。
    - **不转灰度**：保持原图颜色模式。
    - **不裁边**：整页原样保留。
    - **只缩不放**：图比目标窄就原样透传，连重编码都不做。
    - **输出格式**在调用方另行指定（默认保持原格式）。
    - 缩放算法默认**双线性**。
    - 第一页设为封面。
    """
    target_w = max(1, int(round(width * headroom)))
    return ConvertOptions(
        max_dimension=target_w,
        image_format="auto",       # 保持原格式
        grayscale=False,           # 不动颜色
        trim_borders=False,        # 不动边界
        title=title,
        display_mode="fit",
        use_cover=True,
        fit_width_only=True,       # 按宽度适配（竖屏看宽度）
        allow_upscale=False,       # 只缩不放
        resample=resample,
    )


# 向后兼容：旧的 eego_options 名字仍可用
eego_options = device_options


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------

_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_filename(name: str, fallback: str = "book") -> str:
    """把任意字符串变成安全的文件名。"""
    name = _ILLEGAL.sub("_", (name or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:120] or fallback


def _human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.1f} GB"


def _trim_box(img: Image.Image, threshold: int) -> Image.Image:
    """
    自动裁掉四周接近纯白的边框。
    threshold 越高，判定为"背景"的像素范围越宽，裁得越多。
    """
    gray = img.convert("L")
    # 反相后做 bbox：非白色区域 -> 亮点
    inverted = gray.point(lambda p: 255 if p < threshold else 0)
    bbox = inverted.getbbox()
    if bbox is None:
        return img  # 整页都是白/接近白，保持原样
    left, top, right, bottom = bbox
    # 留一点边距，避免贴得太紧
    pad = max(2, int(min(img.width, img.height) * 0.005))
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(img.width, right + pad)
    bottom = min(img.height, bottom + pad)
    if right - left < 16 or bottom - top < 16:
        return img  # 裁得过分了，放弃
    if left == 0 and top == 0 and right == img.width and bottom == img.height:
        return img
    return img.crop((left, top, right, bottom))


def _process_image(raw: bytes, opts: ConvertOptions) -> tuple[bytes, str, int, int]:
    """
    对单页原始图片做后处理，返回 (字节, 扩展名, 宽, 高)。

    只有在确实需要处理时才走 Pillow 重编码；否则原样透传，保证无损且快速。
    """
    need_pillow = (
        opts.max_dimension is not None
        or opts.trim_borders
        or opts.grayscale
        or opts.image_format != "auto"
    )
    if not need_pillow:
        # 直接透传：用 Pillow 只探测尺寸，不改数据
        with Image.open(_BytesIO(raw)) as im:
            return raw, _ext_of(im.format), im.width, im.height

    with Image.open(_BytesIO(raw)) as im:
        im.load()
        orig_w, orig_h = im.width, im.height

        # 缩放：默认限制最长边；fit_width_only 时按宽度适配
        need_resize = False
        if opts.max_dimension:
            if opts.fit_width_only:
                scale = opts.max_dimension / float(im.width)
            else:
                longest = max(im.width, im.height)
                scale = opts.max_dimension / float(longest) if longest else 1.0
            if scale < 1.0 or (scale > 1.0 and opts.allow_upscale):
                need_resize = True
                new_size = (max(1, int(round(im.width * scale))),
                            max(1, int(round(im.height * scale))))
                im = im.resize(
                    new_size,
                    _RESAMPLE_MAP.get(opts.resample, _BILINEAR_DEFAULT))

        # 尺寸没变、也不需要裁边/转色/转格式 -> 原字节直接透传，零改动
        if (not need_resize
                and not opts.trim_borders
                and not opts.grayscale
                and opts.image_format == "auto"):
            return raw, _ext_of(im.format), orig_w, orig_h

        if opts.trim_borders:
            im = _trim_box(im, opts.trim_threshold)

        if opts.grayscale and im.mode not in ("L", "1"):
            im = im.convert("L")

        # 决定输出格式
        fmt = opts.image_format
        if fmt == "auto":
            fmt = "png" if im.mode in ("1", "L", "LA", "RGBA", "P") else "jpg"

        buf = _BytesIO()
        if fmt == "jpg":
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            # progressive=False -> 输出 SOF0（基线 JPEG）。
            # 渐进式 JPEG 是 SOF2，部分小屏阅读器不支持，会解码失败或显示异常。
            # 基线 JPEG 兼容性最好，所有解码器都能读。
            im.save(buf, format="JPEG", quality=opts.jpeg_quality,
                    optimize=True, progressive=False)
            ext = "jpg"
        else:
            if im.mode == "P":
                im = im.convert("RGBA")
            im.save(buf, format="PNG", optimize=True)
            ext = "png"

        return buf.getvalue(), ext, im.width, im.height


def _ext_of(pil_format: Optional[str]) -> str:
    f = (pil_format or "PNG").upper()
    if f == "JPEG":
        return "jpg"
    if f == "PNG":
        return "png"
    return "jpg"


# --------------------------------------------------------------------------
# 主转换流程
# --------------------------------------------------------------------------

def extract_pages(
    pdf_path: str,
    opts: ConvertOptions,
    progress: Optional[Callable[[int, int, str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> list[PageImage]:
    """把 PDF 的每一页渲染/抽取成图片。"""
    pages: list[PageImage] = []

    with fitz.open(pdf_path) as doc:
        total = doc.page_count
        if total == 0:
            raise ValueError("PDF 没有任何页面。")

        for i in range(total):
            if should_stop and should_stop():
                raise InterruptedError("用户已取消。")

            page = doc.load_page(i)
            raw = _extract_page_bytes(page)
            data, ext, w, h = _process_image(raw, opts)
            pages.append(PageImage(index=i, data=data, ext=ext, width=w, height=h))

            if progress:
                progress(i + 1, total, f"第 {i + 1}/{total} 页 {w}x{h}")

    return pages


def _extract_page_bytes(page: "fitz.Page") -> bytes:
    """
    优先直接抽取页面上嵌入的原图（无损、快）；
    若一页里没有或有多张图 / 是矢量内容，则整页渲染成位图。
    """
    images = page.get_images(full=True)

    if len(images) == 1:
        xref = images[0][0]
        try:
            info = page.parent.extract_image(xref)
            data = info.get("image")
            # 只接受足够大的图，避免把一个小 logo 当成整页
            if data and len(data) > 2048:
                return data
        except Exception:
            pass

    # 回退：整页高分辨率渲染
    dpi = _guess_render_dpi(page)
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    return pix.tobytes("png")


def _guess_render_dpi(page: "fitz.Page") -> int:
    """根据页面上最大图片的像素密度，估算合理的渲染 DPI。"""
    try:
        rect = page.rect
        best = 150
        for img in page.get_images(full=True):
            xref = img[0]
            try:
                info = page.parent.extract_image(xref)
            except Exception:
                continue
            w = info.get("width") or 0
            if w and rect.width > 0:
                dpi = int(round(w / (rect.width / 72.0)))
                best = max(best, dpi)
        return max(120, min(best, 600))
    except Exception:
        return 150


# --------------------------------------------------------------------------
# EPUB 打包
# --------------------------------------------------------------------------

_CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_STYLE_CSS = """@page { margin: 0; padding: 0; }
html, body { margin: 0; padding: 0; }
body { text-align: center; }
div.page { margin: 0; padding: 0; text-align: center;
           page-break-after: always; break-after: page; }
div.page:last-child { page-break-after: auto; break-after: auto; }
img.page-image { display: block; margin: 0 auto; padding: 0; border: 0; }
"""

# 章节文件名（单章节模式固定用它）
CONTENT_NAME = "content.xhtml"


def _img_media_type(ext: str) -> str:
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/jpeg")


def _page_div(p: PageImage, display_mode: str) -> str:
    """
    一页对应的 <div>；带 id 供目录锚点跳转。

    每张图都写显式像素宽高（而不是 CSS 的 width:100%），
    这样阅读器不需要自己推断尺寸，也不需要为了铺满宽度而重采样。
    """
    n = p.index + 1
    src = f"../images/p{n:05d}.{p.ext}"

    if display_mode == "native":
        cls = "page-image native"
        style = f' style="width:{p.width}px;height:{p.height}px;max-width:none;"'
    else:
        cls = "page-image fit"
        style = (f' style="width:{p.width}px;height:{p.height}px;'
                 f'max-width:100%;height:auto;"')

    return (
        f'  <div class="page" id="page-{n}">'
        f'<img class="{cls}" src="{src}" '
        f'alt="第 {n} 页"{style}/></div>'
    )


def _cover_xhtml(title: str, ext: str, w: int, h: int) -> str:
    """封面页：单张图，写死像素宽高避免阅读器重采样。"""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh">\n'
        '<head>\n'
        f'  <title>{html.escape(title)}</title>\n'
        '  <link rel="stylesheet" type="text/css" href="../style.css"/>\n'
        '  <style type="text/css">\n'
        '    html, body { margin:0; padding:0; }\n'
        '    div.cover { margin:0; padding:0; text-align:center; }\n'
        '  </style>\n'
        '</head>\n'
        '<body>\n'
        '  <div class="cover">'
        f'<img class="cover-image" src="../images/cover.{ext}" '
        f'style="width:{w}px;height:{h}px;max-width:100%;height:auto;" '
        f'alt="{html.escape(title)}"/></div>\n'
        '</body>\n'
        '</html>\n'
    )


def _content_xhtml(pages: list[PageImage], opts: ConvertOptions, title: str) -> str:
    """
    单章节正文：所有页面图片放在同一个 XHTML 文档里。

    这样阅读器只需加载一个章节，翻页时不会再反复解析新文档，
    避免部分阅读器在大量小章节时出现的卡顿。
    """
    body = "\n".join(_page_div(p, opts.display_mode) for p in pages)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="'
        + html.escape(opts.language) + '">\n'
        '<head>\n'
        f'  <title>{html.escape(title)}</title>\n'
        '  <link rel="stylesheet" type="text/css" href="../style.css"/>\n'
        '</head>\n'
        '<body>\n'
        '<section epub:type="bodymatter">\n'
        + body + '\n'
        '</section>\n'
        '</body>\n'
        '</html>\n'
    )


def _opf_xml(pages: list[PageImage], opts: ConvertOptions, book_id: str,
             title: str, cover_ext: Optional[str] = None) -> str:
    manifest = [
        '    <item id="nav" href="nav.xhtml" '
        'media-type="application/xhtml+xml" properties="nav"/>',
        '    <item id="ncx" href="toc.ncx" '
        'media-type="application/x-dtbncx+xml"/>',
        '    <item id="css" href="style.css" media-type="text/css"/>',
    ]

    spine = []
    guide = []

    # 封面：EPUB3 用 properties="cover-image"，同时给旧阅读器留 guide 和 meta
    if cover_ext:
        manifest.append(
            f'    <item id="cover-image" href="images/cover.{cover_ext}" '
            f'media-type="{_img_media_type(cover_ext)}" properties="cover-image"/>'
        )
        manifest.append(
            '    <item id="cover" href="text/cover.xhtml" '
            'media-type="application/xhtml+xml"/>'
        )
        spine.append('    <itemref idref="cover" linear="yes"/>')
        guide.append('    <reference type="cover" title="封面" href="text/cover.xhtml"/>')

    manifest.append(
        f'    <item id="content" href="text/{CONTENT_NAME}" '
        'media-type="application/xhtml+xml"/>'
    )
    for p in pages:
        n = p.index + 1
        manifest.append(
            f'    <item id="img{n:05d}" href="images/p{n:05d}.{p.ext}" '
            f'media-type="{_img_media_type(p.ext)}"/>'
        )

    # 正文紧随封面之后（单章节）
    spine.append('    <itemref idref="content"/>')

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cover_meta = ('    <meta name="cover" content="cover-image"/>\n') if cover_ext else ""
    guide_xml = ('  <guide>\n' + "\n".join(guide) + '\n  </guide>\n') if guide else ""

    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="bookid" xml:lang="' + html.escape(opts.language) + '">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'    <dc:identifier id="bookid">{html.escape(book_id)}</dc:identifier>\n'
        f'    <dc:title>{html.escape(title)}</dc:title>\n'
        f'    <dc:language>{html.escape(opts.language)}</dc:language>\n'
        f'    <dc:creator>{html.escape(opts.author)}</dc:creator>\n'
        f'    <meta property="dcterms:modified">{now}</meta>\n'
        + cover_meta +
        '    <meta name="generator" content="imgpdf2epub"/>\n'
        '  </metadata>\n'
        '  <manifest>\n' + "\n".join(manifest) + '\n  </manifest>\n'
        '  <spine toc="ncx">\n' + "\n".join(spine) + '\n  </spine>\n'
        + guide_xml +
        '</package>\n'
    )


def _nav_xhtml(pages: list[PageImage], title: str) -> str:
    """
    目录：指向同一个文档内的页锚点。
    因为正文只有一个 XHTML，锚点跳转不涉及加载新章节，不会卡。
    """
    items = "\n".join(
        f'      <li><a href="text/{CONTENT_NAME}#page-{p.index + 1}">第 {p.index + 1} 页</a></li>'
        for p in pages
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh">\n'
        '<head><title>目录</title>'
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        '<body>\n'
        '  <nav epub:type="toc" id="toc">\n'
        '    <h1>' + html.escape(title) + '</h1>\n'
        '    <ol>\n' + items + '\n    </ol>\n'
        '  </nav>\n'
        '</body>\n'
        '</html>\n'
    )


def _ncx_xml(pages: list[PageImage], title: str, book_id: str) -> str:
    """NCX 目录：同样指向单文档内的页锚点，兼容旧阅读器。"""
    points = []
    for p in pages:
        n = p.index + 1
        points.append(
            f'    <navPoint id="np{n}" playOrder="{n}">\n'
            f'      <navLabel><text>第 {n} 页</text></navLabel>\n'
            f'      <content src="text/{CONTENT_NAME}#page-{n}"/>\n'
            f'    </navPoint>'
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        '  <head>\n'
        f'    <meta name="dtb:uid" content="{html.escape(book_id)}"/>\n'
        '    <meta name="dtb:depth" content="1"/>\n'
        '    <meta name="dtb:totalPageCount" content="0"/>\n'
        '    <meta name="dtb:maxPageNumber" content="0"/>\n'
        '  </head>\n'
        f'  <docTitle><text>{html.escape(title)}</text></docTitle>\n'
        '  <navMap>\n' + "\n".join(points) + '\n  </navMap>\n'
        '</ncx>\n'
    )


def build_epub(
    pages: list[PageImage],
    out_path: str,
    opts: ConvertOptions,
    progress: Optional[Callable[[int, int, str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> str:
    """把页码图片列表打包为 EPUB 3 文件。"""
    if not pages:
        raise ValueError("没有可写入的页面图片。")

    title = opts.title or os.path.splitext(os.path.basename(out_path))[0]
    book_id = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"imgpdf2epub:{title}:{len(pages)}"))

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)

    # mimetype 必须是 zip 里的第一项且不压缩
    has_cover = bool(opts.use_cover)
    total_steps = len(pages) + (5 if has_cover else 4)
    done = 0

    # 封面就用第一页（已按同一套选项处理过，尺寸/格式一致）
    cover = pages[0] if has_cover else None

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        zi = zipfile.ZipInfo("mimetype", date_time=(1980, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, "application/epub+zip")

        z.writestr("META-INF/container.xml", _CONTAINER_XML)
        z.writestr("OEBPS/style.css", _STYLE_CSS)

        # 封面图
        if cover is not None:
            z.writestr(f"OEBPS/images/cover.{cover.ext}", cover.data)

        # 只写图片，正文 XHTML 最后一次性写入（单章节）
        for p in pages:
            if should_stop and should_stop():
                raise InterruptedError("用户已取消。")
            z.writestr(f"OEBPS/images/p{p.index + 1:05d}.{p.ext}", p.data)
            done += 1
            if progress:
                progress(done, total_steps, f"打包图片 {done}/{len(pages)}")

        # 封面页 XHTML
        if cover is not None:
            z.writestr("OEBPS/text/cover.xhtml",
                       _cover_xhtml(title, cover.ext, cover.width, cover.height))

        # 单章节正文：所有页面图片都在这一个文档里
        z.writestr(f"OEBPS/text/{CONTENT_NAME}",
                   _content_xhtml(pages, opts, title))
        z.writestr("OEBPS/nav.xhtml", _nav_xhtml(pages, title))
        z.writestr("OEBPS/toc.ncx", _ncx_xml(pages, title, book_id))
        z.writestr("OEBPS/content.opf",
                   _opf_xml(pages, opts, book_id, title,
                            cover_ext=cover.ext if cover else None))
        done += 5 if cover else 4
        if progress:
            progress(total_steps, total_steps, "写入正文与元数据")

    return out_path


# --------------------------------------------------------------------------
# 一站式入口
# --------------------------------------------------------------------------

@dataclass
class ConvertResult:
    ok: bool
    out_path: str = ""
    pages: int = 0
    error: str = ""
    src_size: int = 0
    out_size: int = 0


def convert(
    pdf_path: str,
    out_path: Optional[str] = None,
    opts: Optional[ConvertOptions] = None,
    progress: Optional[Callable[[int, int, str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> ConvertResult:
    """把纯图片 PDF 转换成 EPUB。"""
    opts = opts or ConvertOptions()

    if not os.path.isfile(pdf_path):
        return ConvertResult(False, error=f"文件不存在: {pdf_path}")

    if out_path is None:
        base = os.path.splitext(os.path.basename(pdf_path))[0]
        out_path = os.path.join(os.path.dirname(os.path.abspath(pdf_path)),
                                safe_filename(base) + ".epub")

    try:
        src_size = os.path.getsize(pdf_path)
        pages = extract_pages(pdf_path, opts, progress, should_stop)
        build_epub(pages, out_path, opts, progress, should_stop)

        return ConvertResult(
            ok=True,
            out_path=out_path,
            pages=len(pages),
            src_size=src_size,
            out_size=os.path.getsize(out_path),
        )
    except InterruptedError as e:
        return ConvertResult(False, error=str(e))
    except Exception as e:  # noqa: BLE001 - 面向用户，统一成错误信息
        return ConvertResult(False, error=f"{type(e).__name__}: {e}")
