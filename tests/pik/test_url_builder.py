"""Tests for app.pik.url_builder."""

from app.parsing.schema import Criteria, HousingType, MatchedEntity, Rooms, Sort
from app.pik.url_builder import build_url


def test_build_url_empty_criteria():
    criteria = Criteria()
    url = build_url(criteria)
    assert url == "https://www.pik.ru/search"


def test_build_url_etalon_from_tz():
    # Эталон из ТЗ: two-room + priceTo=15000000 + метро «Аэропорт Внуково» + finish + price_asc
    # -> https://www.pik.ru/search/two-room/finish/m-aeroport-vnukovo?priceFrom=0&priceTo=15000000&sortBy=price&orderBy=asc
    criteria = Criteria(
        rooms=[Rooms.TWO],
        price_max=15000000,
        metro=[
            MatchedEntity(name="Аэропорт Внуково", slug="m-aeroport-vnukovo", id="some-guid")
        ],
        finish=True,
        sort=Sort.PRICE_ASC,
    )
    url = build_url(criteria)
    assert (
        url
        == "https://www.pik.ru/search/two-room/finish/m-aeroport-vnukovo?priceFrom=0&priceTo=15000000&sortBy=price&orderBy=asc"
    )


def test_build_url_live_etalon():
    # Живой эталон: 2-комн., 10–15 млн, м. Аэропорт Внуково, area desc
    # -> .../search/two-room/m-aeroport-vnukovo?priceFrom=10000000...
    criteria = Criteria(
        rooms=[Rooms.TWO],
        price_min=10000000,
        price_max=15000000,
        metro=[
            MatchedEntity(name="Аэропорт Внуково", slug="m-aeroport-vnukovo", id="some-guid")
        ],
        sort=Sort.AREA_DESC,
    )
    url = build_url(criteria)
    assert (
        url
        == "https://www.pik.ru/search/two-room/m-aeroport-vnukovo?priceFrom=10000000&priceTo=15000000&sortBy=area&orderBy=desc"
    )


def test_build_url_multiple_rooms_and_counties():
    # single -> путь; 2+ той же категории -> query (комнатность, округа, метро).
    criteria = Criteria(
        rooms=[Rooms.ONE, Rooms.TWO],
        counties=[
            MatchedEntity(name="ЗАО", slug="zao", id="6"),
            MatchedEntity(name="ВАО", slug="vao", id="9"),
        ],
    )
    url = build_url(criteria)
    assert url == "https://www.pik.ru/search?rooms=1,2&districtCounties=6,9"


def test_build_url_districts_and_complexes_always_query():
    # районы всегда в query; ЖК как blocks
    criteria = Criteria(
        districts=[MatchedEntity(name="Район 1", id="203")],
        complexes=[MatchedEntity(name="ЖК 1", id="1108")],
    )
    url = build_url(criteria)
    assert url == "https://www.pik.ru/search?blocks=1108&districtLocations=203"


def test_build_url_all_filters():
    # тип/статус/этаж/площадь/год сдачи
    criteria = Criteria(
        housing_type=HousingType.FLATS_ONLY,
        only_available=True,
        floor_min=2,
        floor_max=10,
        not_first_floor=True,
        area_min=40,
        settlement_year_to=2025,
        settlement_month_from=3,
        current_benefit="mortgage",
        option_groups=["smartHomePIK"],
    )
    url = build_url(criteria)
    assert "floorFrom=2" in url
    assert "floorTo=10" in url
    assert "notFirstFloor=1" in url
    assert "areaFrom=40.0" in url
    assert "settlementYearTo=2025" in url
    assert "settlementMonthFrom=3" in url
    assert "currentBenefit=mortgage" in url
    assert "optionGroups=smartHomePIK" in url
    assert "type=1" in url
    assert "status=free" in url
