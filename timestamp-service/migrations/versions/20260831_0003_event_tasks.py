"""Add exact-generation Cloud Tasks dispatch intents.

Revision ID: 20260831_0003
Revises: 20260827_0002
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260831_0003"
down_revision: str | None = "20260827_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column(
        "durable_jobs",
        sa.Column("generation", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.create_check_constraint("ck_jobs_generation_positive", "durable_jobs", "generation > 0")
    op.create_table(
        "task_dispatches",
        sa.Column("id", UUID, nullable=False),
        sa.Column("job_id", UUID, nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("task_name", sa.String(128), nullable=False),
        sa.Column("schedule_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("safe_error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("generation > 0", name="ck_task_dispatch_generation_positive"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_task_dispatch_attempts_nonnegative"),
        sa.CheckConstraint("state IN ('pending', 'dispatched')", name="ck_task_dispatch_state"),
        sa.CheckConstraint(
            "(state = 'pending' AND dispatched_at IS NULL) OR "
            "(state = 'dispatched' AND dispatched_at IS NOT NULL)",
            name="ck_task_dispatch_completion",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["durable_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "generation", name="uq_task_dispatch_job_generation"),
        sa.UniqueConstraint("task_name"),
    )
    op.create_index("ix_task_dispatches_job_id", "task_dispatches", ["job_id"])
    op.create_index("ix_task_dispatches_schedule_at", "task_dispatches", ["schedule_at"])
    op.execute(
        """
        INSERT INTO task_dispatches (
          id, job_id, generation, task_name, schedule_at, state,
          attempt_count, dispatched_at, safe_error_code, created_at, updated_at
        )
        SELECT md5(jobs.id::text || '-task-generation-1')::uuid,
               jobs.id, 1,
               'timestamp-' || replace(jobs.id::text, '-', '') || '-g1',
               jobs.available_at, 'pending', 0, NULL, NULL, now(), now()
        FROM durable_jobs jobs
        WHERE jobs.state IN ('available', 'retry', 'leased')
        ON CONFLICT (job_id, generation) DO NOTHING
        """
    )
    op.alter_column("durable_jobs", "generation", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_task_dispatches_schedule_at", table_name="task_dispatches")
    op.drop_index("ix_task_dispatches_job_id", table_name="task_dispatches")
    op.drop_table("task_dispatches")
    op.drop_constraint("ck_jobs_generation_positive", "durable_jobs", type_="check")
    op.drop_column("durable_jobs", "generation")
