"""Creates the MongoDB collections (with validation) and indexes. NO sample data is added:
the collections fill up as people use the app.

Run from the project folder:
    python scripts/setup_mongo.py            create what is missing, update validators and indexes
    python scripts/setup_mongo.py --reset    ALSO delete all existing alerts and audit events first
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_config, load_env  # noqa: E402

try:
    from pymongo import ASCENDING, DESCENDING, GEOSPHERE, TEXT, MongoClient
    from pymongo.errors import PyMongoError
except ImportError:
    sys.exit("pymongo is not installed. Run:  pip install -r requirements.txt")

load_env()
cfg = get_config()

ALERT_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["flag_id", "txn_id", "rule_name", "risk_score", "severity", "status", "created_at"],
        "properties": {
            "flag_id": {"bsonType": "number", "description": "fraud_flags.flag_id in MySQL"},
            "txn_id": {"bsonType": "number", "description": "transactions.txn_id in MySQL"},
            "rule_name": {"bsonType": "string"},
            "risk_score": {"bsonType": "number", "minimum": 1, "maximum": 100},
            "severity": {"enum": ["low", "medium", "high"]},
            "status": {"enum": ["open", "confirmed", "dismissed"]},
            "context": {"bsonType": "object"},
            "analyst_notes": {
                "bsonType": "array",
                "items": {
                    "bsonType": "object",
                    "required": ["by", "note", "at"],
                    "properties": {"by": {"bsonType": "number"}, "note": {"bsonType": "string"}, "at": {"bsonType": "date"}},
                },
            },
            "created_at": {"bsonType": "date"},
        },
    }
}

AUDIT_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["event_type", "actor_user_id", "role", "entity", "at"],
        "properties": {
            "event_type": {"enum": ["SIGNUP", "LOGIN", "DEPOSIT", "ACCOUNT_OPENED", "TRANSFER",
                                    "FLAG_REVIEWED", "ACCOUNT_FROZEN", "ACCOUNT_UNFROZEN"]},
            "actor_user_id": {"bsonType": "number"},
            "role": {"enum": ["customer", "admin", "anonymous"]},
            "entity": {"bsonType": "string"},
            "entity_id": {"bsonType": "number"},
            "details": {"bsonType": "object"},
            "ip": {"bsonType": "string"},
            "at": {"bsonType": "date"},
        },
    }
}

try:
    client = MongoClient(cfg["MONGODB_URI"], serverSelectionTimeoutMS=3000, tz_aware=True)
    client.admin.command("ping")
except PyMongoError as exc:
    sys.exit(f"Cannot reach MongoDB at {cfg['MONGODB_URI']}: {exc}\nIs the MongoDB service running?")

db = client[cfg["MONGODB_DB"]]

if "--reset" in sys.argv:
    db.drop_collection("fraud_alerts")
    db.drop_collection("audit_logs")
    print("Existing alerts and audit events deleted.")

existing = set(db.list_collection_names())
for name, validator in (("fraud_alerts", ALERT_VALIDATOR), ("audit_logs", AUDIT_VALIDATOR)):
    if name in existing:
        db.command("collMod", name, validator=validator, validationAction="error")
    else:
        db.create_collection(name, validator=validator, validationAction="error")

db.fraud_alerts.create_index([("flag_id", ASCENDING)], unique=True)
db.fraud_alerts.create_index([("txn_id", ASCENDING)])
db.fraud_alerts.create_index([("status", ASCENDING), ("risk_score", DESCENDING)])
db.fraud_alerts.create_index([("context.geo.location", GEOSPHERE)])
db.fraud_alerts.create_index([("analyst_notes.note", TEXT)])
db.audit_logs.create_index([("at", DESCENDING)])
db.audit_logs.create_index([("entity", ASCENDING), ("entity_id", ASCENDING)])
db.audit_logs.create_index([("actor_user_id", ASCENDING), ("at", DESCENDING)])

print(f"MongoDB '{cfg['MONGODB_DB']}' is ready: collections fraud_alerts ({db.fraud_alerts.count_documents({})} documents) "
      f"and audit_logs ({db.audit_logs.count_documents({})} documents), with validators and indexes.")
