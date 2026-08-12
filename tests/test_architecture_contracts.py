import unittest
from collections import Counter
from html.parser import HTMLParser
from urllib.parse import urlsplit
from unittest.mock import patch

import app


# These URL/method pairs are consumed by the browser client or are documented
# entry points.  Endpoint function names and module locations are deliberately
# not part of the contract, so Flask views can move into blueprints freely.
REQUIRED_ROUTE_CONTRACTS = {
    ("GET", "/"),
    ("GET", "/login"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/auth/me"),
    ("GET", "/api/admin/permissions"),
    ("GET", "/api/admin/roles"),
    ("POST", "/api/admin/roles"),
    ("PUT", "/api/admin/roles/<int:role_id>"),
    ("GET", "/api/admin/users"),
    ("POST", "/api/admin/users"),
    ("PUT", "/api/admin/users/<int:user_id>"),
    ("POST", "/api/admin/users/<int:user_id>/reset-password"),
    ("GET", "/api/projects"),
    ("POST", "/api/projects"),
    ("GET", "/api/projects/export"),
    ("POST", "/api/projects/import"),
    ("GET", "/api/project-settings"),
    ("PUT", "/api/project-settings"),
    ("PUT", "/api/project-language"),
    ("POST", "/api/project-settings/seed/generate"),
    ("POST", "/api/project-settings/seed/test"),
    ("POST", "/api/project-settings/database/test-connection"),
    ("POST", "/api/project-settings/database/test-restore"),
    ("GET", "/api/setup-scripts"),
    ("POST", "/api/setup-scripts"),
    ("PUT", "/api/setup-scripts/<script_uid>"),
    ("DELETE", "/api/setup-scripts/<script_uid>"),
    ("POST", "/api/setup-scripts/<script_uid>/trial-run"),
    ("GET", "/api/setup-bindings"),
    ("POST", "/api/setup-bindings"),
    ("PUT", "/api/setup-bindings/<binding_uid>"),
    ("DELETE", "/api/setup-bindings/<binding_uid>"),
    ("GET", "/api/setup-runs"),
    ("GET", "/api/requirements"),
    ("POST", "/api/requirements/upload"),
    ("GET", "/api/requirements/<requirement_uid>"),
    ("GET", "/api/requirements/<requirement_uid>/download"),
    ("DELETE", "/api/requirements/<requirement_uid>"),
    ("POST", "/api/requirements/<requirement_uid>/analysis-stream"),
    ("GET", "/api/requirements/<requirement_uid>/modules"),
    ("PUT", "/api/requirements/<requirement_uid>/modules/<module_uid>"),
    ("DELETE", "/api/requirements/<requirement_uid>/modules/<module_uid>"),
    (
        "POST",
        "/api/requirements/<requirement_uid>/modules/<module_uid>/generate-plan-stream",
    ),
    ("GET", "/api/page-inventory"),
    ("POST", "/api/page-inventory"),
    ("PUT", "/api/page-inventory/<inventory_uid>"),
    ("DELETE", "/api/page-inventory/<inventory_uid>"),
    ("POST", "/api/page-inventory/import-from-doc"),
    ("GET", "/api/modules"),
    ("GET", "/api/modules/<path:module_name>"),
    ("PUT", "/api/modules/<path:module_name>"),
    ("GET", "/api/plans/<module_name>/<plan_filename>"),
    ("PUT", "/api/plans/<module_name>/<plan_filename>"),
    ("DELETE", "/api/plans/<module_name>/<plan_filename>"),
    ("POST", "/api/plans/<module_name>/<plan_filename>/split-cases"),
    ("GET", "/api/plan-generation-defaults"),
    ("POST", "/api/plan-generation-stream"),
    ("POST", "/api/plan-generation-jobs"),
    ("GET", "/api/plan-generation-jobs/<job_id>"),
    ("POST", "/api/script-generation-stream"),
    ("POST", "/api/script-run-stream"),
    ("POST", "/api/script-run-cancel"),
    ("POST", "/api/script-executions"),
    ("POST", "/api/script-execution-stream"),
    ("POST", "/api/module-script-execution-stream"),
    ("POST", "/api/test-suite-execution-stream"),
    ("POST", "/api/script-recordings"),
    ("GET", "/api/test-scripts"),
    ("GET", "/api/test-scripts/<path:module_name>/<path:filename>"),
    ("PUT", "/api/test-scripts/<path:module_name>/<path:filename>"),
    ("DELETE", "/api/test-scripts/<path:module_name>/<path:filename>"),
    ("GET", "/api/test-suites"),
    ("POST", "/api/test-suites"),
    ("GET", "/api/test-suites/<suite_uid>"),
    ("PUT", "/api/test-suites/<suite_uid>"),
    ("DELETE", "/api/test-suites/<suite_uid>"),
    ("GET", "/api/test-suites/<suite_uid>/execution-records"),
    ("POST", "/api/test-suites/<suite_uid>/execution-stream"),
    ("POST", "/api/test-suites/<suite_uid>/items"),
    ("DELETE", "/api/test-suites/<suite_uid>/items/<int:item_id>"),
    ("PUT", "/api/test-suites/<suite_uid>/items/reorder"),
    ("GET", "/api/jobs/<job_id>"),
    ("GET", "/api/jobs/<job_id>/log"),
    ("GET", "/api/jobs/<job_id>/log/download"),
    ("GET", "/api/assets/<int:asset_id>/revisions"),
    ("GET", "/api/assets/<int:asset_id>/revisions/<int:revision_id>/content"),
    (
        "GET",
        "/api/assets/<int:asset_id>/revisions/<int:revision_id>/diff-current",
    ),
    ("POST", "/api/assets/<int:asset_id>/revisions/<int:revision_id>/restore"),
    ("GET", "/api/platform-records"),
    ("PUT", "/api/platform-records/<bucket>/<path:record_key>"),
    ("GET", "/api/run-videos/<path:relative_path>"),
    ("GET", "/api/playwright-reports/<path:relative_path>"),
    ("GET", "/api/agent/runs"),
    ("POST", "/api/agent/runs"),
    ("GET", "/api/agent/runs/<run_id>"),
    (
        "GET",
        "/api/agent/runs/<run_id>/script-preparation",
    ),
    (
        "GET",
        "/api/agent/runs/<run_id>/script-items/<item_id>",
    ),
    (
        "POST",
        "/api/agent/runs/<run_id>/script-items/<item_id>/actions",
    ),
    (
        "POST",
        "/api/agent/runs/<run_id>/script-items/batch-actions",
    ),
    ("GET", "/api/agent/runs/<run_id>/attempts"),
    (
        "GET",
        "/api/agent/runs/<run_id>/attempts/<attempt_id>/diagnostic-bundle",
    ),
    ("POST", "/api/agent/runs/<run_id>/attempts/<attempt_id>/retry"),
    ("GET", "/api/agent/runs/<run_id>/events"),
    ("GET", "/api/agent/runs/<run_id>/events-stream"),
    ("POST", "/api/agent/runs/<run_id>/legacy-diagnostic-bundle"),
    ("POST", "/api/agent/runs/<run_id>/legacy-failure-attempt"),
    ("POST", "/api/agent/runs/<run_id>/cancel"),
    ("POST", "/api/agent/runs/<run_id>/resume"),
    ("GET", "/api/agent/runs/<run_id>/retry-flows"),
    ("GET", "/api/agent/retry-flows"),
    (
        "POST",
        "/api/agent/runs/<run_id>/retry-flows/<retry_flow_id>/cancel",
    ),
    (
        "POST",
        "/api/agent/runs/<run_id>/retry-flows/<retry_flow_id>/acknowledge",
    ),
}

REQUIRED_HTML_IDS = {
    "appShell",
    "projectSelect",
    "navRail",
    "requirementsNav",
    "plansNav",
    "scriptsNav",
    "testSuitesNav",
    "agentNav",
    "projectSettingsNav",
    "usersNav",
    "rolesNav",
    "moduleList",
    "viewerArea",
    "userAdminPanel",
    "roleAdminPanel",
    "projectSettingsPanel",
    "agentPanel",
    "scriptTabs",
    "planTabs",
    "testSuiteListPanel",
    "testSuiteDetailPanel",
    "modulePlanPanel",
    "requirementsPanel",
    "preview",
    "scriptPreview",
    "editor",
    "planGenerationModal",
    "scriptGenerationModal",
    "projectManageModal",
    "projectDeleteModal",
    "testSuiteCreateModal",
    "executionModeModal",
    "testSuiteProgressModal",
}

REQUIRED_AGENT_IDS = {
    "currentRunMain",
    "currentRunTitle",
    "currentRunStatus",
    "runList",
    "newRunButton",
    "resumeButton",
    "cancelButton",
    "stepTimeline",
    "retryStatusBar",
    "runTitle",
    "runStatus",
    "artifactList",
    "eventLog",
    "executionResultPanel",
    "artifactModal",
    "artifactRetryButton",
    "newTaskModal",
    "launchForm",
    "requirementFile",
    "startButton",
    "notice",
}

REQUIRED_SCRIPT_PREPARATION_IDS = {
    "root",
    "stageMeta",
    "stageTitle",
    "stageSummary",
    "stageStatus",
    "bulkToggle",
    "filterBar",
    "searchInput",
    "batchBar",
    "batchMenu",
    "selectAll",
    "tableBody",
    "detailModal",
    "detailTitle",
    "historyList",
    "detailContent",
    "actionPanel",
    "editorModal",
    "editSection",
    "promptSection",
    "scriptEditor",
    "originalPrompt",
    "supplementalPrompt",
    "editorSave",
    "editorSaveExecute",
    "editorConfirm",
    "localNotice",
}

REQUIRED_STATIC_URLS = {
    "/static/css/features/agent-script-preparation.css",
    "/static/css/features/agent.css",
    "/static/styles.css",
    "/static/js/core/api-client.js",
    "/static/js/core/sse.js",
    "/static/js/core/timers.js",
    "/static/js/features/test-suites.js",
    "/static/js/features/requirements.js",
    "/static/js/features/platform-record-store.js",
    "/static/js/features/generation.js",
    "/static/js/features/script-repair.js",
    "/static/js/features/module-execution.js",
    "/static/js/features/module-plan-generation.js",
    "/static/js/features/admin.js",
    "/static/js/features/projects.js",
    "/static/js/features/project-settings.js",
    "/static/js/features/setup-preparation.js",
    "/static/js/features/agent-script-preparation.js",
    "/static/js/features/agent.js",
    "/static/app.js",
}


class HtmlContractParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_startendtag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def attribute_values(self, attribute):
        return [
            attrs[attribute]
            for _tag, attrs in self.elements
            if attrs.get(attribute) is not None
        ]


class ArchitectureContractTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    def render_index_without_external_services(self):
        with (
            patch.object(app, "get_auth_config", return_value={"enabled": False}),
            patch.object(
                app,
                "platform_mysql_connection",
                side_effect=AssertionError("index rendering must not access MySQL"),
            ),
            patch.object(
                app.urlrequest,
                "urlopen",
                side_effect=AssertionError("index rendering must not access the network"),
            ),
        ):
            return self.client.get("/")

    def test_critical_url_and_method_contracts_are_registered(self):
        registered_contracts = {
            (method, rule.rule)
            for rule in app.app.url_map.iter_rules()
            for method in rule.methods
            if method not in {"HEAD", "OPTIONS"}
        }

        missing_contracts = sorted(REQUIRED_ROUTE_CONTRACTS - registered_contracts)
        self.assertEqual(
            missing_contracts,
            [],
            "Missing stable Flask URL/method contracts: "
            + ", ".join(f"{method} {url}" for method, url in missing_contracts),
        )

    def test_agent_pipeline_exposes_exactly_seven_product_stages(self):
        self.assertEqual(
            app.AGENT_STEP_ORDER,
            [
                ("upload_requirement", "需求"),
                ("analyze_requirement", "需求解析"),
                ("review_modules", "模块审查"),
                ("generate_plans", "计划生成"),
                ("prepare_scripts", "脚本准备"),
                ("create_suite", "测试集"),
                ("run_suite", "执行"),
            ],
        )

    def test_index_renders_with_unique_required_dom_hooks(self):
        response = self.render_index_without_external_services()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")
        parser = HtmlContractParser()
        parser.feed(response.get_data(as_text=True))

        html_ids = parser.attribute_values("id")
        agent_ids = parser.attribute_values("data-agent-id")
        script_preparation_ids = parser.attribute_values(
            "data-script-preparation-id"
        )
        duplicate_html_ids = {
            value: count
            for value, count in Counter(html_ids).items()
            if count > 1
        }
        duplicate_agent_ids = {
            value: count
            for value, count in Counter(agent_ids).items()
            if count > 1
        }
        duplicate_script_preparation_ids = {
            value: count
            for value, count in Counter(script_preparation_ids).items()
            if count > 1
        }

        self.assertEqual(duplicate_html_ids, {}, "HTML id values must be unique")
        self.assertEqual(
            duplicate_agent_ids,
            {},
            "data-agent-id values must be unique within the rendered page",
        )
        self.assertEqual(
            duplicate_script_preparation_ids,
            {},
            "data-script-preparation-id values must be unique",
        )
        self.assertEqual(
            sorted(REQUIRED_HTML_IDS - set(html_ids)),
            [],
            "Required HTML hooks are missing",
        )
        self.assertEqual(
            sorted(REQUIRED_AGENT_IDS - set(agent_ids)),
            [],
            "Required Agent DOM hooks are missing",
        )
        self.assertEqual(
            sorted(
                REQUIRED_SCRIPT_PREPARATION_IDS
                - set(script_preparation_ids)
            ),
            [],
            "Required script-preparation DOM hooks are missing",
        )

    def test_index_references_static_assets_that_flask_can_serve(self):
        response = self.render_index_without_external_services()
        self.assertEqual(response.status_code, 200)

        parser = HtmlContractParser()
        parser.feed(response.get_data(as_text=True))
        referenced_urls = {
            urlsplit(attrs["href"]).path
            for tag, attrs in parser.elements
            if tag == "link" and attrs.get("href")
        }
        referenced_urls.update(
            urlsplit(attrs["src"]).path
            for tag, attrs in parser.elements
            if tag == "script" and attrs.get("src")
        )
        self.assertEqual(
            sorted(REQUIRED_STATIC_URLS - referenced_urls),
            [],
            "The index page must reference its required static assets",
        )
        self.assertNotIn(
            "/static/js/features/agent-failure-workspace.js",
            referenced_urls,
            "The removed failure workspace must not be loaded",
        )

        script_urls = [
            urlsplit(attrs["src"]).path
            for tag, attrs in parser.elements
            if tag == "script" and attrs.get("src")
        ]
        stylesheet_urls = [
            urlsplit(attrs["href"]).path
            for tag, attrs in parser.elements
            if tag == "link"
            and attrs.get("href")
            and "stylesheet" in attrs.get("rel", "").split()
        ]
        self.assertLess(
            stylesheet_urls.index("/static/styles.css"),
            stylesheet_urls.index("/static/css/features/agent.css"),
            "Feature styles must load after the shared stylesheet",
        )
        self.assertLess(
            script_urls.index("/static/js/core/api-client.js"),
            script_urls.index("/static/js/features/test-suites.js"),
            "Core factories must load before the test-suite feature factory",
        )
        self.assertLess(
            script_urls.index("/static/js/core/sse.js"),
            script_urls.index("/static/js/features/generation.js"),
            "The shared SSE parser must load before generation features",
        )
        self.assertLess(
            script_urls.index("/static/js/core/timers.js"),
            script_urls.index("/static/js/features/script-repair.js"),
            "The shared timer runtime must load before repair features",
        )
        self.assertLess(
            script_urls.index("/static/js/features/script-repair.js"),
            script_urls.index("/static/js/features/module-execution.js"),
            "Single-script operations must load before module execution is assembled",
        )
        self.assertLess(
            script_urls.index("/static/js/features/test-suites.js"),
            script_urls.index("/static/js/features/platform-record-store.js"),
            "Test-suite result helpers must load before the platform record store is assembled",
        )
        self.assertLess(
            script_urls.index("/static/js/features/test-suites.js"),
            script_urls.index("/static/app.js"),
            "Test-suite feature factory must load before the main application bootstrap",
        )
        self.assertLess(
            script_urls.index("/static/js/core/api-client.js"),
            script_urls.index("/static/js/features/requirements.js"),
            "Core factories must load before the requirements feature factory",
        )
        self.assertLess(
            script_urls.index("/static/js/features/requirements.js"),
            script_urls.index("/static/app.js"),
            "Requirements feature factory must load before the main application bootstrap",
        )
        self.assertLess(
            script_urls.index("/static/js/core/api-client.js"),
            script_urls.index("/static/js/features/platform-record-store.js"),
            "Core factories must load before the platform record store factory",
        )
        self.assertLess(
            script_urls.index("/static/js/features/platform-record-store.js"),
            script_urls.index("/static/app.js"),
            "Platform record store factory must load before the main application bootstrap",
        )
        self.assertLess(
            script_urls.index("/static/js/features/generation.js"),
            script_urls.index("/static/app.js"),
            "Generation feature factory must load before the main application bootstrap",
        )
        self.assertLess(
            script_urls.index("/static/js/features/script-repair.js"),
            script_urls.index("/static/app.js"),
            "Script repair feature factory must load before the main application bootstrap",
        )
        self.assertLess(
            script_urls.index("/static/js/features/module-execution.js"),
            script_urls.index("/static/app.js"),
            "Module execution feature factory must load before the main application bootstrap",
        )
        self.assertLess(
            script_urls.index("/static/js/features/module-plan-generation.js"),
            script_urls.index("/static/app.js"),
            "Module plan generation factory must load before the main application bootstrap",
        )
        for feature_url in (
            "/static/js/features/admin.js",
            "/static/js/features/projects.js",
            "/static/js/features/project-settings.js",
        ):
            with self.subTest(feature_url=feature_url):
                self.assertLess(
                    script_urls.index(feature_url),
                    script_urls.index("/static/app.js"),
                    "Administration and project factories must load before the main application bootstrap",
                )
        self.assertLess(
            script_urls.index("/static/js/core/api-client.js"),
            script_urls.index("/static/js/features/setup-preparation.js"),
            "Core factories must load before feature factories",
        )
        self.assertLess(
            script_urls.index("/static/js/core/api-client.js"),
            script_urls.index(
                "/static/js/features/agent-script-preparation.js"
            ),
            "Core factories must load before Agent script preparation",
        )
        self.assertLess(
            script_urls.index(
                "/static/js/features/agent-script-preparation.js"
            ),
            script_urls.index("/static/js/features/agent.js"),
            "Agent script preparation must load before Agent assembly",
        )
        self.assertLess(
            script_urls.index("/static/js/core/api-client.js"),
            script_urls.index("/static/js/features/agent.js"),
            "Core factories must load before feature factories",
        )
        self.assertLess(
            script_urls.index("/static/js/features/setup-preparation.js"),
            script_urls.index("/static/app.js"),
            "Setup preparation factory must load before the main application bootstrap",
        )
        self.assertLess(
            script_urls.index("/static/js/features/agent.js"),
            script_urls.index("/static/app.js"),
            "Feature factories must load before the main application bootstrap",
        )

        with patch.object(app, "get_auth_config", return_value={"enabled": False}):
            for static_url in sorted(REQUIRED_STATIC_URLS):
                with self.subTest(static_url=static_url):
                    static_response = self.client.get(static_url)
                    try:
                        self.assertEqual(static_response.status_code, 200)
                        self.assertTrue(static_response.data)
                    finally:
                        static_response.close()

    def test_agent_script_preparation_ui_is_owned_by_its_feature_factory(self):
        static_dir = app.APP_DIR / "static"
        feature_source = (
            static_dir
            / "js"
            / "features"
            / "agent-script-preparation.js"
        ).read_text(encoding="utf-8")
        agent_source = (
            static_dir / "js" / "features" / "agent.js"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "function createAgentScriptPreparationFeature(",
            feature_source,
        )
        self.assertIn(
            "window.createAgentScriptPreparationFeature = ",
            feature_source,
        )
        self.assertIn(
            "createAgentScriptPreparationFeature(",
            agent_source,
        )
        self.assertNotIn(
            "createAgentFailureWorkspace(",
            agent_source,
        )

    def test_platform_record_persistence_is_owned_by_its_feature_factory(self):
        static_dir = app.APP_DIR / "static"
        main_source = (static_dir / "app.js").read_text(encoding="utf-8")
        store_source = (
            static_dir / "js" / "features" / "platform-record-store.js"
        ).read_text(encoding="utf-8")

        self.assertIn("function createPlatformRecordStore(deps", store_source)
        self.assertIn(
            "const platformRecordStore = createPlatformRecordStore({",
            main_source,
        )
        self.assertIn("platformRecordStore.persistRecordMap({", main_source)
        self.assertIn("platformRecordStore.hydrate({", main_source)
        for function_name in (
            "queuePlatformRecordSave",
            "normalizeScriptRunRecord",
            "normalizeModuleExecutionRecord",
            "normalizeTestSuiteExecutionRecord",
            "loadScriptRunRecordsFromStorage",
        ):
            with self.subTest(function_name=function_name):
                self.assertIn(f"function {function_name}(", store_source)
                self.assertNotIn(f"function {function_name}(", main_source)

    def test_test_suite_ui_is_owned_by_its_feature_factory(self):
        static_dir = app.APP_DIR / "static"
        main_source = (static_dir / "app.js").read_text(encoding="utf-8")
        feature_source = (
            static_dir / "js" / "features" / "test-suites.js"
        ).read_text(encoding="utf-8")
        agent_source = (
            static_dir / "js" / "features" / "agent.js"
        ).read_text(encoding="utf-8")

        self.assertIn("function createTestSuiteResultHelpers(", feature_source)
        self.assertIn("function createTestSuitesFeature(deps)", feature_source)
        self.assertIn(
            "const testSuitesFeature = createTestSuitesFeature({",
            main_source,
        )
        self.assertIn("renderExecutionResultPanel,", main_source)
        self.assertIn(
            "const renderExecutionResultPanel = options.renderExecutionResultPanel;",
            agent_source,
        )
        for function_name in (
            "renderTestSuiteList",
            "renderTestSuiteDetail",
            "handleTestSuiteExecutionStreamEvent",
            "executeSelectedTestSuite",
            "loadTestSuites",
        ):
            with self.subTest(function_name=function_name):
                self.assertIn(f"function {function_name}(", feature_source)
                self.assertNotIn(f"function {function_name}(", main_source)

    def test_requirements_ui_is_owned_by_its_feature_factory(self):
        static_dir = app.APP_DIR / "static"
        main_source = (static_dir / "app.js").read_text(encoding="utf-8")
        feature_source = (
            static_dir / "js" / "features" / "requirements.js"
        ).read_text(encoding="utf-8")

        self.assertIn("function createRequirementsFeature(deps)", feature_source)
        self.assertIn(
            "const requirementsFeature = createRequirementsFeature({",
            main_source,
        )
        self.assertIn("parseSseBlock,", main_source)
        self.assertNotIn("function parseSseBlock(", feature_source)
        for function_name in (
            "renderRequirementList",
            "renderRequirementsPanel",
            "handleRequirementBatchPlanGenerationEvent",
            "loadRequirements",
            "uploadRequirementFile",
        ):
            with self.subTest(function_name=function_name):
                self.assertIn(f"function {function_name}(", feature_source)
                self.assertNotIn(f"function {function_name}(", main_source)

    def test_generation_ui_is_owned_by_its_feature_factory(self):
        static_dir = app.APP_DIR / "static"
        main_source = (static_dir / "app.js").read_text(encoding="utf-8")
        feature_source = (
            static_dir / "js" / "features" / "generation.js"
        ).read_text(encoding="utf-8")

        self.assertIn("function createGenerationFeature(deps)", feature_source)
        self.assertIn(
            "const generationFeature = createGenerationFeature({",
            main_source,
        )
        self.assertIn("parseSseBlock,", main_source)
        self.assertIn("timers: timerRuntime,", main_source)
        for function_name in (
            "composeCoveragePrompt",
            "setPlanGenerationRecord",
            "handlePlanStreamEvent",
            "handleScriptStreamEvent",
            "submitPlanGeneration",
            "submitScriptGeneration",
        ):
            with self.subTest(function_name=function_name):
                self.assertIn(f"function {function_name}(", feature_source)
                self.assertNotIn(f"function {function_name}(", main_source)

    def test_script_repair_ui_is_owned_by_its_feature_factory(self):
        static_dir = app.APP_DIR / "static"
        main_source = (static_dir / "app.js").read_text(encoding="utf-8")
        feature_source = (
            static_dir / "js" / "features" / "script-repair.js"
        ).read_text(encoding="utf-8")

        self.assertIn("function createScriptRepairFeature(deps)", feature_source)
        self.assertIn(
            "const scriptRepairFeature = createScriptRepairFeature({",
            main_source,
        )
        for function_name in (
            "setScriptRepairRecord",
            "handleScriptExecutionStreamEvent",
            "handleScriptRunStreamEvent",
            "executeSelectedScript",
            "submitScriptRun",
        ):
            with self.subTest(function_name=function_name):
                self.assertIn(f"function {function_name}(", feature_source)
                self.assertNotIn(f"function {function_name}(", main_source)

    def test_sse_parser_is_shared_by_stream_features(self):
        static_dir = app.APP_DIR / "static"
        main_source = (static_dir / "app.js").read_text(encoding="utf-8")
        sse_source = (static_dir / "js" / "core" / "sse.js").read_text(
            encoding="utf-8"
        )
        feature_sources = [
            (static_dir / "js" / "features" / name).read_text(encoding="utf-8")
            for name in (
                "agent.js",
                "generation.js",
                "module-plan-generation.js",
                "module-execution.js",
                "requirements.js",
                "script-repair.js",
                "test-suites.js",
            )
        ]

        self.assertIn("function parseSseBlock(block)", sse_source)
        self.assertNotIn("function parseSseBlock(", main_source)
        for feature_source in feature_sources:
            self.assertNotIn("function parseSseBlock(", feature_source)

    def test_module_execution_ui_is_owned_by_its_feature_factory(self):
        static_dir = app.APP_DIR / "static"
        main_source = (static_dir / "app.js").read_text(encoding="utf-8")
        feature_source = (
            static_dir / "js" / "features" / "module-execution.js"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "function createModuleExecutionFeature(deps)",
            feature_source,
        )
        self.assertIn(
            "const moduleExecutionFeature = createModuleExecutionFeature({",
            main_source,
        )
        self.assertIn("scriptRepair: scriptRepairFeature,", main_source)
        self.assertIn("timers: timerRuntime,", main_source)
        for function_name in (
            "setModuleExecutionRecord",
            "handleModuleExecutionStreamEvent",
            "handleModuleRepairStreamEvent",
            "executeSelectedModuleScripts",
            "repairSelectedModuleScripts",
            "renderModuleExecutionRecord",
            "renderModuleRepairRecord",
            "renderExecutionRecord",
        ):
            with self.subTest(function_name=function_name):
                self.assertIn(f"function {function_name}(", feature_source)
                self.assertNotIn(f"function {function_name}(", main_source)

    def test_module_plan_generation_ui_is_owned_by_its_feature_factory(self):
        static_dir = app.APP_DIR / "static"
        main_source = (static_dir / "app.js").read_text(encoding="utf-8")
        feature_source = (
            static_dir / "js" / "features" / "module-plan-generation.js"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "function createModulePlanGenerationFeature(deps)",
            feature_source,
        )
        self.assertIn(
            "const modulePlanGenerationFeature = createModulePlanGenerationFeature({",
            main_source,
        )
        self.assertIn("generation: generationFeature,", main_source)
        self.assertIn("moduleExecution: moduleExecutionFeature,", main_source)
        for function_name in (
            "getCurrentModulePlans",
            "deleteSelectedModulePlans",
            "setPlanScriptGenerationBatch",
            "handleModulePlanScriptStreamEvent",
            "generateSelectedModulePlanScripts",
            "renderModulePlanList",
            "renderModulePlanScriptBatchRecord",
        ):
            with self.subTest(function_name=function_name):
                self.assertIn(f"function {function_name}(", feature_source)
                self.assertNotIn(f"function {function_name}(", main_source)

    def test_administration_ui_is_owned_by_its_feature_factory(self):
        static_dir = app.APP_DIR / "static"
        main_source = (static_dir / "app.js").read_text(encoding="utf-8")
        feature_source = (
            static_dir / "js" / "features" / "admin.js"
        ).read_text(encoding="utf-8")

        self.assertIn("function createAdminFeature(deps)", feature_source)
        self.assertIn(
            "const adminFeature = createAdminFeature({",
            main_source,
        )
        self.assertIn(
            "renderProjectSelect: (...args) => renderProjectSelect(...args),",
            main_source,
        )
        for function_name in (
            "loadAuthContext",
            "renderNavigation",
            "loadAdminUsers",
            "saveAdminUser",
            "renderUserAdminPanel",
            "loadAdminRoles",
            "saveAdminRole",
            "renderRoleAdminPanel",
        ):
            with self.subTest(function_name=function_name):
                self.assertIn(f"function {function_name}(", feature_source)
                self.assertNotIn(f"function {function_name}(", main_source)

    def test_projects_ui_is_owned_by_its_feature_factory(self):
        static_dir = app.APP_DIR / "static"
        main_source = (static_dir / "app.js").read_text(encoding="utf-8")
        feature_source = (
            static_dir / "js" / "features" / "projects.js"
        ).read_text(encoding="utf-8")

        self.assertIn("function createProjectsFeature(deps)", feature_source)
        self.assertIn(
            "const projectsFeature = createProjectsFeature({",
            main_source,
        )
        self.assertIn("admin: adminFeature,", main_source)
        for function_name in (
            "normalizeProject",
            "resetProjectScopedState",
            "renderProjectSelect",
            "loadProjects",
            "submitProjectCreate",
            "submitProjectImport",
            "switchProject",
        ):
            with self.subTest(function_name=function_name):
                self.assertIn(f"function {function_name}(", feature_source)
                self.assertNotIn(f"function {function_name}(", main_source)

    def test_project_settings_ui_is_owned_by_its_feature_factory(self):
        static_dir = app.APP_DIR / "static"
        main_source = (static_dir / "app.js").read_text(encoding="utf-8")
        feature_source = (
            static_dir / "js" / "features" / "project-settings.js"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "function createProjectSettingsFeature(deps)",
            feature_source,
        )
        self.assertIn(
            "const projectSettingsFeature = createProjectSettingsFeature({",
            main_source,
        )
        self.assertIn("setupFeature,", main_source)
        self.assertIn("projects: projectsFeature,", main_source)
        self.assertIn("jobs: moduleExecutionFeature,", main_source)
        for function_name in (
            "normalizeTargetSystem",
            "collectProjectSettingsForm",
            "loadProjectSettings",
            "handleProjectSettingsStreamEvent",
            "generateProjectSeed",
            "renderProjectSettingsPanel",
        ):
            with self.subTest(function_name=function_name):
                self.assertIn(f"function {function_name}(", feature_source)
                self.assertNotIn(f"function {function_name}(", main_source)


if __name__ == "__main__":
    unittest.main()
