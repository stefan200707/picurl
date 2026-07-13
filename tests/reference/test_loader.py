"""Тесты слоя справочников (промпт 03): загрузка, кэш, поиск по имени/алиасу."""

from app.reference import loader
from app.reference.loader import (
    RefEntry,
    ReferenceData,
    clear_cache,
    find_by_name,
    load_all,
    load_complexes,
    load_counties,
    load_districts,
    load_metro,
    normalize,
)


class TestLoading:
    def test_each_reference_loads_and_is_nonempty(self) -> None:
        """Все семь справочников читаются с диска и не пусты."""
        data = load_all()

        assert isinstance(data, ReferenceData)
        for field in ReferenceData.model_fields:
            entries = getattr(data, field)
            assert entries, f"справочник {field!r} пуст"
            assert all(isinstance(entry, RefEntry) for entry in entries)

    def test_entries_are_immutable_tuples(self) -> None:
        """Загрузчик возвращает кортежи frozen-моделей — кэш нельзя испортить."""
        entries = load_metro()

        assert isinstance(entries, tuple)
        assert entries[0].model_config.get("frozen") is True

    def test_required_seed_entries_present(self) -> None:
        """Записи, на которые опираются тесты промптов 05–08, есть в стартовых JSON."""
        vnukovo = find_by_name(load_metro(), "Аэропорт Внуково")
        assert vnukovo is not None
        assert vnukovo.slug == "m-aeroport-vnukovo"

        sokol = find_by_name(load_metro(), "Сокол")
        assert sokol is not None

        zao = find_by_name(load_counties(), "ЗАО")
        assert zao is not None
        assert zao.slug == "zao"

        babushkinsky = find_by_name(load_districts(), "Бабушкинский")
        assert babushkinsky is not None
        assert babushkinsky.id == "203"

        mpark = find_by_name(load_complexes(), "Мичуринский парк")
        assert mpark is not None
        assert mpark.id == "1108"
        assert mpark.slug == "mpark"


class TestCaching:
    def test_repeated_load_returns_cached_object(self) -> None:
        """Повторная загрузка не перечитывает диск: тот же объект из lru_cache."""
        clear_cache()
        first = load_metro()
        second = load_metro()

        assert first is second

    def test_clear_cache_forces_reload(self) -> None:
        """После clear_cache() загрузчик перечитывает файл (новый объект)."""
        first = load_metro()
        clear_cache()
        second = load_metro()

        assert first is not second
        assert first == second

    def test_cache_is_per_file(self) -> None:
        """Кэш держит по записи на файл — разные справочники не смешиваются."""
        clear_cache()
        load_metro()
        load_counties()

        info = loader._load.cache_info()
        assert info.currsize == 2


class TestFindByName:
    def test_finds_by_exact_name(self) -> None:
        entry = find_by_name(load_counties(), "ЗАО")

        assert entry is not None
        assert entry.slug == "zao"

    def test_finds_by_alias(self) -> None:
        entry = find_by_name(load_metro(), "Внуково")

        assert entry is not None
        assert entry.name == "Аэропорт Внуково"

    def test_normalizes_case_yo_and_spaces(self) -> None:
        """casefold + ё→е + схлопывание пробелов при сравнении."""
        entries = (RefEntry(name="Тёплый Стан", slug="m-tyoply-stan"),)

        assert find_by_name(entries, "теплый  стан") is not None
        assert find_by_name(entries, "ТЁПЛЫЙ СТАН") is not None

    def test_returns_none_when_missing(self) -> None:
        assert find_by_name(load_metro(), "Хогвартс") is None

    def test_returns_first_match(self) -> None:
        entries = (
            RefEntry(name="Сокол", slug="m-sokol"),
            RefEntry(name="Сокол", id="42"),
        )

        found = find_by_name(entries, "сокол")
        assert found is entries[0]


class TestNormalize:
    def test_normalize_rules(self) -> None:
        assert normalize("  Аэропорт   Внуково ") == "аэропорт внуково"
        assert normalize("Ёлки-Палки") == "елки-палки"
