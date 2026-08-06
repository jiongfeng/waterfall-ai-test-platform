"""Flask delivery for login, session enforcement, and auth administration."""

from dataclasses import dataclass
from typing import Callable, Sequence

from flask import (
    Blueprint,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
)

from test_plan_viewer.auth.model import (
    has_any_permission,
    is_auth_public_endpoint,
    required_permissions_for_endpoint,
)
from test_plan_viewer.auth.policy import build_disabled_auth_payload
from test_plan_viewer.auth.service import AuthNotFoundError
from test_plan_viewer.web.security import (
    csrf_error_response,
    issue_csrf_token,
    validate_csrf_request,
)


@dataclass(frozen=True)
class AuthWebServices:
    """Explicit auth operations consumed by the Flask delivery layer."""

    get_auth_config: Callable
    load_current_user: Callable
    load_user_permission_codes: Callable
    build_auth_payload: Callable
    authenticate: Callable
    list_roles: Callable
    create_role: Callable
    update_role: Callable
    list_users: Callable
    create_user: Callable
    update_user: Callable
    reset_user_password: Callable
    menu_permissions: Sequence[dict]


def auth_web_services_from_service(auth_service):
    """Adapt an ``AuthService`` to the explicit web callback surface."""

    return AuthWebServices(
        get_auth_config=auth_service.get_auth_config,
        load_current_user=auth_service.load_current_user,
        load_user_permission_codes=(
            auth_service.load_user_permission_codes
        ),
        build_auth_payload=auth_service.build_auth_payload,
        authenticate=auth_service.authenticate,
        list_roles=auth_service.list_roles,
        create_role=auth_service.create_role,
        update_role=auth_service.update_role,
        list_users=auth_service.list_users,
        create_user=auth_service.create_user,
        update_user=auth_service.update_user,
        reset_user_password=auth_service.reset_user_password,
        menu_permissions=auth_service.menu_permissions,
    )


def load_current_user_from_session(services):
    user_id = session.get("user_id")
    if not user_id:
        return None

    user = services.load_current_user(user_id)
    if not user:
        session.clear()
        return None
    return user


def auth_error_response(message, status_code):
    if request.path.startswith("/api/"):
        payload = {"error": message}
        if status_code == 401:
            payload["redirect"] = "/login"
        return jsonify(payload), status_code
    if status_code == 401:
        return redirect("/login")
    return (
        render_template(
            "login.html",
            config_error=message,
            csrf_token=issue_csrf_token(),
        ),
        status_code,
    )


