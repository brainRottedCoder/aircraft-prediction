"""Work orders — spec 35. Action is derived from risk, never from the client."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.domain.rules import recommended_action
from app.models.maintenance import WorkOrder

PARTS = ["engine", "radar", "gear", "hyd", "fuel"]


def _free_pair(client, auth, db):
    """An (aircraft, part) combination with no open work order."""
    open_pairs = {(w.aircraft_id, w.part_id) for w in db.scalars(
        select(WorkOrder).where(WorkOrder.status != "done"))}
    for code in [f"Fighter-0{i}" for i in range(1, 9)]:
        detail = client.get(f"/api/v1/aircraft/{code}", headers=auth("viewer")).json()
        for part in detail["parts"]:
            if (detail["id"], _part_id(db, part["part"])) not in open_pairs:
                return code, part["part"], detail["id"]
    pytest.skip("every aircraft+part already has an open work order")


def _part_id(db, code: str) -> int:
    from app.models.fleet import Part

    return db.query(Part).filter(Part.code == code).one().id


def test_create_returns_201_and_the_body(client, auth, db):
    code, part, _ = _free_pair(client, auth, db)
    response = client.post("/api/v1/work-orders",
                           headers=auth("officer"),
                           json={"aircraft": code, "part": part,
                                 "due_date": "2026-12-01", "priority": "high"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["reference"].startswith("WO-")
    assert body["status"] == "open"
    assert body["aircraft"] == code and body["part"] == part
    assert body["created_by"]["username"] == "officer"


def test_action_is_derived_from_risk_not_the_client(client, auth, db):
    code, part, _ = _free_pair(client, auth, db)
    detail = client.get(f"/api/v1/aircraft/{code}/parts/{part}",
                        headers=auth("viewer")).json()
    body = client.post("/api/v1/work-orders", headers=auth("officer"),
                       json={"aircraft": code, "part": part, "due_date": "2026-12-01"}).json()
    assert body["action"] == recommended_action(detail["risk"])


def test_client_cannot_inject_an_action(client, auth, db):
    code, part, _ = _free_pair(client, auth, db)
    body = client.post("/api/v1/work-orders", headers=auth("officer"),
                       json={"aircraft": code, "part": part, "due_date": "2026-12-01",
                             "action": "Ignore everything"}).json()
    assert body["action"] != "Ignore everything"


def test_duplicate_open_work_order_is_409(client, auth, db):
    code, part, _ = _free_pair(client, auth, db)
    payload = {"aircraft": code, "part": part, "due_date": "2026-12-01"}
    assert client.post("/api/v1/work-orders", headers=auth("officer"),
                       json=payload).status_code == 201
    second = client.post("/api/v1/work-orders", headers=auth("officer"), json=payload)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "DUPLICATE_WORK_ORDER"


def test_unknown_aircraft_is_404(client, auth):
    response = client.post("/api/v1/work-orders", headers=auth("officer"),
                           json={"aircraft": "Fighter-99", "part": "engine",
                                 "due_date": "2026-12-01"})
    assert response.status_code == 404


def test_unknown_part_is_422(client, auth):
    response = client.post("/api/v1/work-orders", headers=auth("officer"),
                           json={"aircraft": "Fighter-01", "part": "warp-core",
                                 "due_date": "2026-12-01"})
    assert response.status_code == 422


def test_missing_due_date_is_422(client, auth):
    response = client.post("/api/v1/work-orders", headers=auth("officer"),
                           json={"aircraft": "Fighter-01", "part": "engine"})
    assert response.status_code == 422


def test_invalid_priority_is_422(client, auth):
    response = client.post("/api/v1/work-orders", headers=auth("officer"),
                           json={"aircraft": "Fighter-01", "part": "engine",
                                 "due_date": "2026-12-01", "priority": "urgent"})
    assert response.status_code == 422


def test_open_to_in_progress_sets_started_at(client, auth, db):
    code, part, _ = _free_pair(client, auth, db)
    created = client.post("/api/v1/work-orders", headers=auth("officer"),
                          json={"aircraft": code, "part": part,
                                "due_date": "2026-12-01"}).json()
    body = client.patch(f"/api/v1/work-orders/{created['id']}",
                        headers=auth("officer"), json={"status": "in_progress"}).json()
    assert body["status"] == "in_progress"
    assert body["started_at"] is not None


def test_completion_sets_completed_at(client, auth, db):
    code, part, _ = _free_pair(client, auth, db)
    created = client.post("/api/v1/work-orders", headers=auth("officer"),
                          json={"aircraft": code, "part": part,
                                "due_date": "2026-12-01"}).json()
    body = client.patch(f"/api/v1/work-orders/{created['id']}",
                        headers=auth("officer"), json={"status": "done"}).json()
    assert body["status"] == "done"
    assert body["completed_at"] is not None


def test_in_progress_to_open_is_rejected(client, auth, db):
    code, part, _ = _free_pair(client, auth, db)
    created = client.post("/api/v1/work-orders", headers=auth("officer"),
                          json={"aircraft": code, "part": part,
                                "due_date": "2026-12-01"}).json()
    client.patch(f"/api/v1/work-orders/{created['id']}", headers=auth("officer"),
                 json={"status": "in_progress"})
    back = client.patch(f"/api/v1/work-orders/{created['id']}", headers=auth("officer"),
                        json={"status": "open"})
    assert back.status_code == 422
    assert back.json()["error"]["code"] == "BUSINESS_RULE_VIOLATION"


def test_completed_work_order_is_immutable(client, auth, db):
    code, part, _ = _free_pair(client, auth, db)
    created = client.post("/api/v1/work-orders", headers=auth("officer"),
                          json={"aircraft": code, "part": part,
                                "due_date": "2026-12-01"}).json()
    client.patch(f"/api/v1/work-orders/{created['id']}", headers=auth("officer"),
                 json={"status": "done"})
    again = client.patch(f"/api/v1/work-orders/{created['id']}", headers=auth("officer"),
                         json={"status": "in_progress"})
    assert again.status_code == 409


def test_priority_can_be_raised_without_changing_status(client, auth, db):
    code, part, _ = _free_pair(client, auth, db)
    created = client.post("/api/v1/work-orders", headers=auth("officer"),
                          json={"aircraft": code, "part": part,
                                "due_date": "2026-12-01"}).json()
    body = client.patch(f"/api/v1/work-orders/{created['id']}", headers=auth("officer"),
                        json={"priority": "high"}).json()
    assert body["priority"] == "high"
    assert body["status"] == "open"


def test_completing_a_work_order_frees_the_pair_for_a_new_one(client, auth, db):
    code, part, _ = _free_pair(client, auth, db)
    payload = {"aircraft": code, "part": part, "due_date": "2026-12-01"}
    first = client.post("/api/v1/work-orders", headers=auth("officer"),
                        json=payload).json()
    client.patch(f"/api/v1/work-orders/{first['id']}", headers=auth("officer"),
                 json={"status": "done"})
    assert client.post("/api/v1/work-orders", headers=auth("officer"),
                       json=payload).status_code == 201


def test_list_work_orders(client, auth):
    body = client.get("/api/v1/work-orders", headers=auth("viewer")).json()
    assert body["total"] >= 8
    assert all("reference" in item for item in body["items"])


def test_list_work_orders_filtered_by_status(client, auth):
    done = client.get("/api/v1/work-orders?status=done", headers=auth("viewer")).json()
    assert all(i["status"] == "done" for i in done["items"])


def test_unknown_work_order_is_404(client, auth):
    assert client.patch("/api/v1/work-orders/999999", headers=auth("officer"),
                        json={"status": "done"}).status_code == 404


def test_commander_can_also_create(client, auth, db):
    code, part, _ = _free_pair(client, auth, db)
    response = client.post("/api/v1/work-orders", headers=auth("commander"),
                           json={"aircraft": code, "part": part, "due_date": "2026-12-01"})
    assert response.status_code == 201


def test_every_created_work_order_is_audited(client, auth, db):
    code, part, _ = _free_pair(client, auth, db)
    client.post("/api/v1/work-orders", headers=auth("officer"),
                json={"aircraft": code, "part": part, "due_date": "2026-12-01",
                      "notes": "audit probe"})
    trail = client.get("/api/v1/audit?entity=work_order&limit=5",
                       headers=auth("commander")).json()["items"]
    assert any(row["after"] and row["after"].get("notes") == "audit probe"
               for row in trail)
