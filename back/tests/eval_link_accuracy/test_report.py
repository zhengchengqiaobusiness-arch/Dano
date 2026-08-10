from .report import evaluate


def test_hand_labeled_link_evaluation_has_ten_cases_and_reports_metrics():
    report = evaluate()
    assert report["case_count"] >= 10
    assert 0 <= report["precision"] <= 1
    assert 0 <= report["recall"] <= 1
    assert report["truth_count"] >= 8
