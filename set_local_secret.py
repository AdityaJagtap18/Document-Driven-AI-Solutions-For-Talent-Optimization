"""Set a secret's value in the real local .env file, for local
development when the full Azure Functions backend (and the Secrets
Management admin console's real write path, PUT /admin/secrets/{key})
isn't running.

Uses getpass so the value is never echoed to the terminal, logged, or
passed as a CLI argument (which would leak into shell history/process
listings) -- same discipline as bootstrap_iam.py's --create-admin
password prompt. Writes via secrets_loader.set_env_secret(), the exact
same function the real backend's Secrets Management write path calls --
this script is a local stand-in for that path, not a separate mechanism.

Usage:
    python set_local_secret.py BLOB_CONNECTION_STRING
"""

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import secrets_loader  # noqa: E402
import secrets_registry  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("key", help=f"One of: {', '.join(secrets_registry.SECRET_KEYS)}")
    args = parser.parse_args()

    if args.key not in secrets_registry.SECRET_KEYS:
        print(f"ERROR: '{args.key}' is not a recognized secret key.", file=sys.stderr)
        print(f"Recognized keys: {', '.join(secrets_registry.SECRET_KEYS)}", file=sys.stderr)
        sys.exit(1)

    value = getpass.getpass(f"Value for {args.key} (input hidden, not echoed): ").strip()
    if not value:
        print("ERROR: empty value -- nothing written.", file=sys.stderr)
        sys.exit(1)

    secrets_loader.set_env_secret(args.key, value)
    print(f"Wrote {args.key} to .env ({len(value)} characters). Value not displayed.")


if __name__ == "__main__":
    main()
