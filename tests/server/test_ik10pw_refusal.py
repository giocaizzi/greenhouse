"""S2 / B-2 end to end: a real IK10PW adapter without a local Duration DP refuses to start.

The seeded irrigator has no ``device_ip``, so the local Duration write fails
without I/O. The adapter must refuse before any Cloud ON, and the two server
callers must record the refusal: the pipeline writes an ``attempted`` event,
manual start answers 502 and writes nothing.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest

from fake_data import FAKE_CLIENT_ID, FAKE_CLIENT_SECRET
from greenhouse_core.devices import DeviceGateway, IK10PWAdapter
from server.test_contract_pipeline import Pipeline, _irrigate

REFUSAL_PREFIX = "Local Duration DP unavailable (Local connection failed: No device_ip in config"


class _NoCloudExpected:
    """A ``tinytuya.Cloud`` stand-in that records any call (none is expected)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        def _record(*_args, **_kwargs):
            self.calls.append(name)
            return {"success": True}

        return _record


@pytest.fixture
def ik10pw_pipeline(clean_env, frozen_clock):
    del clean_env, frozen_clock  # requested only for their side effects
    p = Pipeline()
    cloud = _NoCloudExpected()
    adapter = IK10PWAdapter(DeviceGateway(FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, raw=cloud))
    p.wiring.registry.register_irrigator("rainpoint.ik10pw", lambda: adapter)
    yield p, cloud
    p.close()


def test_pipeline_records_attempted_and_sends_no_on(ik10pw_pipeline):
    p, cloud = ik10pw_pipeline
    p.seed_cluster(soil=35.0)
    p.mark()
    result = _irrigate(p)
    assert result["status"] == HTTPStatus.OK
    assert result["response"]["action"] == "error"
    assert REFUSAL_PREFIX in result["response"]["reason"]
    events = p.db_rows()["irrigation_events"]
    assert [(e["action"], e["triggered_by"]) for e in events] == [("attempted", "auto")]
    assert cloud.calls == []


def test_manual_start_returns_502_and_records_nothing(ik10pw_pipeline):
    p, cloud = ik10pw_pipeline
    ids = p.seed_cluster(soil=35.0)
    p.mark()
    result = p.call("POST", f"/api/v1/irrigators/{ids['irrigator_id']}/start", {"minutes": 5})
    assert result["status"] == HTTPStatus.BAD_GATEWAY
    assert result["response"]["detail"].startswith(REFUSAL_PREFIX)
    assert "keep-alive is not supported in the server" in result["response"]["detail"]
    assert p.db_rows()["irrigation_events"] == []
    assert cloud.calls == []
