"""Looks up Minecraft usernames/UUIDs via Mojang's public APIs, used to whitelist/op
the world owner during setup.

Both return None on any failure, network included: callers treat None as "couldn't
identify the owner" and carry on with an explanation, rather than aborting setup
after the world has already been copied."""

import uuid
from urllib.parse import quote

from . import net


def username_for_uuid(uuid_dashed):
    try:
        url = f"https://sessionserver.mojang.com/session/minecraft/profile/{quote(uuid_dashed.replace('-', ''), safe='')}"
        return net.get_json(url, timeout=15).get("name")
    except (*net.ERRORS, ValueError):
        return None


def uuid_for_username(username):
    """For a brand-new world, which has no player data to read an owner UUID from.
    Returns (uuid_dashed, name), or None if the account doesn't exist."""
    try:
        data = net.get_json(f"https://api.mojang.com/users/profiles/minecraft/{quote(username, safe='')}", timeout=15)
        return str(uuid.UUID(data["id"])), data["name"]
    except (*net.ERRORS, ValueError, KeyError, TypeError):
        return None
