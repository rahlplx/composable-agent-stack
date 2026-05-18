"""PostgreSQL State Persistence Layer.

Uses SQLAlchemy async to persist workflows and tasks to PostgreSQL.
This replaces the in-memory dict store in OrchestratorService
for production deployments.

Schema matches the reference design from orchestrator-design-implementation-reference.md.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import (
    Column, String, Text, Integer, DateTime,
    ForeignKey, Index, ARRAY,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.ext.asyncio import (
    AsyncSession, create_async_engine, async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase, relationship, selectinload
from sqlalchemy import select, update, delete



# ─── SQLAlchemy Base ────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class WorkflowORM(Base):
    __tablename__ = "workflows"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_request = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="running")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    tasks = relationship("TaskORM", back_populates="workflow", cascade="all, delete-orphan")


class TaskORM(Base):
    __tablename__ = "tasks"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(PGUUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String(20), nullable=False)
    action_type = Column(String(50), nullable=False, default="execute")
    input_data = Column(JSONB, nullable=False, default=dict)
    status = Column(String(20), nullable=False, default="pending")
    retries = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)
    result = Column(JSONB, nullable=True)
    error = Column(Text, nullable=True)
    depends_on = Column(ARRAY(PGUUID(as_uuid=True)), default=list)
    priority = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    workflow = relationship("WorkflowORM", back_populates="tasks")

    __table_args__ = (
        Index("idx_tasks_workflow", "workflow_id"),
        Index("idx_tasks_status", "status"),
    )


# ─── Persistence Service ────────────────────────────────────────────────────

class PersistenceService:
    """Async PostgreSQL persistence for workflows and tasks.

    Usage:
        persistence = PersistenceService("postgresql+asyncpg://agent:agent@localhost/agent_stack")
        await persistence.initialize()

        wf = await persistence.create_workflow("scrape the price")
        task = await persistence.create_task(wf.id, "browser_use", "extract", {...})
        await persistence.update_task_status(task.id, "running")
    """

    def __init__(self, database_url: str = "postgresql+asyncpg://agent:agent@localhost/5432/agent_stack"):
        self.database_url = database_url
        self._engine = None
        self._session_factory = None

    async def initialize(self) -> None:
        """Create engine, session factory, and tables."""
        self._engine = create_async_engine(self.database_url, echo=False)
        self._session_factory = async_sessionmaker(
            self._engine, class_=AsyncSession, expire_on_commit=False,
        )
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def shutdown(self) -> None:
        """Dispose of the engine."""
        if self._engine:
            await self._engine.dispose()

    async def _get_session(self) -> AsyncSession:
        return self._session_factory()

    # ── Workflow Operations ─────────────────────────────────────────

    async def create_workflow(self, user_request: str) -> WorkflowORM:
        async with self._get_session() as session:
            wf = WorkflowORM(user_request=user_request, status="running")
            session.add(wf)
            await session.commit()
            await session.refresh(wf)
            return wf

    async def get_workflow(self, workflow_id: str) -> Optional[WorkflowORM]:
        async with self._get_session() as session:
            result = await session.execute(
                select(WorkflowORM)
                .options(selectinload(WorkflowORM.tasks))
                .where(WorkflowORM.id == uuid.UUID(workflow_id))
            )
            return result.scalar_one_or_none()

    async def list_workflows(self, limit: int = 100) -> Sequence[WorkflowORM]:
        async with self._get_session() as session:
            result = await session.execute(
                select(WorkflowORM)
                .order_by(WorkflowORM.created_at.desc())
                .limit(limit)
            )
            return result.scalars().all()

    async def update_workflow_status(self, workflow_id: str, status: str) -> None:
        async with self._get_session() as session:
            await session.execute(
                update(WorkflowORM)
                .where(WorkflowORM.id == uuid.UUID(workflow_id))
                .values(status=status)
            )
            await session.commit()

    # ── Task Operations ─────────────────────────────────────────────

    async def create_task(
        self,
        workflow_id: str,
        platform: str,
        action_type: str = "execute",
        input_data: dict | None = None,
        depends_on: list[str] | None = None,
        priority: int = 0,
    ) -> TaskORM:
        async with self._get_session() as session:
            dep_uuids = [uuid.UUID(d) for d in (depends_on or [])]
            task = TaskORM(
                workflow_id=uuid.UUID(workflow_id),
                platform=platform,
                action_type=action_type,
                input_data=input_data or {},
                depends_on=dep_uuids,
                priority=priority,
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)
            return task

    async def get_task(self, task_id: str) -> Optional[TaskORM]:
        async with self._get_session() as session:
            result = await session.execute(
                select(TaskORM).where(TaskORM.id == uuid.UUID(task_id))
            )
            return result.scalar_one_or_none()

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        result: Any = None,
        error: str | None = None,
        retries: int | None = None,
    ) -> None:
        async with self._get_session() as session:
            values = {
                "status": status,
                "updated_at": datetime.now(timezone.utc),
            }
            if result is not None:
                values["result"] = result
            if error is not None:
                values["error"] = error
            if retries is not None:
                values["retries"] = retries

            await session.execute(
                update(TaskORM)
                .where(TaskORM.id == uuid.UUID(task_id))
                .values(**values)
            )
            await session.commit()

    async def get_pending_tasks(self, workflow_id: str | None = None) -> Sequence[TaskORM]:
        """Get all PENDING tasks (optionally filtered by workflow)."""
        async with self._get_session() as session:
            query = select(TaskORM).where(TaskORM.status == "pending")
            if workflow_id:
                query = query.where(TaskORM.workflow_id == uuid.UUID(workflow_id))
            result = await session.execute(query)
            return result.scalars().all()

    async def get_running_tasks(self) -> Sequence[TaskORM]:
        """Get all RUNNING tasks."""
        async with self._get_session() as session:
            result = await session.execute(
                select(TaskORM).where(TaskORM.status == "running")
            )
            return result.scalars().all()

    async def delete_workflow(self, workflow_id: str) -> None:
        async with self._get_session() as session:
            await session.execute(
                delete(WorkflowORM).where(WorkflowORM.id == uuid.UUID(workflow_id))
            )
            await session.commit()
