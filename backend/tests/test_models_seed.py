"""Schema + seed integration tests.

Runs against an in-memory SQLite (portable types create cleanly) so the schema's
guarantees are pinned in CI without a live Postgres. The same DDL is validated
against real Postgres via Alembic at deploy.
"""

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import Assessment, ChargingStation, Load, Truck
from app.seed.seed import seed_all


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")

    # SQLite ignores CHECK/FK constraints unless explicitly enabled.
    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_con, _):
        dbapi_con.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        yield s


def test_seed_is_idempotent(session):
    a = seed_all(session)
    b = seed_all(session)
    assert a == (4, 8)  # first run writes everything (4 trucks incl. Tesla Std Range)
    assert b == (0, 0)  # second run writes nothing new
    assert session.scalar(select(Truck).where(Truck.make == "Tesla")) is not None


def test_all_seeded_loads_are_synthetic(session):
    seed_all(session)
    loads = session.scalars(select(Load)).all()
    assert len(loads) == 8
    assert {l.data_source for l in loads} == {"synthetic"}


def test_truck_provenance_is_per_field(session):
    seed_all(session)
    volvo = session.scalar(select(Truck).where(Truck.make == "Volvo"))
    # Volvo mixes trust: usable kWh is secondary, but range is manufacturer.
    assert volvo.provenance["usable_kwh"]["trust"] == "secondary"
    assert volvo.provenance["published_range_mi"]["trust"] == "manufacturer"
    assert volvo.provenance["base_consumption_kwh_per_mi"]["trust"] == "derived"
    assert volvo.provenance["reference_payload_lb"]["trust"] == "assumption"

    tesla = session.scalar(select(Truck).where(Truck.make == "Tesla"))
    assert tesla.provenance["usable_kwh"]["trust"] == "regulatory"


def test_base_consumption_matches_derivation(session):
    seed_all(session)
    for t in session.scalars(select(Truck)).all():
        derived = round(float(t.usable_kwh) / t.published_range_mi, 3)
        assert float(t.base_consumption_kwh_per_mi) == pytest.approx(derived, abs=0.001)


def test_load_data_source_check_constraint(session):
    bad = Load(
        reference="LD-BAD",
        origin_label="A", origin_lat=0, origin_lon=0,
        dest_label="B", dest_lat=1, dest_lon=1,
        weight_lb=1000,
        pickup_window_start=__import__("datetime").datetime(2026, 6, 15),
        pickup_window_end=__import__("datetime").datetime(2026, 6, 15, 1),
        delivery_window_start=__import__("datetime").datetime(2026, 6, 15, 2),
        delivery_window_end=__import__("datetime").datetime(2026, 6, 15, 3),
        data_source="totally-not-allowed",
    )
    session.add(bad)
    with pytest.raises(IntegrityError):
        session.flush()


def test_station_source_check_constraint(session):
    bad = ChargingStation(
        data_source="BOGUS", external_id="x", lat=0, lon=0,
        expires_at=__import__("datetime").datetime(2026, 6, 15),
    )
    session.add(bad)
    with pytest.raises(IntegrityError):
        session.flush()
