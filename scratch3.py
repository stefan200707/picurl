import requests

text = "нужна квартира с видом на парк, западный округ, предчистовая отделка, в районе 9-16 этажей, два и более санузла, с тёплым полом, от двух комнат, до метро менее 15 минут"
# Try using fastapi directly? We can't easily start it and query it in one script synchronously unless we do it in background.
# But wait, app.main.py just calls `parse(text)`.
