from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.bitcoin.disabled import DisabledVerifier
from app.bitcoin.rpc import BitcoinCoreRpcTransport, BitcoinCoreRpcVerifier
from app.config.settings import AppEnvironment, BitcoinMode, CalendarMode, Settings
from app.db.fulfillment_adapters import SqlFulfillmentAdapters, create_sql_fulfillment_adapters
from app.db.session import create_database_engine, create_session_factory
from app.domain.order import FulfillmentState
from app.fulfillment.errors import ConfirmationPending
from app.fulfillment.handlers import ConfirmationHandler, DeliveryHandler, StampHandler, UpgradeHandler
from app.fulfillment.terminal import TerminalFailureHandler
from app.jobs.claims import JobClaimStore
from app.jobs.models import JobSpec
from app.ports.bitcoin import BitcoinVerifier
from app.ports.system import Clock, RandomSource
from app.ports.timestamping import Timestamper
from app.proofs.factory import create_proof_bundler
from app.timestamping.calendars import CalendarConfiguration, HardenedCalendarTransport, MultiCalendarTimestamper
from app.timestamping.fixture import DisabledTimestamper, FixtureTimestamper
from app.worker.runner import JobHandler, Worker

STAMP_JOB = "stamp_manifest_digest"
UPGRADE_JOB = "upgrade_timestamp"
DELIVERY_JOB = "deliver_timestamp"
CONFIRMATION_JOB = "monitor_bitcoin_confirmations"
PENDING_POLL_INTERVAL = timedelta(hours=6)
PENDING_POLL_MINIMUM_WINDOW = timedelta(days=7)
CONFIRMATION_POLL_INTERVAL = timedelta(minutes=15)


class Handler(Protocol):
    async def __call__(self, order_id: str) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class SystemRandom:
    def bytes(self, length: int) -> bytes:
        return secrets.token_bytes(length)

    def uniform(self, lower: float, upper: float) -> float:
        return secrets.SystemRandom().uniform(lower, upper)


@dataclass(slots=True)
class StampJobHandler:
    handler: Handler
    jobs: JobClaimStore
    clock: Clock

    async def __call__(self, order_id: str) -> None:
        await self.handler(order_id)
        await self.jobs.enqueue_once(
            JobSpec(job_key=f"upgrade:{order_id}", kind=UPGRADE_JOB, order_id=order_id),
            self.clock.now(),
        )


@dataclass(slots=True)
class UpgradeJobHandler:
    handler: Handler
    adapters: SqlFulfillmentAdapters
    clock: Clock

    async def __call__(self, order_id: str) -> None:
        try:
            await self.handler(order_id)
        except ConfirmationPending:
            next_poll = self.clock.now() + PENDING_POLL_INTERVAL
            await self.adapters.jobs.enqueue_once(
                JobSpec(
                    job_key=f"upgrade-poll:{order_id}:{int(next_poll.timestamp())}",
                    kind=UPGRADE_JOB,
                    order_id=order_id,
                ),
                next_poll,
            )
            return
        order = await self.adapters.orders.get_for_fulfillment(order_id)
        if order is None:
            return
        if order.state.fulfillment == FulfillmentState.BITCOIN_VERIFIED:
            await self.adapters.jobs.enqueue_once(
                JobSpec(job_key=f"delivery:{order_id}", kind=DELIVERY_JOB, order_id=order_id),
                self.clock.now(),
            )


@dataclass(slots=True)
class DeliveryJobHandler:
    handler: Handler
    jobs: JobClaimStore
    clock: Clock

    async def __call__(self, order_id: str) -> None:
        await self.handler(order_id)
        first_poll = self.clock.now() + CONFIRMATION_POLL_INTERVAL
        await self.jobs.enqueue_once(
            JobSpec(
                job_key=f"confirmation-poll:{order_id}:{int(first_poll.timestamp())}",
                kind=CONFIRMATION_JOB,
                order_id=order_id,
            ),
            first_poll,
        )


@dataclass(slots=True)
class ConfirmationJobHandler:
    handler: Handler
    jobs: JobClaimStore
    clock: Clock

    async def __call__(self, order_id: str) -> None:
        try:
            await self.handler(order_id)
        except ConfirmationPending:
            next_poll = self.clock.now() + CONFIRMATION_POLL_INTERVAL
            await self.jobs.enqueue_once(
                JobSpec(
                    job_key=f"confirmation-poll:{order_id}:{int(next_poll.timestamp())}",
                    kind=CONFIRMATION_JOB,
                    order_id=order_id,
                ),
                next_poll,
            )


