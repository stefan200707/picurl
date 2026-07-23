"""Инварианты справочника ``metro.json`` после кураторской чистки дублей.

Контекст: гео-обогащение из OSM (``refresh_metro_geo``) оставило часть записей
без ``lat``/``lon`` — часть из них была мусором (станции других городов,
дубли-заглушки), вычищенным скриптом ``scripts/cleanup_metro_duplicates.py``.
Эти тесты фиксируют достигнутое состояние, чтобы регрессия (например,
неаккуратный ручной мёрж будущей ручной докуривки) не прошла незамеченной.

Отдельная история — инцидент с потерей 7 GUID (см. CLAUDE.md, раздел
«Справочники»): первая версия чистки слила в одну запись семь станций,
у которых был непустой ``id`` (разные платформы/линии одной физической
станции — pik.ru трактует их как разные значения фильтра ``metroStations``),
безвозвратно потеряв их GUID. Восстановлены как самостоятельные записи;
``TestNoGuidRegression`` ниже — жёсткий инвариант против повторения именно
этого класса ошибки.
"""

from app.reference.loader import load_metro, normalize

#: Реальные московские станции, у которых сознательно нет геопривязки (пока не
#: попали в OSM/строятся) — задокументированный пробел, а не мусор. Держим
#: список явным, чтобы новая безымянная запись без slug/id/координат сразу
#: провалила тест инварианта, а не тихо просочилась в справочник.
KNOWN_STATIONS_WITHOUT_COORDS = {
    "Ермакова Роща",
    "Ж/д станция Мытищи",
    "Лесная",
    "Липовая Роща",
    "Малино",
    "Новорижская",
    "Серп и Молот",
}

