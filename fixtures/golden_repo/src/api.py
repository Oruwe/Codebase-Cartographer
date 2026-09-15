"""API routes for the golden fixture."""
from src.db import UserTable


def get_user_route(request):
    table = UserTable()
    return table.find_by_email(request)


def health_route(request):
    return "ok"
