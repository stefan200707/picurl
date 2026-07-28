-- Признак неудачной попытки обращения к ИИ (правка Г5).
--
-- До этой колонки провал free-text-ветки был в ai_call_log неотличим от «ИИ не
-- звали»: обе ситуации давали ai_called=false (cooldown circuit breaker'а
-- поднимается ДО сетевого вызова, поэтому «попытка была» там не фиксировалась),
-- criteria_changed_by_ai=false. Ровно та же слепота была и в ответе API
-- (ai_used=false, ai_failed=false на оба случая) — см. docs/history.md.
--
-- Семантика совпадает с полем ai_failed в BuildUrlResponse: попытка была и не
-- удалась (исключение вызова или предохранитель в cooldown). Отсутствие
-- учётных данных ИИ провалом НЕ считается — это «выключен» (инвариант 9),
-- ai_called=false, ai_failed=false.

ALTER TABLE ai_call_log
    ADD COLUMN IF NOT EXISTS ai_failed BOOLEAN NOT NULL DEFAULT false;
