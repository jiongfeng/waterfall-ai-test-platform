"""Framework-independent authentication validation and serialization rules."""

import re

from test_plan_viewer.configuration import AUTH_USERNAME_PATTERN


AUTH_USER_STATUS_ACTIVE = "active"
AUTH_USER_STATUS_DISABLED = "disabled"
AUTH_VALID_USER_STATUSES = {
    AUTH_USER_STATUS_ACTIVE,
    AUTH_USER_STATUS_DISABLED,
}
AUTH_ROLE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{2,64}$")

AUTH_MENU_PERMISSIONS = [
    {
        "code": "menu.requirements",
        "name": "需求",
        "section": "requirements",
        "sort_order": 5,
    },
    {
        "code": "menu.plans",
        "name": "测试计划",
        "section": "plans",
        "sort_order": 10,
    },
    {
        "code": "menu.scripts",
        "name": "测试脚本",
        "section": "scripts",
        "sort_order": 20,
    },
    {
        "code": "menu.testSuites",
        "name": "测试集",
        "section": "testSuites",
        "sort_order": 30,
    },
    {
        "code": "menu.agent",
        "name": "Agent",
        "section": "agent",
        "sort_order": 35,
    },
    {
        "code": "menu.projectSettings",
        "name": "项目配置",
        "section": "projectSettings",
        "sort_order": 40,
    },
    {
        "code": "menu.users",
        "name": "用户管理",
        "section": "users",
        "sort_order": 90,
    },
    {
        "code": "menu.roles",
        "name": "角色管理",
        "section": "roles",
        "sort_order": 100,
    },
]
AUTH_MENU_PERMISSION_CODES = {
    permission["code"]
    for permission in AUTH_MENU_PERMISSIONS
}

AUTH_PUBLIC_ENDPOINT_METHODS = frozenset(
    {
        ("static", "GET"),
        ("auth.login_page", "GET"),
        ("auth.auth_login", "POST"),
    }
)


def _build_api_permission_policy(*groups):
    policy = {}
    for permission_codes, endpoint_methods in groups:
        required_permissions = frozenset(permission_codes)
        for endpoint, method in endpoint_methods:
            key = (endpoint, method.upper())
            if key in policy:
                raise ValueError(
                    f"API 权限策略重复登记：{endpoint} {method}"
                )
            policy[key] = required_permissions
    return policy


