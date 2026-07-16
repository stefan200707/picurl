with open("tests/reference/test_refresh.py", "r") as f:
    c = f.read()

c = c.replace("from app.reference.refresh import BlockPayload,\n    (", "from app.reference.refresh import BlockPayload, \\\n    (")
with open("tests/reference/test_refresh.py", "w") as f:
    f.write(c)
