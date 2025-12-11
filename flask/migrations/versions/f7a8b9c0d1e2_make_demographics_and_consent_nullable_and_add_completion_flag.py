"""make_demographics_and_consent_fields_nullable_and_add_completion_flag

Revision ID: f7a8b9c0d1e2
Revises: d5e6f7a8b9c0
Create Date: 2025-01-17 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Make demographics and consent fields nullable
    op.alter_column('users', 'emirati_citizenship',
                    existing_type=sa.Boolean(),
                    nullable=True)
    op.alter_column('users', 'age_group',
                    existing_type=sa.Integer(),
                    nullable=True)
    op.alter_column('users', 'consent_read_form',
                    existing_type=sa.Boolean(),
                    nullable=True)
    op.alter_column('users', 'consent_required',
                    existing_type=sa.Boolean(),
                    nullable=True)
    op.alter_column('users', 'consent_optional',
                    existing_type=sa.Boolean(),
                    nullable=True)
    op.alter_column('users', 'consent_optional_alternative',
                    existing_type=sa.Boolean(),
                    nullable=True)
    
    # Add new demographics_and_consent_completed field
    op.add_column('users', sa.Column('demographics_and_consent_completed', sa.Boolean(), nullable=False, server_default=sa.text('false')))


def downgrade() -> None:
    # Remove the new field
    op.drop_column('users', 'demographics_and_consent_completed')
    
    # Revert fields back to non-nullable
    op.alter_column('users', 'consent_optional_alternative',
                    existing_type=sa.Boolean(),
                    nullable=False)
    op.alter_column('users', 'consent_optional',
                    existing_type=sa.Boolean(),
                    nullable=False)
    op.alter_column('users', 'consent_required',
                    existing_type=sa.Boolean(),
                    nullable=False)
    op.alter_column('users', 'consent_read_form',
                    existing_type=sa.Boolean(),
                    nullable=False)
    op.alter_column('users', 'age_group',
                    existing_type=sa.Integer(),
                    nullable=False)
    op.alter_column('users', 'emirati_citizenship',
                    existing_type=sa.Boolean(),
                    nullable=False)

