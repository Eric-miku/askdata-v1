from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.analysis.result_analyzer import ResultAnalyzer


class FakeLLM:
    def __init__(self, text=None, error=None):
        self.text = text
        self.error = error
        self.prompt = None

    def Complete(self, prompt):
        self.prompt = prompt
        if self.error:
            raise self.error
        return self.text


def test_analyzer_uses_llm_for_chinese_answer():
    llm = FakeLLM("共有 2 所学校。")
    analyzer = ResultAnalyzer(llm_client=llm)

    answer = analyzer.Analyze(
        "有多少学校？",
        "SELECT COUNT(*) AS count FROM schools",
        ["count"],
        [{"count": 2}],
    )

    assert answer == "共有 2 所学校。"
    assert "请用中文" in llm.prompt
    assert "SELECT COUNT(*) AS count FROM schools" in llm.prompt


def test_analyzer_falls_back_when_llm_fails_for_empty_rows():
    analyzer = ResultAnalyzer(llm_client=FakeLLM(error=RuntimeError("offline")))

    answer = analyzer.Analyze("列出学校", "SELECT name FROM schools", ["name"], [])

    assert answer == "查询没有返回结果。"


def test_analyzer_falls_back_for_single_cell_result():
    analyzer = ResultAnalyzer(llm_client=FakeLLM(error=RuntimeError("offline")))

    answer = analyzer.Analyze("有多少学校？", "SELECT COUNT(*) AS count FROM schools", ["count"], [{"count": 2}])

    assert answer == "查询结果是 2。"


def test_analyzer_falls_back_for_multi_row_result():
    analyzer = ResultAnalyzer(llm_client=FakeLLM(error=RuntimeError("offline")))

    answer = analyzer.Analyze("列出学校", "SELECT name FROM schools", ["name"], [{"name": "A"}, {"name": "B"}])

    assert answer == "查询返回 2 行结果。"
