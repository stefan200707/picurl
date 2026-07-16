from app.parsing.schema import Criteria, Rooms, Finish

c = Criteria(rooms=[Rooms.ONE, Rooms.TWO], price_max=1500000)
print(c.model_dump(exclude_none=True, exclude_unset=True))
