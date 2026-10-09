using System;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.UI.Composition.SystemBackdrops;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.ApplicationModel.DataTransfer;
using Windows.Graphics;
using Windows.Storage;
using Windows.Storage.Pickers;
using WinRT.Interop;

namespace ImgPdf2Epub;

/// <summary>列表里的一行。</summary>
public sealed class FileItem : System.ComponentModel.INotifyPropertyChanged
{
    public string Path { get; init; } = "";
    public string Name { get; init; } = "";
    public string SizeText { get; init; } = "";

    private string _status = "等待";
    public string Status
    {
        get => _status;
        set
        {
            _status = value;
            PropertyChanged?.Invoke(this,
                new System.ComponentModel.PropertyChangedEventArgs(nameof(Status)));
        }
    }

    public event System.ComponentModel.PropertyChangedEventHandler? PropertyChanged;

    public void SetStatus(string s) => Status = s;
}

public sealed partial class MainWindow : Window
{
    private readonly ObservableCollection<FileItem> _items = new();
    private CancellationTokenSource? _cts;
    private bool _running;
    private string _lastOutDir = "";

    // 设备下拉项（与 Python 端保持一致）
    // 最后一项「自定义」由用户自己填屏幕尺寸，Custom=true 时读 TextBox
    private static readonly (string Label, int W, int H, bool Custom)[] Devices =
    {
        ("通用（不缩放）", 0, 0, false),
        ("EEGO A4", 552, 768, false),
        ("Pico", 684, 1216, false),
        ("自定义", 0, 0, true),
    };

    private static readonly string[] Headrooms = { "1.00x", "1.15x", "1.25x", "1.50x", "2.00x" };
    private static readonly string[] Resamples = { "双线性", "Lanczos" };
    private static readonly string[] Formats = { "原始格式", "统一 JPG", "统一 PNG" };
    private static readonly string[] Scales =
        { "保持原分辨率", "限制宽≤1600", "限制宽≤1200", "限制宽≤1000", "限制宽≤800" };
    private static readonly string[] Displays = { "自适应页宽", "原始像素尺寸" };

    public MainWindow()
    {
        InitializeComponent();
        Title = "图片 PDF 转 EPUB";

        ConfigureWindowChrome();

        // 窗口尺寸不能在构造函数里定：那时窗口还没真正创建，
        // 后面会被系统的默认尺寸覆盖。改成首次激活后再设，才生效。
        Activated += OnFirstActivated;

        // 下拉初始化
        foreach (var d in Devices) CboDevice.Items.Add(DeviceLabel(d));
        CboDevice.SelectedIndex = 1;                 // 默认 EEGO A4
        foreach (var s in Headrooms) CboHeadroom.Items.Add(s);
        CboHeadroom.SelectedIndex = 0;
        foreach (var s in Resamples) CboResample.Items.Add(s);
        CboResample.SelectedIndex = 0;
        foreach (var s in Formats) CboFormat.Items.Add(s);
        CboFormat.SelectedIndex = 0;
        foreach (var s in Formats) CboGenFormat.Items.Add(s);
        CboGenFormat.SelectedIndex = 0;
        foreach (var s in Scales) CboScale.Items.Add(s);
        CboScale.SelectedIndex = 0;
        foreach (var s in Displays) CboDisplay.Items.Add(s);
        CboDisplay.SelectedIndex = 0;

        FileList.ItemsSource = _items;
        _items.CollectionChanged += (_, _) => UpdateEmptyHint();

        UpdateDeviceUi();
        UpdateEmptyHint();
    }

    // ---------------------------------------------------------------- 毛玻璃

