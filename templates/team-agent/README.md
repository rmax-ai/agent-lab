# Team Agent template

Starter scaffold for a team-owned domain agent (SPEC §5, §28). Reference a
working exemplar at `agents/device/` and its knowledge corpus at
`knowledge/devices/`.

## What's here

- `agent.py` — `build_team_agent()` factory (id, goal, instructions, knowledge, tools, model).
- `instructions.md` — the agent's system instructions.
- `knowledge/` — the team's Markdown knowledge corpus (frontmatter + body).
- `tools/example.py` — a sample ADK function tool showing the MockWorld HTTP pattern.

## Use

1. Copy this directory for your team.
2. Edit `instructions.md` with your domain workflow and policy pointers.
3. Add `*.md` documents to `knowledge/`.
4. Replace `tools/example.py` with your domain tools and wire them into `agent.py`.
5. Run against the lab with:

```bash
export AGENTLAB_AGENT_ID=my-agent
export MOCKWORLD_URL=http://localhost:8000
export AGENTLAB_MODEL=gemini-2.5-flash
```

The Agent Lab CLI (`agent-lab dev`), which runs a local agent against the lab,
arrives with story A.14.
