param([string]$Version = '', [string]$Python = '', [string]$DistPath = 'app', [switch]$Interactive)
$ErrorActionPreference = 'Stop'
$PSDefaultParameterValues = @{'Out-File:Encoding' = 'utf8'}
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
$stagingRoot = $null
$reportFolder = $null
$logFile = Join-Path $projectRoot 'verification/build.log'
function Invoke-BuildPython([string[]]$BuildArguments) {
    # Windows PowerShell 5 treats native stderr as ErrorRecord, even on success.
    $ErrorActionPreference = 'Continue'
    & $Python @BuildArguments *>> $logFile
    if ($LASTEXITCODE -ne 0) { throw "构建命令失败：$($BuildArguments[0])（退出码 $LASTEXITCODE）。" }
}
try {
    if (-not $Python) {
        $localPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
        $Python = if (Test-Path -LiteralPath $localPython) { $localPython } else { 'python' }
    }
    $null = Get-Command $Python -ErrorAction Stop
    $destination = [System.IO.Path]::GetFullPath((Join-Path $DistPath 'HarmonicaStudio'))
    $running = @(Get-Process -Name HarmonicaStudio -ErrorAction SilentlyContinue | Where-Object { $_.Path -and [System.IO.Path]::GetDirectoryName($_.Path) -eq $destination })
    if ($running.Count) { throw '请先保存并关闭当前程序，再更新这个便携目录。' }
    if ($Interactive -and -not $Version) { $Version = Read-Host '版本号（例如 1.4.7，直接回车保留当前版本）' }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $logFile) | Out-Null
    Set-Content -LiteralPath $logFile -Value "Build started: $(Get-Date -Format s)" -Encoding utf8
    if ($Version) {
        Write-Output "更新版本号：$Version"
        Invoke-BuildPython @('scripts/set_version.py', $Version)
    }
    Write-Output '[1/4] 运行测试…'
    & (Join-Path $PSScriptRoot 'test.ps1') -Python $Python *>> $logFile
    Write-Output '[2/4] 生成便携版…'
    Invoke-BuildPython @('scripts/make_icon.py')
    Invoke-BuildPython @('scripts/collect_licenses.py')
    $buildRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'build'))
    $stagingRoot = Join-Path $buildRoot ('portable-stage-' + [guid]::NewGuid().ToString('N'))
    Invoke-BuildPython @('-m', 'PyInstaller', '--noconfirm', '--distpath', $stagingRoot, '--workpath', (Join-Path $stagingRoot 'work'), 'HarmonicaStudio.spec')
    $stagedApp = Join-Path $stagingRoot 'HarmonicaStudio'
    Invoke-BuildPython @('scripts/package_portable.py', 'prepare', $stagedApp, (Join-Path $projectRoot 'samples'))
    Write-Output '[3/4] 检查成品…'
    $reportFolder = Join-Path $stagingRoot 'checks'
    & (Join-Path $PSScriptRoot 'smoke.ps1') -ExecutablePath (Join-Path $stagedApp 'HarmonicaStudio.exe') -Report (Join-Path $reportFolder 'portable-build-smoke.json') *>> $logFile
    $running = @(Get-Process -Name HarmonicaStudio -ErrorAction SilentlyContinue | Where-Object { $_.Path -and [System.IO.Path]::GetDirectoryName($_.Path) -eq $destination })
    if ($running.Count) { throw '程序在构建期间已启动，未覆盖已有版本；请关闭后重试。' }
    Write-Output '[4/4] 更新程序，保留曲库与个人数据…'
    Invoke-BuildPython @('scripts/package_portable.py', 'install', $stagedApp, $destination)
    Write-Output "已生成并检查 $destination/HarmonicaStudio.exe"
} catch {
    if (Test-Path -LiteralPath $logFile) { $_ | Out-String | Add-Content -LiteralPath $logFile -Encoding utf8 }
    throw "打包未完成：$($_.Exception.Message)  详细日志：$logFile"
} finally {
    try {
    if ($reportFolder -and (Test-Path -LiteralPath $reportFolder)) {
        # Keep reports and screenshots, not generated audio/projects or test data.
        Get-ChildItem -LiteralPath $reportFolder -File | Where-Object { $_.Extension -in '.json','.png' } | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $projectRoot 'verification') -Force
        }
    }
    if ($stagingRoot -and (Test-Path -LiteralPath $stagingRoot)) {
        $resolvedStage = (Resolve-Path -LiteralPath $stagingRoot).Path
        if ([System.IO.Path]::GetDirectoryName($resolvedStage) -ne $buildRoot -or -not [System.IO.Path]::GetFileName($resolvedStage).StartsWith('portable-stage-')) { throw '拒绝清理非构建暂存目录。' }
        if (((Get-Item -LiteralPath $resolvedStage).Attributes -band [IO.FileAttributes]::ReparsePoint) -or @(Get-ChildItem -LiteralPath $resolvedStage -Recurse -Force -Attributes ReparsePoint).Count) { throw '构建暂存目录包含链接，停止清理。' }
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    }
    } finally { Pop-Location }
}
