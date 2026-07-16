with open("app/parsing/rules/__init__.py", "r") as f:
    c = f.read()

c = c.replace("price.min", "price.price_min")
c = c.replace("price.max", "price.price_max")

c = c.replace("area.total_min", "area.area_min")
c = c.replace("area.total_max", "area.area_max")
c = c.replace("area.kitchen_min", "area.area_kitchen_min")
c = c.replace("area.kitchen_max", "area.area_kitchen_max")

c = c.replace("time.minutes", "time.time_on_foot")
c = c.replace("time.transport", "time.time_on_transport")

c = c.replace("floor.min", "floor.floor_min")
c = c.replace("floor.max", "floor.floor_max")

with open("app/parsing/rules/__init__.py", "w") as f:
    f.write(c)

