"""profiles.last_synced_at — track when the profile.json on the Slate was last pushed

Revision ID: s9i6d81k2lh0
Revises: r8h5c70j1kg9
Create Date: 2026-06-09 19:00:00

Adds a nullable timestamp on `profiles` that records when the controller
last pushed this profile's resolved JSON to the Slate's
``/etc/slate-controller/profiles/<name>.json``.

Why : the UI needs to know "your local edit is newer than what's
deployed on the device". The button cycle / LCD will rerun the agent
with that stale JSON otherwise (cf. 2026-06-09 incident with the hotel
profile : tron/redqueen/shadownet flags inverted because the JSON on
the Slate was from 2026-06-02 while the DB had been edited later).

Empty (NULL) = never synced. Comparison is done against `updated_at` :
``out_of_sync = updated_at > last_synced_at``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "s9i6d81k2lh0"
down_revision = "r8h5c70j1kg9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("profiles") as batch:
        batch.add_column(
            sa.Column(
                "last_synced_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("profiles") as batch:
        batch.drop_column("last_synced_at")
