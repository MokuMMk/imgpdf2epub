# build_msix.ps1 - 把 WinUI 界面 + Python 后端打成可分发的 MSIX 安装包
#
# 需要：
#   - 已执行 dotnet publish -c Release -r win-x64 --self-contained true -o publish
#   - Python 后端 dist\图片PDF转EPUB.exe 已打好
#   - icon\assets 下的 MSIX 图标资源
# 产出：dist-msix\图片PDF转EPUB.msix（已签名）+ ImgPdf2Epub.cer（信任用）

param(
  [string]$Version    = "1.0.0.0",
  [string]$Publisher  = "CN=ImgPdf2Epub",
  [string]$Thumbprint = "",
  [switch]$SkipSign
)

$ErrorActionPreference = "Stop"
$root     = $PSScriptRoot                            # D:\debug\imgpdf2epub
$winui    = Join-Path $root "WinUI"
$publish  = Join-Path $winui "publish"
$stage    = Join-Path $root "msix-stage"
$outDir   = Join-Path $root "dist-msix"

$buildtools = Join-Path $env:USERPROFILE ".nuget\packages\microsoft.windows.sdk.buildtools\10.0.26100.4654\bin\10.0.26100.0\x64"
$makeappx = Join-Path $buildtools "makeappx.exe"
$signtool = Join-Path $buildtools "signtool.exe"

Write-Host "== 检查输入 =="
if (-not (Test-Path $publish))  { throw "缺少发布目录: $publish" }
if (-not (Test-Path $makeappx)) { throw "缺少 makeappx: $makeappx" }
$pyExe = Join-Path $root "dist\图片PDF转EPUB.exe"
if (-not (Test-Path $pyExe))    { throw "缺少 Python 后端: $pyExe" }

Write-Host "== 准备暂存目录 =="
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force -Path $stage | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $stage "Assets") | Out-Null

# 1) 界面程序（跳过备份目录与测试文件）
Get-ChildItem $publish -Force | Where-Object {
  $_.Name -notin @('_ai_bak', 'test_scan.pdf') -and
  $_.Name -ne '图片PDF转EPUB.exe'
} | ForEach-Object {
  Copy-Item $_.FullName -Destination $stage -Recurse -Force
}

# 2) Python 后端 + 图标
Copy-Item $pyExe $stage -Force
Copy-Item (Join-Path $root "icon\app.ico") $stage -Force

# 3) MSIX 图标资源
Copy-Item (Join-Path $root "icon\assets\*") (Join-Path $stage "Assets") -Force

# 4) 清单
$manifest = @"
<?xml version="1.0" encoding="utf-8"?>
<Package
  xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
  xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
  xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
  IgnorableNamespaces="uap rescap">

  <Identity Name="ImgPdf2Epub"
            Publisher="$Publisher"
            Version="$Version"
            ProcessorArchitecture="x64" />

  <Properties>
    <DisplayName>图片 PDF 转 EPUB</DisplayName>
    <PublisherDisplayName>ImgPdf2Epub</PublisherDisplayName>
    <Logo>Assets\StoreLogo.png</Logo>
  </Properties>

  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop"
                        MinVersion="10.0.18362.0"
                        MaxVersionTested="10.0.26100.0" />
  </Dependencies>

  <Resources>
    <Resource Language="zh-CN" />
    <Resource Language="en-US" />
  </Resources>

  <Applications>
    <Application Id="App"
                 Executable="图片PDF转EPUB-界面.exe"
                 EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements
          DisplayName="图片 PDF 转 EPUB"
          Description="把纯图片（扫描版）PDF 转成 EPUB"
          BackgroundColor="transparent"
          Square150x150Logo="Assets\Square150x150Logo.png"
          Square44x44Logo="Assets\Square44x44Logo.png">
        <uap:DefaultTile
            Wide310x150Logo="Assets\Wide310x150Logo.png"
            Square310x310Logo="Assets\Square310x310Logo.png"
            Square71x71Logo="Assets\Square71x71Logo.png" />
      </uap:VisualElements>
    </Application>
  </Applications>

  <Capabilities>
    <rescap:Capability Name="runFullTrust" />
  </Capabilities>
</Package>
"@
Set-Content -Path (Join-Path $stage "AppxManifest.xml") -Value $manifest -Encoding UTF8

$files = Get-ChildItem $stage -Recurse -File
Write-Host ("   暂存 {0} 个文件, {1:N1} MB" -f $files.Count,
  (($files | Measure-Object Length -Sum).Sum / 1MB))

Write-Host "== 打包 MSIX =="
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$msix = Join-Path $outDir "图片PDF转EPUB.msix"
if (Test-Path $msix) { Remove-Item $msix -Force }

& $makeappx pack /d $stage /p $msix /o
if ($LASTEXITCODE -ne 0) { throw "makeappx 打包失败" }
Write-Host ("   产出: {0}  ({1:N1} MB)" -f $msix, ((Get-Item $msix).Length / 1MB))

Write-Host "== 校验包结构 =="
& $makeappx validate /p $msix
if ($LASTEXITCODE -ne 0) { Write-Host "   校验有警告/错误，请检查" }

if (-not $SkipSign) {
  Write-Host "== 签名 =="
  if (-not $Thumbprint) {
    $c = Get-ChildItem Cert:\CurrentUser\My |
         Where-Object { $_.Subject -eq $Publisher } |
         Sort-Object NotAfter -Descending | Select-Object -First 1
    if (-not $c) { throw "找不到证书 ($Publisher)，请先创建自签名证书" }
    $Thumbprint = $c.Thumbprint
  }
  Write-Host "   证书指纹: $Thumbprint"
  & $signtool sign /fd SHA256 /sha1 $Thumbprint /tr http://timestamp.digicert.com /td SHA256 $msix
  if ($LASTEXITCODE -ne 0) {
    Write-Host "   带时间戳签名失败，改用不带时间戳重试…"
    & $signtool sign /fd SHA256 /sha1 $Thumbprint $msix
  }
  if ($LASTEXITCODE -ne 0) { throw "签名失败" }
  & $signtool verify /pa $msix
}

Write-Host ""
Write-Host "完成。产出目录: $outDir"
Get-ChildItem $outDir | Select-Object Name, @{n='MB';e={[math]::Round($_.Length/1MB,1)}}
