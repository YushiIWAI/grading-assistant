@echo off
REM ============================================================
REM 採点支援アプリ セットアップスクリプト (Windows)
REM ============================================================

cd /d "%~dp0"
echo === 採点支援アプリ セットアップ ===

REM Python確認
python --version >nul 2>&1
if errorlevel 1 (
    python3 --version >nul 2>&1
    if errorlevel 1 (
        echo エラー: Python がインストールされていません。
        echo https://www.python.org/downloads/ からインストールしてください。
        echo インストール時に「Add Python to PATH」にチェックを入れてください。
        pause
        exit /b 1
    )
    set PYTHON=python3
) else (
    set PYTHON=python
)

for /f "tokens=*" %%i in ('%PYTHON% --version 2^>^&1') do echo Python: %%i

REM 仮想環境の作成
if not exist "venv" (
    echo 仮想環境を作成中...
    %PYTHON% -m venv venv
)

REM 仮想環境の有効化
call venv\Scripts\activate.bat

REM パッケージのインストール
echo 必要なパッケージをインストール中...
pip install --upgrade pip -q
pip install -r requirements.txt -q

REM .envファイルの作成
if not exist ".env" (
    copy .env.example .env
    echo .env ファイルを作成しました。APIキーを設定してください。
)

REM データディレクトリの作成
if not exist "data" mkdir data
if not exist "output" mkdir output

echo.
echo === セットアップ完了 ===
echo.
echo 起動方法:
echo   run.bat をダブルクリック
echo.
echo APIキーの設定（任意）:
echo   .env ファイルを編集するか、アプリのサイドバーで入力してください。
echo.
pause
