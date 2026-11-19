$ErrorActionPreference = 'SilentlyContinue'
$pidPath = Join-Path $PSScriptRoot '.server.pid'
if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Host '没有找到稿匠进程记录。'
    exit 0
}
$serverId = [int](Get-Content -LiteralPath $pidPath -Raw).Trim()
$proc = Get-CimInstance Win32_Process -Filter "ProcessId = $serverId"
$expected = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'app.py'))
$commandLine = if ($proc -and $proc.CommandLine) { [string]$proc.CommandLine } else { '' }
$processName = if ($proc -and $proc.Name) { [string]$proc.Name } else { '' }
# The output directory can be a junction. Python may expose the real target
# path in CommandLine, so also accept the recorded process when it runs app.py.
$sameProjectApp = $commandLine.IndexOf($expected, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
    $commandLine -match '(?i)(?:^|[\\/])app\.py(?:\s|"|$)'
$isPython = $processName -match '^(?:python|pythonw)(?:\.exe)?$'
if ($proc -and $isPython -and $sameProjectApp) {
    Stop-Process -Id $serverId -Force
    Write-Host '稿匠桌面 App 已停止。'
} else {
    Write-Host '记录的进程与稿匠 App 不匹配，未停止任何进程。'
}
Remove-Item -LiteralPath $pidPath -Force