    /// <summary>
    /// 自检模式：环境变量 IMGPDF2EPUB_SELFTEST 指向一个 PDF 时，
    /// 启动后自动添加该文件并开始转换，把过程写进诊断日志。
    /// 目的：验证打包后（尤其 MSIX 里）能否找到并调起 Python 后端。
    /// </summary>
    private async void RunSelfTestIfRequested()
    {
        string pdf = Environment.GetEnvironmentVariable("IMGPDF2EPUB_SELFTEST") ?? "";
        if (pdf.Length == 0) return;

        Diag($"SELFTEST start, pdf={pdf}");
        Diag($"  baseDir={AppContext.BaseDirectory}");
        Diag($"  cli={FindCli()}");
        Diag($"  packaged={IsPackaged()}");

        if (!File.Exists(pdf)) { Diag("SELFTEST FAIL: pdf 不存在"); return; }

        AddFile(pdf);
        if (_items.Count == 0) { Diag("SELFTEST FAIL: 未能加入列表"); return; }

        // 输出到源文件同目录，便于检查产物
        TxtOutDir.Text = System.IO.Path.GetDirectoryName(pdf) ?? "";

        await Task.Delay(600);
        OnStart(this, new RoutedEventArgs());

        // 等转换结束
        for (int i = 0; i < 120 && _running; i++) await Task.Delay(500);
        for (int i = 0; i < 6; i++) await Task.Delay(500);

        string status = _items.Count > 0 ? _items[0].Status : "?";
        Diag($"SELFTEST end, status={status}");
        Diag($"  outDir={TxtOutDir.Text}");

        // 把最后一次的日志尾部也记下来，便于判断失败原因
        try
        {
            string tail = TxtLog.Text;
            if (tail.Length > 1200) tail = tail[^1200..];
            Diag("SELFTEST log tail:\n" + tail);
        }
        catch { }
    }

    /// <summary>当前进程是否运行在 MSIX 包身份下。</summary>
    private static bool IsPackaged()
    {
        try
        {
            uint len = 0;
            int hr = GetCurrentPackageFullName(ref len, null);
            return hr != 15700;   // APPMODEL_ERROR_NO_PACKAGE
        }
        catch { return false; }
    }