AUTH_API_PERMISSION_POLICY = _build_api_permission_policy(
    (
        (),
        (
            ("auth.auth_logout", "POST"),
            ("auth.auth_me", "GET"),
            ("projects.list_projects", "GET"),
        ),
    ),
    (
        ("menu.users",),
        (
            ("auth.list_auth_users", "GET"),
            ("auth.create_auth_user", "POST"),
            ("auth.update_auth_user", "PUT"),
            ("auth.reset_auth_user_password", "POST"),
        ),
    ),
    (
        ("menu.users", "menu.roles"),
        (
            ("auth.list_auth_permissions", "GET"),
            ("auth.list_auth_roles", "GET"),
            ("auth.create_auth_role", "POST"),
            ("auth.update_auth_role", "PUT"),
        ),
    ),
    (
        ("menu.projectSettings",),
        (
            ("projects.create_project", "POST"),
            ("projects.get_project_settings", "GET"),
            ("projects.save_project_settings", "PUT"),
            ("project_archive.export_project", "GET"),
            ("project_archive.import_project", "POST"),
            ("setup.list_setup_scripts", "GET"),
            ("setup.create_setup_script", "POST"),
            ("setup.update_setup_script", "PUT"),
            ("setup.delete_setup_script", "DELETE"),
            ("setup.trial_run_setup_script", "POST"),
            ("setup.list_setup_bindings", "GET"),
            ("setup.create_setup_binding", "POST"),
            ("setup.update_setup_binding", "PUT"),
            ("setup.delete_setup_binding", "DELETE"),
            ("setup.list_setup_runs", "GET"),
            ("test_project_database_connection", "POST"),
            ("test_project_database_restore", "POST"),
            ("generate_project_seed", "POST"),
            ("test_project_seed", "POST"),
        ),
    ),
    (
        ("menu.requirements",),
        (
            ("requirements.list_requirements", "GET"),
            ("requirements.upload_requirement", "POST"),
            ("requirements.get_requirement", "GET"),
            ("requirements.delete_requirement", "DELETE"),
            ("requirements.download_requirement", "GET"),
            ("requirements.get_requirement_modules", "GET"),
            ("requirements.put_requirement_module", "PUT"),
            ("requirements.remove_requirement_module", "DELETE"),
            ("analyze_requirement_stream", "POST"),
            (
                "generate_requirement_module_plan_stream",
                "POST",
            ),
            ("page_inventory.list_page_inventory", "GET"),
            ("page_inventory.create_page_inventory", "POST"),
            ("page_inventory.update_page_inventory", "PUT"),
            ("page_inventory.delete_page_inventory", "DELETE"),
            ("page_inventory.import_page_inventory", "POST"),
        ),
    ),
    (
        ("menu.plans",),
        (
            ("list_modules", "GET"),
            ("get_module", "GET"),
            ("save_module", "PUT"),
            ("get_plan", "GET"),
            ("save_plan", "PUT"),
            ("delete_plan", "DELETE"),
            ("split_plan_cases", "POST"),
            ("get_plan_generation_defaults", "GET"),
            ("create_plan_generation_job", "POST"),
            ("get_plan_generation_job", "GET"),
            ("create_plan_generation_stream", "POST"),
        ),
    ),
    (
        ("menu.scripts", "menu.testSuites"),
        (
            ("list_test_scripts", "GET"),
            ("get_test_script", "GET"),
            ("save_test_script", "PUT"),
            ("delete_test_script", "DELETE"),
            ("execute_module_test_scripts_stream", "POST"),
            ("get_playwright_report", "GET"),
            ("get_run_video", "GET"),
            ("execute_test_script_stream", "POST"),
            ("execute_test_script", "POST"),
            ("create_script_generation_stream", "POST"),
            ("record_test_script", "POST"),
            ("cancel_script_run_stream", "POST"),
            ("create_script_run_stream", "POST"),
        ),
    ),
    (
        ("menu.testSuites",),
        (
            ("execute_test_suite_stream", "POST"),
            ("test_suites.list_test_suites", "GET"),
            ("test_suites.create_test_suite", "POST"),
            ("test_suites.get_test_suite", "GET"),
            ("test_suites.update_test_suite", "PUT"),
            ("test_suites.delete_test_suite", "DELETE"),
            ("test_suites.add_test_suite_items", "POST"),
            ("test_suites.delete_test_suite_item", "DELETE"),
            ("test_suites.reorder_test_suite_items", "PUT"),
            ("list_test_suite_execution_records", "GET"),
            ("execute_persisted_test_suite_stream", "POST"),
        ),
    ),
    (
        ("menu.plans", "menu.scripts", "menu.testSuites"),
        (
            ("get_asset_revisions", "GET"),
            ("get_asset_revision_content", "GET"),
            ("get_asset_revision_diff", "GET"),
            ("restore_asset_revision", "POST"),
            ("get_job", "GET"),
            ("get_job_log", "GET"),
            ("download_job_log", "GET"),
        ),
    ),
    (
        ("menu.agent",),
        (
            ("get_project_agent_item_retry_flows_api", "GET"),
            ("list_agent_runs", "GET"),
            ("create_agent_run_api", "POST"),
            ("get_agent_run_api", "GET"),
            ("get_agent_run_attempts_api", "GET"),
            (
                "download_agent_attempt_diagnostic_bundle",
                "GET",
            ),
            ("retry_agent_generation_attempt_api", "POST"),
            ("cancel_agent_run_api", "POST"),
            ("agent_failures.continue_failure_checkpoint", "POST"),
            ("get_agent_run_events_api", "GET"),
            ("stream_agent_run_events_api", "GET"),
            ("agent_failures.get_failure_item", "GET"),
            ("agent_failures.delete_failure_item", "DELETE"),
            ("agent_failures.analyze_failure_item", "POST"),
            ("agent_failures.execute_failure_item", "POST"),
            ("agent_failures.ignore_failure_item", "POST"),
            ("agent_failures.retry_failure_item", "POST"),
            ("agent_failures.get_failure_item_script", "GET"),
            ("agent_failures.save_failure_item_script", "PATCH"),
            (
                "download_legacy_agent_failure_diagnostic_bundle",
                "POST",
            ),
            ("create_legacy_agent_failure_attempt_api", "POST"),
            ("resume_agent_run_api", "POST"),
            ("get_agent_item_retry_flows_api", "GET"),
            (
                "acknowledge_agent_item_retry_flow_api",
                "POST",
            ),
            ("cancel_agent_item_retry_flow_api", "POST"),
        ),
    ),
    (
        (
            "menu.requirements",
            "menu.plans",
            "menu.scripts",
            "menu.testSuites",
            "menu.agent",
            "menu.projectSettings",
        ),
        (
            ("platform_records.get_platform_records", "GET"),
            ("platform_records.save_platform_record", "PUT"),
        ),
    ),
)


