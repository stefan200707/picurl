from app.parsing.schema import Criteria, HousingType, MatchedEntity, Rooms, Sort
from app.pik.url_builder import build_url


def test_build_url_empty_criteria():
    assert build_url(Criteria()) == "https://www.pik.ru/search"


def test_build_url_tz_example():
    # Эталон из ТЗ: two-room + priceTo=15000000 + метро «Аэропорт Внуково» + finish + price_asc
    # -> /search/two-room/finish/m-aeroport-vnukovo?priceFrom=0&priceTo=15000000&sortBy=price...
    criteria = Criteria(
        rooms=[Rooms.TWO],
        price_max=15000000,
        metro=[MatchedEntity(name="Аэропорт Внуково", slug="m-aeroport-vnukovo", id="guid-123")],
        finish=[1],
        sort=Sort.PRICE_ASC,
    )
    url = build_url(criteria)
    expected = (
        "https://www.pik.ru/search/two-room/finish/m-aeroport-vnukovo"
        "?priceFrom=0&priceTo=15000000&sortBy=price&orderBy=asc"
    )
    assert url == expected


def test_build_url_live_example():
    # Живой эталон: 2-комн., 10–15 млн, м. Аэропорт Внуково, area desc
    # -> .../search/two-room/m-aeroport-vnukovo?priceFrom=10000000&priceTo=...
    criteria = Criteria(
        rooms=[Rooms.TWO],
        price_min=10000000,
        price_max=15000000,
        metro=[MatchedEntity(name="Аэропорт Внуково", slug="m-aeroport-vnukovo", id="guid-123")],
        sort=Sort.AREA_DESC,
    )
    url = build_url(criteria)
    expected = (
        "https://www.pik.ru/search/two-room/m-aeroport-vnukovo"
        "?priceFrom=10000000&priceTo=15000000&sortBy=area&orderBy=desc"
    )
    assert url == expected


def test_build_url_single_vs_multi_rooms():
    # Single
    url = build_url(Criteria(rooms=[Rooms.STUDIO]))
    assert url == "https://www.pik.ru/search/studio"

    # Multi
    url = build_url(Criteria(rooms=[Rooms.STUDIO, Rooms.ONE, Rooms.TWO]))
    assert url == "https://www.pik.ru/search?rooms=-1,1,2"


def test_build_url_single_vs_multi_location():
    # Single county
    url = build_url(Criteria(counties=[MatchedEntity(name="ЗАО", slug="zao", id="9")]))
    assert url == "https://www.pik.ru/search/zao"

    # Multi county
    url = build_url(
        Criteria(
            counties=[
                MatchedEntity(name="ЗАО", slug="zao", id="9"),
                MatchedEntity(name="САО", slug="sao", id="6"),
            ]
        )
    )
    assert url == "https://www.pik.ru/search?districtCounties=9,6"

    # Single metro
    url = build_url(Criteria(metro=[MatchedEntity(name="Внуково", slug="m-vnukovo", id="m-1")]))
    assert url == "https://www.pik.ru/search/m-vnukovo"

    # Multi metro
    url = build_url(
        Criteria(
            metro=[
                MatchedEntity(name="Внуково", slug="m-vnukovo", id="m-1"),
                MatchedEntity(name="Динамо", slug="m-dinamo", id="m-2"),
            ]
        )
    )
    assert url == "https://www.pik.ru/search?metroStations=m-1,m-2"

    # Mix county and metro (multi logic applies if len total > 1)
    url = build_url(
        Criteria(
            counties=[MatchedEntity(name="ЗАО", slug="zao", id="9")],
            metro=[MatchedEntity(name="Внуково", slug="m-vnukovo", id="m-1")],
        )
    )
    assert url == "https://www.pik.ru/search?districtCounties=9&metroStations=m-1"


def test_build_url_districts_and_complexes_always_query():
    # Single district -> query
    url = build_url(Criteria(districts=[MatchedEntity(name="Солнцево", slug="solncevo", id="101")]))
    assert url == "https://www.pik.ru/search?districtLocations=101"

    # Single complex -> query
    url = build_url(Criteria(complexes=[MatchedEntity(name="ЖК Тест", slug="test", id="202")]))
    assert url == "https://www.pik.ru/search?blocks=202"

    # Complex + room
    url = build_url(
        Criteria(
            rooms=[Rooms.ONE],
            complexes=[MatchedEntity(name="ЖК Тест", slug="test", id="202")],
        )
    )
    assert url == "https://www.pik.ru/search/one-room?blocks=202"


