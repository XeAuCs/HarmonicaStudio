param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$oldPythonPath = $env:PYTHONPATH
Push-Location -LiteralPath $projectRoot
try {
    $env:PYTHONPATH = (Join-Path $projectRoot 'src') + ';' + $oldPythonPath
    & $Python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw '测试失败。' }
} finally { $env:PYTHONPATH = $oldPythonPath; Pop-Location }
