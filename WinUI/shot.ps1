# shot.ps1 - capture a window by PID (DPI-aware so coordinates match reality)
param(
  [Parameter(Mandatory=$true)][int]$TargetPid,
  [string]$Out = "D:\debug\imgpdf2epub\WinUI\_shot.png",
  [int]$Scale = 0,
  [double]$DpiScale = 2.0
)

Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public class Cap {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RR r);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after,
      int x, int y, int cx, int cy, uint flags);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  public struct RR { public int L, T, Rt, B; }
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  public static readonly IntPtr TOPMOST = new IntPtr(-1);
  public static readonly IntPtr NOTOPMOST = new IntPtr(-2);
  public static IntPtr Found = IntPtr.Zero;
  public static IntPtr Pick(uint pid) {
    Found = IntPtr.Zero;
    EnumWindows((h, l) => {
      uint p; GetWindowThreadProcessId(h, out p);
      if (p == pid && IsWindowVisible(h)) {
        var sb = new StringBuilder(300); GetWindowText(h, sb, 300);
        if (sb.Length > 0) { Found = h; return false; }
      }
      return true;
    }, IntPtr.Zero);
    return Found;
  }
}
'@

# 关键：声明 DPI 感知，否则 GetWindowRect / CopyFromScreen 会用虚拟化坐标
[Cap]::SetProcessDPIAware() | Out-Null

$h = [Cap]::Pick([uint32]$TargetPid)
if ($h -eq [IntPtr]::Zero) { Write-Host "window not found"; exit 1 }

[Cap]::ShowWindow($h, 9) | Out-Null
[Cap]::SetWindowPos($h, [Cap]::TOPMOST, 8, 8, 0, 0, 0x0001 -bor 0x0002 -bor 0x0040) | Out-Null
[Cap]::SetForegroundWindow($h) | Out-Null
Start-Sleep -Milliseconds 1500

$r = New-Object Cap+RR
[Cap]::GetWindowRect($h, [ref]$r) | Out-Null
# PowerShell 进程不是 DPI 感知的，GetWindowRect 返回的是虚拟化坐标，
# 而 CopyFromScreen 走的是物理像素，所以要按缩放系数换算。
$sx = [int][math]::Round($r.L  * $DpiScale)
$sy = [int][math]::Round($r.T  * $DpiScale)
$w  = [int][math]::Round(($r.Rt - $r.L) * $DpiScale)
$ht = [int][math]::Round(($r.B  - $r.T) * $DpiScale)
Write-Host "rect(px, scaled x$DpiScale): $sx,$sy  ${w}x${ht}"

Add-Type -AssemblyName System.Drawing
$bmp = New-Object System.Drawing.Bitmap $w, $ht
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($sx, $sy, 0, 0, $bmp.Size)
$g.Dispose()

if ($Scale -gt 0 -and $Scale -lt $w) {
  $nh = [int]($ht * $Scale / $w)
  $small = New-Object System.Drawing.Bitmap $Scale, $nh
  $g2 = [System.Drawing.Graphics]::FromImage($small)
  $g2.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $g2.DrawImage($bmp, 0, 0, $Scale, $nh)
  $g2.Dispose()
  $small.Save($Out)
  $small.Dispose()
  Write-Host "saved (scaled): $Out  ${Scale}x${nh}"
} else {
  $bmp.Save($Out)
  Write-Host "saved: $Out  ${w}x${ht}"
}

$bmp.Dispose()
[Cap]::SetWindowPos($h, [Cap]::NOTOPMOST, 0, 0, 0, 0, 0x0001 -bor 0x0002) | Out-Null
