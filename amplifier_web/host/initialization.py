"""Compose native initialization hooks; session lifecycle stays with its caller."""


def initialization_hooks(*, isolation, decision_accounts, routing, retain):
    if not isolation and not decision_accounts.records:
        return {"before_initialize": routing} if routing else {}

    async def before_initialize(constructing):
        # create_session can fail after constructing a session. Give the caller
        # cleanup ownership before any of its initialization policies can fail.
        retain(constructing)
        if isolation:
            await isolation.before_initialize(constructing)
        if decision_accounts.records:
            await decision_accounts.before_initialize(constructing)
        if routing:
            await routing(constructing)

    return {"before_initialize": before_initialize}