def normalize_auth_status(value):
    status = (
        str(value or AUTH_USER_STATUS_ACTIVE).strip().lower()
        or AUTH_USER_STATUS_ACTIVE
    )
    if status not in AUTH_VALID_USER_STATUSES:
        raise ValueError("状态只能是 active 或 disabled。")
    return status


def validate_username(value):
    username = str(value or "").strip()
    if not AUTH_USERNAME_PATTERN.match(username):
        raise ValueError(
            "用户名必须是 3-64 位字母、数字、'.'、'_' 或 '-'。"
        )
    return username


def validate_role_code(value):
    code = str(value or "").strip()
    if not AUTH_ROLE_CODE_PATTERN.match(code):
        raise ValueError(
            "角色编码必须是 2-64 位字母、数字、'.'、'_' 或 '-'。"
        )
    return code


def normalize_display_name(value, fallback):
    display_name = str(value or "").strip() or fallback
    if len(display_name) > 128:
        raise ValueError("显示名称不能超过 128 个字符。")
    return display_name


def normalize_role_name(value):
    name = str(value or "").strip()
    if not name:
        raise ValueError("角色名称不能为空。")
    if len(name) > 128:
        raise ValueError("角色名称不能超过 128 个字符。")
    return name


def normalize_description(value):
    description = str(value or "").strip()
    if len(description) > 512:
        raise ValueError("描述不能超过 512 个字符。")
    return description


def normalize_password(value, required=True):
    password = str(value or "")
    if not password and not required:
        return ""
    if len(password) < 8:
        raise ValueError("密码长度不能少于 8 位。")
    if len(password) > 128:
        raise ValueError("密码长度不能超过 128 位。")
    return password


def normalize_id_list(value):
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError("ID 列表必须是数组。")

    ids = []
    seen = set()
    for item in value:
        try:
            item_id = int(item)
        except (TypeError, ValueError) as exc:
            raise ValueError("ID 必须是正整数。") from exc
        if item_id <= 0:
            raise ValueError("ID 必须是正整数。")
        if item_id not in seen:
            ids.append(item_id)
            seen.add(item_id)
    return ids


def normalize_permission_codes(value):
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError("权限列表必须是数组。")

    codes = []
    seen = set()
    for item in value:
        code = str(item or "").strip()
        if code not in AUTH_MENU_PERMISSION_CODES:
            raise ValueError(f"不支持的权限：{code}")
        if code not in seen:
            codes.append(code)
            seen.add(code)
    return codes


def serialize_user(row, roles=None):
    return {
        "id": int(row["id"]),
        "username": row["username"],
        "display_name": row.get("display_name") or row["username"],
        "status": row.get("status") or AUTH_USER_STATUS_DISABLED,
        "last_login_at": row.get("last_login_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "roles": roles or [],
    }


def serialize_role(row, permission_codes=None):
    return {
        "id": int(row["id"]),
        "code": row["code"],
        "name": row["name"],
        "description": row.get("description") or "",
        "status": row.get("status") or AUTH_USER_STATUS_DISABLED,
        "is_system": bool(row.get("is_system")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "permissions": permission_codes or [],
    }


def build_auth_payload(user, permission_codes):
    permission_set = set(permission_codes or [])
    return {
        "user": {
            "id": int(user["id"]),
            "username": user["username"],
            "display_name": (
                user.get("display_name")
                or user["username"]
            ),
        },
        "permissions": [
            permission["code"]
            for permission in AUTH_MENU_PERMISSIONS
            if permission["code"] in permission_set
        ],
        "menus": [
            permission["section"]
            for permission in AUTH_MENU_PERMISSIONS
            if permission["code"] in permission_set
        ],
    }


def normalize_authorization_method(method):
    normalized = str(method or "").strip().upper()
    if normalized == "HEAD":
        return "GET"
    return normalized


def required_permissions_for_endpoint(endpoint, method):
    key = (
        str(endpoint or "").strip(),
        normalize_authorization_method(method),
    )
    return AUTH_API_PERMISSION_POLICY.get(key)


def is_auth_public_endpoint(endpoint, method):
    key = (
        str(endpoint or "").strip(),
        normalize_authorization_method(method),
    )
    return key in AUTH_PUBLIC_ENDPOINT_METHODS


def has_any_permission(current_permissions, required_permissions):
    if not required_permissions:
        return True
    return bool(
        set(current_permissions or ()).intersection(
            required_permissions
        )
    )
