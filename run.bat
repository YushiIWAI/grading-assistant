@echo off
REM 採点支援アプリ起動スクリプト (Windows)
cd /d "%~dp0"

REM 仮想環境があれば使う
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

python -m streamlit run app.py
pause
