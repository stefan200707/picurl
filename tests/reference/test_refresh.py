"""Тесты скрипта обновления справочников (промпт 03).

Сетевые вызовы замоканы через ``httpx.MockTransport`` — реальных обращений
к ``api.pik.ru`` в тестах нет (конвенция проекта). Проверяется парсинг ответа
в корректный формат записи, мёрж с кураторскими данными и идемпотентность.
"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.reference import refresh as refresh_mod
from app.reference.loader import REFERENCE_FILES, RefEntry
from app.reference.refresh import (
    complexes_from_blocks,
    counties_from_blocks,
    districts_from_blocks,
    fetch_blocks,
    merge_entries,
    metro_from_blocks,
    refresh,
    write_entries,
)

#: Репрезентативный ответ api.pik.ru/v2/block (сокращённый).
BLOCKS_PAYLOAD: list[dict[str, Any]] = [
    {
        "id": 1108,
        "name": "Мичуринский парк",
        "url": "/mpark",
        "metro": "Озёрная",
        "district": "Очаково-Матвеевское",
        "locations": {"parent": {"name": "Москва"}, "child": {"name": "ЗАО", "url": "zao"}},
    },
    {
        "id": 481,
        "name": "Амурский парк",
        "url": "/amur",
        "metro": "Щёлковская",
        "district": "Гольяново",
        "locations": {"parent": {"name": "Москва"}, "child": {"name": "ВАО", "url": "vao"}},
    },
    {
        # Дубликат округа/метро + ЖК без url — slug должен стать None.
        "id": 999,
        "name": "Второй в ЗАО",
        "url": "",
        "metro": "Озёрная",
        "locations": {"parent": {"name": "Москва"}, "child": {"name": "ЗАО", "url": "zao"}},
    },
    {
        # Не Москва — в counties.json не попадает.
        "id": 1518,
        "name": "Босфорский парк",
        "url": "/vladivostok/bosforskiypark",
        "metro": None,
        "district": "Первомайский",
        "locations": {"parent": {"name": "Приморский край"}, "child": {"name": "Владивосток"}},
    },
    {"name": None},  # мусорная запись без имени — молча пропускается
]


def make_client(payload: Any, status_code: int = 200) -> httpx.Client:
    """httpx-клиент с замоканным транспортом вместо реальной сети."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.pik.ru"
        return httpx.Response(status_code, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


class TestFetchBlocks:
    def test_returns_block_list(self) -> None:
        with make_client(BLOCKS_PAYLOAD) as client:
            blocks = fetch_blocks(client)

        assert [b.get("id") for b in blocks[:2]] == [1108, 481]

    def test_raises_on_http_error(self) -> None:
        with make_client({}, status_code=503) as client, pytest.raises(httpx.HTTPStatusError):
            fetch_blocks(client)

    def test_raises_on_unexpected_shape(self) -> None:
        with make_client({"items": []}) as client, pytest.raises(ValueError, match="список"):
            fetch_blocks(client)


class TestExtractors:
    def test_complexes_have_both_url_forms(self) -> None:
        """ЖК: слаг (url без ведущего /) и числовой id — обе формы для url_builder."""
        entries = complexes_from_blocks(BLOCKS_PAYLOAD)

        mpark = next(e for e in entries if e.name == "Мичуринский парк")
        assert mpark.slug == "mpark"
        assert mpark.id == "1108"

        no_url = next(e for e in entries if e.name == "Второй в ЗАО")
        assert no_url.slug is None

    def test_complexes_skip_nameless(self) -> None:
        names = [e.name for e in complexes_from_blocks(BLOCKS_PAYLOAD)]
        assert None not in names
        assert len(names) == 4

    def test_counties_only_moscow_and_deduped(self) -> None:
        entries = counties_from_blocks(BLOCKS_PAYLOAD)

        assert [(e.name, e.slug) for e in entries] == [("ЗАО", "zao"), ("ВАО", "vao")]

    def test_metro_names_deduped_without_ids(self) -> None:
        entries = metro_from_blocks(BLOCKS_PAYLOAD)

        assert [e.name for e in entries] == ["Озёрная", "Щёлковская"]
        assert all(e.slug is None and e.id is None for e in entries)

    def test_districts_names_only(self) -> None:
        names = [e.name for e in districts_from_blocks(BLOCKS_PAYLOAD)]

        assert names == ["Очаково-Матвеевское", "Гольяново", "Первомайский"]


class TestMergeEntries:
    def test_fetched_ids_fill_curated_gaps(self) -> None:
        """Свежий id дополняет кураторскую запись, алиасы сохраняются."""
        curated = [RefEntry(name="Мичуринский парк", slug="mpark", aliases=("Мичуринский",))]
        fetched = [RefEntry(name="Мичуринский парк", slug="mpark", id="1108")]

        merged = merge_entries(curated, fetched)

        assert len(merged) == 1
        assert merged[0].id == "1108"
        assert merged[0].aliases == ("Мичуринский",)

    def test_match_by_alias_keeps_curated_name(self) -> None:
        """Совпадение по алиасу не затирает каноническое кураторское имя."""
        curated = [
            RefEntry(name="Аэропорт Внуково", slug="m-aeroport-vnukovo", aliases=("Внуково",))
        ]
        fetched = [RefEntry(name="Внуково", id="guid-1")]

        merged = merge_entries(curated, fetched)

        assert len(merged) == 1
        assert merged[0].name == "Аэропорт Внуково"
        assert merged[0].id == "guid-1"

    def test_match_by_slug_updates_renamed_entry(self) -> None:
        """Переименование на сайте: слаг тот же — имя обновляется."""
        curated = [RefEntry(name="Старое имя", slug="mpark", id="1108")]
        fetched = [RefEntry(name="Мичуринский парк", slug="mpark", id="1108")]

        merged = merge_entries(curated, fetched)

        assert len(merged) == 1
        assert merged[0].name == "Мичуринский парк"

    def test_untouched_manual_entries_survive(self) -> None:
        """Ручные записи, отсутствующие в ответе API, не теряются."""
        curated = [RefEntry(name="Только вручную", id="777")]

        merged = merge_entries(curated, [RefEntry(name="Новый ЖК", slug="new")])

        assert {e.name for e in merged} == {"Только вручную", "Новый ЖК"}

    def test_result_sorted_by_name(self) -> None:
        merged = merge_entries([], [RefEntry(name="Яуза"), RefEntry(name="Амур")])

        assert [e.name for e in merged] == ["Амур", "Яуза"]


class TestRefresh:
    def test_writes_all_refreshable_files(self, tmp_path: Path) -> None:
        with make_client(BLOCKS_PAYLOAD) as client:
            counts = refresh(client, data_dir=tmp_path)

        assert set(counts) == set(refresh_mod.REFRESHABLE)
        for kind in refresh_mod.REFRESHABLE:
            path = tmp_path / REFERENCE_FILES[kind]
            entries = json.loads(path.read_text(encoding="utf-8"))
            assert len(entries) == counts[kind]

    def test_is_idempotent(self, tmp_path: Path) -> None:
        """Повторный запуск на тех же данных не меняет файлы (диффы читаемы)."""
        with make_client(BLOCKS_PAYLOAD) as client:
            refresh(client, data_dir=tmp_path)
            first = {
                kind: (tmp_path / REFERENCE_FILES[kind]).read_text(encoding="utf-8")
                for kind in refresh_mod.REFRESHABLE
            }
            refresh(client, data_dir=tmp_path)
            second = {
                kind: (tmp_path / REFERENCE_FILES[kind]).read_text(encoding="utf-8")
                for kind in refresh_mod.REFRESHABLE
            }

        assert first == second

    def test_preserves_manual_curation_on_refresh(self, tmp_path: Path) -> None:
        """Кураторские GUID-ы/алиасы переживают обновление с сайта."""
        write_entries(
            tmp_path / REFERENCE_FILES["metro"],
            [RefEntry(name="Озёрная", slug="m-ozyornaya", id="guid-oz", aliases=("Озерка",))],
        )

        with make_client(BLOCKS_PAYLOAD) as client:
            refresh(client, data_dir=tmp_path)

        entries = json.loads((tmp_path / REFERENCE_FILES["metro"]).read_text(encoding="utf-8"))
        ozyornaya = next(e for e in entries if e["name"] == "Озёрная")
        assert ozyornaya["slug"] == "m-ozyornaya"
        assert ozyornaya["id"] == "guid-oz"
        assert ozyornaya["aliases"] == ["Озерка"]

    def test_written_json_omits_empty_fields(self, tmp_path: Path) -> None:
        """В JSON не пишутся null/пустые поля — файлы компактные и читаемые."""
        with make_client(BLOCKS_PAYLOAD) as client:
            refresh(client, data_dir=tmp_path)

        entries = json.loads((tmp_path / REFERENCE_FILES["districts"]).read_text(encoding="utf-8"))
        assert entries and all(set(e) == {"name"} for e in entries)
