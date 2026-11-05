"""Retired probe for host-owned automatic image-provider selection.

The host no longer selects an image provider by priority or injects a native
model. Shared routing owns that choice. Keep this historical entry point
fail-closed: it must not read credentials or quietly run a different paid test.
The old implementation and its evidence remain in Git history.
"""
import asyncio
import sys


async def main(folder):
    raise RuntimeError(
        'Automatic-provider discovery was replaced by shared image routing. '
        'This historical probe sends no request. Qualify the selected routed '
        'graph with a separately authorized provider test.'
    )


if __name__ == '__main__':
    try:
        asyncio.run(main(None))
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
