from app.ai.usage_report import CallLogRow, aggregate, format_report


def _row(had, fully, cache, ai, changed) -> CallLogRow:
    return CallLogRow(
        had_poi_or_center=had,
        fully_resolved_deterministically=fully,
        cache_hit=cache,
        ai_called=ai,
        criteria_changed_by_ai=changed,
    )


#: Фикстура с известным распределением полей (10 строк):
#: had_poi_or_center=6, из них fully_resolved=3, cache_hit=2, ai_called=6,
#: criteria_changed=5 (все внутри ai_called), из них без POI/центра=1.
FIXTURE_ROWS = [
    _row(True, True, False, True, True),
    _row(True, True, False, True, True),
    _row(True, True, False, True, False),
    _row(True, False, True, False, False),
    _row(True, False, False, True, True),
    _row(True, False, False, True, True),
    _row(False, False, True, False, False),
    _row(False, False, False, True, True),
    _row(False, False, False, False, False),
    _row(False, False, False, False, False),
]


def test_aggregate_counts():
    report = aggregate(FIXTURE_ROWS)
    assert report.total == 10
    assert report.had_poi_or_center == 6
    assert report.fully_resolved_within_had == 3
    assert report.cache_hit == 2
    assert report.ai_called == 6
    assert report.criteria_changed_by_ai == 5
    assert report.criteria_changed_within_ai_called == 5
    assert report.changed_without_poi_or_center == 1


def test_aggregate_percentages():
    report = aggregate(FIXTURE_ROWS)
    assert report.pct_had_poi_or_center == 60.0
    # Кандидаты на «гейт 2 спас бы вызов»: 3 из 6 запросов с POI/центром.
    assert report.pct_fully_resolved_within_had == 50.0
    assert report.pct_cache_hit == 20.0
    # Реальная польза: 5 из 6 вызовов ИИ реально изменили criteria.
    assert report.pct_criteria_changed_within_ai_called == 83.3
    # Метрика гейта 1: 1 из 5 изменений — для запросов без POI/центра.
    assert report.pct_changed_without_poi_or_center == 20.0


def test_aggregate_empty():
    report = aggregate([])
    assert report.total == 0
    # Нулевые знаменатели не должны падать делением на ноль.
    assert report.pct_had_poi_or_center == 0.0
    assert report.pct_fully_resolved_within_had == 0.0
    assert report.pct_criteria_changed_within_ai_called == 0.0


def test_format_report_empty():
    text = format_report(aggregate([]), days=None)
    assert "данных пока нет" in text


def test_format_report_has_period_and_numbers():
    text = format_report(aggregate(FIXTURE_ROWS), days=30)
    assert "за последние 30 дн." in text
    assert "60.0%" in text
    assert "50.0%" in text
