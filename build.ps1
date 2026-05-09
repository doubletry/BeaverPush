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
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"

# ── 项目根目录（脚本所在位置）──
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PyprojectPath = Join-Path $ProjectRoot "pyproject.toml"

function Get-ProjectVersion {
    param([string]$Path)

    $match = Select-String -Path $Path -Pattern '^\s*version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $match) {
        throw "未能从 pyproject.toml 读取版本号: $Path"
    }
    return $match.Matches[0].Groups[1].Value
}

function Convert-ToWindowsVersion {
    param([string]$RawVersion)

    # Windows 版本资源必须是纯数字的四段格式；若 tag/pyproject 使用
    # semver 预发布或 build metadata（如 1.2.3-beta.1 / 1.2.3+5），这里
    # 仅截取前面的核心版本号用于 EXE/Setup 的版本资源。
    $normalized = $RawVersion.Trim() -replace '[-+].*$', ''
    $parts = $normalized.Split(".")
    if ($parts.Count -gt 4) {
        throw "版本号段数过多，无法转换为 Windows 四段版本: $RawVersion"
    }
    foreach ($part in $parts) {
        if ($part -notmatch '^\d+$') {
            throw "版本号包含非数字字段，无法转换为 Windows 四段版本: $RawVersion"
        }
    }
    while ($parts.Count -lt 4) {
        $parts += "0"
    }
    return ($parts -join ".")
}

function Get-MissingInstallerInputs {
    param(
        [string]$ProjectRoot,
        [string]$ProductName
    )

    $ffmpegDir = Join-Path $ProjectRoot "ffmpeg"
    $requiredPaths = @(
        (Join-Path $ProjectRoot "dist\main.dist\$ProductName.exe")
        (Join-Path $ffmpegDir "ffmpeg.exe")
        (Join-Path $ffmpegDir "ffplay.exe")
        (Join-Path $ffmpegDir "ffprobe.exe")
    )
    $missing = @()

    foreach ($path in $requiredPaths) {
        if (-not (Test-Path $path)) {
            $missing += $path
        }
    }

    if (-not (Test-Path $ffmpegDir)) {
        return $missing
    }

    $ffmpegDlls = Get-ChildItem -Path $ffmpegDir -Filter "*.dll" -File -ErrorAction SilentlyContinue
    if ($ffmpegDlls.Count -eq 0) {
        $missing += "$ffmpegDir (缺少至少 1 个 shared FFmpeg DLL)"
    }

    return $missing
}

function Sync-NuitkaBuildOutput {
    param(
        [string]$StagingOutputDir,
        [string]$FinalOutputDir,
        [string]$ProductName
    )

    $stagedMainDist = Join-Path $StagingOutputDir "main.dist"
    $stagedExe = Join-Path $stagedMainDist "$ProductName.exe"
    if (-not (Test-Path $stagedExe)) {
        throw "Nuitka 输出目录中未找到编译结果: $stagedExe"
    }

    New-Item -ItemType Directory -Path $FinalOutputDir -Force | Out-Null

    $finalMainDist = Join-Path $FinalOutputDir "main.dist"
    if (Test-Path $finalMainDist) {
        Remove-Item $finalMainDist -Recurse -Force
    }

    Copy-Item -Path $stagedMainDist -Destination $finalMainDist -Recurse -Force
}

if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = Get-ProjectVersion -Path $PyprojectPath
}
$WindowsVersion = Convert-ToWindowsVersion -RawVersion $Version
$GeneratedVersionFile = (New-TemporaryFile).FullName
Set-Content -Path $GeneratedVersionFile -Value $Version -Encoding utf8
$StagingStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$NuitkaStagingRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("beaverpush-nuitka-$StagingStamp-" + [guid]::NewGuid().ToString("N"))

