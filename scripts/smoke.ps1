param([string]$Report = '', [string]$ExecutablePath = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$exe = if ($ExecutablePath) { [System.IO.Path]::GetFullPath($ExecutablePath) } else { Join-Path $projectRoot 'app\HarmonicaStudio\HarmonicaStudio.exe' }
if (-not $Report) { $Report = Join-Path $projectRoot 'verification\smoke.json' }
$Report = [System.IO.Path]::GetFullPath($Report)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Report) | Out-Null
$process = Start-Process -FilePath $exe -ArgumentList @('--self-test', ('"' + $Report + '"')) -WindowStyle Hidden -PassThru -Wait
if ($process.ExitCode -ne 0) { throw "成品检查失败，请查看 $Report" }
$result = Get-Content -LiteralPath $Report -Raw -Encoding utf8 | ConvertFrom-Json
if (-not $result.ok) { throw "成品检查失败，请查看 $Report" }
Write-Output "成品检查通过：$Report"
