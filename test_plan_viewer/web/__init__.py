"""Flask delivery layer for the test-plan viewer."""

from test_plan_viewer.web.application import create_application
from test_plan_viewer.web.auth import (
    AuthWebServices,
    create_auth_blueprint,
)
from test_plan_viewer.web.index import index_blueprint
from test_plan_viewer.web.page_inventory import (
    PageInventoryWebServices,
    create_page_inventory_blueprint,
)
from test_plan_viewer.web.platform_records import (
    PlatformRecordServices,
    create_platform_records_blueprint,
)
from test_plan_viewer.web.project_archive import (
    ProjectArchiveWebServices,
    create_project_archive_blueprint,
)
from test_plan_viewer.web.projects import (
    ProjectWebServices,
    create_projects_blueprint,
)
from test_plan_viewer.web.requirements import (
    RequirementWebServices,
    create_requirements_blueprint,
)
from test_plan_viewer.web.setup import (
    SetupWebServices,
    create_setup_blueprint,
)
from test_plan_viewer.web.test_suites import (
    TestSuiteWebServices,
    create_test_suites_blueprint,
)

__all__ = [
    "AuthWebServices",
    "PageInventoryWebServices",
    "PlatformRecordServices",
    "ProjectArchiveWebServices",
    "ProjectWebServices",
    "RequirementWebServices",
    "SetupWebServices",
    "TestSuiteWebServices",
    "create_application",
    "create_auth_blueprint",
    "create_page_inventory_blueprint",
    "create_platform_records_blueprint",
    "create_project_archive_blueprint",
    "create_projects_blueprint",
    "create_requirements_blueprint",
    "create_setup_blueprint",
    "create_test_suites_blueprint",
    "index_blueprint",
]
