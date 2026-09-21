"""
Authentication Service — Flask Session-based login/logout with role control.

The v3 permission contract has three roles:
- admin: device/configuration management and incident handling
- operator: incident handling and full Web read access
- user: paired APP access only (device scoping is implemented by v3 APIs)
"""
from functools import wraps
from flask import session, jsonify, request
from werkzeug.security import check_password_hash

from database import query_one, execute
from contracts import Role, enum_values


ALLOWED_ROLES = set(enum_values(Role))


def effective_role(username: str, stored_role: str | None = None) -> str:
    """Normalize a role read from the trusted user record or signed session."""
    if stored_role in ALLOWED_ROLES:
        return str(stored_role)
    return Role.USER.value


def login_user(username: str, password: str) -> dict:
    """Validate credentials and create session. Returns result dict."""
    user = query_one("SELECT * FROM users WHERE username = ?", (username,))
    if not user:
        return {'success': False, 'message': '账号不存在'}

    profile_table = query_one(
        "SELECT 1 AS present FROM sqlite_master "
        "WHERE type='table' AND name='v3_mobile_user_profiles'"
    )
    if profile_table:
        mobile_profile = query_one(
            "SELECT mobile_only FROM v3_mobile_user_profiles WHERE user_id=?",
            (user['id'],),
        )
        if mobile_profile and bool(mobile_profile['mobile_only']):
            execute(
                "INSERT INTO audit_logs (user_id, username, action, detail) "
                "VALUES (?, ?, ?, ?)",
                (
                    user['id'], username, 'login_failed',
                    'mobile_only_web_login_denied',
                ),
            )
            return {'success': False, 'message': '该账号仅支持 APP 配对登录'}

    if not check_password_hash(user['password_hash'], password):
        execute(
            "INSERT INTO audit_logs (user_id, username, action, detail) VALUES (?, ?, ?, ?)",
            (user['id'], username, 'login_failed', '密码错误'),
        )
        return {'success': False, 'message': '密码错误'}

    role = effective_role(user['username'], user.get('role'))
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
    role = effective_role(username, session.get('role'))
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


def require_roles(*allowed_roles: str):
    """Build a decorator that permits only the specified normalized roles."""
    allowed = {
        role.value if isinstance(role, Role) else str(role)
        for role in allowed_roles
    }

    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user_id' not in session:
                return jsonify({'error': '未登录，请先登录'}), 401
            role = effective_role(session.get('username', ''), session.get('role'))
            session['role'] = role
            if role not in allowed:
                return jsonify({'error': '权限不足'}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


require_admin = require_roles(Role.ADMIN)
require_operator = require_roles(Role.ADMIN, Role.OPERATOR)


def log_action(action: str, detail: str = ''):
    """Log a user action to audit_logs."""
    user_id = session.get('user_id')
    username = session.get('username', 'system')
    execute(
        "INSERT INTO audit_logs (user_id, username, action, detail) VALUES (?, ?, ?, ?)",
        (user_id, username, action, detail),
    )
