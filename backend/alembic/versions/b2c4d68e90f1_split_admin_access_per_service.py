"""split admin_access into services_access + admin_ui_access + ssh_access

Revision ID: b2c4d68e90f1
Revises: a8b3c2d10e44
Create Date: 2026-05-26 09:00:00

Rationale :

The previous ``admin_access`` flag conflated three very different exposure
profiles into a single checkbox :

  - essential services (DHCP / DNS local / ICMP) — almost always wanted
    by clients, even on guest networks ;
  - admin web UI (LuCI + GL.iNet UI on TCP 80 & 443) — should be granted
    explicitly to trusted networks only ;
  - SSH / dropbear (TCP 22) — never granted implicitly, ops-only.

Lumping them cost us a misconfig where the guest network had LuCI
exposed for a week. Splitting them per service makes the UCI firewall
output (per-port ``config rule`` sections) much more honest, and lets
the UI render the three checkboxes with distinct defaults.

Migration of existing rows :

  - services_access  = admin_access  (preserve legacy "can talk to Slate")
  - admin_ui_access  = admin_access  (preserve LuCI access for everyone
                                       who had it ; user can tighten later)
  - ssh_access       = 0             (intentionally reset — SSH was never
                                       a deliberate grant under the old
                                       single-flag model, and leaving it
                                       on by default would be unsafe)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "b2c4d68e90f1"
down_revision = "a8b3c2d10e44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Add the 3 new columns with sane server defaults so the ADD COLUMN
    #    against existing SQLite rows doesn't blow up. We migrate the
    #    legacy `admin_access` value over in step 2.
    with op.batch_alter_table("networks") as batch:
        batch.add_column(
            sa.Column(
                "services_access", sa.Boolean(),
                nullable=False, server_default=sa.text("1"),
            ),
        )
        batch.add_column(
            sa.Column(
                "admin_ui_access", sa.Boolean(),
                nullable=False, server_default=sa.text("0"),
            ),
        )
        batch.add_column(
            sa.Column(
                "ssh_access", sa.Boolean(),
                nullable=False, server_default=sa.text("0"),
            ),
        )

    # 2. Carry over the legacy `admin_access` value when the column is
    #    still present (idempotent on already-migrated schemas).
    cols = [
        r[1] for r in bind.execute(
            sa.text("PRAGMA table_info('networks')"),
        ).fetchall()
    ]
    if "admin_access" in cols:
        # services_access AND admin_ui_access inherit the old grant ;
        # ssh_access stays at its restrictive default (False). See the
        # module docstring for why.
        bind.execute(
            sa.text(
                "UPDATE networks SET "
                "services_access = admin_access, "
                "admin_ui_access = admin_access"
            ),
        )

    # 3. Drop the legacy column. batch_alter_table because SQLite
    #    rebuilds the table.
    if "admin_access" in cols:
        with op.batch_alter_table("networks") as batch:
            batch.drop_column("admin_access")


def downgrade() -> None:
    bind = op.get_bind()

    # 1. Re-add the legacy column.
    with op.batch_alter_table("networks") as batch:
        batch.add_column(
            sa.Column(
                "admin_access", sa.Boolean(),
                nullable=False, server_default=sa.text("1"),
            ),
        )

    # 2. Collapse the three flags back into the single flag. Rule :
    #    admin_access is True iff *any* of the three was True. This is
    #    the safest reconstruction — a downgrade should never reduce
    #    a network's effective grants.
    bind.execute(
        sa.text(
            "UPDATE networks SET admin_access = "
            "CASE WHEN services_access = 1 OR admin_ui_access = 1 "
            "          OR ssh_access = 1 THEN 1 ELSE 0 END"
        ),
    )

    # 3. Drop the new columns.
    with op.batch_alter_table("networks") as batch:
        batch.drop_column("ssh_access")
        batch.drop_column("admin_ui_access")
        batch.drop_column("services_access")
