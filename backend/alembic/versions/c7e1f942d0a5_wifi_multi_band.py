"""wifi multi-band: replace `band` (single) with `bands` (list) + `mlo` flag

Revision ID: c7e1f942d0a5
Revises: b2c4d68e90f1
Create Date: 2026-05-26 09:30:00

Rationale :

The previous ``band`` column was a single string (``"2GHz"`` /
``"5GHz"`` / ``"6GHz"`` / ``"MLO"``). That forced the user to choose
ONE band per SSID, even though the most common home setup wants the
same SSID broadcast on both 2.4 GHz (range / legacy devices) and 5 GHz
(throughput).

We split into two columns :

  - ``bands`` (JSON list of strings)
      Subset of ``["2", "5", "6"]``. The agent creates one
      ``wifi-iface`` per band, all sharing ssid + key + network.

  - ``mlo`` (bool)
      Wi-Fi 7 Multi-Link Operation : bundles >=2 radios under a single
      MLD so Wi-Fi 7 clients aggregate them. Mutually exclusive with the
      "N independent VAPs" path. Disabled in the UI until the agent
      handler implements the MTK MLD glue (next phase).

Migration of existing rows :
   - "2GHz" → bands=["2"],     mlo=False
   - "5GHz" → bands=["5"],     mlo=False
   - "6GHz" → bands=["6"],     mlo=False
   - "MLO"  → bands=["2","5"], mlo=True   (best-effort guess — most
                                            real MLOs paired 2.4+5;
                                            user can re-tick 6 if needed)
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "c7e1f942d0a5"
down_revision = "b2c4d68e90f1"
branch_labels = None
depends_on = None


# Old `band` value → (bands list, mlo flag).
_LEGACY_BAND_MAP = {
    "2GHz": (["2"], False),
    "5GHz": (["5"], False),
    "6GHz": (["6"], False),
    "MLO":  (["2", "5"], True),
}


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Add the new columns with sane server defaults so existing rows
    #    don't break the NOT NULL constraint.
    with op.batch_alter_table("wifi_ssids") as batch:
        batch.add_column(
            sa.Column(
                "bands", sa.JSON(),
                nullable=False, server_default=sa.text("'[]'"),
            ),
        )
        batch.add_column(
            sa.Column(
                "mlo", sa.Boolean(),
                nullable=False, server_default=sa.text("0"),
            ),
        )

    # 2. Migrate every legacy `band` value into the new columns. We do
    #    this row-by-row instead of a bulk CASE because the bands value
    #    is a JSON literal and SQLite's UPDATE…SET with json_array() is
    #    painful to write portably here.
    cols = [
        r[1] for r in bind.execute(
            sa.text("PRAGMA table_info('wifi_ssids')"),
        ).fetchall()
    ]
    if "band" in cols:
        rows = bind.execute(
            sa.text("SELECT id, band FROM wifi_ssids"),
        ).fetchall()
        for row_id, legacy_band in rows:
            bands, mlo = _LEGACY_BAND_MAP.get(
                legacy_band or "", (["5"], False),  # safe default
            )
            bind.execute(
                sa.text(
                    "UPDATE wifi_ssids "
                    "SET bands = :bands, mlo = :mlo "
                    "WHERE id = :id"
                ),
                {"bands": json.dumps(bands), "mlo": 1 if mlo else 0, "id": row_id},
            )

    # 3. Drop the legacy column.
    if "band" in cols:
        with op.batch_alter_table("wifi_ssids") as batch:
            batch.drop_column("band")


def downgrade() -> None:
    bind = op.get_bind()

    # 1. Re-add the legacy column.
    with op.batch_alter_table("wifi_ssids") as batch:
        batch.add_column(
            sa.Column(
                "band", sa.String(length=8),
                nullable=False, server_default=sa.text("'5GHz'"),
            ),
        )

    # 2. Collapse bands/mlo back to a single value. Rule :
    #    - mlo=True               → "MLO"
    #    - one band  ["2"|"5"|"6"]→ "<X>GHz"
    #    - two+ bands             → "MLO" (lossy, but no single legacy
    #                                       value represented dual-band
    #                                       without MLO)
    rows = bind.execute(
        sa.text("SELECT id, bands, mlo FROM wifi_ssids"),
    ).fetchall()
    for row_id, bands_json, mlo in rows:
        try:
            bands = json.loads(bands_json) if bands_json else []
        except (TypeError, ValueError):
            bands = []
        if mlo or len(bands) > 1:
            legacy = "MLO"
        elif len(bands) == 1:
            legacy = f"{bands[0]}GHz"
        else:
            legacy = "5GHz"  # safe default
        bind.execute(
            sa.text("UPDATE wifi_ssids SET band = :band WHERE id = :id"),
            {"band": legacy, "id": row_id},
        )

    # 3. Drop the new columns.
    with op.batch_alter_table("wifi_ssids") as batch:
        batch.drop_column("mlo")
        batch.drop_column("bands")