try {
    # ── 基本 参数 ──
    $EntryPoint    = Join-Path $ProjectRoot "src\beaverpush\main.py"
    $OutputDir     = Join-Path $ProjectRoot "dist"
    $NuitkaOutputDir = Join-Path $NuitkaStagingRoot "dist"
    $ProductName   = "BeaverPush"
    $IconPath      = Join-Path $ProjectRoot "assets\beaver_logo.ico"

    # ── Nuitka 编译参数 ──
    $NuitkaArgs = @(
        "--standalone"
        "--assume-yes-for-downloads"
        "--enable-plugin=pyside6"
        "--disable-cache=ccache"
        "--windows-console-mode=disable"
        "--windows-product-name=$ProductName"
        "--output-filename=$ProductName.exe"
        "--product-version=$WindowsVersion"
        "--output-dir=$NuitkaOutputDir"
        "--include-data-dir=$ProjectRoot\assets=assets"
        "--include-data-file=$GeneratedVersionFile=assets/version.txt"
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
    Write-Host "[INFO] Nuitka 暂存目录: $NuitkaOutputDir"
    Write-Host "[INFO] 产品名称:  $ProductName"
    Write-Host "[INFO] 版本号:    $Version"
    Write-Host "[INFO] 安装器版本: $WindowsVersion"
    Write-Host ""

    # ── 执行编译 ──
    Write-Host "[BUILD] 开始编译..." -ForegroundColor Yellow
    uv run python -m nuitka @NuitkaArgs $EntryPoint

    if ($LASTEXITCODE -eq 0) {
        Sync-NuitkaBuildOutput -StagingOutputDir $NuitkaOutputDir -FinalOutputDir $OutputDir -ProductName $ProductName
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
    $IsccPaths = @()
    if (-not [string]::IsNullOrWhiteSpace(${env:ProgramFiles(x86)})) {
        $IsccPaths += "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
        $IsccPaths += "${env:ProgramFiles(x86)}\Inno Setup 5\ISCC.exe"
    }
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $IsccPaths += "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    }
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

    $missingInstallerInputs = Get-MissingInstallerInputs -ProjectRoot $ProjectRoot -ProductName $ProductName
    if ($missingInstallerInputs.Count -gt 0) {
        $requiredSharedFfmpegFiles = "ffmpeg.exe / ffplay.exe / ffprobe.exe / *.dll"
        Write-Host ""
        Write-Host "[SKIP] 缺少安装包生成所需文件，跳过 Inno Setup 打包" -ForegroundColor Yellow
        foreach ($missingPath in $missingInstallerInputs) {
            Write-Host "[MISS] $missingPath" -ForegroundColor Yellow
        }
        Write-Host "[TIP]  请先准备 dist\main.dist\$ProductName.exe 与 ffmpeg 目录中的 shared build（含 $requiredSharedFfmpegFiles）" -ForegroundColor Yellow
        Write-Host "[TIP]  CI 会在调用 build.ps1 前自动下载并展开 FFmpeg；本地打包也需要同样的文件布局" -ForegroundColor Yellow
        exit 0
    }

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Blue
    Write-Host "  Inno Setup 安装包生成" -ForegroundColor Blue
    Write-Host "========================================" -ForegroundColor Blue
    Write-Host "[INFO] ISCC: $Iscc"
    Write-Host "[INFO] ISS:  $IssFile"
    Write-Host ""

    & $Iscc "/DMySourceDir=$ProjectRoot" "/DMyAppVersion=$Version" "/DMyAppVersionInfoVersion=$WindowsVersion" $IssFile

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
}
finally {
    if (Test-Path $NuitkaStagingRoot) {
        try {
            Remove-Item $NuitkaStagingRoot -Recurse -Force
        }
        catch {
            Write-Warning "未能清理 Nuitka 暂存目录: $NuitkaStagingRoot"
        }
    }
    if (Test-Path $GeneratedVersionFile) {
        Remove-Item $GeneratedVersionFile -Force -ErrorAction SilentlyContinue
    }
}
