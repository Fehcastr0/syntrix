@echo off
echo ============================================
echo   SYNTRIX — Instalando dependencias...
echo ============================================
echo.

pip install pyyaml
pip install fake_useragent
pip install git+https://github.com/zagmi/iqbroker.git

echo.
echo ============================================
echo   Instalacao concluida!
echo   Rode: python start.py
echo ============================================
pause
