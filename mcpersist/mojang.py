"""Looks up Minecraft usernames/UUIDs via Mojang's public APIs, used to whitelist/op
the world owner during setup."""

from urllib.parse import quote

import requests


def username_for_uuid(uuid_dashed):
    # Network failures return None rather than propagating, same as a 404 already
    # does: every caller treats None as "couldn't identify the owner" and degrades
    # to leaving the whitelist off with an explanation. Letting a dropped
    # connection raise instead would abort setup partway through - after the world
    # has already been copied - over something that has a graceful answer.
    try:
        uuid_nodash = uuid_dashed.replace("-", "")
        resp = requests.get(
            f"https://sessionserver.mojang.com/session/minecraft/profile/{quote(uuid_nodash, safe='')}", timeout=15
        )
        if resp.status_code != 200:
            return None
        return resp.json().get("name")
    except (requests.RequestException, ValueError):
        return None


def uuid_for_username(username):
    """For a brand-new world there's no player data to read an owner UUID from, so the
    owner is resolved from a typed-in username instead. Returns (uuid_dashed, name) or
    None if the account doesn't exist."""
    try:
        resp = requests.get(f"https://api.mojang.com/users/profiles/minecraft/{quote(username, safe='')}", timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json()
    except (requests.RequestException, ValueError):  # see username_for_uuid
        return None
    raw = data.get("id")
    name = data.get("name")
    if not raw or not name:
        return None
    dashed = f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"
    return dashed, name
