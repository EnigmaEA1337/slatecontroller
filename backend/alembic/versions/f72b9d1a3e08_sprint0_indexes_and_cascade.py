"""sprint 0: add indexes on hot columns + CASCADE on profile_wallpapers FK

Audit-driven (sprint 0 quick wins) :
  - Indexes on `created_at` columns scanned in DESC order (history tabs).
  - Indexes on `taken_at` for the security snapshot timeline.
  - Index on `vulnerability_findings.snapshot_id` for the lookup-per-snapshot
    pattern in /api/security/findings.
  - Index on `cve_exploit_cache.fetched_at` for the TTL eviction scan.
  - Index on `device_inventory_snapshots.device_id + taken_at DESC` for
    "latest snapshot of this device" lookups.

Slugs and names are already covered by their UNIQUE constraints
(SQLAlchemy/SQLite auto-creates an index for those). No-op there.

FK CASCADE — historically the `profile_wallpapers.profile_name` was a bare
String column with no foreign key declared, so deleting a profile left the
wallpaper row orphan. Adding the FK with ON DELETE CASCADE on SQLite
requires the batch_alter_table dance.

Revision ID: f72b9d1a3e08
Revises: d4f1e85c9a23
Create Date: 2026-05-24 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f72b9d1a3e08"
down_revision: Union[str, Sequence[str], None] = "d4f1e85c9a23"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, index_name, columns). Centralized so downgrade can iterate.
INDEXES = [
    ("profiles", "ix_profiles_created_at", ["created_at"]),
    ("vpn_configs", "ix_vpn_configs_created_at", ["created_at"]),
    ("devices", "ix_devices_created_at", ["created_at"]),
    ("networks", "ix_networks_created_at", ["created_at"]),
    ("wifi_ssids", "ix_wifi_ssids_created_at", ["created_at"]),
    (
        "device_inventory_snapshots",
        "ix_inv_snapshots_device_taken",
        ["device_id", "taken_at"],
    ),
    (
        "vulnerability_findings",
        "ix_vuln_findings_snapshot",
        ["snapshot_id"],
    ),
    (
        "cve_exploit_cache",
        "ix_cve_exploit_cache_refreshed_at",
        ["last_refreshed_at"],
    ),
    (
        "cve_attack_path_cache",
        "ix_cve_attack_path_cache_fetched_at",
        ["fetched_at"],
    ),
]


def upgrade() -> None:
    # 1. Indexes — idempotent CREATE INDEX IF NOT EXISTS. A previous failed
    #    run of this migration may have created some indexes before crashing
    #    on the FK step; the IF NOT EXISTS absorbs that partial state without
    #    forcing the operator to clean up manually.
    for table, name, cols in INDEXES:
        cols_sql = ", ".join(cols)
        op.execute(f'CREATE INDEX IF NOT EXISTS "{name}" ON "{table}" ({cols_sql})')

    # 2. FK CASCADE on profile_wallpapers.profile_name. SQLite can't ALTER
    #    a column to add a constraint, so batch_alter_table copies the table.
    #    The existing unique constraint on (profile_name, kind) is preserved.
    with op.batch_alter_table("profile_wallpapers") as batch:
        batch.create_foreign_key(
            "fk_profile_wallpapers_profile_name",
            "profiles",
            ["profile_name"],
            ["name"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("profile_wallpapers") as batch:
        batch.drop_constraint("fk_profile_wallpapers_profile_name", type_="foreignkey")
    for _, name, _ in INDEXES:
        op.drop_index(name)
