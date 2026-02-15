from src.zubot.core.central_db_queue import CentralDbQueue


def test_central_db_queue_read_and_write(tmp_path):
    db_path = tmp_path / "central.db"
    queue = CentralDbQueue(db_path=db_path, busy_timeout_ms=2000)

    create = queue.execute(sql="CREATE TABLE IF NOT EXISTS demo(id INTEGER PRIMARY KEY, name TEXT);", read_only=False)
    assert create["ok"] is True

    ins = queue.execute(sql="INSERT INTO demo(name) VALUES (?);", params=["alice"], read_only=False)
    assert ins["ok"] is True

    read = queue.execute(sql="SELECT id, name FROM demo ORDER BY id ASC;", read_only=True)
    assert read["ok"] is True
    assert read["row_count"] == 1
    assert read["rows"][0]["name"] == "alice"

    blocked = queue.execute(sql="DELETE FROM demo;", read_only=True)
    assert blocked["ok"] is False

    queue.stop()

