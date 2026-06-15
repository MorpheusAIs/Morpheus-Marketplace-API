"""Manual integration check for SessionRoutingService._claim_idle_session.

Runs against the local docker Postgres (docker-compose.local.yml db-local,
host port 5433). Validates the real `UPDATE ... FOR UPDATE SKIP LOCKED` claim:
  1. claims the idle, LRU (oldest last_used_at) OPEN session and increments it;
  2. skips utilized / expired / wrong-model / non-OPEN rows;
  3. returns None when nothing is claimable;
  4. under concurrency, two simultaneous claims on a single idle session hand it
     to exactly one caller (the other opens nothing) — the property an in-process
     asyncio.Lock could not provide across connections.

Usage:
    DATABASE_URL=postgresql+asyncpg://morpheus_local:local_dev_password@localhost:5433/morpheus_local_db \
        .venv/bin/python scripts/verify_claim_idle_session.py
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.db.models import RoutedSession, SessionState
from src.services.session_routing_service import SessionRoutingService

DB_URL = os.environ["DATABASE_URL"]
MODEL = "0xmodelA"
OTHER_MODEL = "0xmodelB"


def naive_utc(offset_seconds: int = 0):
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=offset_seconds)


async def reset(engine):
    # Surgical: only manage routed_sessions so we don't disturb any existing
    # app schema/enum types persisted in the local dev volume.
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS routed_sessions CASCADE"))
        await conn.run_sync(RoutedSession.__table__.create)


def row(id_, model_id, *, active=0, state=SessionState.OPEN, expires_in=3600, last_used=None):
    return RoutedSession(
        id=id_,
        model_id=model_id,
        model_name="m",
        state=state.value if isinstance(state, SessionState) else state,
        active_requests=active,
        expires_at=naive_utc(expires_in),
        last_used_at=last_used,
        created_at=naive_utc(-100),
        updated_at=naive_utc(-100),
    )


async def fetch(Session, id_):
    async with Session() as db:
        return await db.get(RoutedSession, id_)


async def main():
    engine = create_async_engine(DB_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    svc = SessionRoutingService()
    failures = []

    def check(name, cond):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            failures.append(name)

    # --- 1: picks idle LRU, increments active_requests -------------------
    await reset(engine)
    async with Session() as db:
        db.add_all([
            row("0xfresh", MODEL, last_used=naive_utc(-10)),   # used 10s ago
            row("0xstale", MODEL, last_used=naive_utc(-100)),  # used 100s ago -> LRU
            row("0xbusy", MODEL, active=1, last_used=naive_utc(-200)),  # utilized
        ])
        await db.commit()
    async with Session() as db:
        claimed = await svc._claim_idle_session(db, MODEL)
    print("1) claim idle LRU ->", claimed)
    check("claims the LRU idle session (0xstale)", claimed == "0xstale")
    check("increments active_requests to 1", (await fetch(Session, "0xstale")).active_requests == 1)
    check("did not touch the busy session", (await fetch(Session, "0xbusy")).active_requests == 1)

    # --- 2: skips utilized/expired/other-model/non-OPEN ------------------
    await reset(engine)
    async with Session() as db:
        db.add_all([
            row("0xb1", MODEL, active=2),                       # utilized
            row("0xexp", MODEL, expires_in=-5),                 # expired
            row("0xother", OTHER_MODEL),                        # different model
            row("0xclosed", MODEL, state=SessionState.CLOSED),  # not OPEN
        ])
        await db.commit()
    async with Session() as db:
        claimed = await svc._claim_idle_session(db, MODEL)
    print("2) nothing claimable ->", claimed)
    check("returns None when no idle OPEN row matches", claimed is None)

    # --- 3: concurrency — one idle row, two simultaneous claimers --------
    await reset(engine)
    async with Session() as db:
        db.add(row("0xsolo", MODEL, last_used=naive_utc(-50)))
        await db.commit()

    async def claim_once():
        async with Session() as db:
            return await svc._claim_idle_session(db, MODEL)

    results = await asyncio.gather(*[claim_once() for _ in range(8)])
    winners = [r for r in results if r == "0xsolo"]
    nones = [r for r in results if r is None]
    print(f"3) concurrent claims -> winners={len(winners)} none={len(nones)} raw={results}")
    check("exactly one concurrent claimer wins the single idle session", len(winners) == 1)
    check("all other concurrent claimers get None (SKIP LOCKED, no double-assign)", len(nones) == 7)
    check("winner's active_requests == 1 (no lost/dup increment)", (await fetch(Session, "0xsolo")).active_requests == 1)

    await engine.dispose()
    print()
    if failures:
        print(f"RESULT: FAILED ({len(failures)}): {failures}")
        raise SystemExit(1)
    print("RESULT: ALL CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
