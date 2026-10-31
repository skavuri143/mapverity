"""Load JSON Lines documents into a MongoDB/DocumentDB collection (demo helper)."""

import json
import sys

from pymongo import MongoClient

path, uri, db, coll = sys.argv[1:5]
with open(path, encoding="utf-8") as fh:
    docs = [json.loads(line) for line in fh if line.strip()]
c = MongoClient(uri)[db][coll]
c.delete_many({})
c.insert_many(docs)
print(f"loaded {len(docs)} documents into {db}.{coll}")
