"""
Authentication Service — Flask Session-based login/logout with role control
"""
from functools import wraps
from flask import session, jsonify, request
from werkzeug.security import check_password_hash

from database import query_one, execute


def login_user(username: str, password: str) -> dict:
    """Validate credentials and create session. Returns result dict."""
    user = query_one("SELECT * FROM users WHERE username = ?", (username,))
    if not user:
        return {'success': False, 'message': '账号不存在'}

    if not check_password_hash(user['password_hash'], password):
        # Log failed attempt
        execute(
            "INSERT INTO audit_logs (user_id, username, action, detail) VALUES (?, ?, ?, ?)",
            (user['id'], username, 'login_failed', '密码错误'),
        )
        return {'success': False, 'message': '密码错误'}

    # Create session
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['role'] = user['role']
    session.permanent = True

    # Log successful login
    ip = request.remote_addr or 'unknown'
    execute(
        "INSERT INTO audit_logs (user_id, username, action, detail, ip_address) VALUES (?, ?, ?, ?, ?)",
        (user['id'], username, 'login', '登录成功', ip),
    )

    return {
        'success': True,
        'user': {
            'id': user['id'],
            'username': user['username'],
            'role': user['role'],
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
    """Get current logged-in user from session."""
    if 'user_id' not in session:
        return None
    return {
        'id': session['user_id'],
        'username': session['username'],
        'role': session['role'],
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
    """Decorator: require admin role."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': '未登录'}), 401
        if session.get('role') != 'admin':
            return jsonify({'error': '权限不足，仅管理员可操作'}), 403
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
