param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    & $Python scripts/make_icon.py
    if ($LASTEXITCODE -ne 0) { throw '图标生成失败。' }
    & $Python scripts/collect_licenses.py
    if ($LASTEXITCODE -ne 0) { throw '许可证收集失败。' }
    & $Python -m PyInstaller --noconfirm --distpath app --workpath build/pyinstaller HarmonicaStudio.spec
    if ($LASTEXITCODE -ne 0) { throw '打包失败。请先安装 requirements-build.txt。' }
    Write-Output '已生成 app/HarmonicaStudio/HarmonicaStudio.exe'
} finally { Pop-Location }
