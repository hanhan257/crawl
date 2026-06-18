@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   Build %~n0 - Crawl Feed GMC
echo ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [LOI] Chua cai Python. Tai tai: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Cai dependencies...
pip install -r requirements.txt -q
pip install pyinstaller -q

echo.
echo Dang build (co the mat vai phut)...
pyinstaller crawl_feed_gmc.spec --noconfirm
if errorlevel 1 (
    echo [LOI] Build that bai.
    pause
    exit /b 1
)

if not exist "dist\Crawl Feed GMC\output" mkdir "dist\Crawl Feed GMC\output"
copy /Y "HUONG_DAN.txt" "dist\Crawl Feed GMC\" >nul

echo.
echo ========================================
echo   Build xong!
echo   Chay app: dist\Crawl Feed GMC\Crawl Feed GMC.exe
echo ========================================
echo.
pause
