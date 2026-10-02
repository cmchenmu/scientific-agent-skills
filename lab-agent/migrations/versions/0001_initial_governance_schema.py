"""Create the governance and audit schema.

Revision ID: 0001_initial_governance_schema
Revises:
Create Date: 2026-10-01
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial_governance_schema"
down_revision = None
branch_labels = None
depends_on = None


UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("external_subject", sa.Text(), nullable=False, unique=True),
        sa.Column("organization_id", UUID, nullable=False, index=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "roles",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
    )
    op.create_table(
        "projects",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID, nullable=False, index=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "organization_id", "name", name="uq_projects_organization_name"
        ),
    )
    op.create_table(
        "user_roles",
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            UUID,
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "user_id", "role_id", "project_id", name="uq_user_roles_assignment"
        ),
    )
    op.create_table(
        "documents",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False, unique=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "project_id", "type", "title", "version", name="uq_documents_version"
        ),
    )
    op.create_table(
        "document_acl",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "document_id",
            UUID,
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("principal_type", sa.Text(), nullable=False),
        sa.Column("principal_id", UUID, nullable=False),
        sa.Column("permission", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "principal_type IN ('user', 'role')", name="ck_document_acl_principal_type"
        ),
        sa.CheckConstraint(
            "permission IN ('read', 'write', 'admin')",
            name="ck_document_acl_permission",
        ),
        sa.UniqueConstraint(
            "document_id",
            "principal_type",
            "principal_id",
            "permission",
            name="uq_document_acl_grant",
        ),
    )
    op.create_table(
        "chunks",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "document_id",
            UUID,
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("tsv", postgresql.TSVECTOR(), nullable=False),
        sa.Column("embedding", JSONB, nullable=True),
        sa.Column(
            "metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.UniqueConstraint(
            "document_id", "ordinal", name="uq_chunks_document_ordinal"
        ),
    )
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], postgresql_using="gin")
    op.create_table(
        "tasks",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("requester_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("budget_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.CheckConstraint("budget_cents >= 0", name="ck_tasks_budget_nonnegative"),
    )
    op.create_table(
        "approvals",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "task_id",
            UUID,
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("approver_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')", name="ck_approvals_decision"
        ),
        sa.UniqueConstraint(
            "task_id", "approver_id", name="uq_approvals_task_approver"
        ),
    )
    op.create_table(
        "tool_runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "task_id",
            UUID,
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("tool_version", sa.Text(), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("result_digest", sa.String(64)),
        sa.Column("state", sa.Text(), nullable=False),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("actor_id", UUID, sa.ForeignKey("users.id")),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_id", UUID, nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False, index=True),
        sa.Column(
            "payload_redacted",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index(
        "ix_audit_events_resource", "audit_events", ["resource_type", "resource_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_resource", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("tool_runs")
    op.drop_table("approvals")
    op.drop_table("tasks")
    op.drop_index("ix_chunks_tsv", table_name="chunks")
    op.drop_table("chunks")
    op.drop_table("document_acl")
    op.drop_table("documents")
    op.drop_table("user_roles")
    op.drop_table("projects")
    op.drop_table("roles")
    op.drop_table("users")
