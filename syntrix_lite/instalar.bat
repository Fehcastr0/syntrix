@echo off
echo ========================================
echo  SYNTRIX LITE - Instalacao
echo ========================================
echo.
echo Instalando dependencias...
pip install pyyaml fake_useragent iqbroker
echo.
echo Pronto! Para rodar:
echo   python main.py --shadow          (modo simulacao)
echo   python main.py                   (modo real demo)
echo.
pause
