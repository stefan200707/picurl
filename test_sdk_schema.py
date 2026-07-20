import asyncio
import json
from google.antigravity import Agent, LocalAgentConfig
from pydantic import BaseModel

class MyAnswer(BaseModel):
    complexes: list[str]

async def main():
    config = LocalAgentConfig(
        system_instructions="You are an AI.",
        response_schema=MyAnswer
    )
    async with Agent(config) as agent:
        response = await agent.chat("List some complexes in Moscow.")
        text = ""
        async for token in response:
            text += token
        print("Response:", text)

if __name__ == "__main__":
    asyncio.run(main())
