from askdata.agent.understanding import QuestionUnderstanding


def test_understanding_extracts_time_dimension_metric_filter_and_topn():
    result = QuestionUnderstanding().Parse("本月按部门查看销售额前5名，只看华东")
    assert result["time_range"] == "本月"
    assert result["dimensions"] == ["部门"]
    assert "销售额" in result["metrics"]
    assert result["filters"] == [{"expression": "华东", "source": "explicit"}]
    assert result["top_n"] == 5
    assert result["sort"] == "desc"


def test_follow_up_inherits_and_overrides_structured_context():
    understanding = QuestionUnderstanding()
    previous = understanding.Parse("本月按部门查看销售额，只看华东")
    current = understanding.Resolve("改成上个月", previous)
    assert current["time_range"] == "上个月"
    assert current["dimensions"] == ["部门"]
    assert current["filters"] == [{"expression": "华东", "source": "explicit"}]


def test_follow_up_can_clear_filters_and_dimensions():
    understanding = QuestionUnderstanding()
    previous = understanding.Parse("本月按部门查看销售额，只看华东")
    current = understanding.Resolve("不限制华东，取消部门分组", previous)
    assert current["filters"] == []
    assert current["dimensions"] == []


def test_follow_up_explicit_only_filter_replaces_previous_exclusive_filter():
    understanding = QuestionUnderstanding()
    previous = understanding.Parse("本月按部门查看销售额，只看华东")

    current = understanding.Resolve("只看华南", previous)

    assert current["filters"] == [{"expression": "华南", "source": "explicit"}]
