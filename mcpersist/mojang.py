"""Looks up Minecraft usernames/UUIDs via Mojang's public APIs, used to whitelist/op
the world owner during setup."""

import requests


def username_for_uuid(uuid_dashed):
    uuid_nodash = uuid_dashed.replace("-", "")
    resp = requests.get(
        f"https://sessionserver.mojang.com/session/minecraft/profile/{uuid_nodash}", timeout=15
    )
    if resp.status_code != 200:
        return None
    return resp.json().get("name")


def uuid_for_username(username):
    """For a brand-new world there's no player data to read an owner UUID from, so the
    owner is resolved from a typed-in username instead. Returns (uuid_dashed, name) or
    None if the account doesn't exist."""
    resp = requests.get(f"https://api.mojang.com/users/profiles/minecraft/{username}", timeout=15)
    if resp.status_code != 200:
        return None
    data = resp.json()
    raw = data.get("id")
    name = data.get("name")
    if not raw or not name:
        return None
    dashed = f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"
    return dashed, name
