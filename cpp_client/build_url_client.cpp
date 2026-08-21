// Минимальный консольный C++-клиент picurl: текст запроса -> POST /build-url -> ссылка.
//
// Доказывает, что контракт BuildUrlResponse (app/api/schemas.py) собирается на C++
// без генератора кода: libcurl для HTTP, nlohmann/json для разбора ответа.
//
// Сборка и запуск — cpp_client/README.md.
//
// Коды выхода:
//   0 — ответ получен и разобран;
//   1 — неверные аргументы командной строки;
//   2 — сервер недоступен / сетевая ошибка (типовой случай: сервис не запущен);
//   3 — сервер ответил, но статусом не 2xx;
//   4 — тело ответа не разбирается как ожидаемый JSON.

#include <curl/curl.h>

#include <cstdlib>
#include <exception>
#include <iostream>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

namespace {

using json = nlohmann::json;

constexpr const char *kDefaultBaseUrl = "http://localhost:8000";

constexpr int kExitOk = 0;
constexpr int kExitUsage = 1;
constexpr int kExitNetwork = 2;
constexpr int kExitHttp = 3;
constexpr int kExitBadPayload = 4;

// RAII вокруг curl_easy_*: клиент не должен утекать хендлом ни на одной ветке выхода.
class CurlHandle {
public:
    CurlHandle() : handle_(curl_easy_init()) {}
    ~CurlHandle() {
        if (handle_ != nullptr) {
            curl_easy_cleanup(handle_);
        }
    }

    CurlHandle(const CurlHandle &) = delete;
    CurlHandle &operator=(const CurlHandle &) = delete;

    CURL *get() const { return handle_; }

private:
    CURL *handle_;
};

// RAII вокруг списка заголовков.
class CurlHeaders {
public:
    ~CurlHeaders() {
        if (list_ != nullptr) {
            curl_slist_free_all(list_);
        }
    }

    CurlHeaders() = default;
    CurlHeaders(const CurlHeaders &) = delete;
    CurlHeaders &operator=(const CurlHeaders &) = delete;

    bool append(const char *header) {
        curl_slist *updated = curl_slist_append(list_, header);
        if (updated == nullptr) {
            return false;
        }
        list_ = updated;
        return true;
    }

