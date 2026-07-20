import asyncio
import inspect
from google.antigravity import Agent, LocalAgentConfig, CapabilitiesConfig

print(inspect.signature(LocalAgentConfig))
print(inspect.signature(Agent.chat))
