"""db.connect — single connection helper for all db/ops/* modules.

Use:
  from task_hounds_api.db import connect, init_db
  with connect() as db:
      rows = db.execute("SELECT * FROM ...").fetchall()
"""
from task_hounds_api.db import connect, init_db, reset_db, DB_PATH  # noqa: F401