    curl_slist *get() const { return list_; }

private:
    curl_slist *list_ = nullptr;
};

size_t write_body(char *ptr, size_t size, size_t nmemb, void *userdata) {
    const size_t chunk = size * nmemb;
    auto *sink = static_cast<std::string *>(userdata);
    sink->append(ptr, chunk);
    return chunk;
}

std::string env_or(const char *name, const char *fallback) {
    const char *value = std::getenv(name);
    if (value == nullptr || *value == '\0') {
        return std::string(fallback);
    }
    return std::string(value);
}

std::string strip_trailing_slashes(std::string value) {
    while (!value.empty() && value.back() == '/') {
        value.pop_back();
    }
    return value;
}

std::string join_args(int argc, char **argv) {
    std::string text;
    for (int i = 1; i < argc; ++i) {
        if (!text.empty()) {
            text.push_back(' ');
        }
        text.append(argv[i]);
    }
    return text;
}

// Ответ pydantic-модели: поле может отсутствовать целиком или прийти null —
// оба случая штатные (result_count, ai_explanation, map_config).
bool has_value(const json &payload, const char *key) {
    auto it = payload.find(key);
    return it != payload.end() && !it->is_null();
}

std::string string_field(const json &payload, const char *key, const std::string &fallback) {
    auto it = payload.find(key);
    if (it == payload.end() || !it->is_string()) {
        return fallback;
    }
    return it->get<std::string>();
}

bool bool_field(const json &payload, const char *key, bool fallback) {
    auto it = payload.find(key);
    if (it == payload.end() || !it->is_boolean()) {
        return fallback;
    }
    return it->get<bool>();
}

std::vector<std::string> string_list_field(const json &payload, const char *key) {
    std::vector<std::string> items;
    auto it = payload.find(key);
    if (it == payload.end() || !it->is_array()) {
        return items;
    }
    for (const json &item : *it) {
        items.push_back(item.is_string() ? item.get<std::string>() : item.dump());
    }
    return items;
}

void print_usage(const char *program) {
    std::cerr << "Использование: " << program << " \"<текст запроса>\"\n"
              << "Например:      " << program << " \"двушка у метро до 15 млн\"\n"
              << "Базовый URL сервиса — переменная окружения PICURL_BASE_URL "
              << "(по умолчанию " << kDefaultBaseUrl << ").\n";
}

void print_response(const json &payload) {
    std::cout << "url: " << string_field(payload, "url", "(нет)") << "\n";

    std::cout << "result_count: ";
    auto count = payload.find("result_count");
    if (count != payload.end() && count->is_number_integer()) {
        std::cout << count->get<long long>() << "\n";
    } else {
        // null/отсутствие — валидация выдачи не выполнялась либо не удалась.
        std::cout << "нет данных\n";
    }

    const std::vector<std::string> warnings = string_list_field(payload, "warnings");
    std::cout << "warnings (" << warnings.size() << "):\n";
    if (warnings.empty()) {
        std::cout << "  (нет)\n";
    } else {
        for (const std::string &warning : warnings) {
            std::cout << "  " << warning << "\n";
        }
    }

    std::cout << "ai_used: " << (bool_field(payload, "ai_used", false) ? "true" : "false") << "\n";
    std::cout << "map_config: " << (has_value(payload, "map_config") ? "есть" : "нет") << "\n";
}

int run(int argc, char **argv) {
    const char *program = (argc > 0 && argv[0] != nullptr) ? argv[0] : "build_url_client";
    const std::string text = join_args(argc, argv);
    if (text.empty()) {
        print_usage(program);
        return kExitUsage;
    }

    const std::string base_url = strip_trailing_slashes(env_or("PICURL_BASE_URL", kDefaultBaseUrl));
    const std::string endpoint = base_url + "/build-url";
    const std::string request_body = json{{"text", text}}.dump();

    CurlHandle curl;
    if (curl.get() == nullptr) {
        std::cerr << "Не удалось инициализировать libcurl.\n";
        return kExitNetwork;
    }

    CurlHeaders headers;
    if (!headers.append("Content-Type: application/json") ||
        !headers.append("Accept: application/json")) {
        std::cerr << "Не удалось собрать HTTP-заголовки.\n";
        return kExitNetwork;
    }

    std::string response_body;
    char error_buffer[CURL_ERROR_SIZE] = {0};

    curl_easy_setopt(curl.get(), CURLOPT_URL, endpoint.c_str());
    curl_easy_setopt(curl.get(), CURLOPT_POST, 1L);
    curl_easy_setopt(curl.get(), CURLOPT_POSTFIELDS, request_body.c_str());
    curl_easy_setopt(curl.get(), CURLOPT_POSTFIELDSIZE, static_cast<long>(request_body.size()));
    curl_easy_setopt(curl.get(), CURLOPT_HTTPHEADER, headers.get());
    curl_easy_setopt(curl.get(), CURLOPT_WRITEFUNCTION, write_body);
    curl_easy_setopt(curl.get(), CURLOPT_WRITEDATA, &response_body);
    curl_easy_setopt(curl.get(), CURLOPT_ERRORBUFFER, error_buffer);
    curl_easy_setopt(curl.get(), CURLOPT_FOLLOWLOCATION, 1L);
    curl_easy_setopt(curl.get(), CURLOPT_CONNECTTIMEOUT, 5L);
    curl_easy_setopt(curl.get(), CURLOPT_TIMEOUT, 60L);
    curl_easy_setopt(curl.get(), CURLOPT_USERAGENT, "picurl-cpp-client/1.0");

    const CURLcode code = curl_easy_perform(curl.get());
    if (code != CURLE_OK) {
        const char *detail = (error_buffer[0] != '\0') ? error_buffer : curl_easy_strerror(code);
        std::cerr << "Сервис picurl недоступен по адресу " << endpoint << ": " << detail << "\n"
                  << "Проверьте, что сервер запущен (uv run fastapi dev) и что "
                  << "PICURL_BASE_URL указывает на него.\n";
        return kExitNetwork;
    }

    long status = 0;
    curl_easy_getinfo(curl.get(), CURLINFO_RESPONSE_CODE, &status);
    if (status < 200 || status >= 300) {
        std::cerr << "Сервис ответил HTTP " << status << " на POST " << endpoint << ".\n";
        if (!response_body.empty()) {
            std::cerr << "Тело ответа: " << response_body.substr(0, 1000) << "\n";
        }
        return kExitHttp;
    }

    const json payload = json::parse(response_body, nullptr, /*allow_exceptions=*/false);
    if (payload.is_discarded() || !payload.is_object()) {
        std::cerr << "Ответ сервиса не является JSON-объектом BuildUrlResponse.\n";
        if (!response_body.empty()) {
            std::cerr << "Тело ответа: " << response_body.substr(0, 1000) << "\n";
        }
        return kExitBadPayload;
    }

    print_response(payload);
    return kExitOk;
}

}  // namespace

int main(int argc, char **argv) {
    if (curl_global_init(CURL_GLOBAL_DEFAULT) != CURLE_OK) {
        std::cerr << "Не удалось инициализировать libcurl (curl_global_init).\n";
        return kExitNetwork;
    }

    int exit_code = kExitBadPayload;
    // Ни одна ветка не должна завершаться необработанным исключением:
    // недоступный сервер — это сообщение и код возврата, а не std::terminate.
    try {
        exit_code = run(argc, argv);
    } catch (const std::exception &error) {
        std::cerr << "Непредвиденная ошибка клиента: " << error.what() << "\n";
    } catch (...) {
        std::cerr << "Непредвиденная ошибка клиента.\n";
    }

    curl_global_cleanup();
    return exit_code;
}
