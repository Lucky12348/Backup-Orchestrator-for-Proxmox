"""`python -m app.agent_update_cli`: run at the end of the LXC `update`
(scripts/install/pbo-update.sh) to bring the agents to the build this app
ships, when "Update agents automatically" is on. Never fails the app update:
it prints what happened to each agent and exits 0.
"""

from __future__ import annotations

import sys


def main() -> int:
    from app.core.config import get_settings
    from app.db.session import SessionLocal
    from app.services import agent_updates
    from app.services.settings_service import activate_settings_store

    activate_settings_store(SessionLocal)
    if not get_settings().auto_update_agents:
        print("Agents: automatic update is off (Settings), nothing done.")
        return 0
    with SessionLocal() as db:
        agents = agent_updates.active_agents(db)
        if not agents:
            print("Agents: no registered agent.")
            return 0
        rows = agent_updates.update_all(db, trigger="update_command", wait=True)
        updated = {row.agent_id: row for row in rows}
        for agent in agents:
            db.refresh(agent)
            row = updated.get(agent.id)
            if row is None:
                state = agent_updates.agent_state(db, agent, agent_updates.shipped_agent())
                print(f"Agent {agent.role} ({agent.hostname}): {state}.")
                continue
            db.refresh(row)
            print(f"Agent {agent.role} ({agent.hostname}): {row.status}. {row.message or ''}".rstrip())
            if row.status == "too_old":
                print("  Run the agent install command once more on that host (Integrations > Agent installation).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
