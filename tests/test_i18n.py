from test_plan_viewer.i18n import localize_platform_error


def test_english_translates_known_platform_errors():
    assert localize_platform_error("需求不存在。", "en") == "Requirement not found."
    assert (
        localize_platform_error("创建 Agent 任务失败：需求不存在。", "en")
        == "Could not create Agent task: Requirement not found."
    )


def test_chinese_and_unknown_errors_are_preserved():
    assert localize_platform_error("需求不存在。", "zh-CN") == "需求不存在。"
    third_party = "浏览器连接被目标系统拒绝"
    assert localize_platform_error(third_party, "en") == third_party
