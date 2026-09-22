
from app import db


def main() -> None:
    conn = db.connect()
    applied = db.migrate(conn)
    conn.commit()
    if applied:
        print("应用迁移：" + ", ".join(applied))
    print(f"数据库迁移完成：{db.default_database_path()}")


if __name__ == "__main__":
    main()
