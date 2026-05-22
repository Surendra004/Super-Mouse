@echo off
setlocal

set "IDF_TOOLS_PATH=C:\Espressif"
set "IDF_CCACHE_ENABLE=0"
set "CCACHE_DIR=%~dp0.ccache"
set "CCACHE_TEMPDIR=%~dp0.ccache\tmp"
set "PATH=C:\Espressif\python_env\idf6.0_py3.14_env\Scripts;C:\Espressif\tools\git\bin;%PATH%"

if not exist "%CCACHE_TEMPDIR%" mkdir "%CCACHE_TEMPDIR%"

call C:\esp\v6.0.1\esp-idf\export.bat
if errorlevel 1 exit /b %errorlevel%

cd /d "%~dp0supermouse_35b"
idf.py %*