#: Эталонный снимок всех (имя, GUID) справочника metro.json на момент ПЕРЕД
#: кураторской чисткой дублей (``git show HEAD:app/reference/metro.json`` на
#: коммите инцидента), т.е. после гео-обогащения из OSM, но до слияния дублей.
#: 66 записей. Зафиксировано константой (а не через ``git show`` из теста),
#: чтобы:
#:   1) тест не зависел от наличия git-истории в окружении запуска (CI-образ,
#:      архив без ``.git``, будущий сквош истории);
#:   2) инвариант был читаемым и явным без побочного процесса ``git`` в тестах.
#: НАЗНАЧЕНИЕ: GUID станции — самый дефицитный ресурс справочника (отдаёт
#: только закрытый бот-защитой front-API www.pik.ru, докуривается вручную;
#: ни OSM, ни refresh-скрипт его не восстановят). Однажды потерянный GUID
#: означает: либо ручную повторную докуривку с нуля, либо необратимо более
#: узкую/неверную выдачу ссылок для этой станции. Множество id в metro.json
#: НЕ ДОЛЖНО уменьшаться — это и проверяет тест ниже.
KNOWN_METRO_GUIDS: frozenset[tuple[str, str]] = frozenset(
    {
        ("Аминьевская", "1ee78b52-0be1-65de-bef4-d7f06b038fc5"),
        ("Аннино", "1ee78b51-fea9-6358-b7ba-d7f06b038fc5"),
        ("Аэропорт Внуково", "1eed15eb-d0e1-6acc-a70e-038dbdc5bee8"),
        ("Багратионовская", "1ee78b51-fcc8-6958-9fa8-d7f06b038fc5"),
        ("Баковка", "b1c2d3e4-f5a6-7b8c-9d0e-1f2a3b4c5d6e"),
        ("Ботанический сад", "1ee78b51-f7fb-6a92-bfd1-d7f06b038fc5"),
        ("Братиславская", "1ee78b52-0357-6a76-a2cc-d7f06b038fc5"),
        ("Бульвар Дмитрия Донского", "1ee78b51-feaa-6672-b1da-d7f06b038fc5"),
        ("Бульвар Рокоссовского", "1ee78b51-f900-6d84-bc1a-d7f06b038fc5"),
        ("Бунинская аллея", "1ee78b52-0546-68fa-bc5a-d7f06b038fc5"),
        ("Бутово", "1ee78b52-11ac-6004-8279-d7f06b038fc5"),
        ("Бутырская", "1ee78b52-027c-6142-bf23-d7f06b038fc5"),
        ("Варшавская", "guid-varshavskaya"),
        ("Владыкино", "1ee78b51-fe8d-65c2-918f-d7f06b038fc5"),
        ("Водный стадион", "1ee78b51-f611-6f88-a8c4-d7f06b038fc5"),
        ("Волгоградский проспект", "1ee78b52-007f-6790-8ffa-d7f06b038fc5"),
        ("Грачёвская", "1ee78b52-13a6-6620-9c2a-d7f06b038fc5"),
        ("Дубровка", "1ee78b52-0350-6140-af14-d7f06b038fc5"),
        ("Ж/д станция Мытищи", "1ef007ab-8873-6632-b356-11de4b9e221f"),
        ("Кантемировская", "1ee78b51-f623-6d82-8b69-d7f06b038fc5"),
        ("Каширская", "1ee78b51-f622-6694-88d4-d7f06b038fc5"),
        ("Коломенская", "1ee78b51-f620-6f9c-a346-d7f06b038fc5"),
        ("Коммунарка", "c1d2e3f4-a5b6-7c8d-9e0f-1a2b3c4d5e6f"),
        ("Коптево", "1ee78b52-082e-6cca-9258-d7f06b038fc5"),
        ("Котельники", "1ee78b52-0088-69b2-b8c8-d7f06b038fc5"),
        ("Красный Строитель", "1ee78b52-11a9-6476-9b3e-d7f06b038fc5"),
        ("Крюково", "1ee78b52-1398-6bc4-81ea-d7f06b038fc5"),
        ("Кунцевская (Арбатско-Покровская)", "1ee78b51-fbb6-6ad8-8ab4-d7f06b038fc5"),
        ("Кунцевская (БКЛ)", "1ee78b52-0b1e-6458-9190-d7f06b038fc5"),
        ("Кунцевская (Филёвская)", "1ee78b51-fcc4-6f10-8d8d-d7f06b038fc5"),
        ("Липовая Роща 2027 год", "1eeb9355-461a-6868-8873-212d041ba9fa"),
        ("Лихоборы", "1ee78b52-082f-6f62-a1cd-d7f06b038fc5"),
        ("Локомотив", "1ee78b52-0810-6ca2-aa0c-d7f06b038fc5"),
        ("Люберцы I", "1ee78b52-1487-6116-8616-d7f06b038fc5"),
        ("МЦД-1 Кунцевская", "1ee78b52-0ed9-6f20-8275-d7f06b038fc5"),
        ("МЦК Бульвар Рокоссовского", "1ee78b52-0740-6dc2-91cb-d7f06b038fc5"),
        ("МЦК Шоссе Энтузиастов", "1ee78b52-0816-63d2-ae3e-d7f06b038fc5"),
        ("Медведково", "1ee78b51-f7f7-6e2e-ae4e-d7f06b038fc5"),
        ("Митино", "23bd023f-e8b3-469b-98f2-d9633e6fa16b"),
        ("Мичуринский проспект", "1ee78b52-0afa-644a-b579-d7f06b038fc5"),
        ("Моссельмаш", "1ee78b52-13a7-6fa2-9a7f-d7f06b038fc5"),
        ("Мякинино", "23bd023f-e8b3-469b-98f2-d9633e6fa16c"),
        ("Нагатинская", "1ee78b51-fe9f-6312-a62b-d7f06b038fc5"),
        ("Новорижская 2027 год", "1eeb9269-7ead-6384-8a24-ed747bc5bad8"),
        ("Новохохловская", "1ee78b52-0819-6ff0-ac7b-d7f06b038fc5"),
        ("Одинцово", "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d"),
        ("Озёрная", "1ee78b52-0645-6bd4-a4d1-d7f06b038fc5"),
        ("Окружная", "1ee78b52-0278-6452-b591-d7f06b038fc5"),
        ("Отрадное", "1ee78b51-fe8b-695c-8b53-d7f06b038fc5"),
        ("Очаково I", "1ee78b52-176d-6ed4-a38a-d7f06b038fc5"),
        ("Парк Победы", "1ee78b52-063f-6b58-8ae4-d7f06b038fc5"),
        ("Перерва", "1ee78b52-11a2-6ae0-8bcc-d7f06b038fc5"),
        ("Потапово", "1ef72f58-8928-698e-b20a-d378b0e97da1"),
        ("Пятницкое шоссе", "1ee78b51-fbc0-6416-af87-d7f06b038fc5"),
        ("Саларьево", "1ee78b51-f9df-6c50-865e-d7f06b038fc5"),
        ("Свиблово", "1ee78b51-f7fa-661a-b8fa-d7f06b038fc5"),
        ("Селигерская", "1ee78b52-0275-6a40-a2ab-d7f06b038fc5"),
        ("Сокол", "guid-sokol"),
        ("Строгино", "1ee78b51-fbba-658e-b785-d7f06b038fc5"),
        ("Текстильщики", "1ee78b52-0081-6180-985e-d7f06b038fc5"),
        ("Улица Скобелевская", "1ee78b52-054a-6a68-be5a-d7f06b038fc5"),
        ("Филатов луг", "1ee78b51-f9e1-6000-b49a-d7f06b038fc5"),
        ("Ховрино", "1ee78b51-f60e-62c0-8460-d7f06b038fc5"),
        ("Черкизовская", "1ee78b51-f9c2-6c54-b314-d7f06b038fc5"),
        ("Шоссе Энтузиастов", "1ee78b51-f4fe-60f6-9c87-d7f06b038fc5"),
        ("Щербинка", "1ee78b52-11ad-6878-8ca3-d7f06b038fc5"),
    }
)

