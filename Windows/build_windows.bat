@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 exit /b 1
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller
if errorlevel 1 exit /b 1

pyinstaller --noconfirm --clean --windowed --name iPhone5DualBooter ^
  --add-data "iphone5dualbooter\assets;iphone5dualbooter\assets" app.py
if errorlevel 1 exit /b 1

echo.
echo Built app is in dist\iPhone5DualBooter\
