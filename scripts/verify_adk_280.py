"""ADK 2.8.0 re-verification probe (story A.1 prerequisite).

Checks the 4 flagged drift items from docs/research/phase-1-findings.md §2.
Run: uv run python scripts/verify_adk_280.py
"""
import inspect
import importlib

print("google-adk 2.8.0 re-verification")
print("=" * 60)

# 1. InMemoryRunner constructor + auto_create_session default
from google.adk.runners import InMemoryRunner

sig = inspect.signature(InMemoryRunner.__init__)
print("1. InMemoryRunner.__init__ params:", [p for p in sig.parameters])
runner = InMemoryRunner.__new__(InMemoryRunner)
try:
    from google.adk.agents.llm_agent import LlmAgent
    a = LlmAgent(model="gemini-2.5-flash", name="probe_agent")
    r = InMemoryRunner(agent=a, app_name="probe")
    print("   auto_create_session default:", getattr(r, "auto_create_session", "<attr missing>"))
except Exception as e:
    print("   runner probe failed:", e)

# 2. LlmAgent callback params (fault injection seams)
sig = inspect.signature(LlmAgent.__init__)
cb = [p for p in sig.parameters if "callback" in p]
print("2. LlmAgent callback params:", cb)

# 3. FastAPI / serving modules for remote agents
for mod in ["google.adk.serving", "google.adk.fastapi", "google.adk.web", "google.adk.cli"]:
    try:
        m = importlib.import_module(mod)
        names = [n for n in dir(m) if not n.startswith("_")][:12]
        print(f"3. {mod} EXISTS: {names}")
    except Exception as e:
        print(f"3. {mod}: {type(e).__name__}")

# 4. Session service / state APIs
try:
    from google.adk.sessions import InMemorySessionService
    print("4. InMemorySessionService OK; methods:", [n for n in dir(InMemorySessionService) if not n.startswith("_")][:8])
except Exception as e:
    print("4. session service:", e)
