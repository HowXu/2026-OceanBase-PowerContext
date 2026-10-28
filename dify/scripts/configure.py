"""Generate deployment secrets once. Existing configuration is never overwritten."""
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / ".env"
if path.exists():
    raise SystemExit(".env already exists; edit it directly to preserve your configuration.")
text = (ROOT / ".env.example").read_text(encoding="utf-8")
for name in ["GATEWAY_TOKEN", "DIFY_SECRET_KEY", "DB_PASSWORD", "REDIS_PASSWORD", "SANDBOX_API_KEY"]:
    lines = text.splitlines()
    text = "\n".join(name + "=" + secrets.token_hex(32) if line.startswith(name + "=") else line for line in lines) + "\n"
path.write_text(text, encoding="utf-8")
try:
    path.chmod(0o600)
except OSError:
    pass
print("Created .env with random local deployment secrets. No values are printed.")
