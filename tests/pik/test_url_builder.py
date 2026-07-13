from app.parsing.schema import Criteria, HousingType, MatchedEntity, Rooms, Sort
from app.pik.url_builder import build_url


def test_build_url_empty_criteria():
    assert build_url(Criteria()) == "https://www.pik.ru/search"


def test_build_url_tz_example():
    # Эталон из ТЗ: two-room + priceTo=15000000 + метро «Аэропорт Внуково» + finish + price_asc
    # -> https://www.pik.ru/search/two-room/finish/m-aeroport-vnukovo?priceFrom=0&priceTo=15000000&sortBy=price&orderBy=asc
    criteria = Criteria(
        rooms=[Rooms.TWO],
        price_max=15000000,
        metro=[MatchedEntity(name="Аэропорт Внуково", slug="aeroport-vnukovo", id="guid-123")],
        finish=True,
        sort=Sort.PRICE_ASC,
    )
    url = build_url(criteria)
    assert (
        url
        == "https://www.pik.ru/search/two-room/finish/m-aeroport-vnukovo?priceFrom=0&priceTo=15000000&sortBy=price&orderBy=asc"
    )


def test_build_url_live_example():
    # Живой эталон: 2-комн., 10–15 млн, м. Аэропорт Внуково, area desc
    # -> .../search/two-room/m-aeroport-vnukovo?priceFrom=10000000&priceTo=...
    criteria = Criteria(
        rooms=[Rooms.TWO],
        price_min=10000000,
        price_max=15000000,
        metro=[MatchedEntity(name="Аэропорт Внуково", slug="aeroport-vnukovo", id="guid-123")],
        sort=Sort.AREA_DESC,
    )
    url = build_url(criteria)
    assert (
        url
        == "https://www.pik.ru/search/two-room/m-aeroport-vnukovo?priceFrom=10000000&priceTo=15000000&sortBy=area&orderBy=desc"
    )


def test_build_url_single_vs_multi_rooms():
    # Single
    url = build_url(Criteria(rooms=[Rooms.STUDIO]))
    assert url == "https://www.pik.ru/search/studio"

    # Multi
    url = build_url(Criteria(rooms=[Rooms.STUDIO, Rooms.ONE, Rooms.TWO]))
    assert url == "https://www.pik.ru/search?rooms=-1%2C1%2C2"


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
    assert url == "https://www.pik.ru/search?districtCounties=9%2C6"

    # Single metro
    url = build_url(Criteria(metro=[MatchedEntity(name="Внуково", slug="vnukovo", id="m-1")]))
    assert url == "https://www.pik.ru/search/m-vnukovo"

    # Multi metro
    url = build_url(
        Criteria(
            metro=[
                MatchedEntity(name="Внуково", slug="vnukovo", id="m-1"),
                MatchedEntity(name="Динамо", slug="dinamo", id="m-2"),
            ]
        )
    )
    assert url == "https://www.pik.ru/search?metroStations=m-1%2Cm-2"

    # Mix county and metro (multi logic applies if len total > 1)
    url = build_url(
        Criteria(
            counties=[MatchedEntity(name="ЗАО", slug="zao", id="9")],
            metro=[MatchedEntity(name="Внуково", slug="vnukovo", id="m-1")],
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
    assert "optionGroups=smartHomePIK%2Cbalcony" in url
    assert "options=vidNaVodu" in url
    assert "type=1" in url
    assert "status=free" in url
