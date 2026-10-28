"""Read a real save from PowerContext. Run inside gateway with its configured env."""
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from station7.memory import Memory


async def main():
    memory = Memory()
    try:
        loaded = await memory.load(sys.argv[1])
        print(json.dumps(loaded, ensure_ascii=False, indent=2))
    finally:
        await memory.client.aclose()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/inspect_save.py <player_id>")
    asyncio.run(main())
