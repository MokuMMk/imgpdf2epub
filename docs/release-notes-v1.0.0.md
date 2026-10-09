## 图片 PDF 转 EPUB v1.0.0

把**纯图片（扫描版）PDF** 转成 EPUB 电子书。不做 OCR，直接把每一页的图像抽出并封装成标准 EPUB 3。

原生 **WinUI 3** 界面（C# + XAML），Python 转换引擎。

---

### 下载

**`ImgPdf2Epub-Setup-1.0.0.exe`**（98.3 MB）

- ✅ 不需要管理员权限
- ✅ 不需要证书
- ✅ 不需要装 .NET / Python / 任何运行环境

**双击即可**，装完自动启动。会创建桌面和开始菜单快捷方式，可在「设置 → 应用」中卸载。

系统要求：Windows 10 1903+ / Windows 11（64 位）

> 附件名用英文是因为 GitHub 会过滤非 ASCII 字符。装好后程序名与快捷方式仍是「图片 PDF 转 EPUB」。

---

### 主要特性

- **原生 WinUI 3 界面** —— Mica 毛玻璃背景、自绘标题栏、跟随系统深浅色
- **拖拽导入** —— 直接拖 PDF 文件或文件夹进来
- **单章节输出** —— 所有页面放在同一个 XHTML 里，避免阅读器频繁加载新章节造成卡顿
- **小屏设备预设** —— 内置 EEGO A4 (552×768)、Pico (684×1216)，也可自定义屏幕尺寸
- **只缩不放** —— 图片小于目标宽度时原样透传，连重编码都不做
- **基线 JPEG（SOF0）** —— 规避部分小屏阅读器不支持渐进式 JPEG 的问题
- **自动封面** —— 第一页设为封面，EPUB3 `cover-image` 与旧版 `meta`/`guide` 双声明
- **不联网** —— 全程本地处理

### 输出格式

| 选项 | 说明 |
| --- | --- |
| 原始格式 | 保持原图格式不动（默认） |
| 统一 JPG | 全部转基线 JPEG（SOF0），可调质量 |
| 统一 PNG | 全部转 PNG，无损但体积约为 JPEG 的 2.2 倍 |

### 缩放算法

默认**双线性（bilinear）**，比 Lanczos 更柔和，文字边缘不易产生振铃。可切换为 Lanczos。

---

### 命令行

安装后 `%LOCALAPPDATA%\Programs\ImgPdf2Epub\图片PDF转EPUB.exe` 同时支持命令行：

```bat
图片PDF转EPUB.exe book.pdf --eego                   :: EEGO A4，552px
图片PDF转EPUB.exe book.pdf --pico                   :: Pico，684px
图片PDF转EPUB.exe book.pdf --device-mode --screen 800x1200
图片PDF转EPUB.exe book.pdf --eego --format jpg      :: 转 JPEG
图片PDF转EPUB.exe C:\扫描书 --recursive --eego      :: 批量
```

---

### 关于许可证

本项目以 **AGPL-3.0** 发布，因为使用了 [PyMuPDF](https://github.com/pymupdf/PyMuPDF)（AGPL-3.0 / 商业授权双许可）。
AGPL 具有传染性，因此整个项目必须采用同一协议。

若需闭源或商业使用，可向 Artifex 购买 PyMuPDF 商业授权，或将 PDF 解析层替换为
宽松协议的库（如 [pypdfium2](https://github.com/pypdfium2-team/pypdfium2)，Apache-2.0）。

---

**完整源码**：https://github.com/MokuMMk/imgpdf2epub
