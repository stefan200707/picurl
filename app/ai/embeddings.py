import functools


@functools.cache
def get_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer("all-MiniLM-L6-v2")

def embed(text: str) -> list[float]:
    """
    Создаёт эмбеддинг для текста с использованием локальной модели.
    Используется all-MiniLM-L6-v2, размерность 384.
    """
    model = get_model()
    # возвращаем список float
    return model.encode(text).tolist()
