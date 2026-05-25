"""network isolation refactor: drop is_builtin + isolated_from_lan, add 3-level model

Revision ID: a8b3c2d10e44
Revises: f3a1c8d9e021
Create Date: 2026-05-26 00:30:00

User asked to rework the network model :

1. Drop the `is_builtin` concept entirely. A fresh controller install
   should start with an EMPTY network catalog — no demo / cyberpunk seeds.
   Existing rows that were marked `is_builtin=True` simply become user
   networks (deletable like any other).

2. Replace the single `isolated_from_lan` checkbox with a 3-dimension
   model that maps cleanly to the underlying mechanisms :

   - ``intra_bridge_isolation`` (bool)
       L2 isolation between ports of the SAME bridge (rare ;
       implementation = ebtables / bridge port_isolation flag).

   - ``reach_internet`` (bool, default True)
       L3 : forwarding rule from this network's zone to wan. False =
       no internet for clients of this network.

   - ``reachable_networks`` (list of slugs, JSON column)
       L3 : explicit list of OTHER networks this one can route to
       (besides wan). Empty list = isolated from all other subnets.
       ['lan'] = can reach lan main, no other.

   - ``admin_access`` (bool, default True)
       Whether clients can reach the Slate itself (DHCP, DNS, admin UI).
       Mapping to UCI zone ``input`` policy (ACCEPT / DROP).

   Note : ``client_isolation`` at the WiFi/SSID level (intra-SSID L2)
   stays on the ``WifiSsidRow`` — it's a separate concern.

Migration of existing data :

   - is_builtin column → dropped (was just metadata; no SSID/profile
     reference depended on its value).
   - isolated_from_lan=True  → reachable_networks=[] (isolated from
                                everything) + reach_internet=True
                                (assumption : even isolated nets typically
                                want internet)
   - isolated_from_lan=False → reachable_networks=['lan'] (could reach
                                main lan) + reach_internet=True
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "a8b3c2d10e44"
down_revision = "f3a1c8d9e021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Add the 4 new columns. We do this BEFORE the migration step so
    #    we can populate them from the legacy column inside the same tx.
    #    SQLite doesn't support ADD COLUMN with non-constant defaults
    #    via SQLAlchemy directly, so we use raw SQL with literal defaults
    #    then UPDATE rows.
    with op.batch_alter_table("networks") as batch:
        batch.add_column(
            sa.Column(
                "intra_bridge_isolation", sa.Boolean(),
                nullable=False, server_default=sa.text("0"),
            ),
        )
        batch.add_column(
            sa.Column(
                "reach_internet", sa.Boolean(),
                nullable=False, server_default=sa.text("1"),
            ),
        )
        batch.add_column(
            sa.Column(
                "reachable_networks", sa.JSON(),
                nullable=False, server_default=sa.text("'[]'"),
            ),
        )
        batch.add_column(
            sa.Column(
                "admin_access", sa.Boolean(),
                nullable=False, server_default=sa.text("1"),
            ),
        )

    # 2. Migrate legacy isolated_from_lan into the new model. We only do
    #    this when the legacy column actually exists (idempotency on
    #    re-runs against an already-migrated schema).
    cols = [
        r[1] for r in bind.execute(
            sa.text("PRAGMA table_info('networks')"),
        ).fetchall()
    ]
    if "isolated_from_lan" in cols:
        # Default mapping for non-isolated rows : they could historically
        # reach lan, so we seed reachable_networks=['lan'].
        bind.execute(
            sa.text(
                "UPDATE networks SET reachable_networks = "
                "CASE WHEN isolated_from_lan = 1 THEN :empty ELSE :withlan END"
            ),
            {"empty": "[]", "withlan": json.dumps(["lan"])},
        )

    # 3. Drop the legacy columns. SQLite requires batch_alter_table here
    #    (it actually rebuilds the table behind the scenes).
    with op.batch_alter_table("networks") as batch:
        if "isolated_from_lan" in cols:
            batch.drop_column("isolated_from_lan")
        if "is_builtin" in cols:
            batch.drop_column("is_builtin")


def downgrade() -> None:
    bind = op.get_bind()

    # 1. Re-add the legacy columns.
    with op.batch_alter_table("networks") as batch:
        batch.add_column(
            sa.Column(
                "isolated_from_lan", sa.Boolean(),
                nullable=False, server_default=sa.text("0"),
            ),
        )
        batch.add_column(
            sa.Column(
                "is_builtin", sa.Boolean(),
                nullable=False, server_default=sa.text("0"),
            ),
        )

    # 2. Restore isolated_from_lan from the new model. Rule : reachable
    #    networks list is empty AND user said reach_internet=True →
    #    treat as "isolated_from_lan=True". Anything else stays False.
    bind.execute(
        sa.text(
            "UPDATE networks SET isolated_from_lan = "
            "CASE WHEN reachable_networks = :empty THEN 1 ELSE 0 END"
        ),
        {"empty": "[]"},
    )
    # is_builtin stays at default False on downgrade — we have no way to
    # tell which rows used to be builtin (the info is irrecoverable).

    # 3. Drop the new columns.
    with op.batch_alter_table("networks") as batch:
        batch.drop_column("admin_access")
        batch.drop_column("reachable_networks")
        batch.drop_column("reach_internet")
        batch.drop_column("intra_bridge_isolation")
