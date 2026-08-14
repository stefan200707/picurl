"""Исследование аналогов picurl на GitHub: сбор метаданных и сборка research_data.json.

Скрипт ходит в сеть (GitHub REST API + raw.githubusercontent) и НЕ является частью
pytest — это офлайн-аудит по образцу `scripts/reference_audit.py`.

Что делает:

1. Берёт курируемый список репозиториев (`CURATED`) — отобран вручную из выдачи
   поисковых запросов `SEARCH_QUERIES` (они сохранены в артефакте ради
   воспроизводимости: их можно перезапустить и увидеть, не появилось ли нового).
2. Дотягивает по каждому актуальные факты: описание, язык, звёзды, лицензию,
   топики, дату последнего пуша, признак архива.
3. Сканирует README на упоминания технологий (`TECH_MARKERS`) — грубая, но честная
   оценка стека: это то, что проект сам о себе пишет.
4. Пишет `research_data.json` в корне репозитория.
5. С флагом `--discover` дополнительно прогоняет `SEARCH_QUERIES` и печатает
   кандидатов, которых ещё нет в `CURATED`, — так список обновляют, а не
   переписывают с нуля.

Курирование (категория, степень схожести, чему учит) живёт здесь же, в коде, —
чтобы данные и их интерпретация обновлялись одним прогоном.

Почему метаданные берутся ПОИСКОМ, а не `GET /repos/{owner}/{repo}`
-------------------------------------------------------------------

Без токена у GitHub два независимых лимита: core — 60 запросов в час (на 40+
репозиториев не хватает и делится со всем, что ходило с этого IP), search — 10
запросов в минуту. Поэтому факты о репозиториях собираются пачками через
`search/repositories` с несколькими квалификаторами `repo:` в одном запросе
(они объединяются по ИЛИ): 40 репозиториев — четыре запроса вместо сорока.
Поля выдачи поиска те же, что у `GET /repos`. README тянется с
raw.githubusercontent — он лимитами API не считается.

Репозиторий, не вернувшийся в пачке (удалён, переименован, стал приватным),
молча не исчезает: он попадает в `missing` артефакта — инвариант «ничего не
теряем молча» действует и здесь.

Запуск: `uv run python scripts/similar_projects_research.py [--out research_data.json]`
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx

GITHUB_API = "https://api.github.com"
RAW_BASE = "https://raw.githubusercontent.com"
USER_AGENT = "picurl-similar-projects-research"

# Поиск без токена — 10 запросов в минуту; 7 секунд паузы держат нас под лимитом
# даже с учётом чужого трафика с того же IP.
SEARCH_DELAY_S = 7.0
# Сколько квалификаторов repo: класть в один поисковый запрос.
BATCH_SIZE = 10

ROOT = Path(__file__).resolve().parent.parent

# Запросы, которыми искали кандидатов (GitHub search AND-ит все термины,
# поэтому запросы короткие и в нескольких формулировках).
SEARCH_QUERIES: list[str] = [
    "natural language search query builder in:name,description,readme language:python",
    "natural language to search filters LLM in:name,description",
    "natural language query structured filters fastapi in:name,description,readme",
    "natural language to query dsl in:name,description",
    "natural language elasticsearch query in:name,description",
    "query understanding search in:name,description",
    "topic:text-to-sql",
    "topic:natural-language-search",
    "quepy in:name,description",
    "duckling parsing text structured data in:name,description,readme",
    "topic:real-estate language:python",
    "topic:proptech",
    "real estate llm agent in:name,description",
    "real estate chatbot natural language search in:name,description",
    "property search nlp in:name,description",
    "недвижимость in:name,description",
    "квартир in:name,description,readme",
    "новостройки in:name,description",
    "pik.ru in:name,description,readme",
    "поиск квартиры бот llm in:name,description,readme",
    "avito parser in:name,description",
    "cian in:name,description",
    "krisha.kz in:name,description,readme",
    "яндекс недвижимость in:name,description",
    "yargy in:name,description,readme",
    "pymorphy2 in:name,description",
    "natasha russian nlp in:name,description,readme",
    "topic:russian-nlp",
    "topic:russian-language",
    "topic:nlp topic:russian",
    "address normalization russian in:name,description",
    "topic:fuzzy-matching language:python",
    "fuzzy matching russian text rapidfuzz in:name,description,readme",
    "topic:geocoding language:python",
    "overpass api poi in:name,description",
    "rule-based llm hybrid in:name,description",
    "llm structured extraction pydantic in:name,description",
    "llm output validation schema in:name,description",
    "semantic cache llm in:name,description",
    "topic:information-extraction language:python",
]

TECH_MARKERS: list[str] = [
    "fastapi",
    "flask",
    "django",
    "streamlit",
    "next.js",
    "react",
    "pydantic",
    "langchain",
    "langgraph",
    "crewai",
    "llamaindex",
    "openai",
    "anthropic",
    "claude",
    "gemini",
    "ollama",
    "huggingface",
    "transformers",
    "sentence-transformers",
    "spacy",
    "pymorphy",
    "rapidfuzz",
    "fuzzywuzzy",
    "regex",
    "earley",
    "postgres",
    "pgvector",
    "sqlite",
    "elasticsearch",
    "chromadb",
    "qdrant",
    "faiss",
    "redis",
    "docker",
    "selenium",
    "playwright",
    "beautifulsoup",
    "scrapy",
    "httpx",
    "requests",
    "asyncpg",
    "sqlalchemy",
    "telegram",
    "aiogram",
    "haskell",
    "rust",
    "typescript",
    "duckdb",
    "mcp",
    "rag",
    "embedding",
    "haversine",
    "geojson",
    "overpass",
    "openstreetmap",
    "yandex",
]

CATEGORIES: dict[str, str] = {
    "nl_to_query": "Свободный текст → структурированный запрос (URL / SQL / DSL / гео-фильтры)",
    "real_estate": "Поиск и агрегация недвижимости, парсеры площадок, ассистенты по объектам",
    "hybrid_pipeline": "Гибрид «детерминированное ядро + LLM»: валидация ответа модели, "
    "структурированный вывод, семантический кэш",
    "russian_nlp": "Обработка русского языка: морфология, правила, извлечение фактов",
}

# Курирование: что это, чем похоже на picurl, что можно забрать.
# similarity: high — прямой аналог механики; medium — соседняя механика или домен;
#             low — полезный частный приём или фон.
CURATED: list[dict[str, Any]] = [
    {
        "full_name": "natasha/yargy",
        "categories": ["russian_nlp", "nl_to_query"],
        "similarity": "high",
        "key_features": [
            "Правиловое извлечение фактов из русского текста (Earley-парсер по грамматикам)",
            "Согласование по морфологии через pymorphy2, интерпретация в объекты-факты",
            "Полностью детерминирован: без обучения и без сети",
        ],
        "relevance": "Ближайший идейный аналог слоя app/parsing: то же самое обещание — "
        "русский текст → факты без модели. У picurl роль грамматик играют regex-правила "
        "в rules/ плюс rapidfuzz по справочникам.",
        "takeaways": [
            "Грамматики yargy — альтернатива ручным regex для падежных форм ориентиров",
            "Интерпретация спанов в типизированные факты близка к нашей схеме span→Criteria",
        ],
    },
    {
        "full_name": "natasha/natasha",
        "categories": ["russian_nlp"],
        "similarity": "medium",
        "key_features": [
            "Зонтичный API над razdel/slovnet/yargy: токенизация, морфология, NER, нормализация",
            "Готовые экстракторы адресов, денег и дат для русского языка",
        ],
        "relevance": "Экстракторы денег/адресов решают ту же задачу, что наши правила "
        "price.py и распознавание локаций.",
        "takeaways": [
            "Их AddrExtractor/MoneyExtractor — эталон для тест-кейсов на суммы и адреса",
        ],
    },
    {
        "full_name": "natasha/razdel",
        "categories": ["russian_nlp"],
        "similarity": "low",
        "key_features": [
            "Правиловая сегментация русского текста на предложения и токены",
            "Возвращает спаны (start, stop) — а не только строки",
        ],
        "relevance": "Спановая модель совпадает с нашей: у picurl вся защита от молчаливой "
        "потери фрагмента построена на покрытии спанов.",
        "takeaways": ["Их корпуса ошибок сегментации — источник каверзных кейсов для корпуса QA"],
    },
    {
        "full_name": "natasha/slovnet",
        "categories": ["russian_nlp"],
        "similarity": "low",
        "key_features": [
            "Компактные нейросетевые модели русского NER/морфологии/синтаксиса",
            "Работают на CPU, без внешнего API",
        ],
        "relevance": "Вариант локального (не облачного) ИИ-фолбэка вместо LLM: "
        "модель рядом, инвариант «нет кредов — не ошибка» соблюдается автоматически.",
        "takeaways": ["Локальный NER мог бы удешевить ветку ИИ для распознавания названий ЖК"],
    },
    {
        "full_name": "pymorphy2/pymorphy2",
        "categories": ["russian_nlp"],
        "similarity": "medium",
        "key_features": [
            "Морфологический анализатор и склонение для русского и украинского",
            "Словарь OpenCorpora + предсказание по суффиксам для неизвестных слов",
        ],
        "relevance": "Падежные формы ориентиров («у Кремля», «около Сокольников») picurl сейчас "
        "покрывает алиасами и фаззи-матчингом; pymorphy2 — альтернатива через лемматизацию.",
        "takeaways": [
            "Генерация падежных форм могла бы автоматизировать scripts/landmark_alias_audit.py",
            "Проект в режиме поддержки: активная ветка — pymorphy3",
        ],
    },
    {
        "full_name": "SergeyShk/Word-to-Number-Russian",
        "categories": ["russian_nlp"],
        "similarity": "medium",
        "key_features": [
            "Числительные прописью на русском → число («пятнадцать миллионов» → 15000000)",
            "Маленькая библиотека без зависимостей от моделей",
        ],
        "relevance": "Прямо закрывает дыру формы запроса «до пятнадцати миллионов», которую "
        "цифровой regex в rules/price.py не ловит.",
        "takeaways": ["Кандидат на заимствование алгоритма (лицензия MIT) в правило цены"],
    },
    {
        "full_name": "deeppavlov/DeepPavlov",
        "categories": ["russian_nlp", "hybrid_pipeline"],
        "similarity": "low",
        "key_features": [
            "Библиотека диалоговых систем и NLU с сильной поддержкой русского",
            "Slot filling и классификация интентов как конфигурируемые пайплайны",
        ],
        "relevance": "Slot filling — это ровно «заполнить Criteria из реплики»; отличие в том, "
        "что там это ML-модель с обучением, а у picurl — детерминированные правила.",
        "takeaways": ["Их разметка слотов — образец для расширения корпуса запросов"],
    },
    {
        "full_name": "machinalis/quepy",
        "categories": ["nl_to_query"],
        "similarity": "high",
        "key_features": [
            "Фреймворк «вопрос на естественном языке → запрос к БД» (SPARQL, MQL)",
            "Правила = regex/POS-шаблоны, отображаемые в промежуточное представление запроса",
            "Никакого LLM: полностью детерминированный конвейер",
        ],
        "relevance": "Архитектура один в один как у picurl до слоя ИИ: правила → "
        "промежуточная модель → генератор целевого запроса. Проект заброшен (2020), "
        "то есть показывает и потолок чисто правилового подхода.",
        "takeaways": [
            "Промежуточное представление между правилами и генератором — решение уровня Criteria",
            "Урок: без корпуса регрессий правиловый парсер тихо загнивает",
        ],
    },
    {
        "full_name": "facebook/duckling",
        "categories": ["nl_to_query", "hybrid_pipeline"],
        "similarity": "high",
        "key_features": [
            "Правиловый движок: текст → типизированные сущности (время, суммы, расстояния, объёмы)",
            "Десятки языков, включая русский; композиция правил и ранжирование кандидатов",
            "Отдаёт спаны и все конкурирующие разборы, а не один «победивший»",
        ],
        "relevance": "Промышленный образец того же контракта, что у нас: спаны + конкурирующие "
        "кандидаты вместо молчаливого выбора. Ровно наш инвариант 1 и коллизии спанов.",
        "takeaways": [
            "Их latent/резолюция кандидатов — модель для option_candidates",
            "Разделение «размерность vs единица» полезно для площади и расстояний",
        ],
    },
    {
        "full_name": "geoblocks/etter",
        "categories": ["nl_to_query", "real_estate"],
        "similarity": "high",
        "key_features": [
            "Текстовые гео-запросы → структурированные гео-фильтры для поисковых движков",
            "LLM извлекает намерение, привязка к геоданным остаётся вне модели",
            "FastAPI + pydantic-контракт ответа",
        ],
        "relevance": "Самый близкий по задаче свежий проект: гео-фильтры из фразы, "
        "тот же стек (FastAPI + pydantic) и тот же принцип «модель не считает геометрию».",
        "takeaways": [
            "Сравнить их гейты вызова LLM с нашими гейтами enrichment",
            "Их формат гео-фильтра — ориентир для будущего экспорта Criteria наружу",
        ],
    },
    {
        "full_name": "charonviz/text2geo",
        "categories": ["nl_to_query", "russian_nlp"],
        "similarity": "medium",
        "key_features": [
            "Офлайн-геокодер: название места → координаты, без внешних API и лимитов",
            "Фаззи-сопоставление по локальному справочнику (rapidfuzz)",
        ],
        "relevance": "Та же связка, что в app/parsing/entity_match.py + app/geo: локальный "
        "справочник и фаззи-матчинг вместо сетевого геокодера (наш инвариант 6).",
        "takeaways": ["Их пороги фаззи-совпадения — внешняя точка сверки для OPTION_MIN_QRATIO"],
    },
    {
        "full_name": "Canner/WrenAI",
        "categories": ["nl_to_query", "hybrid_pipeline"],
        "similarity": "medium",
        "key_features": [
            "Text-to-SQL поверх семантического слоя: модель видит описанную схему, а не сырую БД",
            "Валидация и «заземление» сгенерированного запроса, объяснение результата",
            "Мультипровайдерные LLM, векторный поиск по контексту",
        ],
        "relevance": "Тот же приём, что у нас: LLM не выдумывает поля, а выбирает из явно "
        "заданного словаря; всё, что не прошло валидацию, отбрасывается.",
        "takeaways": [
            "Семантический слой = наши JSON-справочники как контракт для модели",
            "Их подход к объяснению («почему такой запрос») близок к ai_explanation",
        ],
    },
    {
        "full_name": "Dataherald/dataherald",
        "categories": ["nl_to_query"],
        "similarity": "medium",
        "key_features": [
            "NL→SQL как сервис с REST API и хранилищем контекста/примеров",
            "Golden-запросы как способ дообучения промпта",
        ],
        "relevance": "Хранилище проверенных примеров — это наша память ИИ (pgvector) и "
        "промоушен фактов, только на SQL-домене. Репозиторий заморожен с 2024.",
        "takeaways": ["Golden SQL ≈ наш промоушен по числу независимых наблюдений"],
    },
    {
        "full_name": "holoviz/lumen",
        "categories": ["nl_to_query"],
        "similarity": "low",
        "key_features": [
            "Агентный фреймворк: естественный язык → SQL, графики, дашборды",
            "Декларативные спецификации источников данных",
        ],
        "relevance": "Пример живого (активно развиваемого) NL→запрос стека; полезен как "
        "источник приёмов по объяснимости шагов агента.",
        "takeaways": ["Пошаговая трассировка агента — идея для расширения warnings_detailed"],
    },
    {
        "full_name": "totalhack/zillion",
        "categories": ["nl_to_query"],
        "similarity": "low",
        "key_features": [
            "Семантическое моделирование данных с опциональным ИИ-слоем поверх",
            "ИИ строго опционален: без него библиотека полностью работоспособна",
        ],
        "relevance": "Ровно наш инвариант 9 («нет ИИ — не ошибка»), реализованный в чужом домене.",
        "takeaways": [
            "Формулировки в их доках про «ИИ как приправа» — аргументация нашего дизайна"
        ],
    },
    {
        "full_name": "XGenerationLab/xiyan_mcp_server",
        "categories": ["nl_to_query", "hybrid_pipeline"],
        "similarity": "low",
        "key_features": [
            "MCP-сервер: любой агент задаёт вопрос к БД на естественном языке",
            "Отдельная модель, специализированная под text-to-SQL",
        ],
        "relevance": "Показывает вариант «picurl как MCP-инструмент»: сервис остаётся "
        "детерминированным, а агент снаружи.",
        "takeaways": ["MCP-обёртка над POST /build-url — дешёвый способ отдать сервис агентам"],
    },
    {
        "full_name": "AleksNeStu/ai-real-estate-assistant",
        "categories": ["real_estate", "hybrid_pipeline"],
        "similarity": "high",
        "key_features": [
            "ИИ-ассистент по подбору недвижимости: RAG + векторный поиск по объявлениям",
            "FastAPI + Next.js, мультипровайдерные LLM (OpenAI/Anthropic/Ollama), ChromaDB",
            "Docker-компоуз со всей инфраструктурой",
        ],
        "relevance": "Самый близкий действующий аналог по домену и стеку. Ключевое расхождение: "
        "они ищут по собственному индексу объявлений, picurl не хранит объекты, а строит "
        "ссылку на чужой поиск.",
        "takeaways": [
            "Сравнить их промпты подбора с app/ai/prompts.py",
            "Их UI-контракт (чат) — альтернатива нашему одноразовому POST /build-url",
        ],
    },
    {
        "full_name": "AnthonyBloomer/daftlistings",
        "categories": ["real_estate", "nl_to_query"],
        "similarity": "high",
        "key_features": [
            "Программный конструктор поисковых запросов к площадке Daft.ie (Ирландия)",
            "Типизированные фильтры: цена, комнаты, площадь, район, тип жилья",
            "Обратно разобранный API площадки + постраничный сбор результатов",
        ],
        "relevance": "Прямой аналог app/pik/url_builder.py + validator.py: чужая площадка, "
        "свой типизированный слой фильтров. Разница — у них вход программный, у нас текст.",
        "takeaways": [
            "Их перечисления фильтров — образец полноты каталога параметров площадки",
            "Урок сопровождения: фильтры площадки меняются, нужен аудит справочников",
        ],
    },
    {
        "full_name": "ZacharyHampton/HomeHarvest",
        "categories": ["real_estate"],
        "similarity": "medium",
        "key_features": [
            "Сбор данных об объектах недвижимости (США, MLS/Realtor) в табличный вид",
            "Единый интерфейс поиска по локации и типу сделки",
        ],
        "relevance": "Показывает противоположную стратегию: тянуть карточки к себе, а не "
        "отдавать ссылку. Хороший ориентир для аудита выдачи (scripts/outcome_audit.py).",
        "takeaways": ["Их нормализация полей карточки — что именно стоит сверять в outcome-аудите"],
    },
    {
        "full_name": "0xMH/pyfunda",
        "categories": ["real_estate"],
        "similarity": "medium",
        "key_features": [
            "Клиент обратно разобранного мобильного API площадки Funda.nl без скрейпинга",
            "Строит запросы фильтрации и разбирает ответ в модели",
        ],
        "relevance": "Тот же приём, что у нас с api.pik.ru: обратная разработка публичного "
        "бэкенда площадки вместо парсинга HTML.",
        "takeaways": [
            "Их описание расхождений между мобильным и веб-бэкендом — прямая параллель "
            "нашему разделу про два бэкенда ПИК",
        ],
    },
    {
        "full_name": "lenarsaitov/cianparser",
        "categories": ["real_estate", "russian_nlp"],
        "similarity": "medium",
        "key_features": [
            "Сбор объявлений с cian.ru: локации, типы сделок, обход защиты",
            "Русскоязычный домен: районы, метро, типы жилья",
        ],
        "relevance": "Самый популярный русский real-estate парсер; его справочники локаций — "
        "внешняя точка сверки для app/reference/*.json.",
        "takeaways": ["Сверить наши алиасы районов/метро с их номенклатурой"],
    },
    {
        "full_name": "Duff89/parser_avito",
        "categories": ["real_estate"],
        "similarity": "low",
        "key_features": [
            "Мониторинг новых объявлений Avito с уведомлениями, Playwright под капотом",
            "Активно поддерживается, большая русскоязычная аудитория",
        ],
        "relevance": "Домен пересекается, механика — нет: там мониторинг выдачи, "
        "у нас построение ссылки.",
        "takeaways": ["Их набор фильтров Avito — чек-лист «что вообще спрашивают про квартиру»"],
    },
    {
        "full_name": "andprov/krisha.kz",
        "categories": ["real_estate"],
        "similarity": "low",
        "key_features": ["Парсер аренды с krisha.kz", "Простой стек: requests + BeautifulSoup"],
        "relevance": "Русскоязычный (Казахстан) домен; полезен как источник формулировок "
        "запросов для корпуса.",
        "takeaways": ["Формулировки объявлений — материал для расширения tests/corpus"],
    },
    {
        "full_name": "ai-engineers-guild/apartment-hunter",
        "categories": ["real_estate", "hybrid_pipeline"],
        "similarity": "high",
        "key_features": [
            "Агрегатор аренды (krisha.kz) с MCP-сервером и поиском по полигону на карте",
            "Скоринг объявлений через LLM поверх детерминированного отбора",
            "ChromaDB для семантического поиска",
        ],
        "relevance": "Тот же порядок слоёв, что у нас: сначала геометрия и фильтры "
        "детерминированно, LLM — сверху и опционально.",
        "takeaways": [
            "Полигональный отбор — расширение нашей МКАД-логики на произвольные области",
            "MCP-интерфейс к поиску жилья — подтверждение идеи обёртки над /build-url",
        ],
    },
    {
        "full_name": "Modern-Messiah/Autonomous-Personal-Assistant-AI-Agent-",
        "categories": ["real_estate", "hybrid_pipeline"],
        "similarity": "medium",
        "key_features": [
            "Мультиагентная система на LangGraph: парсинг krisha.kz, обогащение данными 2GIS",
            "Ранжирование объявлений LLM, доставка через Telegram, хранение в Postgres",
        ],
        "relevance": "Обогащение гео-данными (2GIS) — аналог нашего POI-кэша и Яндекс.Карт; "
        "отличие в том, что решения принимает агент, а не детерминированный код.",
        "takeaways": ["Сравнить их источник POI (2GIS) с нашим OSM/Overpass по полноте"],
    },
    {
        "full_name": "gostak-dd/condo_gpt",
        "categories": ["real_estate", "nl_to_query"],
        "similarity": "medium",
        "key_features": [
            "Ассистент по базе кондоминиумов Майами: вопрос на естественном языке → SQL",
            "LangChain/LangGraph поверх Postgres",
        ],
        "relevance": "Домен недвижимости + NL→запрос, но целевой язык SQL, а не URL площадки.",
        "takeaways": ["Их приёмы ограничения SQL-вольностей модели — параллель нашей санитизации"],
    },
    {
        "full_name": "brightdata/real-estate-ai-agent",
        "categories": ["real_estate"],
        "similarity": "low",
        "key_features": [
            "Агенты (CrewAI) извлекают данные объектов в структурированный JSON",
            "Ставка на LLM в самом извлечении, без правилового ядра",
        ],
        "relevance": "Контрпример нашей архитектуре: всё отдано модели. Полезен как аргумент "
        "о стоимости и воспроизводимости.",
        "takeaways": [
            "Оценить, во сколько вызовов обходится их путь против нашего офлайн-парсинга"
        ],
    },
    {
        "full_name": "smurkiooo/SmartPropertyScraper",
        "categories": ["real_estate", "russian_nlp"],
        "similarity": "medium",
        "key_features": [
            "Скрейпинг объявлений о квартирах в Москве с Циана + RAG/LLM поиск по ним",
            "FastAPI, ChromaDB, эмбеддинги, Telegram-интерфейс",
        ],
        "relevance": "Русскоязычный запрос про московские квартиры — тот же вход, что у picurl, "
        "но ответ ищется в своём индексе, а не в ссылке на площадку.",
        "takeaways": ["Их запросы пользователей — материал для корпуса длинных запросов"],
    },
    {
        "full_name": "Bavalpreet/Ilore-AI-Assisted-Property-Search-System",
        "categories": ["real_estate", "nl_to_query"],
        "similarity": "medium",
        "key_features": [
            "LLM извлекает сущности недвижимости из свободного запроса пользователя",
            "Streamlit-демо поверх извлечённых сущностей",
        ],
        "relevance": "Тот же шаг «фраза → сущности», но сразу моделью и без справочника: "
        "иллюстрация того, что picurl отдаёт ИИ только остаток.",
        "takeaways": ["Список извлекаемых сущностей — чек-лист полноты Criteria"],
    },
    {
        "full_name": "KKorzec/housing-krk-rag-frontend",
        "categories": ["real_estate"],
        "similarity": "low",
        "key_features": [
            "Чат-интерфейс поиска квартир на естественном языке с интерактивной картой (Краков)",
            "RAG-бэкенд отдельно, фронтенд на React",
        ],
        "relevance": "Связка «естественный язык + карта» — то, что у нас делает map_config "
        "и templates/index.html.",
        "takeaways": ["Их сценарий «результат на карте» — ориентир для развития веб-интерфейса"],
    },
    {
        "full_name": "nkbolg/find-a-flat-bot",
        "categories": ["real_estate"],
        "similarity": "low",
        "key_features": [
            "Telegram-бот с уведомлениями о подходящих объявлениях аренды (Avito)",
            "Фильтры задаются конфигом, не текстом",
        ],
        "relevance": "Показывает соседний UX: подписка на фильтр вместо разовой ссылки.",
        "takeaways": ["Идея сохранённого запроса поверх Criteria"],
    },
    {
        "full_name": "guardrails-ai/guardrails",
        "categories": ["hybrid_pipeline"],
        "similarity": "high",
        "key_features": [
            "Валидаторы поверх ответов LLM: схема, ограничения, повторный запрос при провале",
            "Явное разделение «модель ответила» и «ответ пригоден»",
        ],
        "relevance": "Ровно наша дисциплина санитизации (sanitize_against_shortlist и др.) "
        "и различие ai_failed vs «ответила и не пригодилась».",
        "takeaways": [
            "Их таксономия исходов валидации — материал для порций 3-5 категоризации warnings",
        ],
    },
    {
        "full_name": "567-labs/instructor",
        "categories": ["hybrid_pipeline"],
        "similarity": "medium",
        "key_features": [
            "Структурированный вывод LLM через pydantic-модели с ретраями по ошибкам валидации",
            "Мультипровайдерность (OpenAI, Anthropic, локальные модели)",
        ],
        "relevance": "Тот же контракт «ответ модели = pydantic-объект или ничего», что в "
        "app/ai/schema.py.",
        "takeaways": ["Ретрай по тексту ошибки валидации — возможное улучшение ветки enrichment"],
    },
    {
        "full_name": "dottxt-ai/outlines",
        "categories": ["hybrid_pipeline"],
        "similarity": "medium",
        "key_features": [
            "Ограниченная генерация: грамматика/regex/JSON-схема принуждают форму ответа",
            "Гарантия формы на уровне декодирования, а не постпроверки",
        ],
        "relevance": "Альтернатива нашей поствалидации: для локальных моделей форму ответа "
        "можно гарантировать, вместо того чтобы отбивать её потом.",
        "takeaways": ["Применимо только к самостоятельно запускаемым моделям, не к облачным API"],
    },
    {
        "full_name": "zilliztech/GPTCache",
        "categories": ["hybrid_pipeline"],
        "similarity": "high",
        "key_features": [
            "Семантический кэш ответов LLM: эмбеддинги запроса + порог похожести",
            "Сменные хранилища векторов, включая pgvector",
        ],
        "relevance": "Прямой аналог app/ai/memory.py: тот же приём и та же развилка "
        "с калибровкой порога похожести.",
        "takeaways": [
            "Их дефолтные пороги и метрики попаданий — внешняя сверка для нашего порога кэша",
            "Разбор ложных попаданий кэша — риск, который стоит описать в thresholds-rationale",
        ],
    },
    {
        "full_name": "RasaHQ/rasa",
        "categories": ["hybrid_pipeline", "nl_to_query"],
        "similarity": "medium",
        "key_features": [
            "Классика NLU: интенты + слоты, правила и ML в одном конвейере",
            "Формы (forms) как способ добрать недостающие слоты уточняющими вопросами",
        ],
        "relevance": "Идея «форма добирает недостающие слоты» — то, чего у picurl нет: "
        "мы отвечаем ссылкой сразу, без уточняющего диалога.",
        "takeaways": ["Уточняющий вопрос по недостающему фильтру — возможное развитие API"],
    },
    {
        "full_name": "typesense/typesense",
        "categories": ["nl_to_query"],
        "similarity": "low",
        "key_features": [
            "Поисковый движок с опечаточной устойчивостью и фасетами",
            "Есть режим natural language search: фраза → фильтры движка через LLM",
        ],
        "relevance": "Их NL-режим — та же задача «фраза → фасетные фильтры», но внутри "
        "собственного индекса.",
        "takeaways": ["Их схема описания фасетов для модели — образец компактного контекста"],
    },
    {
        "full_name": "sqlalchemy-filterset/sqlalchemy-filterset",
        "categories": ["nl_to_query"],
        "similarity": "low",
        "key_features": [
            "Декларативные наборы фильтров поверх SQLAlchemy для API",
            "Фильтр как объект: тип, поле, способ применения",
        ],
        "relevance": "Декларативное описание фильтра как объекта — альтернатива нашему "
        "императивному url_builder.",
        "takeaways": ["Идея реестра фильтров с метаданными вместо ветвлений в build_url"],
    },
]


def search(client: httpx.Client, query: str, per_page: int = 30) -> list[dict[str, Any]]:
    """Один запрос к search/repositories. 403 (лимит) — не молча: печатаем и отдаём пусто."""
    try:
        response = client.get(
            f"{GITHUB_API}/search/repositories",
            params={"q": query, "per_page": per_page},
            headers={"Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"ОШИБКА поиска [{query[:60]}…]: {exc}")
        return []
    return response.json().get("items", [])


def fetch_metadata(client: httpx.Client, full_names: list[str]) -> dict[str, dict[str, Any]]:
    """Факты о репозиториях пачками: несколько `repo:` в одном запросе = ИЛИ."""
    found: dict[str, dict[str, Any]] = {}
    for start in range(0, len(full_names), BATCH_SIZE):
        batch = full_names[start : start + BATCH_SIZE]
        query = " ".join(f"repo:{name}" for name in batch)
        for item in search(client, query, per_page=BATCH_SIZE * 2):
            found[item["full_name"].lower()] = item
        if start + BATCH_SIZE < len(full_names):
            time.sleep(SEARCH_DELAY_S)
    return found


def fetch_readme(client: httpx.Client, full_name: str, branch_hint: str) -> str:
    """README из raw.githubusercontent — лимиты API не тратит."""
    branches = [branch_hint, "main", "master"]
    for branch in dict.fromkeys(b for b in branches if b):
        for filename in ("README.md", "readme.md", "README.rst"):
            try:
                response = client.get(f"{RAW_BASE}/{full_name}/{branch}/{filename}")
            except httpx.HTTPError:
                continue
            if response.status_code == 200:
                return response.text
    return ""


def _record(client: httpx.Client, meta: dict[str, Any], curated: dict[str, Any]) -> dict[str, Any]:
    readme = fetch_readme(client, meta["full_name"], meta.get("default_branch") or "main")
    lowered = readme.lower()
    return {
        "full_name": meta["full_name"],
        "url": meta["html_url"],
        "homepage": meta.get("homepage") or None,
        "description": meta.get("description"),
        "language": meta.get("language"),
        "stars": meta.get("stargazers_count"),
        "forks": meta.get("forks_count"),
        "license": (meta.get("license") or {}).get("spdx_id"),
        "topics": meta.get("topics") or [],
        "created_at": meta.get("created_at"),
        "last_push": meta.get("pushed_at"),
        "archived": bool(meta.get("archived")),
        "readme_found": bool(readme),
        "tech_stack": [marker for marker in TECH_MARKERS if marker in lowered],
        "categories": curated["categories"],
        "similarity": curated["similarity"],
        "key_features": curated["key_features"],
        "relevance": curated["relevance"],
        "takeaways": curated["takeaways"],
    }


def discover(client: httpx.Client) -> list[dict[str, Any]]:
    """Прогон SEARCH_QUERIES: что нашлось сверх CURATED (кандидаты на курирование)."""
    known = {item["full_name"].lower() for item in CURATED}
    seen: dict[str, dict[str, Any]] = {}
    for index, query in enumerate(SEARCH_QUERIES):
        items = search(client, query, per_page=20)
        fresh = 0
        for item in items:
            key = item["full_name"].lower()
            if key in known or key in seen:
                continue
            seen[key] = {
                "full_name": item["full_name"],
                "url": item["html_url"],
                "description": item.get("description"),
                "stars": item.get("stargazers_count"),
                "language": item.get("language"),
                "found_by": query,
            }
            fresh += 1
        total = len(SEARCH_QUERIES)
        print(f"[{index + 1:>2}/{total}] {len(items):>2} найдено, {fresh} новых — {query}")
        if index + 1 < len(SEARCH_QUERIES):
            time.sleep(SEARCH_DELAY_S)
    return sorted(seen.values(), key=lambda r: -(r["stars"] or 0))


def build_dataset(client: httpx.Client) -> dict[str, Any]:
    metadata = fetch_metadata(client, [item["full_name"] for item in CURATED])

    repositories: list[dict[str, Any]] = []
    missing: list[str] = []
    for item in CURATED:
        meta = metadata.get(item["full_name"].lower())
        if meta is None:
            missing.append(item["full_name"])
            print(f"НЕ НАЙДЕН (удалён/переименован/приватный): {item['full_name']}")
            continue
        record = _record(client, meta, item)
        repositories.append(record)
        print(f"{record['full_name']:<52} {record['stars']:>6}* {record['language']}")

    repositories.sort(key=lambda r: (r["categories"][0], -(r["stars"] or 0)))
    return {
        "schema_version": 1,
        "generated_by": "scripts/similar_projects_research.py",
        "topic": "Аналоги picurl на GitHub: NL→URL, поиск недвижимости, "
        "гибрид «правила + ИИ», обработка русского языка",
        "method": {
            "source": "GitHub REST API (search/repositories), README с raw.githubusercontent",
            "search_queries": SEARCH_QUERIES,
            "curation": "Кандидаты из выдачи отобраны вручную по близости механики, "
            "а не по числу звёзд; метаданные обновляются перезапуском скрипта",
            "limits": [
                "Поиск GitHub объединяет термины через AND — длинные фразы дают пустую выдачу",
                "Стек определён по упоминаниям в README, а не по разбору зависимостей",
                "Без токена GitHub: поиск — 10 запросов в минуту, core — 60 в час; "
                "поэтому факты собираются пачками, а список курируемый, а не «всё, что нашлось»",
                "Приватные и не проиндексированные репозитории в выдачу не попадают",
                "Звёзды и даты — снимок на момент прогона, а не постоянная величина",
            ],
        },
        "categories": CATEGORIES,
        "repositories": repositories,
        "missing": missing,
        "findings": FINDINGS,
    }


FINDINGS: dict[str, Any] = {
    "no_direct_competitor": "Проекта, который строит ссылку на поиск pik.ru из русской фразы, "
    "на GitHub нет: запросы по pik.ru дают только совпадения в списках доменов. "
    "Ниша picurl (текст → чужой поисковый URL, а не собственный индекс) свободна.",
    "dominant_pattern": "Подавляющее большинство свежих проектов домена решает задачу так: "
    "стянуть объявления к себе → эмбеддинги → RAG/LLM-ранжирование. Никакой ссылки на площадку "
    "они не отдают и на её фильтрах не завязаны — зато обязаны поддерживать свой индекс.",
    "closest_analogues": [
        "machinalis/quepy и facebook/duckling — правиловое ядро «текст → структура» "
        "с конкурирующими кандидатами и спанами",
        "AnthonyBloomer/daftlistings — типизированный конструктор запросов к чужой площадке",
        "geoblocks/etter — гео-фильтры из фразы на том же стеке FastAPI + pydantic",
        "AleksNeStu/ai-real-estate-assistant — ближайший действующий аналог по домену",
    ],
    "gaps_in_picurl_worth_watching": [
        "Числительные прописью («до пятнадцати миллионов») — закрывается приёмом из "
        "SergeyShk/Word-to-Number-Russian",
        "Падежные формы ориентиров: pymorphy2/yargy вместо ручных алиасов",
        "Уточняющий вопрос по недостающему фильтру (forms в RasaHQ/rasa) — у нас ответ всегда "
        "одноразовый",
        "Полигональный гео-отбор (ai-engineers-guild/apartment-hunter) — обобщение МКАД-логики",
    ],
    "picurl_differentiators": [
        "Детерминированное ядро: без сети и без модели строится полноценная ссылка",
        "ИИ обрабатывает только остаток и обязан валидироваться против справочника",
        "Ничего не отбрасывается молча: непонятый фрагмент попадает в warnings с категорией",
        "Два корпуса регрессий с порогами — редкость даже среди проектов с тысячами звёзд",
    ],
    "recommended_deep_dives": [
        "facebook/duckling — модель кандидатов и спанов",
        "guardrails-ai/guardrails — таксономия исходов валидации ответа модели",
        "zilliztech/GPTCache — калибровка порога семантического кэша",
        "AnthonyBloomer/daftlistings — полнота каталога фильтров чужой площадки",
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Собрать research_data.json по аналогам picurl")
    parser.add_argument("--out", default=str(ROOT / "research_data.json"))
    parser.add_argument(
        "--discover",
        action="store_true",
        help="прогнать SEARCH_QUERIES и напечатать кандидатов сверх CURATED",
    )
    parser.add_argument(
        "--discover-out", default=None, help="куда сложить полную выдачу --discover (JSON)"
    )
    args = parser.parse_args()

    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=30.0,
        follow_redirects=True,
    ) as client:
        if args.discover:
            candidates = discover(client)
            print(f"\nНовых кандидатов: {len(candidates)}")
            for candidate in candidates[:40]:
                stars = candidate["stars"] or 0
                print(f"{stars:>6}* {candidate['full_name'][:50]:<50} {candidate['description']}")
            if args.discover_out:
                Path(args.discover_out).write_text(
                    json.dumps(candidates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
            return

        dataset = build_dataset(client)

    out_path = Path(args.out)
    out_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nЗаписано: {out_path} ({len(dataset['repositories'])} репозиториев)")
    if dataset["missing"]:
        print(f"Не найдено: {', '.join(dataset['missing'])}")


if __name__ == "__main__":
    main()
