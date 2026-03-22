"""認証UI モジュール"""
from __future__ import annotations

import os

import streamlit as st

import api_client
from api_client import ApiClientError


def check_auth() -> bool:
    """JWT認証またはレガシーパスワード認証。成功時にTrueを返す。"""
    # 既に認証済みの場合: トークンを復元
    if st.session_state.get("authenticated"):
        token = st.session_state.get("access_token")
        if token:
            api_client.set_auth_token(token)
        return True

    # DBにユーザーが存在するか確認（/auth/me で判定）
    try:
        me_result = api_client.get_me()
    except Exception:
        me_result = {"authenticated": False}

    # ユーザーが登録されていない場合: レガシーパスワード認証にフォールバック
    has_users = _check_has_users()
    if not has_users:
        return _check_legacy_password()

    # MFA検証待ち状態の場合: TOTPコード入力画面
    if st.session_state.get("mfa_pending"):
        return _check_mfa_verify()

    # JWT ログインフォーム
    st.markdown(
        "<h2 style='text-align:center; margin-top:2rem;'>📝 国語 採点支援</h2>"
        "<p style='text-align:center; color:#64748b;'>メールアドレスとパスワードでログインしてください</p>",
        unsafe_allow_html=True,
    )
    with st.form("login_form"):
        email = st.text_input("メールアドレス")
        password = st.text_input("パスワード", type="password")
        submitted = st.form_submit_button("ログイン", use_container_width=True)
        if submitted and email and password:
            try:
                result = api_client.login(email, password)
                if result.get("mfa_required"):
                    # MFA検証待ち状態に遷移
                    st.session_state["mfa_pending"] = True
                    st.session_state["mfa_token"] = result["mfa_token"]
                    st.rerun()
                else:
                    _complete_login(result)
                    st.rerun()
            except ApiClientError:
                st.error("メールアドレスまたはパスワードが正しくありません")
    return False


def _check_mfa_verify() -> bool:
    """MFA検証画面。TOTPコードまたはバックアップコードを入力して認証を完了する。"""
    st.markdown(
        "<h2 style='text-align:center; margin-top:2rem;'>🔐 二要素認証</h2>"
        "<p style='text-align:center; color:#64748b;'>認証アプリのコードを入力してください</p>",
        unsafe_allow_html=True,
    )
    with st.form("mfa_form"):
        code = st.text_input("認証コード（6桁）またはバックアップコード", max_chars=16)
        col1, col2 = st.columns(2)
        with col1:
            submitted = st.form_submit_button("認証", use_container_width=True)
        with col2:
            cancelled = st.form_submit_button("キャンセル", use_container_width=True)

        if cancelled:
            st.session_state.pop("mfa_pending", None)
            st.session_state.pop("mfa_token", None)
            st.rerun()

        if submitted and code:
            mfa_token = st.session_state.get("mfa_token", "")
            try:
                result = api_client.mfa_verify(mfa_token, code)
                st.session_state.pop("mfa_pending", None)
                st.session_state.pop("mfa_token", None)
                _complete_login(result)
                st.rerun()
            except ApiClientError:
                st.error("認証コードが正しくありません。もう一度お試しください。")
    return False


def _complete_login(result: dict) -> None:
    """ログイン成功時の共通処理: セッションステートにトークンとユーザー情報を保存する。"""
    st.session_state["authenticated"] = True
    st.session_state["access_token"] = result["access_token"]
    st.session_state["refresh_token"] = result["refresh_token"]
    st.session_state["user"] = result["user"]
    api_client.set_auth_token(result["access_token"])


def _check_has_users() -> bool:
    """DBにユーザーが1人でも登録されているか確認する。"""
    try:
        from storage import get_user_by_email
        # seed-admin のデフォルトメールで存在チェック（軽量な方法）
        admin_email = os.environ.get("ADMIN_EMAIL", "admin@example.com")
        if get_user_by_email(admin_email):
            return True
        # 任意のユーザーが存在するかの簡易チェック
        from db import get_engine, init_db, users
        import sqlalchemy as sa
        init_db()
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(sa.select(users.c.id).limit(1)).fetchone()
        return row is not None
    except Exception:
        return False


def _check_legacy_password() -> bool:
    """レガシーの共通パスワード認証（ユーザー未登録時のフォールバック）。"""
    password = st.secrets.get("password", "")
    if not password:
        return True

    st.markdown(
        "<h2 style='text-align:center; margin-top:2rem;'>📝 国語 採点支援</h2>"
        "<p style='text-align:center; color:#64748b;'>ログインしてください</p>",
        unsafe_allow_html=True,
    )
    with st.form("login_form"):
        entered = st.text_input("パスワード", type="password")
        submitted = st.form_submit_button("ログイン", use_container_width=True)
        if submitted:
            if entered == password:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("パスワードが正しくありません")
    return False
