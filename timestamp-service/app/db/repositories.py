from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import Select, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.auth.status_token import StoredToken, authenticate_status_token
from app.db.models import (
    DurableJob,
    IdempotencyRequest,
    JobAttempt,
    Order,
    OrderToken,
    OutboxMessage,
    ProofVersion,
    RateCounter,
    StateEvent,
    StripeEvent,
    TaskDispatch,
)
from app.domain.order import FulfillmentState, OrderSnapshot, OrderState, PaymentState
from app.jobs.models import ClaimedJob, JobOutcome, JobSpec, JobState, SpecificJobRetryable
from app.security.tokens import HashedToken, TokenHasher, generate_bearer_token
from app.tasks.dispatch import TaskDispatchCoordinator, add_task_dispatch


@dataclass(frozen=True, slots=True)
class AuthenticatedOrder:
    order: Order
    token: OrderToken


class OrderStore:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        token_hasher: TokenHasher,
        active_pepper_version: int,
        token_ttl: timedelta,
    ) -> None:
        self.session_factory = session_factory
        self.token_hasher = token_hasher
        self.active_pepper_version = active_pepper_version
        self.token_ttl = token_ttl

    def issue_token(self, session: Session, order: Order, now: datetime, *, revoke_existing: bool) -> str:
        active = session.scalars(
            select(OrderToken).where(OrderToken.order_id == order.id, OrderToken.revoked_at.is_(None)).with_for_update()
        ).all()
        if revoke_existing:
            for existing in active:
                existing.revoked_at = now
        sequence = session.scalar(select(func.max(OrderToken.version)).where(OrderToken.order_id == order.id)) or 0
        raw = generate_bearer_token(self.active_pepper_version)
        hashed = self.token_hasher.hash(raw)
        session.add(
            OrderToken(
                order_id=order.id,
                version=sequence + 1,
                pepper_version=hashed.version,
                token_hash=hashed.digest,
                revoked_at=None,
                expires_at=now + self.token_ttl,
                created_at=now,
            )
        )
        return raw

    def authenticate(
        self, session: Session, raw_token: str, now: datetime, *, for_update: bool = False
    ) -> AuthenticatedOrder | None:
        try:
            hashed = self.token_hasher.hash(raw_token)
        except ValueError:
            return None
        statement: Select[tuple[OrderToken]] = select(OrderToken).where(
            OrderToken.pepper_version == hashed.version,
            OrderToken.token_hash == hashed.digest,
        )
        if for_update:
            statement = statement.with_for_update()
        token = session.scalar(statement)
        if token is None:
            return None
        stored = StoredToken(
            token_hash=HashedToken(token.pepper_version, token.token_hash),
            revoked_at=token.revoked_at,
            expires_at=_aware(token.expires_at),
        )
        if not authenticate_status_token(raw_token, stored, self.token_hasher, _aware(now)):
            return None
        order = session.get(Order, token.order_id)
        return AuthenticatedOrder(order=order, token=token) if order is not None else None

    @staticmethod
    def latest_proof(session: Session, order_id: uuid.UUID) -> ProofVersion | None:
        return session.scalar(
            select(ProofVersion)
            .where(ProofVersion.order_id == order_id)
            .order_by(ProofVersion.version.desc())
            .limit(1)
        )

    @staticmethod
    def find_idempotency(session: Session, endpoint: str, key_hash: bytes) -> IdempotencyRequest | None:
        return session.scalar(OrderStore.idempotency_query(endpoint, key_hash))

    @staticmethod
    def idempotency_query(endpoint: str, key_hash: bytes) -> Select[tuple[IdempotencyRequest]]:
        return (
            select(IdempotencyRequest)
            .where(IdempotencyRequest.endpoint == endpoint, IdempotencyRequest.key_hash == key_hash)
            .with_for_update()
        )

    @staticmethod
    def find_order_for_event(
        session: Session,
        internal_order_id: str | None,
        payment_intent_id: str | None,
        *,
        for_update: bool = True,
    ) -> Order | None:
        statement: Select[tuple[Order]] | None = None
        if internal_order_id:
            try:
                statement = select(Order).where(Order.id == uuid.UUID(internal_order_id))
            except ValueError:
                return None
        elif payment_intent_id:
            statement = select(Order).where(Order.payment_intent_id == payment_intent_id)
        if statement is None:
            return None
        return session.scalar(statement.with_for_update() if for_update else statement)

    @staticmethod
    def record_state_event(
        session: Session,
        order: Order,
        event_key: str,
        source: str,
        previous_payment: str,
        previous_fulfillment: str,
        now: datetime,
    ) -> None:
        sequence = session.scalar(select(func.max(StateEvent.sequence)).where(StateEvent.order_id == order.id)) or 0
        session.add(
            StateEvent(
                order_id=order.id,
                sequence=sequence + 1,
                event_key=event_key,
                source=source,
                previous_payment_state=previous_payment,
                payment_state=order.payment_state,
                previous_fulfillment_state=previous_fulfillment,
                fulfillment_state=order.fulfillment_state,
                evidence={},
                created_at=now,
            )
        )

    def record_download_event(
        self,
        session: Session,
        order_id: uuid.UUID,
        proof_version: int,
        artifact_sha256: bytes,
        artifact_kind: str,
        now: datetime,
    ) -> None:
        if len(artifact_sha256) != 32 or artifact_kind not in {"persisted_verified", "generated_pending"}:
            raise ValueError("download_audit_invalid")
        order = session.scalar(select(Order).where(Order.id == order_id).with_for_update())
        if order is None:
            raise ValueError("download_order_not_found")
        sequence = session.scalar(select(func.max(StateEvent.sequence)).where(StateEvent.order_id == order.id)) or 0
        session.add(
            StateEvent(
                order_id=order.id,
                sequence=sequence + 1,
                event_key=f"download:{uuid.uuid4()}",
                source="customer_download",
                previous_payment_state=order.payment_state,
                payment_state=order.payment_state,
                previous_fulfillment_state=order.fulfillment_state,
                fulfillment_state=order.fulfillment_state,
                evidence={
                    "proof_version": proof_version,
                    "artifact_sha256": artifact_sha256.hex(),
                    "artifact_kind": artifact_kind,
                },
                created_at=now,
            )
        )

    @staticmethod
    def transition_payment(order: Order, target: PaymentState) -> bool:
        state = OrderState(
            snapshot=cast(OrderSnapshot, None),
            payment=PaymentState(order.payment_state),
            fulfillment=FulfillmentState(order.fulfillment_state),
        )
        try:
            changed = state.transition_payment(target)
        except ValueError:
            return False
        if changed is state:
            return False
        order.payment_state = changed.payment.value
        return True


