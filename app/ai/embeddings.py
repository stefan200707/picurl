import functools

#: Многоязычная модель эмбеддингов. Запросы к сервису — на русском, поэтому
#: англоцентричная all-MiniLM-L6-v2 давала слабые эмбеддинги и почти нулевой
#: hit-rate семантического кэша. У этой модели та же размерность 384 — схема БД
#: (halfvec(384)) не меняется.
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


@functools.cache
def get_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBEDDING_MODEL)


def embed(text: str) -> list[float]:
    """Создаёт эмбеддинг для текста локальной многоязычной моделью (размерность 384)."""
    model = get_model()
    # возвращаем список float
    return model.encode(text).tolist()
