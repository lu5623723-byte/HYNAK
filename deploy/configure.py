"""Generate a private production .env on the server; never overwrite secrets."""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import secrets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('domain', help='Public DNS hostname or public IPv4, without scheme or path')
    args = parser.parse_args()
    domain = args.domain.lower().rstrip('.')
    try:
        address = ipaddress.ip_address(domain)
    except ValueError:
        address = None
    if address is not None and (address.version != 4 or not address.is_global):
        parser.error('IP mode requires a public IPv4 address.')
    labels = domain.split('.')
    if address is None and (len(domain) > 253 or len(labels) < 2 or
            any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels) or
            domain.endswith(('.localhost', '.local', '.example', '.invalid', '.test')) or
            domain in ('example.com', 'example.net', 'example.org') or
            domain.endswith(('.example.com', '.example.net', '.example.org'))):
        parser.error('A real DNS hostname or public IPv4 is required; omit https://, ports and paths.')
    target = Path(__file__).resolve().parent / '.env'
    body = (f'DOMAIN={domain}\nDB_PASSWORD={secrets.token_urlsafe(36)}\n'
            f'SETUP_TOKEN={secrets.token_urlsafe(36)}\n'
            f'CADDY_CONFIG={"./Caddyfile.ip" if address else "./Caddyfile"}\n')
    try:
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        parser.exit(1, 'Existing deploy/.env preserved. Inspect it privately before updating.\n')
    with os.fdopen(fd, 'w') as stream:
        stream.write(body)
    print('Created deploy/.env with private permissions. Secrets were not printed.')
    print('Use SETUP_TOKEN from this file privately for initial administrator setup.')


if __name__ == '__main__':
    main()