def create_auth_blueprint(services):
    """Create auth routes and the application-wide request guard."""

    if not isinstance(services, AuthWebServices):
        raise TypeError(
            "services must be an AuthWebServices instance"
        )

    blueprint = Blueprint("auth", __name__)

    @blueprint.before_app_request
    def require_authenticated_user():
        try:
            auth = services.get_auth_config()
        except RuntimeError as exc:
            if request.path.startswith("/static/"):
                return None
            return auth_error_response(str(exc), 500)

        if (
            not auth.get("enabled")
            or is_auth_public_endpoint(
                request.endpoint,
                request.method,
            )
        ):
            return None

        try:
            user = load_current_user_from_session(services)
        except Exception as exc:
            return auth_error_response(
                f"读取登录状态失败：{exc}",
                500,
            )

        if not user:
            return auth_error_response("Please sign in.", 401)

        g.current_user = user
        try:
            g.current_permissions = set(
                services.load_user_permission_codes(user["id"])
            )
        except Exception as exc:
            return auth_error_response(
                f"读取用户权限失败：{exc}",
                500,
            )

        if request.path.startswith("/api/"):
            required_permissions = required_permissions_for_endpoint(
                request.endpoint,
                request.method,
            )
            is_allowed = (
                required_permissions is not None
                and has_any_permission(
                    g.current_permissions,
                    required_permissions,
                )
            )
        else:
            is_allowed = True

        if not is_allowed:
            if request.path.startswith("/api/"):
                return (
                    jsonify(
                        {
                            "error": (
                                "当前账号没有访问该功能的权限。"
                            )
                        }
                    ),
                    403,
                )
            return redirect("/")

        return None

    @blueprint.before_app_request
    def require_valid_csrf_request():
        try:
            auth = services.get_auth_config()
        except RuntimeError:
            # The authentication guard above returns the stable configuration
            # error before this guard is reached.
            return None

        if (
            not auth.get("enabled")
            or validate_csrf_request()
        ):
            return None
        return csrf_error_response()

    @blueprint.get("/login")
    def login_page():
        try:
            auth = services.get_auth_config()
            if not auth.get("enabled"):
                return redirect("/")
            if load_current_user_from_session(services):
                return redirect("/")
            return render_template(
                "login.html",
                config_error=None,
                csrf_token=issue_csrf_token(),
            )
        except Exception as exc:
            return (
                render_template(
                    "login.html",
                    config_error=f"Sign-in configuration unavailable: {exc}",
                    csrf_token=issue_csrf_token(),
                ),
                500,
            )

    @blueprint.post("/api/auth/login")
    def auth_login():
        try:
            auth = services.get_auth_config()
            if not auth.get("enabled"):
                return (
                    jsonify(
                        {"error": "Authentication is disabled."}
                    ),
                    400,
                )

            payload = request.get_json(silent=True) or {}
            user = services.authenticate(
                payload.get("username"),
                str(payload.get("password") or ""),
            )
            if not user:
                return (
                    jsonify({"error": "Invalid username or password."}),
                    401,
                )

            session.clear()
            session["user_id"] = int(user["id"])
            session["username"] = user["username"]
            return jsonify(
                {
                    **services.build_auth_payload(user),
                    "error": None,
                }
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return (
                jsonify({"error": f"Sign-in failed: {exc}"}),
                500,
            )

    @blueprint.post("/api/auth/logout")
    def auth_logout():
        session.clear()
        return jsonify({"ok": True, "error": None})

    @blueprint.get("/api/auth/me")
    def auth_me():
        if not services.get_auth_config().get("enabled"):
            return jsonify(
                {
                    **build_disabled_auth_payload(
                        services.menu_permissions
                    ),
                    "error": None,
                }
            )
        user = getattr(g, "current_user", None)
        if not user:
            return jsonify({"error": "请先登录。"}), 401
        return jsonify(
            {
                **services.build_auth_payload(
                    user,
                    list(
                        getattr(
                            g,
                            "current_permissions",
                            set(),
                        )
                    ),
                ),
                "error": None,
            }
        )

    @blueprint.get("/api/admin/permissions")
    def list_auth_permissions():
        return jsonify(
            {
                "permissions": services.menu_permissions,
                "error": None,
            }
        )

    @blueprint.get("/api/admin/roles")
    def list_auth_roles():
        try:
            return jsonify(
                {
                    "roles": services.list_roles(),
                    "error": None,
                }
            )
        except Exception as exc:
            return (
                jsonify({"error": f"读取角色失败：{exc}"}),
                500,
            )

    @blueprint.post("/api/admin/roles")
    def create_auth_role():
        payload = request.get_json(silent=True) or {}
        try:
            role_id = services.create_role(payload)
            return jsonify(
                {
                    "ok": True,
                    "role_id": role_id,
                    "error": None,
                }
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return (
                jsonify({"error": f"创建角色失败：{exc}"}),
                500,
            )

    @blueprint.put("/api/admin/roles/<int:role_id>")
    def update_auth_role(role_id):
        payload = request.get_json(silent=True) or {}
        try:
            updated_role_id = services.update_role(
                role_id,
                payload,
            )
            return jsonify(
                {
                    "ok": True,
                    "role_id": updated_role_id,
                    "error": None,
                }
            )
        except AuthNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return (
                jsonify({"error": f"保存角色失败：{exc}"}),
                500,
            )

    @blueprint.get("/api/admin/users")
    def list_auth_users():
        try:
            return jsonify(
                {
                    "users": services.list_users(),
                    "error": None,
                }
            )
        except Exception as exc:
            return (
                jsonify({"error": f"读取用户失败：{exc}"}),
                500,
            )

    @blueprint.post("/api/admin/users")
    def create_auth_user():
        payload = request.get_json(silent=True) or {}
        try:
            user_id = services.create_user(payload)
            return jsonify(
                {
                    "ok": True,
                    "user_id": user_id,
                    "error": None,
                }
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return (
                jsonify({"error": f"创建用户失败：{exc}"}),
                500,
            )

    @blueprint.put("/api/admin/users/<int:user_id>")
    def update_auth_user(user_id):
        payload = request.get_json(silent=True) or {}
        try:
            current_user = getattr(g, "current_user", {})
            updated_user_id = services.update_user(
                user_id,
                payload,
                current_user_id=current_user.get("id"),
            )
            return jsonify(
                {
                    "ok": True,
                    "user_id": updated_user_id,
                    "error": None,
                }
            )
        except AuthNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return (
                jsonify({"error": f"保存用户失败：{exc}"}),
                500,
            )

    @blueprint.post(
        "/api/admin/users/<int:user_id>/reset-password"
    )
    def reset_auth_user_password(user_id):
        payload = request.get_json(silent=True) or {}
        try:
            updated_user_id = services.reset_user_password(
                user_id,
                payload,
            )
            return jsonify(
                {
                    "ok": True,
                    "user_id": updated_user_id,
                    "error": None,
                }
            )
        except AuthNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return (
                jsonify({"error": f"重置密码失败：{exc}"}),
                500,
            )

    return blueprint
