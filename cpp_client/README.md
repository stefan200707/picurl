# C++-клиент `/build-url` (прототип)

Однофайловый консольный клиент: принимает текст пожеланий к квартире, шлёт
`POST /build-url` в picurl и печатает разобранный `BuildUrlResponse`.

Назначение — доказать, что контракт ответа (`app/api/schemas.py`) собирается на
C++ вручную, без кодогенерации: HTTP берёт на себя libcurl, JSON — nlohmann/json.
Это прототип, а не часть рантайма: Python-код проекта он не трогает и в
`uv run pytest` не участвует.

## Зависимости

Обе библиотеки ставятся системно, **в репозиторий не добавляются**:

- **libcurl** (dev-пакет с заголовками);
- **nlohmann/json** — header-only, нужен только заголовок `nlohmann/json.hpp`.

```bash
# Debian / Ubuntu
sudo apt install g++ libcurl4-openssl-dev nlohmann-json3-dev

# Fedora
sudo dnf install gcc-c++ libcurl-devel json-devel

# macOS (Homebrew; заголовки лягут в $(brew --prefix)/include)
brew install curl nlohmann-json
```

Альтернатива без пакетного менеджера: скачать единственный заголовок
`json.hpp` из релизов [nlohmann/json](https://github.com/nlohmann/json/releases)
в `./nlohmann/json.hpp` и собирать с `-I.`.

## Сборка

Из каталога `cpp_client/`:

```bash
g++ -std=c++17 -O2 -o build_url_client build_url_client.cpp -lcurl
```

На macOS (Homebrew кладёт заголовки вне путей компилятора по умолчанию):

```bash
g++ -std=c++17 -O2 -I"$(brew --prefix)/include" -o build_url_client build_url_client.cpp -lcurl
```

## Запуск

Сервис должен быть поднят: `uv run fastapi dev` в корне репозитория.

Базовый URL берётся из переменной окружения `PICURL_BASE_URL`
(по умолчанию `http://localhost:8000`). Текст запроса — аргумент командной
строки; несколько аргументов склеиваются через пробел, но кавычки надёжнее.

```bash
./build_url_client "хочу двушку у метро Аэропорт до 15 млн с отделкой, привет"
```

Ожидаемый вывод (тексты — из реального ответа сервиса; конкретные `blocks`,
`result_count` и набор warnings зависят от справочников и доступности
`api.pik.ru`):

```
url: https://www.pik.ru/search/two-room/finish?blocks=477,1165,518,1372,2106,378,464,1555&priceFrom=0&priceTo=15000000
result_count: 0
warnings (4):
  Неоднозначность для «Аэропорт»: выбрано Аэропорт (метро), возможные варианты: Аэропорт Внуково (метро)
  «привет»: не удалось распознать, не попало в ссылку
  метро «Аэропорт»: id не подтверждён, в радиусе 1.5 км от станции ЖК нет — показаны 8 ближайших, от 4.22 км по прямой
  под критерии ничего не найдено
ai_used: false
map_config: есть
```

Другой базовый адрес:

```bash
PICURL_BASE_URL=http://127.0.0.1:8080 ./build_url_client "однушка до 10 млн"
```

Простой запрос без warnings:

```bash
$ ./build_url_client "однушка"
url: https://www.pik.ru/search/one-room
result_count: 3321
warnings (0):
  (нет)
ai_used: false
map_config: есть
```

Если валидация выдачи не выполнялась или не удалась (нет доступа к
`api.pik.ru`), сервис присылает `result_count: null`, а `map_config` может
отсутствовать целиком. Это штатный ответ, а не ошибка — клиент печатает
`нет данных` / `нет` и завершается кодом 0:

```
url: https://www.pik.ru/search/one-room
result_count: нет данных
warnings (0):
  (нет)
ai_used: false
map_config: нет
```

Недоступный сервер — сообщение в stderr и ненулевой код выхода, без падения:

```bash
$ PICURL_BASE_URL=http://localhost:9 ./build_url_client "однушка"
Сервис picurl недоступен по адресу http://localhost:9/build-url: Connection refused
Проверьте, что сервер запущен (uv run fastapi dev) и что PICURL_BASE_URL указывает на него.
$ echo $?
2
```

## Коды выхода

| Код | Значение |
|---|---|
| 0 | ответ получен и разобран |
| 1 | не передан текст запроса (напечатана подсказка по использованию) |
| 2 | сервис недоступен / сетевая ошибка / сбой инициализации libcurl |
| 3 | сервис ответил статусом не 2xx (тело ответа выводится в stderr) |
| 4 | тело ответа не разбирается как JSON-объект `BuildUrlResponse` |

## Что клиент печатает

Из `BuildUrlResponse` берутся `url`, `result_count`, `warnings`, `ai_used` плюс
признак наличия `map_config`. Необязательные поля (`result_count`,
`map_config`, `ai_explanation`) читаются защитно: отсутствие ключа и `null`
обрабатываются одинаково и на код выхода не влияют. Остальные поля ответа
(`criteria`, `warnings_detailed`, `ai_failed`, `ai_cache_hit`) клиент не
печатает, но и не требует — незнакомые ключи игнорируются, так что расширение
ответа сервера прототип не ломает.
