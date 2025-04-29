# sa/db/migrations/68550a046b57_initial_schema.py
# Copyright (C) 2025 Martin Bukowski

"""Initial schema."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '68550a046b57'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('event',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('start_time', sa.DateTime(), nullable=False),
        sa.Column('end_time', sa.DateTime(), nullable=False),
        sa.Column('time_zone', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('location', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('notes', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('rrule', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('calendar_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('deleted', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_event_end_time'), 'event', ['end_time'], unique=False)
    op.create_index(op.f('ix_event_id'), 'event', ['id'], unique=False)
    op.create_index(op.f('ix_event_rrule'), 'event', ['rrule'], unique=False)
    op.create_index(op.f('ix_event_start_time'), 'event', ['start_time'], unique=False)
    op.create_index(op.f('ix_event_title'), 'event', ['title'], unique=False)
    op.create_table('excludeddate',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('excluded_date', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['event_id'], ['event.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_excludeddate_event_id'), 'excludeddate', ['event_id'], unique=False)
    op.create_table('modifiedinstance',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('original_date', sa.DateTime(), nullable=False),
        sa.Column('start_time', sa.DateTime(), nullable=False),
        sa.Column('end_time', sa.DateTime(), nullable=False),
        sa.Column('time_zone', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('location', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('notes', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('calendar_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('deleted', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['event_id'], ['event.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_modifiedinstance_event_id'), 'modifiedinstance', ['event_id'], unique=False)
    op.create_table('participant',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('identifier', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['event.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_participant_event_id'), 'participant', ['event_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_participant_event_id'), table_name='participant')
    op.drop_table('participant')
    op.drop_index(op.f('ix_modifiedinstance_event_id'), table_name='modifiedinstance')
    op.drop_table('modifiedinstance')
    op.drop_index(op.f('ix_excludeddate_event_id'), table_name='excludeddate')
    op.drop_table('excludeddate')
    op.drop_index(op.f('ix_event_title'), table_name='event')
    op.drop_index(op.f('ix_event_start_time'), table_name='event')
    op.drop_index(op.f('ix_event_rrule'), table_name='event')
    op.drop_index(op.f('ix_event_id'), table_name='event')
    op.drop_index(op.f('ix_event_end_time'), table_name='event')
    op.drop_table('event')
