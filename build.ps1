<#
.SYNOPSIS
    使用 Nuitka 编译构建 BeaverPush 项目。

.DESCRIPTION
    将 beaverpush 编译为独立可执行文件 BeaverPush.exe，
    输出到 dist 目录，不显示控制台窗口。

.NOTES
    前置条件：
      - Python >= 3.12（通过 uv 管理虚拟环境）
      - uv sync 已执行
      - Nuitka 已安装（包含在 pyproject.toml 依赖中）
#>

param(
    [string]$Version = "0.1.0"
)

$ErrorActionPreference = "Stop"

# ── 项目根目录（脚本所在位置）──
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

# ── 基本 参数 ──
$EntryPoint    = Join-Path $ProjectRoot "src\beaverpush\main.py"
$OutputDir     = Join-Path $ProjectRoot "dist"
$ProductName   = "BeaverPush"
$IconPath      = Join-Path $ProjectRoot "assets\beaver_logo.ico"

# ── Nuitka 编译参数 ──
$NuitkaArgs = @(
    "--standalone"
    "--assume-yes-for-downloads"
    "--enable-plugin=pyside6"
    "--windows-console-mode=disable"
    "--windows-product-name=$ProductName"
    "--output-filename=$ProductName.exe"
    "--product-version=$Version"
    "--output-dir=$OutputDir"
    "--include-data-dir=$ProjectRoot\assets=assets"
)

# 如果有 .ico 图标则添加
if (Test-Path $IconPath) {
    $NuitkaArgs += "--windows-icon-from-ico=$IconPath"
    Write-Host "[INFO] 使用图标: $IconPath" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Blue
Write-Host "  BeaverPush Nuitka 编译构建" -ForegroundColor Blue
Write-Host "========================================" -ForegroundColor Blue
Write-Host ""
Write-Host "[INFO] 入口文件:  $EntryPoint"
Write-Host "[INFO] 输出目录:  $OutputDir"
Write-Host "[INFO] 产品名称:  $ProductName"
Write-Host "[INFO] 版本号:    $Version"
Write-Host ""

# ── 执行编译 ──
Write-Host "[BUILD] 开始编译..." -ForegroundColor Yellow
uv run python -m nuitka @NuitkaArgs $EntryPoint

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "[SUCCESS] 编译完成!" -ForegroundColor Green
    Write-Host "[INFO] 输出位置: $OutputDir\main.dist" -ForegroundColor Cyan
    Write-Host ""
} else {
    Write-Host ""
    Write-Host "[ERROR] 编译失败, 退出码: $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}

# ── 生成安装包 (Inno Setup) ──
$IssFile = Join-Path $ProjectRoot "installer.iss"
if (-not (Test-Path $IssFile)) {
    Write-Host "[SKIP] 未找到 installer.iss, 跳过安装包生成" -ForegroundColor Yellow
    exit 0
}

# 查找 iscc.exe
$IsccPaths = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    "${env:ProgramFiles(x86)}\Inno Setup 5\ISCC.exe"
)
$Iscc = $null
foreach ($p in $IsccPaths) {
    if (Test-Path $p) { $Iscc = $p; break }
}
# 也尝试从 PATH 中查找
if (-not $Iscc) {
    $Iscc = (Get-Command "iscc" -ErrorAction SilentlyContinue).Source
}

if (-not $Iscc) {
    Write-Host ""
    Write-Host "[SKIP] 未找到 Inno Setup (iscc.exe), 跳过安装包生成" -ForegroundColor Yellow
    Write-Host "[TIP]  访问 https://jrsoftware.org/isinfo.php 下载安装 Inno Setup 6" -ForegroundColor Yellow
    Write-Host "[TIP]  安装后再次运行此脚本即可自动生成安装包" -ForegroundColor Yellow
    exit 0
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Blue
Write-Host "  Inno Setup 安装包生成" -ForegroundColor Blue
Write-Host "========================================" -ForegroundColor Blue
Write-Host "[INFO] ISCC: $Iscc"
Write-Host "[INFO] ISS:  $IssFile"
Write-Host ""

& $Iscc $IssFile

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "[SUCCESS] 安装包生成完成!" -ForegroundColor Green
    Write-Host "[INFO] 安装包: $OutputDir\BeaverPushSetup.exe" -ForegroundColor Cyan
    Write-Host ""
} else {
    Write-Host ""
    Write-Host "[ERROR] 安装包生成失败, 退出码: $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}
