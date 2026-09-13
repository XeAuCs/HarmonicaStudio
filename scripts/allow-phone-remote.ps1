#Requires -RunAsAdministrator
param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$program = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'app\HarmonicaStudio\HarmonicaStudio.exe')).Path
$ruleName = 'HarmonicaStudio.PhoneRemote'
$existing = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
if ($existing) {
    $filter = $existing | Get-NetFirewallApplicationFilter
    if ($filter.Program -ne $program) { throw '已有同名规则指向其他程序，请检查后再设置。' }
    Write-Output '口琴工坊的局域网连接规则已存在。'
    return
}
New-NetFirewallRule -Name $ruleName -DisplayName '口琴工坊 · 手机遥控（仅局域网）' `
    -Description '仅允许局域网设备连接口琴工坊的 TCP 47638 端口；配对仍需二维码凭证。' `
    -Direction Inbound -Action Allow -Enabled True -Profile Private -Protocol TCP `
    -Program $program -LocalPort 47638 -RemoteAddress LocalSubnet | Out-Null
Write-Output '已允许本程序的局域网手机连接。未更改其他程序或防火墙总开关。'
