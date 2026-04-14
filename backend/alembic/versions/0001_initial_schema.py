"""Initial schema with PostGIS tables

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import geoalchemy2

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable PostGIS extension
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    # channels table
    op.create_table(
        "channels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("source_type", sa.String(100), nullable=False),
        sa.Column("endpoint", sa.String(500), nullable=True),
        sa.Column("auth_config", postgresql.JSONB, nullable=True),
        sa.Column("refresh_schedule", sa.String(100), nullable=True),
        sa.Column("spatial_coverage", postgresql.JSONB, nullable=True),
        sa.Column("data_type", sa.String(100), nullable=True),
        sa.Column("normalization_profile", sa.String(100), nullable=True),
        sa.Column("last_synced_at", sa.DateTime, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.UniqueConstraint("name", name="uq_channels_name"),
    )

    # features table
    op.create_table(
        "features",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_channel", sa.String(100), nullable=False),
        sa.Column("source_record_id", sa.String(255), nullable=True),
        sa.Column("raw_record_ref", sa.String(500), nullable=True),
        sa.Column("feature_type", sa.String(100), nullable=True),
        sa.Column("name", sa.String(500), nullable=True),
        sa.Column("deposit_type", sa.String(200), nullable=True),
        sa.Column("status", sa.String(100), nullable=True),
        sa.Column("geologic_unit", sa.String(200), nullable=True),
        sa.Column("rock_type", sa.String(200), nullable=True),
        sa.Column("commodity_primary", sa.String(100), nullable=True),
        sa.Column("commodity_secondary", postgresql.ARRAY(sa.String), nullable=True),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry(geometry_type="GEOMETRY", srid=4326),
            nullable=True,
        ),
        sa.Column("geochemical_values", postgresql.JSONB, nullable=True),
        sa.Column("source_quality", sa.Float, nullable=True),
        sa.Column("ingested_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    # analysis_jobs table
    op.create_table(
        "analysis_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="queued"),
        sa.Column("aoi_geojson", postgresql.JSONB, nullable=False),
        sa.Column("target_mineral", sa.String(100), nullable=False),
        sa.Column("config", postgresql.JSONB, nullable=True),
        sa.Column("agent_results", postgresql.JSONB, nullable=True),
        sa.Column("final_scores", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
    )

    # Indexes
    op.create_index("ix_features_source_channel", "features", ["source_channel"])
    op.create_index("ix_features_feature_type", "features", ["feature_type"])
    op.create_index("ix_features_commodity_primary", "features", ["commodity_primary"])
    op.create_index("ix_analysis_jobs_status", "analysis_jobs", ["status"])
    op.create_index("ix_analysis_jobs_target_mineral", "analysis_jobs", ["target_mineral"])

    # PostGIS spatial index
    op.execute(
        "CREATE INDEX ix_features_geometry ON features USING GIST (geometry)"
    )


def downgrade() -> None:
    op.drop_table("analysis_jobs")
    op.drop_table("features")
    op.drop_table("channels")
