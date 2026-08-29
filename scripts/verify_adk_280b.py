"""Probe 2: where did LlmAgent callbacks go in ADK 2.8.0?"""
import inspect
from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents.base_agent import BaseAgent

# Class-level attributes containing 'callback'
print("LlmAgent class attrs w/ callback:")
for n in dir(LlmAgent):
    if "callback" in n.lower():
        print("  ", n)
print("BaseAgent class attrs w/ callback:")
for n in dir(BaseAgent):
    if "callback" in n.lower():
        print("  ", n)

# model_fields if pydantic
try:
    mf = getattr(LlmAgent, "model_fields", None)
    if mf:
        cbs = {k: str(v.annotation) for k, v in mf.items() if "callback" in k.lower()}
        print("LlmAgent model_fields w/ callback:", cbs)
except Exception as e:
    print("model_fields err:", e)

# BaseAgent signature
print("BaseAgent.__init__ params:", [p for p in inspect.signature(BaseAgent.__init__).parameters][:14])

# Check for a config object pattern
import google.adk.agents as agents_mod
print("agents module members:", [n for n in dir(agents_mod) if not n.startswith("_")][:25])

# Tool-level callbacks?
from google.adk.tools import ToolContext, FunctionTool
print("FunctionTool.__init__ params:", [p for p in inspect.signature(FunctionTool.__init__).parameters])
