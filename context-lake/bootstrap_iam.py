"""One-time IAM setup: seeds the four default system roles (Admin,
Recruiter, Interviewer, Candidate -- see ../auth.py's DEFAULT_ROLES),
syncs the Dynamic Page Access Management registry (see
repositories.sync_pages()'s docstring), and, if you ask for one, creates
the very first Admin account. Admin is the top-level role in this app --
there is no separate Super Admin tier.

There is deliberately NO automatic default Admin and NO hardcoded
default password anywhere in this app -- per the spec's explicit "no
default admin privileges" requirement, someone has to run this script
and supply real credentials before anyone can administer the system at
all. Needs Cosmos DB configured (.env or Key Vault) same as
migrate_from_csv.py.

Usage:
    python bootstrap_iam.py                                   # seeds roles only
    python bootstrap_iam.py --create-admin alice --email alice@example.com
        (prompts for a password -- never pass it as a CLI argument, that
        would leak it into shell history and process listings)
    python bootstrap_iam.py --create-demo-personas
        (OPT-IN -- creates one synthetic/test account per persona in
        persona_catalogue.py, Section 8 of the CDPI Product Personas doc.
        Never run automatically. Generated passwords are printed ONCE;
        write them down immediately, they are never shown again.)
"""

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import repositories  # noqa: E402
import auth  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create-admin", metavar="USERNAME", help="Also create an Admin account with this username.")
    parser.add_argument("--email", default="", help="Email for the new Admin account (used with --create-admin).")
    parser.add_argument("--display-name", default="", help="Display name for the new Admin account (defaults to the username).")
    parser.add_argument(
        "--create-demo-personas", action="store_true",
        help="OPT-IN: also create one synthetic/test demo account per persona (see persona_catalogue.py). Never run automatically.",
    )
    args = parser.parse_args()

    created_roles = repositories.ensure_default_roles()
    if created_roles:
        print(f"Seeded {len(created_roles)} default role(s): {', '.join(created_roles)}")
    else:
        print("All default roles already exist -- nothing to seed.")

    page_sync = repositories.sync_pages()
    print(
        f"Page registry sync: {len(page_sync['new'])} new, {len(page_sync['updated'])} updated, "
        f"{len(page_sync['stale'])} stale" + (" (initial policy seed applied)" if page_sync["first_run"] else "")
    )

    created_integrations = repositories.ensure_default_integration_instances()
    if created_integrations:
        print(f"Azure Service Hub: seeded {len(created_integrations)} default integration instance(s): {', '.join(created_integrations)}")
    else:
        print("Azure Service Hub: default integration instances already exist -- nothing to seed.")

    seeded_prompts = repositories.ensure_default_rag_prompts()
    if seeded_prompts:
        print(f"RAG Configuration: seeded version 1 (active) of {len(seeded_prompts)} prompt(s): {', '.join(seeded_prompts)}")
    else:
        print("RAG Configuration: prompt versions already exist -- nothing to seed.")

    seeded_templates = repositories.ensure_default_comms_templates()
    if seeded_templates:
        print(f"Communications Configuration: seeded version 1 (active) of {len(seeded_templates)} template(s): {', '.join(seeded_templates)}")
    else:
        print("Communications Configuration: template versions already exist -- nothing to seed.")

    if args.create_demo_personas:
        created_personas = repositories.ensure_demo_personas()
        if created_personas:
            print(f"\nDemo personas: created {len(created_personas)} synthetic/test account(s) -- NOT real users, "
                  f"write these passwords down now, they will never be shown again:")
            for p in created_personas:
                print(f"  [{p['persona_id']}] username={p['username']!r} role={p['role_key']!r} password={p['password']!r}")
        else:
            print("Demo personas: all 12 demo accounts already exist -- nothing to seed.")

    if not args.create_admin:
        return

    username = args.create_admin.strip().lower()
    if repositories.get_user_by_username(username) is not None:
        print(f"ERROR: username '{username}' already exists. Refusing to create a duplicate.", file=sys.stderr)
        sys.exit(1)

    password = getpass.getpass(f"Password for new Admin '{username}': ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("ERROR: passwords did not match.", file=sys.stderr)
        sys.exit(1)
    if len(password) < 8:
        print("ERROR: password must be at least 8 characters.", file=sys.stderr)
        sys.exit(1)

    password_hash = auth.hash_password(password)
    user = repositories.create_user(
        username=username,
        password_hash=password_hash,
        display_name=args.display_name.strip() or username,
        email=args.email.strip(),
        role_keys=["admin"],
        created_by="bootstrap_iam.py",
    )
    print(f"Created Admin '{username}' (id={user['id']}). Log in at POST /auth/login.")


if __name__ == "__main__":
    main()
