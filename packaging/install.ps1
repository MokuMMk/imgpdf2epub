# install.ps1 - 免管理员、免证书的安装逻辑
# 装到 %LOCALAPPDATA%\Programs\图片PDF转EPUB，建快捷方式，登记卸载项

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$AppName    = '图片 PDF 转 EPUB'
$AppId      = 'ImgPdf2Epub'
$Ver        = '1.0.0'
$Publisher  = 'Moku Yui'
$ExeName    = '图片PDF转EPUB-界面.exe'

$src  = Split-Path -Parent $MyInvocation.MyCommand.Path
$zip  = Join-Path $src 'payload.zip'
$dest = Join-Path $env:LOCALAPPDATA "Programs\$AppId"

function Say($m) { Write-Host $m }

Say ''
Say "  正在安装 $AppName ..."
Say "  安装位置: $dest"
Say ''

if (-not (Test-Path $zip)) { Say "  [错误] 找不到 payload.zip"; exit 1 }

# --- 1) 解压 ---
if (Test-Path $dest) {
    Say '  检测到旧版本，正在覆盖…'
    Get-ChildItem $dest -Recurse -File | ForEach-Object { $_.IsReadOnly = $false }
    Remove-Item -Recurse -Force $dest -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path $dest | Out-Null

Add-Type -AssemblyName System.IO.Compression.FileSystem
Say '  正在释放文件（约 200 MB，需要十几秒）…'
[System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $dest)

$exe = Join-Path $dest $ExeName
if (-not (Test-Path $exe)) { Say "  [错误] 解压后找不到 $ExeName"; exit 1 }
Say '  文件释放完成'

# --- 2) 快捷方式 ---
Say '  正在创建快捷方式…'
$ws = New-Object -ComObject WScript.Shell

function New-Lnk($path) {
    $sc = $ws.CreateShortcut($path)
    $sc.TargetPath       = $exe
    $sc.WorkingDirectory = $dest
    $sc.IconLocation     = "$exe,0"
    $sc.Description      = '纯图片 PDF 转 EPUB'
    $sc.Save()
}

$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
New-Lnk (Join-Path $startMenu "$AppName.lnk")

$desktop = [Environment]::GetFolderPath('Desktop')
New-Lnk (Join-Path $desktop "$AppName.lnk")

# --- 3) 卸载脚本（放在安装目录里）---
$uninstallPs1 = Join-Path $dest 'uninstall.ps1'
$uninstallBody = @"
`$dest = Split-Path -Parent `$MyInvocation.MyCommand.Path
`$AppName = '$AppName'
`$AppId = '$AppId'

Remove-Item "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\`$AppId" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path `$env:APPDATA "Microsoft\Windows\Start Menu\Programs\`$AppName.lnk") -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path ([Environment]::GetFolderPath('Desktop')) "`$AppName.lnk") -Force -ErrorAction SilentlyContinue
Remove-Item "`$env:LOCALAPPDATA\Programs\`$AppId" -Recurse -Force -ErrorAction SilentlyContinue
"@
Set-Content -Path $uninstallPs1 -Value $uninstallBody -Encoding UTF8

# --- 4) 登记到「应用和功能」 ---
Say '  正在登记卸载信息…'
$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppId"
New-Item -Path $key -Force | Out-Null
$sizeMb = [int]((Get-ChildItem $dest -Recurse -File | Measure-Object Length -Sum).Sum / 1KB)
Set-ItemProperty -Path $key -Name DisplayName     -Value $AppName
Set-ItemProperty -Path $key -Name DisplayVersion  -Value $Ver
Set-ItemProperty -Path $key -Name Publisher       -Value $Publisher
Set-ItemProperty -Path $key -Name DisplayIcon     -Value "$exe,0"
Set-ItemProperty -Path $key -Name InstallLocation -Value $dest
Set-ItemProperty -Path $key -Name EstimatedSize   -Value $sizeMb -Type DWord
Set-ItemProperty -Path $key -Name NoModify        -Value 1 -Type DWord
Set-ItemProperty -Path $key -Name NoRepair        -Value 1 -Type DWord
Set-ItemProperty -Path $key -Name UninstallString -Value `
  "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$uninstallPs1`""

Say ''
Say "  安装完成！"
Say "  已创建：桌面快捷方式、开始菜单项"
Say "  可在「设置 → 应用」中卸载"
Say ''
Say '  正在启动程序…'
Start-Process -FilePath $exe -WorkingDirectory $dest
Say ''