#: Семь записей, чей GUID был безвозвратно потерян первой версией чистки
#: дублей и восстановлен как самостоятельная запись (см. CLAUDE.md). Держим
#: список явным, чтобы закрепить, что они существуют СЕЙЧАС как отдельные
#: записи, а не как алиас другой записи (иначе восстановление легко откатить
#: будущим "причёсыванием" дублей той же ошибкой).
RESTORED_STANDALONE_STATIONS = {
    "Крюково",
    "Кунцевская (БКЛ)",
    "Кунцевская (Филёвская)",
    "МЦД-1 Кунцевская",
    "МЦК Бульвар Рокоссовского",
    "МЦК Шоссе Энтузиастов",
    "Очаково I",
}


class TestNoOrphanEntries:
    """Запись без координат обязана иметь хотя бы slug или id, либо быть в белом списке."""

    def test_no_coordless_entry_without_slug_or_id_or_allowlist(self) -> None:
        metro = load_metro()

        offenders = [
            entry.name
            for entry in metro
            if entry.lat is None
            and entry.lon is None
            and not entry.slug
            and not entry.id
            and entry.name not in KNOWN_STATIONS_WITHOUT_COORDS
        ]

        assert offenders == [], (
            "Найдены записи metro.json без координат, slug, id и без явного "
            f"обоснования в allowlist-е теста: {offenders}"
        )

    def test_known_allowlist_entries_still_exist(self) -> None:
        """Белый список не рассинхронизировался с фактическим содержимым файла."""
        names = {entry.name for entry in load_metro()}

        missing = KNOWN_STATIONS_WITHOUT_COORDS - names
        assert missing == set(), f"Записи из allowlist-а пропали из metro.json: {missing}"


class TestNoDuplicateNames:
    """Нет двух записей с одинаковым нормализованным именем (казус «мусор-дубль»)."""

    def test_no_duplicate_normalized_names(self) -> None:
        metro = load_metro()

        seen: dict[str, str] = {}
        duplicates: list[tuple[str, str]] = []
        for entry in metro:
            key = normalize(entry.name)
            if key in seen:
                duplicates.append((seen[key], entry.name))
            else:
                seen[key] = entry.name

        assert duplicates == [], f"Обнаружены дубли по нормализованному имени: {duplicates}"

    def test_no_duplicate_ids_across_entries(self) -> None:
        """Один и тот же GUID/id не должен принадлежать двум разным записям."""
        metro = load_metro()

        ids = [entry.id for entry in metro if entry.id]
        assert len(ids) == len(set(ids)), "Найден повторяющийся id/GUID у разных записей metro.json"


class TestCleanedUpDuplicatesAreGone:
    """Регрессия конкретно на устранённые дубли/мусор (Milestone метро-чистки)."""

    def test_other_city_stations_removed(self) -> None:
        names = {entry.name for entry in load_metro()}
        other_city = {
            "Ботаническая",
            "Девяткино",
            "Ладожская",
            "Обводный канал",
            "Приморская",
            "Проспект Ветеранов",
            "Суконная слобода",
        }
        assert names & other_city == set(), "Станции других городов не должны быть в metro.json"

    def test_stub_duplicates_merged_as_aliases(self) -> None:
        """Слитые дубли (без собственного GUID) пропали из имён, их текст доступен
        как алиас основной записи.

        Кунцевская (БКЛ)/(Филёвская)/МЦД-1 Кунцевская сюда больше не входят —
        они были ошибочно слиты (потеряв GUID) и теперь восстановлены как
        самостоятельные записи, см. ``TestRestoredGuidStations`` ниже.
        """
        metro = load_metro()
        by_name = {entry.name: entry for entry in metro}

        assert "Тестовская" not in by_name
        assert "Тестовская" in by_name["Москва-Сити"].aliases

        assert "Библиотека им.Ленина" not in by_name
        assert "Библиотека им.Ленина" in by_name["Библиотека имени Ленина"].aliases


