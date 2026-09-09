"""Looks up a Minecraft username from a UUID via Mojang's public session-server API,
used to whitelist/op the world owner during setup."""

import requests


def username_for_uuid(uuid_dashed):
    uuid_nodash = uuid_dashed.replace("-", "")
    resp = requests.get(
        f"https://sessionserver.mojang.com/session/minecraft/profile/{uuid_nodash}", timeout=15
    )
    if resp.status_code != 200:
        return None
    return resp.json().get("name")
