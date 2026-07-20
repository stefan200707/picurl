from app.geo.distance import haversine, is_center


def test_haversine():
    # Москва - Питер примерно 630 км
    dist = haversine(55.7558, 37.6173, 59.9343, 30.3351)
    assert 600_000 < dist < 650_000

    # Одинаковые точки
    assert haversine(55.7558, 37.6173, 55.7558, 37.6173) == 0.0


def test_is_center():
    # Кремль
    assert is_center(55.7522200, 37.6155600) is True
    # За МКАДом (Саларьево)
    assert is_center(55.61792, 37.41610) is False
    # Рядом с ТТК, внутри 5 км
    # Павелецкая ~ 55.730, 37.636
    assert is_center(55.730, 37.636, 5000) is True
