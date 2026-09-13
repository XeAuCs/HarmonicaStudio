param([string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$executable = Join-Path $ProjectRoot 'app\HarmonicaStudio\HarmonicaStudio.exe'
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw '请先打包程序，再创建快捷方式。'
}
$shortcutPath = Join-Path $ProjectRoot '口琴工坊.lnk'
$shell = New-Object -ComObject WScript.Shell
try {
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $executable
    $shortcut.WorkingDirectory = $ProjectRoot
    $shortcut.IconLocation = "$executable,0"
    $shortcut.Description = '口琴工坊 · MIDI 曲谱编辑与试听'
    $shortcut.Save()
    try {
        if (-not ('HarmonicaStudioShellIcons' -as [type])) {
            Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
public static class HarmonicaStudioShellIcons {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    public static extern void SHChangeNotify(int eventId, uint flags, string item1, string item2);
}
'@
        }
        [HarmonicaStudioShellIcons]::SHChangeNotify(0x2000, 0x0005, $shortcutPath, $null)
    } catch { Write-Verbose '快捷方式已创建，图标将在资源管理器下次刷新时更新。' }
    Write-Output "已创建 $shortcutPath"
} finally {
    if ($null -ne $shortcut) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut) }
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell)
}