def test_build_url_other_query_params():
    criteria = Criteria(
        area_min=50.5,
        area_max=100.0,
        area_kitchen_min=10,
        area_kitchen_max=20,
        floor_min=2,
        floor_max=5,
        not_first_floor=True,
        last_floor=True,
        time_on_foot=15,
        time_on_transport=20,
        settlement_year_from=2025,
        settlement_year_to=2026,
        settlement_month_from=1,
        settlement_month_to=6,
        current_benefit="rassrochka",
        option_groups=["smartHomePIK", "balcony"],
        options=["vidNaVodu"],
        housing_type=HousingType.FLATS_ONLY,
        only_available=True,
        ready=True,
    )
    url = build_url(criteria)
    assert "ready" in url
    assert "areaFrom=50.5" in url
    assert "areaTo=100.0" in url
    assert "areaKitchenFrom=10" in url
    assert "areaKitchenTo=20" in url
    assert "floorFrom=2" in url
    assert "floorTo=5" in url
    assert "notFirstFloor=1" in url
    assert "lastFloor=1" in url
    assert "timeOnFoot=15" in url
    assert "timeOnTransport=20" in url
    assert "settlementYearFrom=2025" in url
    assert "settlementYearTo=2026" in url
    assert "settlementMonthFrom=1" in url
    assert "settlementMonthTo=6" in url
    assert "currentBenefit=rassrochka" in url
    assert "optionGroups=smartHomePIK,balcony" in url
    assert "options=vidNaVodu" in url
    assert "type=1" in url
    assert "status=free" in url


# ---------------------------------------------------------------------------
# Этаж: одиночная граница остаётся ОДИНОЧНОЙ
# ---------------------------------------------------------------------------
#
# Замер 2026-07-28 (docs/measurements/2026-07-28-floor-from-to.md): floorFrom=N
# означает «этаж N и выше», floorTo=N — «до N включительно», и фильтр «Этаж» в UI
# самого pik.ru при вводе только нижнего значения генерирует ровно `?floorFrom=7`,
# без floorTo. Дополнять одиночную границу второй (максимальной этажностью,
# константой, чем угодно) — значит разойтись с эталоном сайта.
#
# Тест закрепляет СОСТАВ параметров в URL, а не семантику выдачи pik.ru: семантика
# человекопроверяема только кликом по ссылке, автотестом она не покрывается.


def test_build_url_floor_min_alone_emits_no_floor_to():
    """«от 7 этажа» → floorFrom=7 и НИКАКОГО floorTo."""
    url = build_url(Criteria(rooms=[Rooms.ONE], floor_min=7))
    assert "floorFrom=7" in url
    assert "floorTo" not in url


def test_build_url_floor_max_alone_emits_no_floor_from():
    """Зеркальный случай: «не выше 12 этажа» → floorTo=12 и никакого floorFrom."""
    url = build_url(Criteria(rooms=[Rooms.ONE], floor_max=12))
    assert "floorTo=12" in url
    assert "floorFrom" not in url


# ---------------------------------------------------------------------------
# Гео-фолбэк ПЕРЕСЕКАЕТСЯ с уже выбранными ЖК, а не объединяется с ними
# ---------------------------------------------------------------------------
#
# Найдено при проверке отчёта по сложному корпусу: «двушку внутри МКАД около
# Патриарших прудов» давало 8 ЖК от ориентира ∪ 27 ЖК внутри МКАД = все 27, то
# есть более узкое требование «около Патриарших» исчезало молча. Объединение не
# складывало два сужения одной и той же оси («какие ЖК»), а стирало сильнейшее —
# тот же класс дефекта, что потеря суперлатива при POI.


def test_geo_fallback_intersects_with_selected_complexes():
    """ЖК внутри МКАД + он же выбран семантикой запроса → остаётся один, не 27."""
    from app.geo.candidates import complexes_in_mkad

    inside = complexes_in_mkad(True)
    assert inside, "нет ЖК внутри МКАД — тест потерял смысл, проверь mkad_ring.json"
    chosen = inside[0]

    url = build_url(Criteria(within_mkad=True, complexes=[MatchedEntity(name="X", id=chosen)]))

    assert f"blocks={chosen}&" in url or url.endswith(f"blocks={chosen}")


def test_geo_fallback_empty_intersection_warns_and_keeps_specific():
    """Несовместимые гео-условия: не расширяемся до объединения, а предупреждаем.

    Расширение и было багом. Оставляем более специфичный список (тот, что пришёл
    из семантики запроса), и говорим об этом вслух — инвариант 1.
    """
    from app.geo.candidates import complexes_in_mkad

    outside = complexes_in_mkad(False)
    assert outside, "нет ЖК за МКАД — тест потерял смысл"
    chosen = outside[0]
    warnings: list[str] = []

    # ЖК заведомо ЗА МКАД, а запрос требует ВНУТРИ — пересечение пусто.
    url = build_url(
        Criteria(within_mkad=True, complexes=[MatchedEntity(name="X", id=chosen)]), warnings
    )

    assert f"blocks={chosen}" in url
    assert any("не пересекаются" in w for w in warnings)


def test_geo_fallback_alone_still_fills_blocks():
    """Без выбранных ЖК фолбэк по-прежнему наполняет blocks целиком."""
    from app.geo.candidates import complexes_in_mkad

    url = build_url(Criteria(within_mkad=True))

    assert "blocks=" in url
    assert len(url.split("blocks=")[1].split("&")[0].split(",")) == len(complexes_in_mkad(True))