class RateLimitStore:
    def __init__(self, session_factory: sessionmaker[Session], pepper: bytes) -> None:
        self.session_factory = session_factory
        self.pepper = pepper

    def hit(self, endpoint: str, client_address: str, now: datetime, limit: int) -> bool:
        key_hash = hmac.new(
            self.pepper,
            b"spacerocks-rate-limit\x00" + client_address.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        window = now.replace(second=0, microsecond=0)
        with self.session_factory() as session, session.begin():
            dialect = session.get_bind().dialect.name
            values = {
                "id": uuid.uuid4(),
                "endpoint": endpoint,
                "key_hash": key_hash,
                "window_started_at": window,
                "request_count": 1,
            }
            if dialect == "postgresql":
                statement = postgres_insert(RateCounter).values(**values).on_conflict_do_update(
                    index_elements=[RateCounter.endpoint, RateCounter.key_hash, RateCounter.window_started_at],
                    set_={"request_count": RateCounter.request_count + 1},
                ).returning(RateCounter.request_count)
                count = session.scalar(statement)
            elif dialect == "sqlite":
                sqlite_statement = sqlite_insert(RateCounter).values(**values).on_conflict_do_update(
                    index_elements=[RateCounter.endpoint, RateCounter.key_hash, RateCounter.window_started_at],
                    set_={"request_count": RateCounter.request_count + 1},
                ).returning(RateCounter.request_count)
                count = session.scalar(sqlite_statement)
            else:
                counter = session.scalar(
                    select(RateCounter)
                    .where(
                        RateCounter.endpoint == endpoint,
                        RateCounter.key_hash == key_hash,
                        RateCounter.window_started_at == window,
                    )
                    .with_for_update()
                )
                if counter is None:
                    counter = RateCounter(**values)
                    session.add(counter)
                else:
                    counter.request_count += 1
                session.flush()
                count = counter.request_count
            return count is not None and count <= limit


class SqlJobClaimStore:
    """PostgreSQL-safe durable claims; SQLite is suitable only for model/unit tests."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        task_dispatch: TaskDispatchCoordinator | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.task_dispatch = task_dispatch

    @staticmethod
    def claim_query(now: datetime) -> Select[tuple[DurableJob]]:
        return (
            select(DurableJob)
            .where(
                DurableJob.state.in_([JobState.AVAILABLE.value, JobState.RETRY.value, JobState.LEASED.value]),
                DurableJob.available_at <= now,
                or_(DurableJob.lease_until.is_(None), DurableJob.lease_until < now),
                DurableJob.attempt_count < DurableJob.max_attempts,
            )
            .order_by(DurableJob.available_at, DurableJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )

    async def enqueue_once(self, spec: JobSpec, available_at: datetime) -> bool:
        now = datetime.now(UTC)
        job_id: uuid.UUID | None = None
        generation = 1
        created = True
        with self.session_factory() as session:
            try:
                with session.begin():
                    job = DurableJob(
                        job_key=spec.job_key,
                        order_id=uuid.UUID(spec.order_id),
                        kind=spec.kind,
                        state=JobState.AVAILABLE.value,
                        generation=generation,
                        attempt_count=0,
                        max_attempts=spec.max_attempts,
                        available_at=available_at,
                        lease_owner=None,
                        lease_until=None,
                        safe_error_code=None,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(job)
                    session.flush()
                    job_id = job.id
                    add_task_dispatch(session, job, available_at, now)
            except IntegrityError:
                created = False
                with self.session_factory() as lookup:
                    existing = lookup.scalar(select(DurableJob).where(DurableJob.job_key == spec.job_key))
                    if existing is not None:
                        job_id = existing.id
                        generation = existing.generation
        if job_id is not None and self.task_dispatch is not None:
            await self.task_dispatch.dispatch(job_id, generation)
        return created

    async def claim(self, worker_id: str, now: datetime, lease_for: timedelta) -> ClaimedJob | None:
        with self.session_factory() as session, session.begin():
            job = session.scalar(self.claim_query(now))
            if job is None:
                return None
            return self._lease(session, job, worker_id, now, lease_for)

    async def claim_specific(
        self,
        job_id: str,
        generation: int,
        worker_id: str,
        now: datetime,
        lease_for: timedelta,
    ) -> ClaimedJob | None:
        if generation < 1:
            raise ValueError("task_generation_invalid")
        try:
            identifier = uuid.UUID(job_id)
        except ValueError as error:
            raise ValueError("job_id_must_be_uuid") from error
        with self.session_factory() as session, session.begin():
            job = session.scalar(select(DurableJob).where(DurableJob.id == identifier).with_for_update())
            if job is None:
                raise SpecificJobRetryable("specific_job_not_committed")
            dispatch = session.scalar(
                select(TaskDispatch).where(
                    TaskDispatch.job_id == identifier,
                    TaskDispatch.generation == generation,
                )
            )
            if dispatch is None:
                raise SpecificJobRetryable("specific_dispatch_not_committed")
            if job.generation != generation:
                return None
            if job.state not in {
                JobState.AVAILABLE.value,
                JobState.RETRY.value,
                JobState.LEASED.value,
            }:
                return None
            if _aware(job.available_at) > _aware(now):
                raise SpecificJobRetryable("specific_job_not_due")
            if job.lease_until is not None and _aware(job.lease_until) >= _aware(now):
                raise SpecificJobRetryable("specific_job_lease_active")
            if job.attempt_count >= job.max_attempts:
                return None
            return self._lease(session, job, worker_id, now, lease_for)

    @staticmethod
    def _lease(
        session: Session,
        job: DurableJob,
        worker_id: str,
        now: datetime,
        lease_for: timedelta,
    ) -> ClaimedJob:
        job.attempt_count += 1
        job.state = JobState.LEASED.value
        job.lease_owner = worker_id
        job.lease_until = now + lease_for
        job.updated_at = now
        session.add(
            JobAttempt(
                job_id=job.id,
                attempt_number=job.attempt_count,
                worker_id=worker_id,
                started_at=now,
                finished_at=None,
                outcome=None,
                safe_error_code=None,
                created_at=now,
            )
        )
        return ClaimedJob(
            id=str(job.id),
            spec=JobSpec(
                job_key=job.job_key,
                kind=job.kind,
                order_id=str(job.order_id),
                max_attempts=job.max_attempts,
            ),
            attempt=job.attempt_count,
            lease_owner=worker_id,
            lease_until=job.lease_until,
        )

    async def heartbeat(self, job_id: str, worker_id: str, attempt: int, lease_until: datetime) -> bool:
        with self.session_factory() as session, session.begin():
            job = session.scalar(select(DurableJob).where(DurableJob.id == uuid.UUID(job_id)).with_for_update())
            if (
                job is None
                or job.lease_owner != worker_id
                or job.attempt_count != attempt
                or job.state != JobState.LEASED.value
            ):
                return False
            job.lease_until = lease_until
            job.updated_at = datetime.now(UTC)
            return True

    async def finish(
        self,
        job: ClaimedJob,
        outcome: JobOutcome,
        now: datetime,
        retry_at: datetime | None = None,
        safe_error_code: str | None = None,
    ) -> None:
        retry_dispatch: tuple[uuid.UUID, int] | None = None
        with self.session_factory() as session, session.begin():
            stored = session.scalar(select(DurableJob).where(DurableJob.id == uuid.UUID(job.id)).with_for_update())
            if stored is None or stored.lease_owner != job.lease_owner or stored.attempt_count != job.attempt:
                raise ValueError("job lease is no longer owned by this attempt")
            attempt = session.scalar(
                select(JobAttempt).where(
                    JobAttempt.job_id == stored.id, JobAttempt.attempt_number == stored.attempt_count
                )
            )
            if attempt is None:
                raise ValueError("job attempt history is missing")
            final_outcome = outcome
            if outcome == JobOutcome.RETRY and stored.attempt_count >= stored.max_attempts:
                final_outcome = JobOutcome.DEAD_LETTER
            if final_outcome == JobOutcome.RETRY and retry_at is None:
                raise ValueError("retry outcome requires retry_at")
            stored.state = final_outcome.value
            stored.available_at = retry_at or stored.available_at
            stored.lease_owner = None
            stored.lease_until = None
            stored.safe_error_code = safe_error_code
            stored.updated_at = now
            attempt.finished_at = now
            attempt.outcome = final_outcome.value
            attempt.safe_error_code = safe_error_code
            if final_outcome == JobOutcome.RETRY:
                assert retry_at is not None
                stored.generation += 1
                add_task_dispatch(session, stored, retry_at, now)
                retry_dispatch = (stored.id, stored.generation)
        if retry_dispatch is not None and self.task_dispatch is not None:
            await self.task_dispatch.dispatch(*retry_dispatch)


def enqueue_fulfillment_once(session: Session, order: Order, now: datetime) -> None:
    job = DurableJob(
        job_key=order.fulfillment_key,
        order_id=order.id,
        kind="stamp_manifest_digest",
        state=JobState.AVAILABLE.value,
        generation=1,
        attempt_count=0,
        max_attempts=10,
        available_at=now,
        lease_owner=None,
        lease_until=None,
        safe_error_code=None,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    session.flush()
    add_task_dispatch(session, job, now, now)


def record_stripe_event(
    session: Session,
    event_id: str,
    event_type: str,
    payload_sha256: bytes,
    now: datetime,
    *,
    livemode: bool,
) -> StripeEvent:
    event = StripeEvent(
        stripe_event_id=event_id,
        event_type=event_type,
        livemode=livemode,
        payload_sha256=payload_sha256,
        processed_at=None,
        safe_error_code=None,
        created_at=now,
    )
    session.add(event)
    session.flush()
    return event


def enqueue_outbox_once(session: Session, order: Order, kind: str, now: datetime) -> None:
    session.add(
        OutboxMessage(
            message_key=f"{kind}:{order.id}",
            order_id=order.id,
            kind=kind,
            recipient=order.email,
            payload={"order_reference": order.order_reference},
            state="available",
            attempt_count=0,
            available_at=now,
            lease_owner=None,
            lease_until=None,
            provider_message_id=None,
            delivered_at=None,
            safe_error_code=None,
            created_at=now,
        )
    )


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
