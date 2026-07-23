from typing import Any

from pydantic import BaseModel, Field


class BuildUrlRequest(BaseModel):
    """Запрос: свободный текст с пожеланиями к квартире."""

    text: str = Field(
        description=(
            "Свободный текст на русском языке с пожеланиями к квартире: "
            "комнатность, бюджет, метро/район, отделка, сортировка и т.д."
        ),
        examples=["хочу двушку у метро, до 15 млн, с отделкой"],
        min_length=1,
    )


class BuildUrlResponse(BaseModel):
    """Ответ: готовая ссылка на pik.ru и предупреждения о нераспознанном."""

    url: str = Field(description="Готовая ссылка на pik.ru с применёнными фильтрами.")
    criteria: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Распознанные критерии в человекочитаемом виде (результат Criteria.to_public_dict())."
        ),
        examples=[
            {
                "rooms": "2",
                "price_max": 15000000,
                "metro": ["Аэропорт Внуково"],
                "finish": ["готовая"],
                "sort": "price_asc",
            }
        ],
    )
    result_count: int | None = Field(
        default=None,
        description=(
            "Число объектов в выдаче по данным backend-API pik.ru; "
            "None — если валидация выдачи не выполнялась/не удалась."
        ),
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Нераспознанные куски текста и ослабленные критерии.",
    )
    ai_used: bool = Field(
        default=False,
        description=(
            "Реально ли ИИ повлиял на результат (успешный вызов модели или "
            "детерминированное сужение по ориентиру). False при отключённом ИИ, "
            "неудачной попытке (см. ai_failed) и при простых запросах без "
            "гео/POI-контекста, для которых обогащать нечего."
        ),
    )
    ai_failed: bool = Field(
        default=False,
        description=(
            "Была попытка обратиться к ИИ, но она провалилась (сеть/валидация/"
            "провайдер). Не путать с ai_used=False — оно означает и «не звали», "
            "и «упало»; это поле различает эти два случая, не теряя факт ошибки."
        ),
    )
    ai_cache_hit: bool = Field(
        default=False,
        description="Взят ли результат ИИ-обогащения из локального семантического кэша.",
    )
    ai_explanation: str | None = Field(
        default=None, description="Объяснение решения ИИ (почему выбраны именно эти ЖК)."
    )
