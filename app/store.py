"""All MongoDB access lives here: flexible fraud-alert context, analyst notes and the audit trail."""
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc)


class MongoStore:
    def __init__(self, uri, db_name):
        self.uri = uri
        self.db_name = db_name
        self._client = None

    def _db(self):
        if self._client is None:
            from pymongo import MongoClient  # imported lazily so tests can run without the driver

            self._client = MongoClient(self.uri, serverSelectionTimeoutMS=2000, tz_aware=True)
        return self._client[self.db_name]

    # ---------------------------------------------------------------- alerts
    def store_alert(self, alert):
        """alert: dict with flag_id, txn_id, rule_name, risk_score, severity, context."""
        doc = {
            "flag_id": alert["flag_id"],
            "txn_id": alert["txn_id"],
            "rule_name": alert["rule_name"],
            "risk_score": alert["risk_score"],
            "severity": alert["severity"],
            "status": "open",
            "context": alert["context"],
            "analyst_notes": [],
            "created_at": _now(),
        }
        self._db().fraud_alerts.insert_one(doc)

    def load_alerts(self, flag_ids):
        """Returns {flag_id: {"context": {...}, "analyst_notes": [...]}}."""
        if not flag_ids:
            return {}
        cursor = self._db().fraud_alerts.find(
            {"flag_id": {"$in": list(flag_ids)}},
            {"_id": 0, "flag_id": 1, "context": 1, "analyst_notes": 1},
        )
        return {
            d["flag_id"]: {"context": d.get("context"), "analyst_notes": d.get("analyst_notes", [])}
            for d in cursor
        }

    def review_alert(self, flag_id, status, note, user_id):
        update = {"$set": {"status": status}}
        if note:
            update["$push"] = {"analyst_notes": {"by": user_id, "note": note, "at": _now()}}
        self._db().fraud_alerts.update_one({"flag_id": flag_id}, update)

    def alerts_by_city(self, limit=8):
        """Aggregation pipeline: number of alerts and average risk per city."""
        pipeline = [
            {"$match": {"context.geo.city": {"$exists": True}}},
            {
                "$group": {
                    "_id": "$context.geo.city",
                    "alerts": {"$sum": 1},
                    "avg_risk": {"$avg": "$risk_score"},
                }
            },
            {"$sort": {"alerts": -1, "_id": 1}},
            {"$limit": limit},
        ]
        return [
            {"city": d["_id"], "alerts": d["alerts"], "avg_risk": round(d["avg_risk"])}
            for d in self._db().fraud_alerts.aggregate(pipeline)
        ]

    # ---------------------------------------------------------------- audit trail
    def log_audit(self, event_type, actor_user_id, role, entity, entity_id=None, details=None, ip="unknown"):
        doc = {
            "event_type": event_type,
            "actor_user_id": actor_user_id,
            "role": role,
            "entity": entity,
            "details": details or {},
            "ip": ip or "unknown",
            "at": _now(),
        }
        if entity_id is not None:
            doc["entity_id"] = entity_id
        self._db().audit_logs.insert_one(doc)

    def recent_audit(self, limit=12):
        cursor = self._db().audit_logs.find({}, {"_id": 0}).sort("at", -1).limit(limit)
        return list(cursor)
