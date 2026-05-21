@echo off
setlocal

set "IDF_TOOLS_PATH=C:\Espressif"
set "IDF_CCACHE_ENABLE=0"
set "PATH=C:\Espressif\python_env\idf6.0_py3.14_env\Scripts;C:\Espressif\tools\git\bin;%PATH%"

call C:\esp\v6.0.1\esp-idf\export.bat
if errorlevel 1 exit /b %errorlevel%

cd /d "%~dp0supermouse_35b"
idf.py %*
