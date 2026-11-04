"""Storage backends. Item layout (DynamoDB): pk='USDMXN#FIX', sk='YYYY-MM-DD', rate=Decimal."""
import os
from datetime import date
from decimal import Decimal

PK = "USDMXN#FIX"


class MemoryStore:
    def __init__(self) -> None:
        self._d: dict[date, Decimal] = {}

    def put_many(self, rows: dict[date, Decimal]) -> None:
        self._d.update(rows)

    def range(self, start: date, end: date) -> dict[date, Decimal]:
        return {d: r for d, r in self._d.items() if start <= d <= end}


class DynamoStore:
    def __init__(self, table_name: str) -> None:
        import boto3  # imported lazily so local dev/tests don't need AWS config
        self._t = boto3.resource("dynamodb").Table(table_name)

    def put_many(self, rows: dict[date, Decimal]) -> None:
        with self._t.batch_writer(overwrite_by_pkeys=["pk", "sk"]) as b:
            for d, r in rows.items():
                b.put_item(Item={"pk": PK, "sk": d.isoformat(), "rate": r})

    def range(self, start: date, end: date) -> dict[date, Decimal]:
        from boto3.dynamodb.conditions import Key
        resp = self._t.query(KeyConditionExpression=Key("pk").eq(PK) & Key("sk").between(
            start.isoformat(), end.isoformat()))
        return {date.fromisoformat(i["sk"]): i["rate"] for i in resp["Items"]}


def from_env():
    table = os.environ.get("TABLE_NAME")
    return DynamoStore(table) if table else MemoryStore()
