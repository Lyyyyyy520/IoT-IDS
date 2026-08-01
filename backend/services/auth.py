"""
Authentication Service — Flask Session-based login/logout with role control.

Permission rule:
- username == "admin": administrator
- every other authenticated account: normal user
"""
from functools import wraps
from flask import session, jsonify, request
from werkzeug.security import check_password_hash

from database import query_one, execute


def effective_role(username: str) -> str:
    """Return the effective role derived from the account name."""
    return 'admin' if username == 'admin' else 'user'


def login_user(username: str, password: str) -> dict:
    """Validate credentials and create session. Returns result dict."""
    user = query_one("SELECT * FROM users WHERE username = ?", (username,))
    if not user:
        return {'success': False, 'message': '账号不存在'}

    if not check_password_hash(user['password_hash'], password):
        execute(
            "INSERT INTO audit_logs (user_id, username, action, detail) VALUES (?, ?, ?, ?)",
            (user['id'], username, 'login_failed', '密码错误'),
        )
        return {'success': False, 'message': '密码错误'}

    role = effective_role(user['username'])
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['role'] = role
    session.permanent = True

    ip = request.remote_addr or 'unknown'
    execute(
        "INSERT INTO audit_logs (user_id, username, action, detail, ip_address) VALUES (?, ?, ?, ?, ?)",
        (user['id'], username, 'login', '登录成功', ip),
    )

    return {
        'success': True,
        'message': '登录成功',
        'user': {
            'id': user['id'],
            'username': user['username'],
            'role': role,
        },
    }


def logout_user():
    """Clear session and log."""
    username = session.get('username', 'unknown')
    user_id = session.get('user_id')
    if user_id:
        execute(
            "INSERT INTO audit_logs (user_id, username, action, detail) VALUES (?, ?, ?, ?)",
            (user_id, username, 'logout', '登出'),
        )
    session.clear()
    return {'success': True}


def get_current_user() -> dict | None:
    """Get current logged-in user and normalize its effective role."""
    if 'user_id' not in session:
        return None

    username = session.get('username', '')
    role = effective_role(username)
    session['role'] = role
    return {
        'id': session['user_id'],
        'username': username,
        'role': role,
    }


def require_auth(f):
    """Decorator: require valid login session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': '未登录，请先登录'}), 401
        return f(*args, **kwargs)
    return decorated


def require_admin(f):
    """Decorator: require the account named 'admin'."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': '未登录，请先登录'}), 401
        if session.get('username') != 'admin':
            return jsonify({'error': '权限不足，仅管理员可操作'}), 403
        session['role'] = 'admin'
        return f(*args, **kwargs)
    return decorated


def log_action(action: str, detail: str = ''):
    """Log a user action to audit_logs."""
    user_id = session.get('user_id')
    username = session.get('username', 'system')
    execute(
        "INSERT INTO audit_logs (user_id, username, action, detail) VALUES (?, ?, ?, ?)",
        (user_id, username, action, detail),
    )
