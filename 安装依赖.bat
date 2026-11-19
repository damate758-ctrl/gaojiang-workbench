@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在安装稿匠桌面组件...
python -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo.
  echo 安装失败，请确认电脑已安装 Python 3.11 或更高版本。
  pause
  exit /b 1
)
echo.
echo 安装完成。请双击“启动稿匠.bat”。
pause
