"""Compose native initialization hooks; session lifecycle stays with its caller."""


def initialization_hooks(*, isolation, decision_accounts, routing, retain, ephemeral_servers=None):
    if not isolation and not decision_accounts.records and ephemeral_servers is None:
        return {"before_initialize": routing} if routing else {}

    async def before_initialize(constructing):
        # create_session can fail after constructing a session. Give the caller
        # cleanup ownership before any of its initialization policies can fail.
        retain(constructing)
        if ephemeral_servers is not None:
            constructing.coordinator.register_capability("mcp.ephemeral_servers", ephemeral_servers)
        if isolation:
            await isolation.before_initialize(constructing)
        if decision_accounts.records:
            await decision_accounts.before_initialize(constructing)
        if routing:
            await routing(constructing)

    return {"before_initialize": before_initialize}
