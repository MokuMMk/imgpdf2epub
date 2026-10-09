"""verify_epub.py - 严格校验生成的 EPUB 是否符合规范且可正常阅读。"""
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from io import BytesIO

from PIL import Image

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

NS = {"opf": "http://www.idpf.org/2007/opf",
      "dc": "http://purl.org/dc/elements/1.1/"}


def fail(msg):
    print(f"  [FAIL] {msg}")
    return 1


def ok(msg):
    print(f"  [ok]   {msg}")
    return 0


def _jpeg_sof(data: bytes):
    """
    读取 JPEG 的 SOF 标记，返回标记字节（0xC0=基线 SOF0, 0xC2=渐进 SOF2）。
    找不到时返回 None。
    """
    i = 2  # 跳过 SOI (0xFFD8)
    n = len(data)
    while i < n - 1:
        if data[i] != 0xFF:
            i += 1
            continue
        m = data[i + 1]
        # 无长度字段的标记
        if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7 or m == 0x01:
            i += 2
            continue
        if i + 3 >= n:
            break
        ln = int.from_bytes(data[i + 2:i + 4], "big")
        # SOF0..SOF3 / SOF5..SOF7 / SOF9..SOF11 / SOF13..SOF15
        if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            return m
        i += 2 + ln
    return None


