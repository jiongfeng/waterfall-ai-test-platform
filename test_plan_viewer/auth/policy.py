"""Framework-independent authentication policy helpers."""


def build_disabled_auth_payload(menu_permissions):
    """Return the unrestricted browser context used when login is disabled."""

    permissions = [
        permission["code"]
        for permission in menu_permissions
    ]
    menus = [
        permission["section"]
        for permission in menu_permissions
    ]
    return {
        "user": None,
        "is_admin": False,
        "permissions": permissions,
        "menus": menus,
    }
