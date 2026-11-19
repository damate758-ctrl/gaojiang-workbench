param(
    [ValidateSet('Install', 'Uninstall')]
    [string]$Mode = 'Install'
)

$ErrorActionPreference = 'Stop'
$appDir = $PSScriptRoot
$target = Join-Path $env:LOCALAPPDATA 'Programs\稿匠'

function Write-Title {
    param([string]$Text)
    Write-Host ''
    Write-Host '  ==========================================' -ForegroundColor DarkCyan
    Write-Host "     $Text" -ForegroundColor Cyan
    Write-Host '  ==========================================' -ForegroundColor DarkCyan
    Write-Host ''
}

function Stop-RunningApp {
    param([string]$Directory)

    $stopScript = Join-Path $Directory 'stop.ps1'
    if (Test-Path -LiteralPath $stopScript) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopScript 2>&1 | Out-Null
        return
    }

    # 没有停止脚本时（例如手动删过文件），按命令行里的路径兜底结束进程。
    $full = [IO.Path]::GetFullPath($Directory)
    Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='pythonw.exe'" |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine.IndexOf($full, [StringComparison]::OrdinalIgnoreCase) -ge 0
        } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function New-Shortcut {
    param(
        [string]$Path,
        [string]$Target,
        [string]$Arguments,
        [string]$WorkingDirectory,
        [string]$IconLocation,
        [string]$Description
    )

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $Target
    $shortcut.Arguments = $Arguments
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.IconLocation = $IconLocation
    $shortcut.Description = $Description
    $shortcut.Save()
}