# ---------------------------------------------------------------------------
# Дефект №1: пустой матч ориентира не должен молча теряться при AND с МКАД
# ---------------------------------------------------------------------------
#
# Repro: «двушку внутри МКАД рядом с Третьяковкой не дальше 1,5 км» — ориентир
# РЕАЛЬНО участвовал (жёсткая отсечка дистанции) и дал легитимный ноль
# (ближайший ЖК ПИК — 4.18 км). До фикса criteria.complexes оставался нетронут
# (== [] неотличимо от «ориентир не считали»), и build_url подставлял ВЕСЬ
# МКАД-список, будто ориентира не было вовсе.
#
# Д1 (2026-08-04) исход считаного нуля пересмотрел: пустой ``blocks=`` на pik.ru
# не сужает выдачу, а СНИМАЕТ фильтр — «ноль ЖК» превращался в «весь город»,
# исход ШИРЕ МКАД-списка. Теперь отдаётся МКАД-список, но считаный ноль
# по-прежнему отличим от «не считали» — теперь по warning'у, а не по пустоте.


def test_geo_fallback_counted_zero_falls_back_to_mkad_with_honest_warning():
    """complexes_matched_empty=True + within_mkad=True → МКАД-список + честный warning."""
    from app.geo.candidates import complexes_in_mkad
    from app.pik.location_fallback import LOCATION_ZERO_MATCH_NOT_APPLIED_WARNING

    full_mkad = complexes_in_mkad(True)
    assert full_mkad, "нет ЖК внутри МКАД — тест потерял смысл"

    warnings: list[str] = []
    url = build_url(Criteria(within_mkad=True, complexes_matched_empty=True), warnings)

    got = url.split("blocks=")[1].split("&")[0]
    assert got.split(",") == full_mkad, "фильтр по ЖК обязан остаться в ссылке"
    # Ноль ПОСЧИТАН, и требование, давшее его, не применено — об этом сказано.
    assert LOCATION_ZERO_MATCH_NOT_APPLIED_WARNING in warnings


def test_geo_fallback_without_landmark_flag_still_uses_full_mkad():
    """Контраст: ориентир НЕ участвовал вовсе (флаг не выставлен) — тот же список,
    но БЕЗ предупреждения: сообщать не о чем, требования не было."""
    from app.geo.candidates import complexes_in_mkad
    from app.pik.location_fallback import LOCATION_ZERO_MATCH_NOT_APPLIED_WARNING

    warnings: list[str] = []
    url = build_url(Criteria(within_mkad=True, complexes_matched_empty=False), warnings)

    assert "blocks=" in url
    assert len(url.split("blocks=")[1].split("&")[0].split(",")) == len(complexes_in_mkad(True))
    assert LOCATION_ZERO_MATCH_NOT_APPLIED_WARNING not in warnings


# --- Г1: заметка о гео-фолбэке не должна утверждать то, чего в ссылке нет ----
# Фолбэк по координатам станции честно считает ближайшие ЖК, но пересечение с
# другим гео-условием может их отбросить. Заметку при этом печатали безусловно,
# и выдача сообщала «показаны 8 ближайших» о ЖК, которых в blocks уже нет.
# Удалять заметку нельзя (инвариант 1: факт расчёта не должен пропадать) —
# поэтому она остаётся, но перестаёт врать.


def _unverified_metro(name: str) -> "MatchedEntity":
    from app.parsing.schema import MatchedEntity

    return MatchedEntity(name=name, slug=None, id=None)


def test_metro_fallback_note_marks_ids_that_did_not_reach_url():
    """Отсев фолбэка более специфичным списком (правило AND) — с оговоркой в заметке.

    Повод для оговорки после Д1 остался ровно один: НЕПУСТОЙ специфичный список
    (здесь — явно названный ЖК), который с фолбэком не пересёкся. Считаный ноль
    сюда больше не относится: он теперь отдаёт список фолбэка, и заметка о
    «показаны N ближайших» становится правдой, а не требует оговорки.
    """
    from app.parsing.schema import MatchedEntity

    warnings: list[str] = []
    criteria = Criteria(
        metro=[_unverified_metro("ВДНХ")],
        complexes=[MatchedEntity(name="Заведомо другой ЖК", id="999999")],
    )

    url = build_url(criteria, warnings)

    assert url.split("blocks=")[1].split("&")[0] == "999999"
    notes = [w for w in warnings if "ближайших" in w]
    assert notes, "заметка о расчёте фолбэка обязана остаться"
    assert "в ссылку не попали" in notes[0]


def test_metro_fallback_note_stays_plain_when_ids_reach_url():
    """Контраст: ЖК фолбэка доехали до blocks — заметка без оговорки."""
    warnings: list[str] = []
    criteria = Criteria(metro=[_unverified_metro("ВДНХ")])

    url = build_url(criteria, warnings)

    assert url.split("blocks=")[1].split("&")[0] != ""
    notes = [w for w in warnings if "ближайших" in w]
    assert notes
    assert "в ссылку не попали" not in notes[0]
