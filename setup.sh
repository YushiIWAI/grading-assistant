#!/bin/bash
# ============================================================
# 採点支援アプリ セットアップスクリプト (Mac / Linux)
# ============================================================
set -e

cd "$(dirname "$0")"
echo "=== 採点支援アプリ セットアップ ==="

# Python確認
if ! command -v python3 &> /dev/null; then
    echo "エラー: Python3 がインストールされていません。"
    echo "https://www.python.org/downloads/ からインストールしてください。"
    exit 1
fi

PYTHON_VER=$(python3 --version 2>&1)
echo "Python: $PYTHON_VER"

# 仮想環境の作成
if [ ! -d "venv" ]; then
    echo "仮想環境を作成中..."
    python3 -m venv venv
fi

# 仮想環境の有効化
source venv/bin/activate

# パッケージのインストール
echo "必要なパッケージをインストール中..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

# .envファイルの作成（なければ）
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ".env ファイルを作成しました。APIキーを設定してください。"
fi

# データディレクトリの作成
mkdir -p data output

echo ""
echo "=== セットアップ完了 ==="
echo ""
echo "起動方法:"
echo "  ./run.sh"
echo ""
echo "APIキーの設定（任意）:"
echo "  .env ファイルを編集するか、アプリのサイドバーで入力してください。"
