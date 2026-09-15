"""Database layer for the golden fixture."""
import sqlite3


def connect(url):
    return sqlite3.connect(url)


class UserTable:
    def find_by_email(self, email):
        return query_users(email)


def query_users(email):
    conn = connect(":memory:")
    return conn.execute("select 1")
