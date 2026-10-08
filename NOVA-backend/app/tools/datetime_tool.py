"""get_current_datetime() — local date and time."""

from datetime import datetime


async def get_current_datetime() -> str:
    now = datetime.now().astimezone()
    return now.strftime("It is %A, %B %d, %Y, %I:%M %p (%Z).")
