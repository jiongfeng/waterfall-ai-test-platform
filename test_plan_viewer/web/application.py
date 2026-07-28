"""Flask application construction shared by deployment and tests."""

import os
import secrets

from flask import Flask

from test_plan_viewer.configuration import APP_DIR, parse_boolean
from test_plan_viewer.web.index import index_blueprint
from test_plan_viewer.web.security import install_web_security


def create_application(import_name="test_plan_viewer"):
    """Create the Flask shell and register framework-owned page routes."""

    application = Flask(
        import_name,
        template_folder=str(APP_DIR / "templates"),
        static_folder=str(APP_DIR / "static"),
    )
    application.secret_key = (
        os.environ.get("PLATFORM_SESSION_SECRET")
        or secrets.token_urlsafe(48)
    )
    application.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_NAME=(
            os.environ.get("PLATFORM_SESSION_COOKIE_NAME")
            or "session"
        ),
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=parse_boolean(
            os.environ.get("PLATFORM_COOKIE_SECURE"),
            True,
        ),
    )
    install_web_security(application)
    application.register_blueprint(index_blueprint)
    return application
