"""Generate deployment credentials locally without printing the token or overwriting files."""
import argparse
import os
from pathlib import Path
import re
import secrets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hostname', required=True, help='Public DNS name pointing to your server')
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / '.env.production')
    args = parser.parse_args()
    hostname = args.hostname.lower()
    label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
    if len(hostname) > 253 or not re.fullmatch(label + r'(?:\.' + label + r')+', hostname):
        parser.error('Use a DNS hostname without a scheme, port, whitespace or path.')
    if all(part.isdigit() for part in hostname.split('.')):
        parser.error('Use a DNS hostname rather than an IP address for this HTTPS configuration.')
    token = secrets.token_urlsafe(32)
    content = f'PC_PUBLIC_HOSTNAME={hostname}\nPOWERCONTEXT_SERVER_AUTH_TOKEN={token}\n'
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as file:
        file.write(content)
    print(f'Created {args.output.resolve()}; token was not printed. Public URL: https://{hostname}')


if __name__ == '__main__':
    main()
