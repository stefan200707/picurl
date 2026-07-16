import re

with open("tests/reference/test_refresh.py", "r") as f:
    c = f.read()

c = c.replace("from app.reference.refresh import (", "from app.reference.refresh import BlockPayload,\n    (")
c = c.replace("complexes_from_blocks(BLOCKS_PAYLOAD)", "complexes_from_blocks([BlockPayload.model_validate(b) for b in BLOCKS_PAYLOAD])")
c = c.replace("counties_from_blocks(BLOCKS_PAYLOAD)", "counties_from_blocks([BlockPayload.model_validate(b) for b in BLOCKS_PAYLOAD])")
c = c.replace("metro_from_blocks(BLOCKS_PAYLOAD)", "metro_from_blocks([BlockPayload.model_validate(b) for b in BLOCKS_PAYLOAD])")
c = c.replace("districts_from_blocks(BLOCKS_PAYLOAD)", "districts_from_blocks([BlockPayload.model_validate(b) for b in BLOCKS_PAYLOAD])")

with open("tests/reference/test_refresh.py", "w") as f:
    f.write(c)
