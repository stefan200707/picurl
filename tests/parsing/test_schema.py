"""Тесты контракта Criteria и моделей API (промпт 02)."""

import pytest
from pydantic import ValidationError

from app.main import BuildUrlRequest, BuildUrlResponse
from app.parsing.schema import Criteria, HousingType, MatchedEntity, Rooms, Sort


class TestCriteriaConstruction:
    def test_empty_criteria_is_valid(self) -> None:
        """«Пустой» Criteria() валиден: все скаляры None, списки пусты, флаги False."""
        criteria = Criteria()

        assert criteria.rooms == []
        assert criteria.price_min is None
        assert criteria.price_max is None
        assert criteria.area_min is None
        assert criteria.finish is None
        assert criteria.ready is None
        assert criteria.metro == []
        assert criteria.counties == []
        assert criteria.districts == []
        assert criteria.complexes == []
        assert criteria.sort is None
        assert criteria.housing_type is None
        assert criteria.not_first_floor is False
        assert criteria.last_floor is False
        assert criteria.only_available is False
        assert criteria.option_groups == []
        assert criteria.options == []

    def test_full_construction(self) -> None:
        criteria = Criteria(
            rooms=[Rooms.TWO, Rooms.THREE_PLUS],
            price_min=10_000_000,
            price_max=15_000_000,
            area_min=50.5,
            floor_min=5,
            not_first_floor=True,
            finish=True,
            metro=[
                MatchedEntity(
                    name="Аэропорт Внуково",
                    slug="aeroport-vnukovo",
                    id="7cd0e0e3-5be1-4a56-8b9c-000000000000",
                )
            ],
            time_on_foot=15,
            settlement_year_from=2027,
            settlement_month_to=8,
            sort=Sort.AREA_DESC,
            housing_type=HousingType.FLATS_ONLY,
            only_available=True,
        )

        assert criteria.rooms == [Rooms.TWO, Rooms.THREE_PLUS]
        assert criteria.metro[0].slug == "aeroport-vnukovo"
        assert criteria.sort is Sort.AREA_DESC

    def test_rooms_deduplicated_preserving_order(self) -> None:
        criteria = Criteria(rooms=[Rooms.TWO, Rooms.ONE, Rooms.TWO])
        assert criteria.rooms == [Rooms.TWO, Rooms.ONE]

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            Criteria(price_maximum=1)  # type: ignore[call-arg]

    @pytest.mark.parametrize("month", [0, 13])
    def test_settlement_month_out_of_range(self, month: int) -> None:
        with pytest.raises(ValidationError):
            Criteria(settlement_month_from=month)

    def test_negative_price_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Criteria(price_max=-1)


class TestSortEnum:
    @pytest.mark.parametrize(
        ("sort", "field", "order"),
        [
            (Sort.PRICE_ASC, "price", "asc"),
            (Sort.PRICE_DESC, "price", "desc"),
            (Sort.AREA_ASC, "area", "asc"),
            (Sort.AREA_DESC, "area", "desc"),
        ],
    )
    def test_field_and_order(self, sort: Sort, field: str, order: str) -> None:
        """Ключ сортировки раскладывается в значения sortBy/orderBy."""
        assert sort.field == field
        assert sort.order == order


class TestSerialization:
    def test_roundtrip(self) -> None:
        original = Criteria(
            rooms=[Rooms.STUDIO],
            price_max=8_000_000,
            districts=[MatchedEntity(name="Некрасовка", id="203")],
            sort=Sort.PRICE_ASC,
        )
        restored = Criteria.model_validate(original.model_dump())
        assert restored == original

    def test_json_roundtrip(self) -> None:
        original = Criteria(rooms=[Rooms.ONE, Rooms.TWO], finish=False, ready=True)
        restored = Criteria.model_validate_json(original.model_dump_json())
        assert restored == original


class TestPublicDict:
    def test_empty_criteria_gives_empty_dict(self) -> None:
        assert Criteria().to_public_dict() == {}

    def test_matches_tz_example(self) -> None:
        """Публичный вид соответствует примеру ответа из ТЗ."""
        criteria = Criteria(
            rooms=[Rooms.TWO],
            price_max=15_000_000,
            metro=[
                MatchedEntity(
                    name="Аэропорт Внуково",
                    slug="aeroport-vnukovo",
                    id="7cd0e0e3-5be1-4a56-8b9c-000000000000",
                )
            ],
            finish=True,
            sort=Sort.PRICE_ASC,
        )

        assert criteria.to_public_dict() == {
            "rooms": "2",
            "price_max": 15000000,
            "metro": ["Аэропорт Внуково"],
            "finish": True,
            "sort": "price_asc",
        }

    def test_single_room_is_scalar_multi_is_list(self) -> None:
        assert Criteria(rooms=[Rooms.STUDIO]).to_public_dict()["rooms"] == "студия"
        assert Criteria(rooms=[Rooms.ONE, Rooms.THREE_PLUS]).to_public_dict()["rooms"] == [
            "1",
            "3+",
        ]

    def test_false_finish_is_kept(self) -> None:
        """«Без отделки» (finish=False) — значимое значение, не теряется."""
        assert Criteria(finish=False).to_public_dict() == {"finish": False}

    def test_flags_only_when_true(self) -> None:
        public = Criteria(not_first_floor=True).to_public_dict()
        assert public == {"not_first_floor": True}
        assert "last_floor" not in public
        assert "only_available" not in public

    def test_entities_rendered_as_names(self) -> None:
        criteria = Criteria(
            counties=[MatchedEntity(name="ЗАО", slug="zao", id="9")],
            complexes=[MatchedEntity(name="Саларьево парк", id="1108")],
        )
        assert criteria.to_public_dict() == {
            "counties": ["ЗАО"],
            "complexes": ["Саларьево парк"],
        }

    def test_enums_rendered_as_strings(self) -> None:
        public = Criteria(sort=Sort.AREA_DESC, housing_type=HousingType.FLATS_ONLY).to_public_dict()
        assert public == {"sort": "area_desc", "housing_type": "flats_only"}


class TestApiModels:
    def test_request_requires_non_empty_text(self) -> None:
        assert BuildUrlRequest(text="двушка у метро").text == "двушка у метро"
        with pytest.raises(ValidationError):
            BuildUrlRequest(text="")

    def test_request_schema_has_prefilled_example(self) -> None:
        """Swagger-форма должна открываться с готовым примером текста."""
        schema = BuildUrlRequest.model_json_schema()
        assert schema["properties"]["text"]["examples"] == [
            "хочу двушку у метро, до 15 млн, с отделкой"
        ]

    def test_response_defaults(self) -> None:
        response = BuildUrlResponse(url="https://www.pik.ru/search")
        assert response.criteria == {}
        assert response.result_count is None
        assert response.warnings == []

    def test_response_accepts_public_criteria(self) -> None:
        criteria = Criteria(rooms=[Rooms.TWO], sort=Sort.PRICE_ASC)
        response = BuildUrlResponse(
            url="https://www.pik.ru/search/two-room?sortBy=price&orderBy=asc",
            criteria=criteria.to_public_dict(),
            result_count=42,
            warnings=["не распознано: «с видом на закат»"],
        )
        assert response.criteria == {"rooms": "2", "sort": "price_asc"}
        assert response.result_count == 42