function Install-App {
    Write-Title '稿匠 · 今日头条图文创作工作台'

    $pythonw = Join-Path $appDir 'runtime\pythonw.exe'
    if (-not (Test-Path -LiteralPath $pythonw)) {
        Write-Host '  [错误] 压缩包不完整，没有找到 runtime 运行环境。' -ForegroundColor Red
        Write-Host '  请重新解压完整的压缩包后再安装。' -ForegroundColor Red
        Write-Host ''
        Read-Host '  按回车关闭安装程序'
        exit 1
    }

    $inPlace = $target.TrimEnd('\') -eq $appDir.TrimEnd('\')
    Write-Host "  安装位置：$target" -ForegroundColor Gray
    Write-Host ''

    if (-not $inPlace) {
        if (Test-Path -LiteralPath (Join-Path $target 'stop.ps1')) {
            Write-Host '  检测到已安装的稿匠，正在关闭旧进程...' -ForegroundColor Yellow
            Stop-RunningApp -Directory $target
            Start-Sleep -Seconds 2
        }

        $sizeMb = [math]::Round(((Get-ChildItem -LiteralPath $appDir -Recurse -Force -File |
            Measure-Object Length -Sum).Sum) / 1MB)
        Write-Host "  正在复制程序文件（约 $sizeMb MB），请稍候..." -ForegroundColor Gray

        $arguments = @(
            "`"$appDir`"", "`"$target`"",
            '/E', '/R:1', '/W:1', '/NFL', '/NDL', '/NJH', '/NJS', '/NP',
            '/XD', '__pycache__', '/XD', 'branding'
        )
        & robocopy.exe @arguments | Out-Null
        if ($LASTEXITCODE -ge 8) {
            Write-Host ''
            Write-Host '  [错误] 复制失败，可能是磁盘空间不足或没有写入权限。' -ForegroundColor Red
            Write-Host ''
            Read-Host '  按回车关闭安装程序'
            exit 1
        }
    }

    Write-Host '  正在创建桌面和开始菜单快捷方式...' -ForegroundColor Gray

    $wscript = Join-Path $env:SystemRoot 'System32\wscript.exe'
    $launcher = Join-Path $target '启动稿匠.vbs'
    $iconPath = Join-Path $target 'icon.ico'
    $desktop = [Environment]::GetFolderPath('Desktop')
    $startMenu = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'
    $shortcutName = '稿匠.lnk'
    $registryKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\GaoJiang'

    foreach ($directory in @($desktop, $startMenu)) {
        if (-not (Test-Path -LiteralPath $directory)) {
            New-Item -Path $directory -ItemType Directory -Force | Out-Null
        }
        New-Shortcut -Path (Join-Path $directory $shortcutName) `
            -Target $wscript `
            -Arguments ('"{0}"' -f $launcher) `
            -WorkingDirectory $target `
            -IconLocation ('{0},0' -f $iconPath) `
            -Description '稿匠 · 今日头条图文内容创作与发布'
    }

    New-Item -Path $registryKey -Force | Out-Null
    Set-ItemProperty -Path $registryKey -Name DisplayName -Value '稿匠'
    Set-ItemProperty -Path $registryKey -Name DisplayVersion -Value '2026.11.18'
    Set-ItemProperty -Path $registryKey -Name Publisher -Value '稿匠'
    Set-ItemProperty -Path $registryKey -Name DisplayIcon -Value ('{0},0' -f $iconPath)
    Set-ItemProperty -Path $registryKey -Name InstallLocation -Value $target
    Set-ItemProperty -Path $registryKey -Name UninstallString -Value ('"{0}"' -f (Join-Path $target '卸载稿匠.bat'))
    Set-ItemProperty -Path $registryKey -Name NoModify -Value 1 -Type DWord
    Set-ItemProperty -Path $registryKey -Name NoRepair -Value 1 -Type DWord

    Write-Host ''
    Write-Host '  安装完成！桌面和开始菜单已创建“稿匠”快捷方式。' -ForegroundColor Green
    Write-Host ''

    $answer = Read-Host '  现在启动稿匠吗？(Y/N)'
    if ($answer -notmatch '^[Nn]') {
        Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\wscript.exe') `
            -ArgumentList ('"{0}"' -f (Join-Path $target '启动稿匠.vbs')) `
            -WorkingDirectory $target
    }

    Write-Host ''
    Read-Host '  按回车关闭安装程序'
}

function Uninstall-App {
    Write-Title '稿匠 · 卸载程序'
    Write-Host "  程序目录：$appDir" -ForegroundColor Gray
    Write-Host ''

    Write-Host '  正在关闭稿匠...' -ForegroundColor Gray
    Stop-RunningApp -Directory $appDir
    Start-Sleep -Seconds 2

    $desktop = [Environment]::GetFolderPath('Desktop')
    $startMenu = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'
    $shortcutName = '稿匠.lnk'
    $registryKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\GaoJiang'

    foreach ($shortcut in @((Join-Path $desktop $shortcutName), (Join-Path $startMenu $shortcutName))) {
        if (Test-Path -LiteralPath $shortcut) {
            Remove-Item -LiteralPath $shortcut -Force
        }
    }

    if (Test-Path -LiteralPath $registryKey) {
        Remove-Item -LiteralPath $registryKey -Recurse -Force
    }

    Write-Host '  快捷方式和卸载入口已移除。' -ForegroundColor Green
    Write-Host ''

    $answer = Read-Host '  是否保留“发布草稿”和头条登录信息？(Y/N)'
    if ($answer -notmatch '^[Nn]') {
        $backup = Join-Path $desktop '稿匠数据备份'
        if (-not (Test-Path -LiteralPath $backup)) {
            New-Item -Path $backup -ItemType Directory -Force | Out-Null
        }
        foreach ($name in @('发布草稿', '.toutiao-profile')) {
            $source = Join-Path $appDir $name
            if (Test-Path -LiteralPath $source) {
                & robocopy.exe $source (Join-Path $backup $name) /E /NFL /NDL /NJH /NJS /NP | Out-Null
            }
        }
        Write-Host "  已备份到：$backup" -ForegroundColor Green
        Write-Host ''
    }

    Write-Host '  正在删除程序文件...' -ForegroundColor Gray

    # 卸载程序自身就在待删除目录里，必须交给一个工作目录在外的分离进程延迟清理；
    # 若子进程继承了这个目录作为工作目录，Windows 会一直锁住它导致删除失败。
    $escaped = $appDir.TrimEnd('\').Replace("'", "''")
    $cleanup = @"
`$ErrorActionPreference = 'SilentlyContinue'
for (`$i = 0; `$i -lt 60; `$i++) {
    Start-Sleep -Milliseconds 500
    if (-not (Test-Path -LiteralPath '$escaped')) { exit 0 }
    Remove-Item -LiteralPath '$escaped' -Recurse -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path -LiteralPath '$escaped')) { exit 0 }
}
"@
    Start-Process -FilePath 'powershell.exe' `
        -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-Command', $cleanup `
        -WorkingDirectory $env:SystemRoot `
        -WindowStyle Hidden | Out-Null

    Write-Host '  卸载完成。' -ForegroundColor Green
    Write-Host '  稍等几秒后程序目录会自动消失。' -ForegroundColor Gray
    Write-Host ''
}

if ($Mode -eq 'Uninstall') {
    Uninstall-App
}
else {
    Install-App
}
