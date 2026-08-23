@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ================================================
echo   澳股市场雷达 ASX Market Radar
echo ================================================
where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未检测到 Python。请先安装 Python 3.9 或以上版本：
  echo        https://www.python.org/downloads/
  echo        安装时请勾选 "Add Python to PATH"
  pause
  exit /b 1
)
python -c "import yfinance,pandas,numpy,sklearn,scipy,requests" 2>nul
if errorlevel 1 (
  echo 首次运行，正在安装依赖库 ...
  python -m pip install -r requirements.txt
)
python run.py
pause