def main(path):
    errs = 0
    print(f"校验: {path}  ({os.path.getsize(path)/1024:.0f} KB)\n")

    if not zipfile.is_zipfile(path):
        return fail("不是合法 zip")

    z = zipfile.ZipFile(path)
    names = z.namelist()

    # 1. mimetype 必须是第一项且 STORED、内容精确
    infos = z.infolist()
    if infos[0].filename != "mimetype":
        errs += fail(f"mimetype 不是第一个条目 (是 {infos[0].filename})")
    else:
        ok("mimetype 为第一个条目")
    if infos[0].compress_type != zipfile.ZIP_STORED:
        errs += fail("mimetype 被压缩了")
    else:
        ok("mimetype 未压缩 (STORED)")
    mt = z.read("mimetype").decode()
    if mt != "application/epub+zip":
        errs += fail(f"mimetype 内容错误: {mt!r}")
    else:
        ok("mimetype 内容正确")

    # 2. container.xml
    if "META-INF/container.xml" not in names:
        return fail("缺少 META-INF/container.xml")
    ok("存在 container.xml")

    # 3. 所有 XML/XHTML 必须能解析
    for n in names:
        if n.endswith((".opf", ".xhtml", ".ncx", ".xml")):
            try:
                ET.fromstring(z.read(n))
            except ET.ParseError as e:
                errs += fail(f"XML 解析失败 {n}: {e}")
    ok("全部 XML/XHTML/OPF/NCX 解析通过")

    # 4. OPF manifest
    opf_name = [n for n in names if n.endswith(".opf")][0]
    opf = ET.fromstring(z.read(opf_name))
    base = os.path.dirname(opf_name)

    manifest = {}
    for it in opf.findall(".//opf:manifest/opf:item", NS):
        manifest[it.get("id")] = it.get("href")

    t = opf.find(".//dc:title", NS)
    if t is None:
        errs += fail("缺少 dc:title")
    else:
        ok(f"标题: {t.text}")

    if opf.find(".//dc:identifier", NS) is None:
        errs += fail("缺少 dc:identifier")
    else:
        ok("存在 dc:identifier")

    missing = []
    for iid, href in manifest.items():
        full = os.path.normpath(os.path.join(base, href)).replace("\\", "/")
        if full not in names:
            missing.append((iid, href))
    if missing:
        errs += fail(f"manifest 引用了不存在的文件: {missing[:5]}")
    else:
        ok(f"manifest 全部 {len(manifest)} 项文件均存在")

    # 5. spine：正文只有 1 项（封面另算）
    spine_ids = [r.get("idref") for r in opf.findall(".//opf:spine/opf:itemref", NS)]
    bad = [s for s in spine_ids if s not in manifest]
    body_spine = [s for s in spine_ids if s != "cover"]
    if bad:
        errs += fail(f"spine 引用了未定义 id: {bad}")
    else:
        ok(f"spine 有 {len(spine_ids)} 项，引用全部有效")
    if len(body_spine) != 1:
        errs += fail(f"正文 spine 应为 1 项（单章节），实际 {len(body_spine)} 项")
    else:
        ok("正文 spine 只有 1 项 -> 所有图片在同一章节内")

    # 5b. 正文 XHTML 只应有一个（cover / nav 除外）
    body_docs = [h for h in manifest.values()
                 if h.endswith(".xhtml") and "nav" not in h and "cover" not in h]
    if len(body_docs) != 1:
        errs += fail(f"正文 XHTML 应为 1 个，实际 {len(body_docs)} 个: {body_docs}")
    else:
        ok(f"正文 XHTML 只有 1 个: {body_docs[0]}")

    # 6. 正文里所有 <img src> 必须存在
    img_missing = img_total = 0
    for iid in body_spine:
        href = manifest[iid]
        full = os.path.normpath(os.path.join(base, href)).replace("\\", "/")
        if full not in names:
            continue
        root = ET.fromstring(z.read(full))
        for img in root.iter("{http://www.w3.org/1999/xhtml}img"):
            img_total += 1
            src = img.get("src")
            if not src:
                img_missing += 1
                continue
            ip = os.path.normpath(os.path.join(os.path.dirname(full), src)).replace("\\", "/")
            if ip not in names:
                img_missing += 1
    if img_missing:
        errs += fail(f"{img_missing} 个 <img src> 指向不存在的图片")
    else:
        ok(f"正文内 {img_total} 个 <img src> 全部指向真实图片")

    # 6b. 目录锚点必须存在于正文
    if len(body_docs) == 1:
        bf = os.path.normpath(os.path.join(base, body_docs[0])).replace("\\", "/")
        anchors = {e.get("id") for e in ET.fromstring(z.read(bf)).iter() if e.get("id")}
        refs = 0
        for n in names:
            if not (n.endswith("nav.xhtml") or n.endswith(".ncx")):
                continue
            txt = z.read(n).decode("utf-8", "replace")
            for m in re.finditer(r'(?:href|src)="[^"#]*#([^"]+)"', txt):
                refs += 1
                if m.group(1) not in anchors:
                    errs += fail(f"{os.path.basename(n)} 锚点 #{m.group(1)} 不存在")
                    break
        if refs:
            ok(f"目录 {refs} 个锚点引用全部存在于正文中")

    # 7. 图片可解码
    try:
        imgs = [n for n in names if n.startswith("OEBPS/images/")]
        if not imgs:
            errs += fail("没有任何图片")
        else:
            for n in imgs:
                with Image.open(BytesIO(z.read(n))) as im:
                    im.verify()
            ok(f"{len(imgs)} 张图片全部可正常解码")
            with Image.open(BytesIO(z.read(imgs[0]))) as im:
                ok(f"首图: {im.width}x{im.height} {im.mode} {im.format}")
    except Exception as e:
        errs += fail(f"图片解码失败: {e}")

    # 7b. JPEG 必须是 SOF0（基线），不能是 SOF2（渐进）
    #     部分小屏阅读器不支持渐进式 JPEG。
    sof_counts = {}
    for n in names:
        if not n.lower().endswith((".jpg", ".jpeg")):
            continue
        m = _jpeg_sof(z.read(n))
        sof_counts[m] = sof_counts.get(m, 0) + 1
    if sof_counts:
        non_baseline = {k: v for k, v in sof_counts.items() if k != 0xC0}
        if non_baseline:
            bad = ", ".join(f"0x{k:02X}×{v}" for k, v in non_baseline.items())
            errs += fail(f"存在非基线 JPEG (应为 SOF0): {bad}")
        else:
            ok(f"{sum(sof_counts.values())} 张 JPEG 全部为 SOF0（基线）")

    # 8. nav
    if not [i for i, h in manifest.items() if "nav" in (h or "")]:
        errs += fail("缺少 nav 导航文档")
    else:
        ok("存在 nav 导航文档")

    # 9. 封面
    cover_items = [it for it in opf.findall(".//opf:manifest/opf:item", NS)
                   if (it.get("properties") or "") == "cover-image"]
    if cover_items:
        href = cover_items[0].get("href")
        full = os.path.normpath(os.path.join(base, href)).replace("\\", "/")
        if full not in names:
            errs += fail(f"封面图不存在: {href}")
        else:
            ok(f"封面图存在: {href}")
            if "OEBPS/text/cover.xhtml" in names:
                ok("封面页 XHTML 存在")
            else:
                errs += fail("有封面图但缺少 text/cover.xhtml")
            meta = opf.find(".//opf:metadata/opf:meta[@name='cover']", NS)
            if meta is None:
                errs += fail('缺少 <meta name="cover"> (旧阅读器不认封面)')
            else:
                ok("存在兼容 meta: name=cover")
            if spine_ids and spine_ids[0] == "cover":
                ok("封面是 spine 第一项")
            else:
                errs += fail(f"封面不是 spine 第一项: {spine_ids[:2]}")
    else:
        print("  [--]  无封面（未启用）")

    z.close()
    print()
    if errs:
        print(f"结果: {errs} 个问题")
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
