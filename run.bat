@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   Crawl Feed GMC v1.0
echo ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [LOI] Chua cai Python. Tai tai: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Dang dong app cu tren port 8501 (neu co)...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8501 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
)

echo Xoa cache Python...
for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"

echo Dang cai dependencies...
pip install -r requirements.txt -q

echo.
echo Mo trinh duyet: http://localhost:8501
echo.

set CRAWL_OPEN_BROWSER=1
python launcher.py

pause