class TestRestoredGuidStations:
    """Регрессия на инцидент потери 7 GUID (см. CLAUDE.md, раздел «Справочники»).

    Каждая из семи записей физически совпадает с другой станцией (общие
    координаты), но представляет ОТДЕЛЬНОЕ значение фильтра ``metroStations``
    pik.ru (разные платформы/линии) — поэтому обязана существовать как
    самостоятельная запись со своим GUID, а не как алиас другой записи.
    """

    def test_restored_stations_exist_standalone_with_guid(self) -> None:
        metro = load_metro()
        by_name = {entry.name: entry for entry in metro}

        expected_ids = {
            name: guid for name, guid in KNOWN_METRO_GUIDS if name in RESTORED_STANDALONE_STATIONS
        }
        assert expected_ids.keys() == RESTORED_STANDALONE_STATIONS

        for name in RESTORED_STANDALONE_STATIONS:
            assert name in by_name, f"Восстановленная запись {name!r} отсутствует в metro.json"
            entry = by_name[name]
            assert entry.id == expected_ids[name], (
                f"У {name!r} не тот GUID: {entry.id!r} != {expected_ids[name]!r}"
            )
            assert entry.lat is not None and entry.lon is not None, (
                f"Восстановленная запись {name!r} должна иметь координаты "
                "(физически совпадает с канонической станцией, в которую её "
                "когда-то ошибочно слили)"
            )

    def test_restored_names_not_left_as_dangling_aliases(self) -> None:
        """Имя восстановленной записи не должно остаться алиасом другой записи —
        иначе один и тот же текст матчится на две записи и парсер вернёт
        предупреждение о неоднозначности вместо чистого совпадения."""
        metro = load_metro()

        for entry in metro:
            if entry.name in RESTORED_STANDALONE_STATIONS:
                continue
            leaked = RESTORED_STANDALONE_STATIONS & set(entry.aliases)
            assert not leaked, (
                f"Имя восстановленной записи {leaked} осталось алиасом {entry.name!r} — "
                "это создаст неоднозначность матчинга"
            )


class TestNoGuidRegression:
    """Жёсткий инвариант: множество id в metro.json не должно уменьшаться.

    GUID станции — самый дефицитный ресурс справочника (закрытый бот-защитой
    front-API, докуривается вручную; ни OSM, ни refresh-скрипт его не
    восстановят). Любая будущая "чистка дублей"/ручная правка, которая молча
    сливает две записи и теряет id одной из них, обязана уронить этот тест
    ДО того, как дойдёт до код-ревью или прода.
    """

    def test_no_known_guid_is_lost(self) -> None:
        """Ни один ДОСТОВЕРНЫЙ GUID из снимка не пропал.

        Снимок ``KNOWN_METRO_GUIDS`` исторически включал и 5 синтетических id
        (hex-«лесенки» и ``guid-*``-заглушки) — аудит validate() 2026-07-23
        доказал их фейковость, и они вычищены из ``metro.json`` осознанно
        (``app.pik.id_trust.REMOVED_SYNTHETIC_METRO_IDS``). Тест защищал фейки
        наравне с настоящими GUID — теперь исключает ровно этот реестр, а
        сам снимок сохранён как есть (это документ эпохи, а не список правды).
        """
        from app.pik.id_trust import REMOVED_SYNTHETIC_METRO_IDS

        metro = load_metro()
        current_ids = {entry.id for entry in metro if entry.id}

        lost = [
            (name, guid)
            for name, guid in KNOWN_METRO_GUIDS
            if guid not in current_ids and guid not in REMOVED_SYNTHETIC_METRO_IDS
        ]
        assert lost == [], (
            f"Потеряны GUID станций метро (были в справочнике, сейчас отсутствуют): {lost}. "
            "GUID нельзя терять при слиянии/чистке дублей — см. CLAUDE.md, раздел «Справочники»."
        )

    def test_removed_synthetic_ids_did_not_return(self) -> None:
        """Вычищенные фейковые id не вернулись в справочник (регрессия аудита)."""
        from app.pik.id_trust import REMOVED_SYNTHETIC_METRO_IDS

        metro = load_metro()
        current_ids = {entry.id for entry in metro if entry.id}
        returned = current_ids & REMOVED_SYNTHETIC_METRO_IDS
        assert returned == set(), (
            f"Синтетические id снова появились в metro.json: {returned}. "
            "Они доказанно фейковые (аудит validate(), 2026-07-23) — см. app/pik/id_trust.py."
        )

    def test_id_count_did_not_shrink(self) -> None:
        from app.pik.id_trust import REMOVED_SYNTHETIC_METRO_IDS

        metro = load_metro()
        current_with_id = sum(1 for entry in metro if entry.id)
        snapshot_genuine = len(KNOWN_METRO_GUIDS) - len(
            {guid for _name, guid in KNOWN_METRO_GUIDS} & REMOVED_SYNTHETIC_METRO_IDS
        )

        assert current_with_id >= snapshot_genuine, (
            f"Число записей metro.json с id ({current_with_id}) стало меньше эталонного "
            f"снимка достоверных GUID ({snapshot_genuine}) — похоже, GUID снова потеряны."
        )