def build_worker(
    settings: Settings,
    session_factory: sessionmaker[Session],
    *,
    worker_id: str = "timestamp-worker",
    clock: Clock | None = None,
    random: RandomSource | None = None,
    timestamper: Timestamper | None = None,
    bitcoin: BitcoinVerifier | None = None,
) -> Worker:
    if (timestamper is not None or bitcoin is not None) and settings.app_env != AppEnvironment.TEST:
        raise RuntimeError("worker_adapter_override_forbidden")
    adapters = create_sql_fulfillment_adapters(session_factory)
    configured_clock = clock or SystemClock()
    configured_timestamper = timestamper or _timestamper(settings)
    configured_bitcoin = bitcoin or _bitcoin_verifier(settings)
    stamp = StampHandler(adapters.orders, adapters.proofs, configured_timestamper)
    upgrade = UpgradeHandler(
        adapters.orders,
        adapters.proofs,
        configured_timestamper,
        configured_bitcoin,
        adapters.verifications,
        adapters.observations,
    )
    delivery = DeliveryHandler(
        adapters.orders,
        adapters.proofs,
        create_proof_bundler(),
        adapters.bundles,
        adapters.verifications,
        adapters.observations,
        adapters.outbox,
        settings.product_version,
    )
    confirmation = ConfirmationHandler(
        adapters.orders,
        adapters.proofs,
        configured_bitcoin,
        adapters.verifications,
        adapters.observations,
        adapters.outbox,
    )
    handlers: dict[str, JobHandler] = {
        STAMP_JOB: StampJobHandler(stamp, adapters.jobs, configured_clock),
        UPGRADE_JOB: UpgradeJobHandler(upgrade, adapters, configured_clock),
        DELIVERY_JOB: DeliveryJobHandler(delivery, adapters.jobs, configured_clock),
        CONFIRMATION_JOB: ConfirmationJobHandler(confirmation, adapters.jobs, configured_clock),
    }
    return Worker(
        worker_id=worker_id,
        claims=adapters.jobs,
        handlers=handlers,
        terminal_failure=TerminalFailureHandler(adapters.orders),
        clock=configured_clock,
        random=random or SystemRandom(),
    )


def create_worker() -> Worker:
    settings = Settings()
    if settings.database_url is None:
        raise RuntimeError("worker_database_required")
    engine = create_database_engine(settings.database_url.get_secret_value())
    session_factory = create_session_factory(engine)
    worker_id = os.environ.get("TIMESTAMP_WORKER_ID") or f"worker-{secrets.token_hex(8)}"
    return build_worker(settings, session_factory, worker_id=worker_id)


def _timestamper(settings: Settings) -> Timestamper:
    if settings.calendar_mode == CalendarMode.FIXTURE:
        if settings.app_env != AppEnvironment.TEST:
            raise RuntimeError("fixture_calendar_forbidden")
        return FixtureTimestamper()
    if settings.calendar_mode == CalendarMode.PUBLIC:
        configuration = CalendarConfiguration(
            allowlist=tuple(settings.calendar_allowlist),
            enabled=True,
            timeout_seconds=settings.calendar_timeout_seconds,
        )
        return MultiCalendarTimestamper(
            configuration,
            HardenedCalendarTransport(settings.calendar_timeout_seconds),
        )
    return DisabledTimestamper()


def _bitcoin_verifier(settings: Settings) -> BitcoinVerifier:
    if settings.bitcoin_verifier == BitcoinMode.FIXTURE:
        if settings.app_env != AppEnvironment.TEST:
            raise RuntimeError("fixture_bitcoin_verifier_forbidden")
        from app.bitcoin.fixture import FixtureBitcoinVerifier

        return FixtureBitcoinVerifier()
    if settings.bitcoin_verifier == BitcoinMode.BITCOIN_CORE:
        if (
            settings.bitcoin_rpc_url is None
            or settings.bitcoin_rpc_username is None
            or settings.bitcoin_rpc_password is None
        ):
            raise RuntimeError("validated_bitcoin_rpc_configuration_unavailable")
        transport = BitcoinCoreRpcTransport(
            settings.bitcoin_rpc_url,
            settings.bitcoin_rpc_username.get_secret_value(),
            settings.bitcoin_rpc_password.get_secret_value(),
            timeout_seconds=settings.bitcoin_rpc_timeout_seconds,
        )
        return BitcoinCoreRpcVerifier(transport, minimum_confirmations=1)
    return DisabledVerifier()