    [System.Runtime.InteropServices.DllImport("kernel32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
    private static extern int GetCurrentPackageFullName(ref uint length, StringBuilder? name);

    private bool _initialSized;

    private void OnFirstActivated(object sender, WindowActivatedEventArgs e)
    {
        if (_initialSized) return;
        _initialSized = true;
        Activated -= OnFirstActivated;

        // 首次激活时窗口还在做初始布局，尺寸会被覆盖；
        // 排到消息队列后面再设，才能生效。
        DispatcherQueue.TryEnqueue(Microsoft.UI.Dispatching.DispatcherQueuePriority.Low,
            ApplyInitialSize);

        // 自检：设置 IMGPDF2EPUB_SELFTEST=<pdf路径> 后启动会自动跑一次转换，
        // 结果写进诊断日志。用于验证打包后的环境（尤其是打包成 MSIX 时）。
        DispatcherQueue.TryEnqueue(Microsoft.UI.Dispatching.DispatcherQueuePriority.Low,
            RunSelfTestIfRequested);
    }

    /// <summary>
    /// 首次显示时把窗口设大一些：按屏幕工作区的比例取尺寸（并设上限），然后居中。
    /// 尺寸以 DIP 为单位计算，再换算成物理像素——AppWindow 用的是物理像素，
    /// 在高 DPI 屏上直接用物理值会导致窗口看起来很小。
    /// </summary>
    private void ApplyInitialSize()
    {
        try
        {
            var area = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary);
            var work = area.WorkArea;   // 物理像素

            double scale = GetDpiScale();

            // 换算成 DIP 后按比例取，并设上限，避免超大屏上窗口过分夸张
            int wDip = (int)Math.Round(Math.Min(1100, work.Width / scale * 0.92));
            int hDip = (int)Math.Round(Math.Min(860, work.Height / scale * 0.94));
            wDip = Math.Max(wDip, 760);
            hDip = Math.Max(hDip, 560);

            int w = (int)Math.Round(wDip * scale);
            int h = (int)Math.Round(hDip * scale);

            var before = AppWindow.Size;
            AppWindow.Resize(new SizeInt32(w, h));
            var after = AppWindow.Size;

            Diag($"scale={scale:0.##} work={work.Width}x{work.Height}(px) " +
                 $"want={wDip}x{hDip}(dip)={w}x{h}(px) " +
                 $"before={before.Width}x{before.Height} after={after.Width}x{after.Height}");

            // 居中显示（工作区坐标同为物理像素）
            int x = work.X + Math.Max(0, (work.Width - w) / 2);
            int y = work.Y + Math.Max(0, (work.Height - h) / 2);
            AppWindow.Move(new PointInt32(x, y));
        }
        catch (Exception ex)
        {
            Log($"设置窗口尺寸失败：{ex.Message}");
            Diag("EX: " + ex);
        }
    }

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern uint GetDpiForWindow(IntPtr hwnd);

    /// <summary>当前窗口所在显示器的缩放系数（1.0 = 100%）。</summary>
    private double GetDpiScale()
    {
        try
        {
            uint dpi = GetDpiForWindow(WindowNative.GetWindowHandle(this));
            if (dpi >= 48) return dpi / 96.0;
        }
        catch { }
        return 1.0;
    }

    /// <summary>把诊断信息写到临时文件，便于排查窗口尺寸这类看不见的问题。</summary>
    private static void Diag(string msg)
    {
        try
        {
            File.AppendAllText(
                System.IO.Path.Combine(System.IO.Path.GetTempPath(), "imgpdf2epub-ui.log"),
                $"[{DateTime.Now:HH:mm:ss.fff}] {msg}{Environment.NewLine}",
                Encoding.UTF8);
        }
        catch { }
    }

    /// <summary>
    /// 按微软的标准做法配置窗口：
    ///   - 背景用 Mica（Base，最淡的那种，和「设置」一致）。
    ///     BaseAlt 是层叠变体，颜色更重，日常应用不用它。
    ///   - 隐藏系统标题栏，把内容延伸到标题栏区域，再用 TitleBar 控件
    ///     自绘一条与界面同色的标题栏（微软推荐做法）。
    /// </summary>
    private void ConfigureWindowChrome()
    {
        try
        {
            if (MicaController.IsSupported())
            {
                SystemBackdrop = new MicaBackdrop { Kind = MicaKind.Base };
            }
            else if (DesktopAcrylicController.IsSupported())
            {
                // 老系统退而求其次
                SystemBackdrop = new DesktopAcrylicBackdrop();
            }
        }
        catch (Exception ex)
        {
            Log($"背景材质不可用，已回退普通背景：{ex.Message}");
        }

        try
        {
            ExtendsContentIntoTitleBar = true;
            SetTitleBar(AppTitleBar);
        }
        catch (Exception ex)
        {
            Log($"标题栏扩展失败：{ex.Message}");
        }

        // 非打包应用必须显式设置窗口图标，否则任务栏/标题栏是默认图标
        try
        {
            string ico = System.IO.Path.Combine(AppContext.BaseDirectory, "app.ico");
            if (File.Exists(ico)) AppWindow.SetIcon(ico);
        }
        catch (Exception ex)
        {
            Log($"设置窗口图标失败：{ex.Message}");
        }
    }

    private static string DeviceLabel((string Label, int W, int H, bool Custom) d) =>
        d.Custom || d.W == 0 ? d.Label : $"{d.Label} ({d.W}x{d.H})";

    // ---------------------------------------------------------------- 界面联动

    private (string Label, int W, int H, bool Custom) CurrentDevice()
    {
        int i = CboDevice.SelectedIndex;
        return i >= 0 && i < Devices.Length ? Devices[i] : Devices[0];
    }

    /// <summary>是否是「按屏幕缩放」这一类模式（含自定义）。</summary>
    private bool IsDeviceMode
    {
        get { var d = CurrentDevice(); return d.Custom || d.W > 0; }
    }

    /// <summary>当前选的是不是「自定义」（需要用户手填屏幕尺寸）。</summary>
    private bool IsCustomDevice => CurrentDevice().Custom;

    private void OnDeviceChanged(object sender, SelectionChangedEventArgs e)
    {
        var d = CurrentDevice();
        if (d.W > 0)
        {
            TxtScreen.Text = $"{d.W}x{d.H}";
        }
        else if (d.Custom)
        {
            // 切到自定义时，若当前内容不是有效尺寸，给个可编辑的起点
            var (w, h) = ParseScreen(d);
            if (w <= 1 || h <= 1) TxtScreen.Text = "800x1200";
        }
        UpdateDeviceUi();
    }

    /// <summary>用户手改屏幕尺寸时实时刷新提示。</summary>
    private void OnScreenChanged(object sender, TextChangedEventArgs e) => UpdateDeviceUi();

    private void OnHeadroomChanged(object sender, SelectionChangedEventArgs e) => UpdateDeviceUi();

    private void OnFormatChanged(object sender, SelectionChangedEventArgs e) => UpdateDeviceUi();

    private void OnQualityChanged(object sender, Microsoft.UI.Xaml.Controls.Primitives.RangeBaseValueChangedEventArgs e)
    {
        if (TxtQuality != null) TxtQuality.Text = ((int)e.NewValue).ToString();
    }

    private void UpdateDeviceUi()
    {
        if (TxtDeviceHint == null) return;   // 初始化早期事件保护

        bool dev = IsDeviceMode;
        bool custom = IsCustomDevice;

        CboHeadroom.IsEnabled = dev;
        CboResample.IsEnabled = dev;
        CboFormat.IsEnabled = dev;
        TxtScreen.IsEnabled = dev;

        // 只有「统一 JPG」才需要质量
        bool jpg = dev && CboFormat.SelectedIndex == 1;
        SldQuality.IsEnabled = jpg;
        TxtQuality.Opacity = jpg ? 1.0 : 0.4;

        // 通用选项仅「通用」模式可用
        CboGenFormat.IsEnabled = !dev;
        CboScale.IsEnabled = !dev;
        CboDisplay.IsEnabled = !dev;
        ChkGray.IsEnabled = !dev;
        ChkTrim.IsEnabled = !dev;

        if (dev)
        {
            var d = CurrentDevice();
            var (w, h) = ParseScreen(d);
            double hr = Headroom();
            int tw = (int)Math.Round(w * hr);
            string fmt = CboFormat.SelectedItem?.ToString() ?? "原始格式";
            string note = fmt == "原始格式" ? "保持原格式" : $"统一转为 {fmt[^3..]}";

            if (custom && !IsScreenTextValid())
            {
                // 输入不合法时明确提示，避免用户以为生效了
                TxtDeviceHint.Text =
                    "自定义：请在「屏幕」里填入宽x高，例如 800x1200 或 600x800。";
                return;
            }

            string who = custom ? "自定义" : d.Label;
            TxtDeviceHint.Text =
                $"{who} ({w}x{h})：按宽度缩放到 {tw}px（屏宽 {w} × 倍数 {hr:0.00}），{note}；" +
                "不转灰度、不裁边、不放大。首张图片自动作封面。";
        }
        else
        {
            TxtDeviceHint.Text = "不做缩放，保持原图尺寸与格式。";
        }
    }

    /// <summary>「屏幕」输入框里是不是一个合法的 宽x高。</summary>
    private bool IsScreenTextValid()
    {
        try
        {
            var parts = TxtScreen.Text.ToLower().Replace("×", "x").Split('x');
            return parts.Length == 2
                   && int.TryParse(parts[0].Trim(), out int w) && w > 0
                   && int.TryParse(parts[1].Trim(), out int h) && h > 0;
        }
        catch { return false; }
    }

    private (int W, int H) ParseScreen((string Label, int W, int H, bool Custom) d)
    {
        try
        {
            var parts = TxtScreen.Text.ToLower().Replace("×", "x").Split('x');
            return (Math.Max(1, int.Parse(parts[0].Trim())),
                    Math.Max(1, int.Parse(parts[1].Trim())));
        }
        catch
        {
            // 已知设备回落到自身尺寸；自定义/通用回落到一个常用值
            return d.W > 0 ? (d.W, d.H) : (800, 1200);
        }
    }

    private double Headroom()
    {
        var s = CboHeadroom.SelectedItem?.ToString()?.TrimEnd('x', 'X');
        return double.TryParse(s, out var v) ? v : 1.0;
    }

    private void UpdateEmptyHint()
    {
        if (EmptyHint != null) EmptyHint.Visibility = _items.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        if (TxtFileCount != null) TxtFileCount.Text = _items.Count == 0 ? "" : $"共 {_items.Count} 个";
    }

    // ---------------------------------------------------------------- 添加文件

    private async void OnAddFiles(object sender, RoutedEventArgs e)
    {
        var picker = new FileOpenPicker();
        InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
        picker.ViewMode = PickerViewMode.List;
        picker.SuggestedStartLocation = PickerLocationId.DocumentsLibrary;
        picker.FileTypeFilter.Add(".pdf");

        var files = await picker.PickMultipleFilesAsync();
        if (files == null) return;
        foreach (var f in files) AddFile(f.Path);
    }

    private async void OnAddFolder(object sender, RoutedEventArgs e)
    {
        var picker = new FolderPicker();
        InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
        picker.SuggestedStartLocation = PickerLocationId.DocumentsLibrary;
        picker.FileTypeFilter.Add("*");

        var dir = await picker.PickSingleFolderAsync();
        if (dir == null) return;
        AddFolder(dir.Path);
    }

    private void AddFolder(string dir)
    {
        try
        {
            int before = _items.Count;
            foreach (var f in Directory.EnumerateFiles(dir, "*.pdf", SearchOption.TopDirectoryOnly))
                AddFile(f);
            if (_items.Count == before) Log($"文件夹里没有 PDF：{dir}");
        }
        catch (Exception ex)
        {
            Log($"读取文件夹失败：{ex.Message}");
        }
    }

    private void AddFile(string path)
    {
        try
        {
            path = System.IO.Path.GetFullPath(path);
            if (!path.EndsWith(".pdf", StringComparison.OrdinalIgnoreCase)) return;
            foreach (var it in _items)
                if (string.Equals(it.Path, path, StringComparison.OrdinalIgnoreCase)) return;

            var fi = new FileInfo(path);
            _items.Add(new FileItem
            {
                Path = path,
                Name = fi.Name,
                SizeText = fi.Length >= 1048576
                    ? $"{fi.Length / 1048576.0:0.0} MB"
                    : $"{fi.Length / 1024.0:0} KB",
            });
        }
        catch (Exception ex)
        {
            Log($"添加失败 {path}：{ex.Message}");
        }
    }

    // ---------------------------------------------------------------- 拖放

    private void OnDragOver(object sender, DragEventArgs e)
    {
        if (e.DataView.Contains(StandardDataFormats.StorageItems))
        {
            e.AcceptedOperation = DataPackageOperation.Copy;
            e.DragUIOverride.Caption = "添加到列表";
            e.DragUIOverride.IsCaptionVisible = true;
        }
    }

    private async void OnDropFiles(object sender, DragEventArgs e)
    {
        if (!e.DataView.Contains(StandardDataFormats.StorageItems)) return;
        var items = await e.DataView.GetStorageItemsAsync();
        int added = 0;
        foreach (var it in items)
        {
            if (it is StorageFile sf &&
                sf.Name.EndsWith(".pdf", StringComparison.OrdinalIgnoreCase))
            {
                AddFile(sf.Path); added++;
            }
            else if (it is StorageFolder folder)
            {
                AddFolder(folder.Path); added++;
            }
        }
        if (added > 0) Log($"拖入 {added} 项。");
        else Log("拖入的内容里没有 PDF。");
    }

    private void OnRemoveSelected(object sender, RoutedEventArgs e)
    {
        var sel = new System.Collections.Generic.List<FileItem>();
        foreach (var o in FileList.SelectedItems) sel.Add((FileItem)o);
        foreach (var s in sel) _items.Remove(s);
    }

    private void OnClearAll(object sender, RoutedEventArgs e)
    {
        _items.Clear();
        Log("已清空列表。");
    }

    // ---------------------------------------------------------------- 输出目录

    private async void OnBrowseOutDir(object sender, RoutedEventArgs e)
    {
        var picker = new FolderPicker();
        InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
        picker.SuggestedStartLocation = PickerLocationId.DocumentsLibrary;
        picker.FileTypeFilter.Add("*");
        var dir = await picker.PickSingleFolderAsync();
        if (dir != null) TxtOutDir.Text = dir.Path;
    }

    private void OnOpenOutDir(object sender, RoutedEventArgs e)
    {
        string dir = TxtOutDir.Text.Trim();
        if (dir.Length == 0)
        {
            if (_lastOutDir.Length > 0) dir = _lastOutDir;
            else if (_items.Count > 0) dir = System.IO.Path.GetDirectoryName(_items[0].Path) ?? "";
        }
        if (dir.Length == 0 || !Directory.Exists(dir))
        {
            Log("还没有可打开的输出目录。");
            return;
        }
        Process.Start(new ProcessStartInfo("explorer.exe", $"\"{dir}\"") { UseShellExecute = true });
    }

    private void OnClearLog(object sender, RoutedEventArgs e) => TxtLog.Text = "";

    // ---------------------------------------------------------------- 转换

    private void OnCancel(object sender, RoutedEventArgs e)
    {
        try { _cts?.Cancel(); } catch { }
        Log("已请求取消…");
        BtnCancel.IsEnabled = false;
    }

    private async void OnStart(object sender, RoutedEventArgs e)
    {
        if (_running) return;
        if (_items.Count == 0) { Log("请先添加至少一个 PDF 文件。"); return; }

        // 自定义设备必须填一个合法的屏幕尺寸，避免悄悄用默认值转换
        if (IsCustomDevice && !IsScreenTextValid())
        {
            Log("「自定义」设备需要在「屏幕」里填入宽x高，例如 800x1200。");
            TxtScreen.Focus(FocusState.Programmatic);
            return;
        }

        string exe = FindCli();
        if (exe.Length == 0)
        {
            Log("找不到后端程序（imgpdf2epub.exe / 图片PDF转EPUB.exe）。");
            Log("请把本程序放在与后端 exe 相同的目录，或设置环境变量 IMGPDF2EPUB_CLI。");
            return;
        }

        _running = true;
        _cts = new CancellationTokenSource();
        BtnStart.IsEnabled = false;
        BtnCancel.IsEnabled = true;
        Progress.Value = 0;
        TxtStatus.Text = "准备中…";
        foreach (var it in _items) it.SetStatus("等待");

        Log("=".PadRight(46, '='));
        Log($"后端：{exe}");
        Log($"开始转换，共 {_items.Count} 个文件。");

        try
        {
            for (int i = 0; i < _items.Count; i++)
            {
                if (_cts.IsCancellationRequested) { _items[i].SetStatus("已取消"); break; }
                await ConvertOne(exe, _items[i], i + 1, _items.Count, _cts.Token);
            }
        }
        finally
        {
            _running = false;
            BtnStart.IsEnabled = true;
            BtnCancel.IsEnabled = false;
            if (!_cts.IsCancellationRequested) Progress.Value = 100;
        }
    }

    private async Task ConvertOne(string exe, FileItem item, int idx, int total, CancellationToken ct)
    {
        item.SetStatus("转换中…");
        string outDir = TxtOutDir.Text.Trim();
        var args = new System.Collections.Generic.List<string> { item.Path };

        if (outDir.Length > 0)
        {
            try { Directory.CreateDirectory(outDir); } catch { }
            args.Add("-o"); args.Add(outDir);
            _lastOutDir = outDir;
        }
        else
        {
            _lastOutDir = System.IO.Path.GetDirectoryName(item.Path) ?? "";
        }

        // 设备 / 通用参数
        if (IsDeviceMode)
        {
            var d = CurrentDevice();
            var (w, h) = ParseScreen(d);
            args.Add("--screen"); args.Add($"{w}x{h}");
            args.Add("--headroom"); args.Add(Headroom().ToString("0.00", System.Globalization.CultureInfo.InvariantCulture));
            args.Add("--resample");
            args.Add(CboResample.SelectedIndex == 1 ? "lanczos" : "bilinear");
            int fi = CboFormat.SelectedIndex;
            args.Add("--format");
            args.Add(fi == 1 ? "jpg" : fi == 2 ? "png" : "auto");
            if (fi == 1)
            {
                args.Add("--device-quality");
                args.Add(((int)SldQuality.Value).ToString());
            }
            args.Add("--device-mode");
        }
        else
        {
            var scale = new[] { 0, 1600, 1200, 1000, 800 }[
                Math.Clamp(CboScale.SelectedIndex, 0, 4)];
            if (scale > 0) { args.Add("--max-dim"); args.Add(scale.ToString()); }
            args.Add("--format");
            args.Add(CboGenFormat.SelectedIndex switch { 1 => "jpg", 2 => "png", _ => "auto" });
            if (ChkGray.IsChecked == true) args.Add("--gray");
            if (ChkTrim.IsChecked == true) args.Add("--trim");
            if (CboDisplay.SelectedIndex == 1) args.Add("--native");
        }
        if (ChkCover.IsChecked != true) args.Add("--no-cover");
        args.Add("--json-progress");

        var psi = new ProcessStartInfo(exe)
        {
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        foreach (var a in args) psi.ArgumentList.Add(a);

        using var proc = new Process { StartInfo = psi, EnableRaisingEvents = true };
        var stderr = new StringBuilder();

        proc.OutputDataReceived += (_, ev) =>
        {
            if (string.IsNullOrWhiteSpace(ev.Data)) return;
            string line = ev.Data!;
            // JSON 进度行
            if (line.StartsWith("{") && line.Contains("\"percent\""))
            {
                try
                {
                    using var doc = JsonDocument.Parse(line);
                    var root = doc.RootElement;
                    int pct = root.GetProperty("percent").GetInt32();
                    string msg = root.TryGetProperty("message", out var m) ? m.GetString() ?? "" : "";
                    DispatcherQueue.TryEnqueue(() =>
                    {
                        int overall = (int)(((idx - 1) + pct / 100.0) / total * 100);
                        Progress.Value = overall;
                        TxtStatus.Text = $"[{idx}/{total}] {pct}%  {msg}";
                    });
                }
                catch { }
                return;
            }
            DispatcherQueue.TryEnqueue(() => Log($"  {line}"));
        };
        proc.ErrorDataReceived += (_, ev) =>
        {
            if (!string.IsNullOrWhiteSpace(ev.Data))
            {
                lock (stderr) stderr.AppendLine(ev.Data);
            }
        };

        proc.Start();
        proc.BeginOutputReadLine();
        proc.BeginErrorReadLine();

        await Task.Run(() => proc.WaitForExit(), ct);

        if (ct.IsCancellationRequested && !proc.HasExited)
        {
            try { proc.Kill(true); } catch { }
        }

        int code = proc.HasExited ? proc.ExitCode : -1;
        if (code == 0)
        {
            item.SetStatus("完成 ✓");
        }
        else
        {
            item.SetStatus("失败 ✗");
            string err;
            lock (stderr) err = stderr.ToString().Trim();
            if (err.Length > 0) DispatcherQueue.TryEnqueue(() => Log($"  失败：{err}"));
        }
    }

    /// <summary>找后端 exe：优先同目录，其次环境变量。</summary>
    private static string FindCli()
    {
        string env = Environment.GetEnvironmentVariable("IMGPDF2EPUB_CLI") ?? "";
        if (env.Length > 0 && File.Exists(env)) return env;

        string baseDir = AppContext.BaseDirectory;
        string[] names = { "图片PDF转EPUB.exe", "imgpdf2epub.exe", "app.exe" };
        foreach (var n in names)
        {
            string p = System.IO.Path.Combine(baseDir, n);
            if (File.Exists(p)) return p;
        }
        return "";
    }

    private void Log(string text)
    {
        if (TxtLog == null) return;
        TxtLog.Text += text + Environment.NewLine;
        LogScroll?.ChangeView(null, LogScroll.ScrollableHeight, null);
    }
}
