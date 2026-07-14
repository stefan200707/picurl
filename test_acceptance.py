import asyncio
import json

from app.parsing.parser import parse
from app.pik.url_builder import build_url


async def main():
    text = (
        "Ищу квартиру для большой семьи, может двушку, а лучше трешку или даже 4-комнатную, "
        "но точно не студию, бюджет от 10 до 12 миллионов, хотя если будет с отделкой под ключ, "
        "то готов рассмотреть и за 15 млн рублей, главное чтобы кухня от 15 метров и этаж не "
        "первый и не последний, желательно поближе к метро Саларьево или Румянцево, ну и "
        "заселение сразу, без апартаментов, только квартиры."
    )
    criteria, warnings = parse(text)
    url = build_url(criteria)
    print(
        json.dumps(
            {
                "url": url,
                "criteria": criteria.to_public_dict(),
                "warnings": warnings,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
