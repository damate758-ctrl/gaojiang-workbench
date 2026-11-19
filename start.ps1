$ErrorActionPreference = 'Stop'
$url = 'http://127.0.0.1:8766'

function Test-GaojiangReady {
    try {
        $response = Invoke-WebRequest "$url/api/health" -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host '没有找到 Python。请先安装 Python 3.11 或更高版本。' -ForegroundColor Red
    Read-Host '按回车关闭此窗口'
    exit 1
}

$pythonw = Get-Command pythonw -ErrorAction SilentlyContinue
if (-not $pythonw) {
    $pythonw = $python
}

& $python.Source -c "import webview, playwright, bs4, lxml, pystray, PIL" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host '首次运行，正在安装桌面与头条发布组件…' -ForegroundColor Yellow
    & $python.Source -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) {
        Write-Host '依赖安装失败，请双击“安装依赖.bat”查看错误。' -ForegroundColor Red
        Read-Host '按回车关闭此窗口'
        exit 1
    }
}

if (Test-GaojiangReady) {
    $appIdPath = Join-Path $PSScriptRoot '.server.pid'
    if (Test-Path -LiteralPath $appIdPath) {
        try {
            $appId = [int](Get-Content -LiteralPath $appIdPath -Raw).Trim()
            $appProcess = Get-Process -Id $appId -ErrorAction Stop
            if ($appProcess) {
                Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class GaojiangWindow {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")]
    public static extern bool ShowWindowAsync(IntPtr hWnd, int command);
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);
    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
    public static IntPtr FindWindow(uint pid) {
        IntPtr result = IntPtr.Zero;
        EnumWindows((hWnd, lParam) => {
            uint owner;
            GetWindowThreadProcessId(hWnd, out owner);
            if (owner == pid) {
                var title = new StringBuilder(512);
                GetWindowText(hWnd, title, title.Capacity);
                if (title.ToString().Contains("稿匠")) {
                    result = hWnd;
                    return false;
                }
            }
            return true;
        }, IntPtr.Zero);
        return result;
    }
}
'@
                $windowHandle = [GaojiangWindow]::FindWindow([uint32]$appId)
                if ($windowHandle -ne [IntPtr]::Zero) {
                    [GaojiangWindow]::ShowWindowAsync($windowHandle, 9) | Out-Null
                    [GaojiangWindow]::SetForegroundWindow($windowHandle) | Out-Null
                    Write-Host '稿匠已经在运行，已打开现有窗口。' -ForegroundColor Green
                    exit 0
                }
            }
        }
        catch {}
    }
    $activated = $false
    try {
        $shell = New-Object -ComObject WScript.Shell
        $activated = [bool]$shell.AppActivate('稿匠')
    }
    catch {
        $activated = $false
    }
    if ($activated) {
        Write-Host '稿匠已经在运行，已切换到现有窗口。' -ForegroundColor Green
        exit 0
    }

    # 端口被占用但找不到窗口（例如上次隐藏到托盘后托盘图标丢失），
    # 说明残留进程卡死了。这里直接结束它，继续往下重新启动一个新实例。
    Write-Host '检测到残留的稿匠进程，正在重启...' -ForegroundColor Yellow
    if (Test-Path -LiteralPath $appIdPath) {
        try {
            $staleId = [int](Get-Content -LiteralPath $appIdPath -Raw).Trim()
            Stop-Process -Id $staleId -Force -ErrorAction SilentlyContinue
        }
        catch {}
    }
    Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='pythonw.exe'" |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine.IndexOf($PSScriptRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0
        } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    for ($j = 0; $j -lt 20; $j++) {
        Start-Sleep -Milliseconds 250
        if (-not (Test-GaojiangReady)) {
            break
        }
    }
}

$appPath = Join-Path $PSScriptRoot 'app.py'
$outLog = Join-Path $PSScriptRoot 'app.log'
$errLog = Join-Path $PSScriptRoot 'app-error.log'
$proc = Start-Process -FilePath $pythonw.Source `
    -ArgumentList ('"{0}"' -f $appPath) `
    -WorkingDirectory $PSScriptRoot `
    -PassThru `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog

$proc.Id | Set-Content -LiteralPath (Join-Path $PSScriptRoot '.server.pid') -Encoding ascii

for ($i = 0; $i -lt 50; $i++) {
    Start-Sleep -Milliseconds 250
    if (Test-GaojiangReady) {
        Write-Host '稿匠桌面 App 已启动。' -ForegroundColor Green
        exit 0
    }
    if ($proc.HasExited) {
        break
    }
}

if ($proc.HasExited) {
    Write-Host "桌面 App 启动失败，退出代码：$($proc.ExitCode)" -ForegroundColor Red
} else {
    Write-Host '桌面 App 没有在 12.5 秒内完成启动。' -ForegroundColor Red
}
Write-Host '请查看 app-error.log 获取详细错误。'
Read-Host '按回车关闭此窗口'
exit 1
