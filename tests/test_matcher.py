"""Tests for the keyword matcher."""

from __future__ import annotations

from monitor.matcher import KeywordMatcher


def test_urgent_match(sample_keywords_yaml):
    m = KeywordMatcher(str(sample_keywords_yaml))
    severity, matched = m.match("회사 구조조정 안내")
    assert severity == "urgent"
    assert "구조조정" in matched


def test_watch_match(sample_keywords_yaml):
    m = KeywordMatcher(str(sample_keywords_yaml))
    severity, matched = m.match("두 회사 간 인수합병 발표")
    assert severity == "watch"
    assert "인수합병" in matched


def test_info_match(sample_keywords_yaml):
    m = KeywordMatcher(str(sample_keywords_yaml))
    severity, matched = m.match("회사가 정기 공시를 제출했다")
    assert severity == "info"
    assert "공시" in matched


def test_default_when_no_match(sample_keywords_yaml):
    m = KeywordMatcher(str(sample_keywords_yaml))
    severity, matched = m.match("Hello world unrelated text")
    assert severity == "info"
    assert matched == []


def test_priority_urgent_over_watch(sample_keywords_yaml):
    m = KeywordMatcher(str(sample_keywords_yaml))
    severity, matched = m.match("인수합병과 동시에 구조조정 단행")
    assert severity == "urgent"
    # only urgent-tier matches are returned
    assert "구조조정" in matched
    assert "인수합병" not in matched


def test_empty_text_returns_default(sample_keywords_yaml):
    m = KeywordMatcher(str(sample_keywords_yaml))
    assert m.match("") == ("info", [])
    assert m.match(None) == ("info", [])  # type: ignore[arg-type]


def test_missing_yaml_returns_default(tmp_path):
    m = KeywordMatcher(str(tmp_path / "does_not_exist.yaml"))
    severity, matched = m.match("anything 구조조정")
    assert severity == "info"
    assert matched == []
