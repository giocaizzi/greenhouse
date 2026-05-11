"""Cluster detail page — decision rationale panel."""

import time

from sqlalchemy.orm import Session

from greenhouse_core.repository import IrrigationRepository


def test_rationale_panel_renders_without_decisions(seeded_client):
    """Cluster detail renders the rationale panel even with no decision logs."""
    resp = seeded_client.get("/clusters/1")
    assert resp.status_code == 200
    assert "Why this decision" in resp.text
    assert "No decisions yet" in resp.text


def test_rationale_panel_after_irrigate(seeded_client):
    """After a decision is written the rationale rows appear."""
    # Trigger a decision log via the irrigate endpoint
    resp = seeded_client.post("/api/v1/clusters/1/irrigate", json={"dry_run": True, "no_sync": True})
    assert resp.status_code == 200

    resp = seeded_client.get("/clusters/1")
    assert resp.status_code == 200
    assert "Why this decision" in resp.text
    # At least one rationale row must be rendered
    assert 'class="rationale__row"' in resp.text


def test_rationale_panel_seeded_directly(seeded_client, app):
    """Seed a DecisionLog directly via the repository and verify rendering."""
    with Session(app.state.session_factory.kw["bind"]) as session:
        repo = IrrigationRepository(session)
        repo.add_decision_log(
            cluster_id=1,
            evaluated_at=int(time.time()),
            action="skip",
            duration_minutes=5,
            interval_hours=12,
            confidence=0.9,
            primary_code="cooldown",
            reason_text="within cooldown window",
            payload={
                "reasons": [
                    {"code": "cooldown", "message": "Within cooldown window", "severity": "info", "interval_delta": 0}
                ]
            },
        )
        session.commit()

    resp = seeded_client.get("/clusters/1")
    assert resp.status_code == 200
    assert 'class="rationale__row"' in resp.text
    assert "cooldown" in resp.text.lower() or "within cooldown" in resp.text.lower()
