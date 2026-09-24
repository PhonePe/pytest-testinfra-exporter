"""Create the initial reporter schema.

Revision ID: 0001
Revises:
"""

from alembic import op

from pytest_testinfra_exporter.database import metadata


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    metadata.create_all(bind=op.get_bind(), checkfirst=False)


def downgrade():
    metadata.drop_all(bind=op.get_bind(), checkfirst=False)
