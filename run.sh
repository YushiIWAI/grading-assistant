#!/bin/bash
# 採点支援アプリ起動スクリプト (Mac / Linux)
cd "$(dirname "$0")"

# 仮想環境があれば使う
if [ -d "venv" ]; then
    source venv/bin/activate
fi

python3 -m streamlit run app.py
