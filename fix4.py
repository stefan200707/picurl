with open("app/parsing/rules/__init__.py", "r") as f:
    c = f.read()

c = c.replace("floor.not_first", "floor.not_first_floor")
c = c.replace("floor.last", "floor.last_floor")

with open("app/parsing/rules/__init__.py", "w") as f:
    f.write(c)

with open("tests/reference/test_refresh.py", "r") as f:
    c = f.read()
c = c.replace("b.get(\"id\")", "b.id")
with open("tests/reference/test_refresh.py", "w") as f:
    f.write(c)

