"""
cli.py - 命令行入口（也用于批量/脚本调用）

用法:
    imgpdf2epub.exe book.pdf
    imgpdf2epub.exe a.pdf b.pdf -o D:\\out
    imgpdf2epub.exe C:\\scans --recursive --quality 80 --max-dim 1600
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from converter import (DEVICES, EEGO_A4_HEIGHT, EEGO_A4_WIDTH, ConvertOptions,
                       convert, device_options, safe_filename)

# 设备名 -> (显示名, 宽, 高)
DEVICE_TABLE = {name.lower().replace(" ", ""): (name, w, h) for name, w, h in DEVICES}
DEVICE_TABLE["eego"] = ("EEGO A4", EEGO_A4_WIDTH, EEGO_A4_HEIGHT)
DEVICE_TABLE["eegoa4"] = ("EEGO A4", EEGO_A4_WIDTH, EEGO_A4_HEIGHT)
DEVICE_TABLE["pico"] = ("Pico", 684, 1216)


def collect_inputs(paths: list[str], recursive: bool) -> list[str]:
    out: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            if recursive:
                for root, _d, files in os.walk(p):
                    out += [os.path.join(root, f) for f in files if f.lower().endswith(".pdf")]
            else:
                out += [os.path.join(p, f) for f in os.listdir(p) if f.lower().endswith(".pdf")]
        elif os.path.isfile(p) and p.lower().endswith(".pdf"):
            out.append(p)
        else:
            print(f"[跳过] 不是 PDF 或不存在: {p}", file=sys.stderr)
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="imgpdf2epub",
        description="纯图片 PDF -> EPUB 转换（不做 OCR，保留原始图像）")
    ap.add_argument("inputs", nargs="+", help="PDF 文件或包含 PDF 的目录")
    ap.add_argument("-o", "--outdir", default=None, help="输出目录（默认与源文件同目录）")
    ap.add_argument("-r", "--recursive", action="store_true", help="递归扫描子目录")
    ap.add_argument("--quality", type=int, default=90, help="JPG 质量 40-100（默认 90）")
    ap.add_argument("--max-dim", type=int, default=None,
                    help="限制图片最大边像素，例如 1600；默认保持原分辨率")
    ap.add_argument("--width", type=int, default=None,
                    help="按目标宽度缩放（适合小屏设备），例如 552")
    ap.add_argument("--format", choices=["auto", "jpg", "png"], default="auto",
                    help="输出图片格式，默认 auto（保留原格式）")
    ap.add_argument("--gray", action="store_true", help="转灰度，显著减小体积")
    ap.add_argument("--trim", action="store_true", help="自动裁掉白色扫描边")
    ap.add_argument("--native", action="store_true",
                    help="按原始像素尺寸显示（默认自适应页宽，阅读器体验更好）")
    ap.add_argument("--resample", choices=["bilinear", "lanczos"], default="bilinear",
                    help="缩放算法，默认 bilinear（双线性）")
    ap.add_argument("--author", default="未知", help="作者元数据")
    ap.add_argument("--lang", default="zh", help="语言元数据，默认 zh")
    ap.add_argument("-q", "--quiet", action="store_true", help="安静模式")
    ap.add_argument("--json-progress", action="store_true",
                    help="进度以 JSON 逐行输出（供 GUI 调用）")

    g = ap.add_argument_group("小屏设备")
    g.add_argument("--device", choices=["eego", "pico"], default=None,
                   help="设备预设：eego (552x768) / pico (684x1216)")
    g.add_argument("--eego", action="store_true",
                   help="等同 --device eego")
    g.add_argument("--pico", action="store_true",
                   help="等同 --device pico")
    g.add_argument("--device-mode", action="store_true",
                   help="使用设备预设（只按屏幕宽度缩放，不改颜色/边界）")
    g.add_argument("--screen", default=None, metavar="WxH",
                   help="自定义屏幕尺寸，例如 800x1200。"
                        "配合 --device-mode 即可当「自定义设备」用")
    g.add_argument("--headroom", type=float, default=1.0,
                   help="相对屏宽的倍数，默认 1.0（= 屏宽，与屏幕 1:1）")
    g.add_argument("--device-quality", type=int, default=90,
                   help="设备模式选 JPG 时的质量 40-100，默认 90")
    # 旧的参数名保留为别名
    g.add_argument("--eego-quality", type=int, default=None,
                   help=argparse.SUPPRESS)

    c = ap.add_argument_group("封面")
    c.add_argument("--cover", dest="cover", action="store_true", default=None,
                   help="把第一页设为封面（默认开）")
    c.add_argument("--no-cover", dest="cover", action="store_false",
                   help="不生成封面")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    files = collect_inputs(args.inputs, args.recursive)
    if not files:
        print("没有找到可转换的 PDF。", file=sys.stderr)
        return 2

    if args.outdir:
        os.makedirs(args.outdir, exist_ok=True)

    ok = bad = 0
    for i, pdf in enumerate(files, 1):
        if args.outdir:
            out = os.path.join(
                args.outdir, safe_filename(os.path.splitext(os.path.basename(pdf))[0]) + ".epub")
        else:
            out = None

        title = os.path.splitext(os.path.basename(pdf))[0]

        # 解析设备选择：--device / --eego / --pico / --device-mode
        dev_name = args.device
        if args.eego:
            dev_name = "eego"
        if args.pico:
            dev_name = "pico"
        use_device = bool(dev_name) or args.device_mode

        if use_device:
            # 设备预设：按屏幕宽度缩放；格式可选，其余不动
            dev = DEVICE_TABLE.get(dev_name or "eego", ("EEGO A4", EEGO_A4_WIDTH, EEGO_A4_HEIGHT))
            sw, sh = dev[1], dev[2]
            if args.screen:
                try:
                    pw, ph = args.screen.lower().split("x")
                    sw, sh = int(pw), int(ph)
                except Exception:
                    print(f"[警告] --screen 格式应为 宽x高，已用默认 {sw}x{sh}", file=sys.stderr)

            opts = device_options(title=title, width=sw, height=sh,
                                  headroom=args.headroom, resample=args.resample)
            opts.author = args.author
            opts.language = args.lang
            if args.cover is False:
                opts.use_cover = False
            # 输出格式：--format 指定（auto=保持原格式）
            opts.image_format = args.format
            if args.format == "jpg":
                q = args.device_quality
                if args.eego_quality is not None:
                    q = args.eego_quality
                opts.jpeg_quality = max(40, min(100, q))
            # 显式指定的额外处理才生效（默认不做）
            if args.gray:
                opts.grayscale = True
            if args.trim:
                opts.trim_borders = True
        else:
            # --width 优先：按目标宽度缩放（只缩不放）
            max_dim = args.max_dim
            width_only = False
            if args.width:
                max_dim = args.width
                width_only = True

            opts = ConvertOptions(
                max_dimension=max_dim,
                jpeg_quality=max(40, min(100, args.quality)),
                image_format=args.format,
                grayscale=args.gray,
                trim_borders=args.trim,
                author=args.author,
                language=args.lang,
                title=title,
                display_mode="native" if args.native else "fit",
                use_cover=True if args.cover is None else args.cover,
                fit_width_only=width_only,
                allow_upscale=False,
                resample=args.resample,
            )

        def on_prog(cur: int, tot: int, msg: str) -> None:
            if args.json_progress:
                # 给 GUI 用的机器可读进度（每行一个 JSON）
                pct = int(cur * 100 / tot) if tot else 0
                print(json.dumps({"file": i, "total": len(files), "percent": pct,
                                  "message": msg}, ensure_ascii=False), flush=True)
            elif not args.quiet:
                pct = int(cur * 100 / tot) if tot else 0
                print(f"\r  [{i}/{len(files)}] {pct:3d}%  {msg[:50]:<50}", end="", flush=True)

        tag = "[EEGO] " if args.eego else ""
        print(f"[{i}/{len(files)}] {tag}{os.path.basename(pdf)}", flush=True)
        res = convert(pdf, out_path=out, opts=opts, progress=on_prog)
        if not args.quiet and not args.json_progress:
            print()

        if res.ok:
            print(f"  OK -> {res.out_path}", flush=True)
            ratio = (res.out_size / res.src_size * 100) if res.src_size else 0
            print(f"     {res.pages} 页 | {res.src_size/1048576:.1f} MB -> "
                  f"{res.out_size/1048576:.1f} MB (原体积的 {ratio:.0f}%)"
                  f" | 封面: {'有' if opts.use_cover else '无'}")
            ok += 1
        else:
            print(f"  失败: {res.error}", file=sys.stderr)
            bad += 1

    print(f"\n完成: 成功 {ok}，失败 {bad}。")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
