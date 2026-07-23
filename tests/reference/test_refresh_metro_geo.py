"""Тесты обогащения metro.json координатами/линиями из OSM Overpass.

Сеть замокана через ``httpx.MockTransport`` — реальных обращений к Overpass в
тестах нет (конвенция проекта, см. ``test_refresh.py``). Проверяется разбор
ответа, вывод имени линии, мёрж без потери кураторских полей и добавление
станций, которых в справочнике ещё нет (в первую очередь МЦД).
"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.reference import refresh_metro_geo as geo
from app.reference.loader import REFERENCE_FILES, RefEntry
from app.reference.refresh import write_entries
from app.reference.refresh_metro_geo import (
    fetch_overpass,
    line_label,
    merge_metro,
    parse_stations,
    refresh_metro_geo,
)

#: Сокращённый, но структурно точный ответ Overpass: два маршрута метро,
#: маршрут МЦК и маршрут МЦД-2; «Печатники» — пересадка метро/МЦД.
OVERPASS_PAYLOAD: dict[str, Any] = {
    "version": 0.6,
    "elements": [
        {
            "type": "relation",
            "id": 1,
            "members": [
                {"type": "way", "ref": 900, "role": ""},
                {"type": "node", "ref": 101, "role": "stop"},
                {"type": "node", "ref": 102, "role": "stop"},
                {"type": "node", "ref": 999, "role": "stop"},
            ],
            "tags": {
                "route": "subway",
                "ref": "10",
                "network": "Московский метрополитен",
                "name": "Люблинско-Дмитровская линия: Зябликово → Физтех",
            },
        },
        {
            "type": "relation",
            "id": 2,
            "members": [{"type": "node", "ref": 101, "role": "stop"}],
            "tags": {
                "route": "subway",
                "ref": "11",
                "network": "Московский метрополитен",
                "name": "Большая кольцевая линия (внутреннее кольцо)",
            },
        },
        {
            "type": "relation",
            "id": 3,
            "members": [{"type": "node", "ref": 103, "role": "stop"}],
            "tags": {
                "route": "train",
                "ref": "14",
                "network": "Московский метрополитен",
                "name": "Московское Центральное Кольцо (по часовой)",
            },
        },
        {
            "type": "relation",
            "id": 4,
            "members": [
                {"type": "node", "ref": 101, "role": "stop"},
                {"type": "node", "ref": 104, "role": "stop"},
            ],
            "tags": {
                "route": "train",
                "ref": "D2",
                "network": "МЦД",
                "name": "МЦД-2 «Курско-Рижский диаметр»: Нахабино => Подольск",
            },
        },
        {
            "type": "node",
            "id": 101,
            "lat": 55.6934411,
            "lon": 37.7269005,
            "tags": {"name": "Печатники", "railway": "stop", "public_transport": "stop_position"},
        },
        {
            "type": "node",
            "id": 102,
            "lat": 55.8091011,
            "lon": 37.6388112,
            "tags": {"name": "Марьина Роща", "railway": "station", "alt_name": "Марьинa; Роща"},
        },
        {
            "type": "node",
            "id": 103,
            "lat": 55.7470353,
            "lon": 37.7377804,
            "tags": {"name": "Андроновка", "railway": "stop"},
        },
        {
            "type": "node",
            "id": 104,
            "lat": 55.8320233,
            "lon": 37.2200313,
            "tags": {"name": "Аникеевка", "railway": "halt"},
        },
        {
            # Не станция (стрелка без имени и без railway-станционных тегов).
            "type": "node",
            "id": 999,
            "lat": 55.0,
            "lon": 37.0,
            "tags": {"railway": "switch"},
        },
    ],
}


def make_client(payload: Any, statuses: list[int] | None = None) -> httpx.Client:
    """httpx-клиент с замоканным транспортом вместо реальной сети."""
    codes = list(statuses or [200])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "overpass-api.de"
        code = codes.pop(0) if len(codes) > 1 else codes[0]
        return httpx.Response(code, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


class TestQueryAndFetch:
    def test_query_covers_three_networks(self) -> None:
        query = geo.build_query()

        assert '"route"="subway"' in query
        assert f'"network"="{geo.MCD_NETWORK}"' in query
        assert f'"ref"="{geo.MCC_REF}"' in query
        assert "node(r);" in query  # узлы-станции маршрутов

    def test_returns_payload(self) -> None:
        with make_client(OVERPASS_PAYLOAD) as client:
            assert fetch_overpass(client)["elements"]

    def test_retries_transient_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """504/429 у публичного Overpass — временный отказ, он ретраится."""
        monkeypatch.setattr(geo.time, "sleep", lambda _seconds: None)

        with make_client(OVERPASS_PAYLOAD, statuses=[504, 429, 200]) as client:
            assert fetch_overpass(client)["elements"]

    def test_raises_after_retries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(geo.time, "sleep", lambda _seconds: None)

        with (
            make_client(OVERPASS_PAYLOAD, statuses=[504]) as client,
            pytest.raises(httpx.HTTPStatusError),
        ):
            fetch_overpass(client, attempts=2)

    def test_raises_on_unexpected_shape(self) -> None:
        with make_client([1, 2]) as client, pytest.raises(ValueError, match="elements"):
            fetch_overpass(client)


class TestLineLabel:
    @pytest.mark.parametrize(
        ("tags", "expected"),
        [
            (
                {
                    "route": "subway",
                    "network": "Московский метрополитен",
                    "name": "Сокольническая линия: Потапово → Бульвар Рокоссовского",
                },
                "Сокольническая",
            ),
            (
                {
                    "route": "subway",
                    "network": "Московский метрополитен",
                    "name": "Большая кольцевая линия (внутреннее кольцо)",
                },
                "Большая кольцевая",
            ),
            ({"route": "train", "ref": "14", "network": "Московский метрополитен"}, "МЦК"),
            ({"route": "train", "ref": "D2", "network": "МЦД"}, "МЦД-2"),
            # Ветка диаметра сводится к своему диаметру — для пользователя это одна линия.
            ({"route": "train", "ref": "D4А", "network": "МЦД"}, "МЦД-4"),
            # Дальний поезд — не наша сеть.
            ({"route": "train", "ref": "001А", "network": "РЖД"}, None),
        ],
    )
    def test_labels(self, tags: dict[str, str], expected: str | None) -> None:
        assert line_label(tags) == expected


class TestParseStations:
    def test_extracts_names_coords_and_lines(self) -> None:
        entries = {entry.name: entry for entry in parse_stations(OVERPASS_PAYLOAD)}

        assert set(entries) == {"Печатники", "Марьина Роща", "Андроновка", "Аникеевка"}
        assert entries["Андроновка"].line == "МЦК"
        assert entries["Аникеевка"].line == "МЦД-2"
        assert entries["Аникеевка"].lat == pytest.approx(55.8320233)

    def test_transfer_station_keeps_all_lines(self) -> None:
        """Пересадочный узел принадлежит нескольким линиям — не теряем ни одной."""
        entries = {entry.name: entry for entry in parse_stations(OVERPASS_PAYLOAD)}

        assert entries["Печатники"].line == "Большая кольцевая / Люблинско-Дмитровская / МЦД-2"

    def test_aliases_come_from_osm_tags_only(self) -> None:
        entries = {entry.name: entry for entry in parse_stations(OVERPASS_PAYLOAD)}

        assert entries["Марьина Роща"].aliases == ("Марьинa", "Роща")
        assert entries["Андроновка"].aliases == ()

    def test_non_station_nodes_ignored(self) -> None:
        assert all(entry.name for entry in parse_stations(OVERPASS_PAYLOAD))
        assert not any(entry.lat == 55.0 for entry in parse_stations(OVERPASS_PAYLOAD))

    def test_result_is_sorted(self) -> None:
        names = [entry.name for entry in parse_stations(OVERPASS_PAYLOAD)]

        assert names == sorted(names, key=str.casefold)


class TestMergeMetro:
    def test_curated_fields_survive(self) -> None:
        curated = [
            RefEntry(name="Печатники", slug="m-pechatniki", id="guid-1", aliases=("Печатки",))
        ]
        fetched = [RefEntry(name="Печатники", lat=55.69, lon=37.72, line="МЦД-2")]

        merged, added = merge_metro(curated, fetched)

        assert added == 0
        assert merged[0].slug == "m-pechatniki"
        assert merged[0].id == "guid-1"
        assert merged[0].aliases == ("Печатки",)
        assert (merged[0].lat, merged[0].lon, merged[0].line) == (55.69, 37.72, "МЦД-2")

    def test_unknown_station_added_without_invented_ids(self) -> None:
        """Станции МЦД, которых нет в справочнике, добавляются — но без slug/id."""
        merged, added = merge_metro(
            [RefEntry(name="Печатники")],
            [RefEntry(name="Аникеевка", lat=55.83, lon=37.22, line="МЦД-2")],
        )

        assert added == 1
        anikeevka = next(entry for entry in merged if entry.name == "Аникеевка")
        assert anikeevka.slug is None and anikeevka.id is None
        assert anikeevka.line == "МЦД-2"

    def test_match_by_alias_keeps_curated_name(self) -> None:
        curated = [RefEntry(name="Аэропорт Внуково", slug="m-vnukovo", aliases=("Внуково",))]

        merged, added = merge_metro(curated, [RefEntry(name="Внуково", lat=55.6, lon=37.28)])

        assert added == 0
        assert merged[0].name == "Аэропорт Внуково"
        assert merged[0].lat == 55.6

    def test_exact_name_wins_over_someone_elses_alias(self) -> None:
        """Совпадение по имени надёжнее совпадения по чужому алиасу."""
        curated = [
            RefEntry(name="Аэропорт Внуково", aliases=("Внуково",)),
            RefEntry(name="Внуково"),
        ]
        fetched = [RefEntry(name="Внуково", lat=55.64, lon=37.26, line="МЦД-4")]

        merged, _ = merge_metro(curated, fetched)
        by_name = {entry.name: entry for entry in merged}

        assert by_name["Внуково"].line == "МЦД-4"
        assert by_name["Аэропорт Внуково"].line is None

    def test_several_osm_stations_on_one_entry_union_lines(self) -> None:
        """Линии объединяются, координаты — от совпадения по имени, а не по алиасу."""
        curated = [RefEntry(name="Аэропорт Внуково", aliases=("Внуково",))]
        fetched = [
            RefEntry(name="Внуково", lat=55.64, lon=37.26, line="МЦД-4"),
            RefEntry(name="Аэропорт Внуково", lat=55.60, lon=37.28, line="Солнцевская"),
        ]

        merged, _ = merge_metro(curated, fetched)

        assert merged[0].line == "МЦД-4 / Солнцевская"
        assert merged[0].lat == 55.60

    def test_fuzzy_match_is_conservative(self) -> None:
        """Похожие, но разные станции не сливаются: «Волоколамск» ≠ «Волоколамская»."""
        merged, added = merge_metro(
            [RefEntry(name="Волоколамская")],
            [RefEntry(name="Волоколамск", lat=56.03, lon=35.95, line="МЦД-2")],
        )

        assert added == 1
        assert {entry.name for entry in merged} == {"Волоколамская", "Волоколамск"}

    def test_fuzzy_match_catches_spelling_variants(self) -> None:
        merged, added = merge_metro(
            [RefEntry(name="Улица Академика Янгеля", id="guid-9")],
            [RefEntry(name="Улица академика Янгеля", lat=55.59, lon=37.6, line="Серпуховская")],
        )

        assert added == 0
        assert merged[0].id == "guid-9" and merged[0].lat == 55.59

    def test_result_sorted_by_name(self) -> None:
        merged, _ = merge_metro([], [RefEntry(name="Яуза"), RefEntry(name="Аминьевская")])

        assert [entry.name for entry in merged] == ["Аминьевская", "Яуза"]


class TestRefreshMetroGeo:
    def test_writes_metro_json_with_geo(self, tmp_path: Path) -> None:
        write_entries(
            tmp_path / REFERENCE_FILES["metro"],
            [RefEntry(name="Печатники", slug="m-pechatniki", id="guid-1")],
        )

        with make_client(OVERPASS_PAYLOAD) as client:
            counts = refresh_metro_geo(client, data_dir=tmp_path)

        entries = json.loads((tmp_path / REFERENCE_FILES["metro"]).read_text(encoding="utf-8"))
        by_name = {entry["name"]: entry for entry in entries}

        assert counts["fetched"] == 4
        assert counts["added"] == 3
        assert counts["total"] == 4
        assert counts["with_coords"] == 4
        assert counts["mcd"] == 2
        assert by_name["Печатники"]["slug"] == "m-pechatniki"
        assert by_name["Печатники"]["lat"] == pytest.approx(55.6934411)

    def test_is_idempotent(self, tmp_path: Path) -> None:
        """Повторный прогон на тех же данных не меняет файл (диффы читаемы)."""
        path = tmp_path / REFERENCE_FILES["metro"]

        with make_client(OVERPASS_PAYLOAD) as client:
            refresh_metro_geo(client, data_dir=tmp_path)
            first = path.read_text(encoding="utf-8")
            refresh_metro_geo(client, data_dir=tmp_path)
            second = path.read_text(encoding="utf-8")

        assert first == second
