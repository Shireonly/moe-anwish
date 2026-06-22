#!/usr/bin/env python3
"""
awishr Chatroom - Local Development Server
"""

import asyncio
import json
import time
import uuid
import html as html_module
import os
from pathlib import Path
from dataclasses import dataclass, field

import aiohttp
from aiohttp import web, WSMsgType

# =============================================================================
# Configuration
# =============================================================================
HOST = "0.0.0.0"
PORT = 8766
MAX_MESSAGES_PER_ROOM = 100
MESSAGE_RATE_LIMIT = 0.5
MAX_USERNAME_LENGTH = 20
MAX_MESSAGE_LENGTH = 500

ADMIN_PASSWORD = "xrdqcFPvIb1TJGxlccfe5LkE"

SENSITIVE_WORDS = [
    "法轮功", "六四", "天安门", "台湾独立", "藏独", "疆独",
    "分裂国家", "颠覆国家", "邪教",
]

STATIC_DIR = Path(__file__).parent / "static"
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
BAN_FILE = DATA_DIR / "banned_clients.json"

# Avatar colors (pastel / light tones for white silhouette)
AVATAR_COLORS = [
    "#87CEEB", "#98D8C8", "#F7DC6F", "#BB8FCE", "#85C1E9",
    "#F1948A", "#82E0AA", "#F8C471", "#AED6F1", "#D7BDE2",
    "#A3E4D7", "#FAD7A0", "#FADBD8", "#D5F5E3", "#FCF3CF",
    "#D6EAF8", "#E8DAEF", "#D1F2EB", "#FAE5D3", "#FDEDEC",
    "#E8F6F3", "#FEF9E7", "#EBF5FB", "#F4ECF7", "#EAFAF1",
    "#FEF5E7", "#FDEBD0", "#F9EBEA", "#EAF2F8", "#F5EEF8",
    "#E8F8F5", "#FFF9C4", "#B39DDB", "#81D4FA", "#A5D6A7",
    "#FFCC80", "#EF9A9A", "#90CAF9", "#A5D6A7", "#FFE082",
    "#CE93D8", "#80DEEA", "#FFAB91", "#C5E1A5", "#FFE0B2",
    "#B388FF", "#4DD0E1", "#FF7043", "#69F0AE", "#FFD54F",
    "#E040FB", "#18FFFF", "#FF6E40", "#76FF03", "#FFAB00",
    "#D500F9", "#00E5FF", "#FF3D00", "#00E676", "#FFC400",
    "#AA00FF", "#00B8D4", "#DD2C00", "#00C853", "#FFD600",
]
_avatar_color_idx = 0


def next_avatar_color():
    global _avatar_color_idx
    color = AVATAR_COLORS[_avatar_color_idx % len(AVATAR_COLORS)]
    _avatar_color_idx += 1
    return color


# =============================================================================
# Global nickname registry (case-insensitive) + ban list
# =============================================================================
# Ban persistence: dict format {client_id: {timestamp, reason}}
# Backward-compatible with old list-of-strings format
def _load_bans():
    if BAN_FILE.exists():
        try:
            with open(BAN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                # Old format: list of client_id strings
                return {cid: {"timestamp": 0, "reason": "管理员封禁"} for cid in data}
            elif isinstance(data, dict):
                return data
        except Exception:
            return {}
    return {}


def _save_bans():
    try:
        with open(BAN_FILE, "w", encoding="utf-8") as f:
            json.dump(_banned_clients, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


_used_nicknames: set[str] = set()
_banned_clients: dict[str, dict] = _load_bans()  # client_id -> {timestamp, reason}


def is_nickname_available(name: str) -> bool:
    return name.lower() not in _used_nicknames


def register_nickname(name: str):
    _used_nicknames.add(name.lower())


def unregister_nickname(name: str):
    _used_nicknames.discard(name.lower())


def is_client_banned(client_id: str) -> bool:
    return client_id in _banned_clients


def ban_client(client_id: str, reason: str = "管理员封禁"):
    _banned_clients[client_id] = {"timestamp": int(time.time()), "reason": reason}
    _save_bans()


def unban_client(client_id: str) -> bool:
    if client_id in _banned_clients:
        del _banned_clients[client_id]
        _save_bans()
        return True
    return False


def get_banned_clients():
    return [{"client_id": cid, **info} for cid, info in _banned_clients.items()]


def generate_visitor_name(sid: str) -> str:
    base = f"游客{sid}"
    attempt = base
    suffix = 0
    while not is_nickname_available(attempt):
        suffix += 1
        attempt = f"{base}{suffix}"
    register_nickname(attempt)
    return attempt


# =============================================================================
# Bubble colors (light/pastel only, ~64 options)
# =============================================================================
BUBBLE_COLORS = [
    "#FFF0F0", "#FFE8E8", "#FFE0E0", "#FFD8D8", "#FFD0D0",
    "#FFF5E6", "#FFECD2", "#FFE3C4", "#FFDAB9", "#FFD1A8",
    "#FFF9E6", "#FFF3CC", "#FFECB3", "#FFE599", "#FFDF80",
    "#F0FFF0", "#E6FFE6", "#DCFFDC", "#D2FFD2", "#C8FFC8",
    "#E6FFF9", "#CCFFF3", "#B3FFE7", "#99FFDB", "#80FFCF",
    "#F0F8FF", "#E6F2FF", "#DCEAFF", "#D2E2FF", "#C8DAFF",
    "#F5F0FF", "#EBE0FF", "#E0D0FF", "#D6C0FF", "#CCB0FF",
    "#FFF0F5", "#FFE0EB", "#FFD0E1", "#FFC0D7", "#FFB0CD",
    "#F0FFFF", "#E0FFFF", "#D0FFFF", "#C0FFFF", "#B0FFFF",
    "#F5FFF0", "#EBFFE6", "#E0FFD0", "#D6FFC0", "#CCFFB0",
    "#FFF5F0", "#FFEBE6", "#FFE0D0", "#FFD6C0", "#FFCCB0",
    "#F0F5FF", "#E6EBFF", "#DCE0FF", "#D2D6FF", "#C8CCFF",
]


# =============================================================================
# Room Manager
# =============================================================================
class ChatRoom:
    def __init__(self, room_id: str, name: str):
        self.room_id = room_id
        self.name = name
        self.messages: list[dict] = []
        self.users: dict[str, dict] = {}
        self.created_at = time.time()

    def add_message(self, msg: dict):
        self.messages.append(msg)
        if len(self.messages) > MAX_MESSAGES_PER_ROOM:
            self.messages = self.messages[-MAX_MESSAGES_PER_ROOM:]

    def to_dict(self):
        return {
            "id": self.room_id,
            "name": self.name,
            "online": len(self.users),
            "messages": len(self.messages),
        }


class RoomManager:
    def __init__(self):
        self.rooms: dict[str, ChatRoom] = {}
        self._init_default_rooms()

    def _init_default_rooms(self):
        self.rooms["room1"] = ChatRoom("room1", "聊天室 1")
        self.rooms["room2"] = ChatRoom("room2", "聊天室 2")

    def get_room(self, room_id: str) -> ChatRoom | None:
        return self.rooms.get(room_id)

    def get_all_rooms(self):
        return [room.to_dict() for room in self.rooms.values()]

    def user_left(self, session_id: str):
        for room in self.rooms.values():
            room.users.pop(session_id, None)

    def get_online_count(self):
        return sum(len(r.users) for r in self.rooms.values())

    def get_all_online_users(self):
        """Return list of {session_id, username, client_id, room} for all connected users."""
        users = []
        for room_id, room in self.rooms.items():
            for sid, user in room.users.items():
                users.append({
                    "session_id": sid,
                    "username": user.get("username", "?"),
                    "client_id": user.get("client_id", ""),
                    "room": room_id,
                })
        return users

    def remove_user_by_session(self, session_id: str):
        for room in self.rooms.values():
            room.users.pop(session_id, None)


# =============================================================================
# Sensitive Word Filter
# =============================================================================
def filter_content(text: str) -> str:
    text = html_module.escape(text)
    for word in SENSITIVE_WORDS:
        text = text.replace(word, "***")
    return text


# =============================================================================
# Admin session store
# =============================================================================
_admin_sessions: set[str] = set()


# =============================================================================
# WebSocket Handler
# =============================================================================
room_manager = RoomManager()
_ws_clients: dict[str, web.WebSocketResponse] = {}  # session_id → ws


async def websocket_handler(request):
    ws = web.WebSocketResponse(max_msg_size=65536)
    await ws.prepare(request)

    session_id = str(uuid.uuid4())[:8]
    client_id = ""  # will be sent from client on connect
    username = generate_visitor_name(session_id)
    current_room: str | None = None
    last_msg_time = 0.0
    avatar_color = next_avatar_color()
    avatar_style = 0
    bubble_color = BUBBLE_COLORS[session_id.__hash__() % len(BUBBLE_COLORS)]
    is_admin = False

    _ws_clients[session_id] = ws

    await ws.send_json({
        "type": "welcome",
        "session_id": session_id,
        "username": username,
        "rooms": room_manager.get_all_rooms(),
        "online_total": room_manager.get_online_count(),
        "avatar_color": avatar_color,
        "avatar_style": avatar_style,
        "bubble_color": bubble_color,
        "bubble_colors": BUBBLE_COLORS,
    })

    await broadcast_room_list()

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                msg_type = data.get("type")

                if msg_type == "identify":
                    client_id = data.get("client_id", "")
                    # Check ban
                    if client_id and is_client_banned(client_id):
                        await ws.send_json({
                            "type": "banned",
                            "message": "你已被管理员封禁，无法进入聊天室",
                        })
                        await ws.close()
                        break
                    continue

                if msg_type == "chat":
                    if current_room is None:
                        await ws.send_json({
                            "type": "error",
                            "message": "请先选择一个房间",
                        })
                        continue

                    now = time.time()
                    if now - last_msg_time < MESSAGE_RATE_LIMIT:
                        await ws.send_json({
                            "type": "error",
                            "message": "发送太快了，请稍后再试",
                        })
                        continue
                    last_msg_time = now

                    content = data.get("content", "").strip()
                    if not content or len(content) > MAX_MESSAGE_LENGTH:
                        continue

                    content = filter_content(content)

                    msg_payload = {
                        "type": "chat",
                        "username": username,
                        "content": content,
                        "timestamp": int(time.time()),
                        "session_id": session_id,
                        "avatar_color": avatar_color,
                        "avatar_style": avatar_style,
                        "bubble_color": bubble_color,
                    }

                    room = room_manager.get_room(current_room)
                    if room:
                        room.add_message(msg_payload)
                        await broadcast_to_room(current_room, msg_payload)

                elif msg_type == "join_room":
                    new_room = data.get("room_id")
                    if new_room and room_manager.get_room(new_room):
                        if current_room is not None:
                            old_room = room_manager.get_room(current_room)
                            if old_room:
                                old_room.users.pop(session_id, None)
                                await broadcast_to_room(current_room, {
                                    "type": "system",
                                    "message": f"{username} 离开了房间",
                                    "timestamp": int(time.time()),
                                })

                        current_room = new_room
                        room = room_manager.get_room(current_room)
                        if room:
                            room.users[session_id] = {
                                "username": username,
                                "ws": ws,
                                "avatar_color": avatar_color,
                                "avatar_style": avatar_style,
                                "bubble_color": bubble_color,
                                "client_id": client_id,
                                "session_id": session_id,
                            }
                            await ws.send_json({
                                "type": "room_history",
                                "room_id": current_room,
                                "messages": room.messages,
                                "online": len(room.users),
                            })
                            await broadcast_to_room(current_room, {
                                "type": "system",
                                "message": f"{username} 进入了房间",
                                "timestamp": int(time.time()),
                            })

                        await broadcast_room_list()

                elif msg_type == "set_username":
                    new_name = data.get("username", "").strip()
                    if 1 <= len(new_name) <= MAX_USERNAME_LENGTH:
                        new_name_lower = new_name.lower()
                        current_lower = username.lower()
                        if new_name_lower == current_lower:
                            username = new_name
                        elif is_nickname_available(new_name):
                            unregister_nickname(username)
                            register_nickname(new_name)
                            username = new_name
                        else:
                            await ws.send_json({
                                "type": "error",
                                "message": f"昵称「{new_name}」已被使用，请换一个",
                            })
                            continue

                        username = filter_content(username)

                        room = room_manager.get_room(current_room) if current_room else None
                        if room and session_id in room.users:
                            room.users[session_id]["username"] = username

                        await ws.send_json({
                            "type": "username_set",
                            "username": username,
                        })

                elif msg_type == "set_bubble_color":
                    color = data.get("color", "").strip().lower()
                    if color in BUBBLE_COLORS or (color.startswith("#") and len(color) == 7):
                        bubble_color = color
                        room = room_manager.get_room(current_room) if current_room else None
                        if room and session_id in room.users:
                            room.users[session_id]["bubble_color"] = bubble_color
                        await ws.send_json({
                            "type": "bubble_set",
                            "color": bubble_color,
                        })

                elif msg_type == "set_avatar_style":
                    style = data.get("style", 0)
                    if isinstance(style, int) and 0 <= style <= 11:
                        avatar_style = style
                        room = room_manager.get_room(current_room) if current_room else None
                        if room and session_id in room.users:
                            room.users[session_id]["avatar_style"] = avatar_style
                        await ws.send_json({
                            "type": "avatar_style_set",
                            "style": avatar_style,
                        })

                elif msg_type == "admin_auth":
                    pwd = data.get("password", "")
                    if pwd == ADMIN_PASSWORD:
                        is_admin = True
                        _admin_sessions.add(session_id)
                        await ws.send_json({"type": "admin_auth_ok"})
                    else:
                        await ws.send_json({
                            "type": "error",
                            "message": "管理员密码错误",
                        })

                elif msg_type == "admin_list":
                    if is_admin or session_id in _admin_sessions:
                        await ws.send_json({
                            "type": "admin_user_list",
                            "users": room_manager.get_all_online_users(),
                        })
                    else:
                        await ws.send_json({
                            "type": "error",
                            "message": "你不是管理员",
                        })

                elif msg_type == "admin_kick":
                    if is_admin or session_id in _admin_sessions:
                        target_id = data.get("target_session_id", "")
                        target_client = data.get("target_client_id", "")
                        # Ban the client
                        if target_client:
                            ban_client(target_client)
                        # Disconnect and remove
                        target_ws = _ws_clients.get(target_id)
                        if target_ws:
                            try:
                                await target_ws.send_json({
                                    "type": "kicked",
                                    "message": f"你已被管理员踢出 (client: {target_client[:6]}...)",
                                })
                                await target_ws.close()
                            except Exception:
                                pass
                        room_manager.remove_user_by_session(target_id)
                        # Remove from ws clients
                        _ws_clients.pop(target_id, None)
                        await broadcast_room_list()
                        await ws.send_json({"type": "admin_kick_ok"})
                    else:
                        await ws.send_json({
                            "type": "error",
                            "message": "你不是管理员",
                        })

                elif msg_type == "admin_delete_message":
                    if is_admin or session_id in _admin_sessions:
                        target_room = data.get("room_id", "")
                        target_ts = data.get("timestamp", 0)
                        room = room_manager.get_room(target_room)
                        if room:
                            before = len(room.messages)
                            room.messages = [m for m in room.messages if m.get("timestamp") != target_ts]
                            if len(room.messages) < before:
                                await broadcast_to_room(target_room, {
                                    "type": "delete_message",
                                    "timestamp": target_ts,
                                })
                                await ws.send_json({"type": "delete_ok"})
                            else:
                                await ws.send_json({"type": "error", "message": "未找到该消息"})
                    else:
                        await ws.send_json({"type": "error", "message": "你不是管理员"})

                elif msg_type == "admin_unban":
                    if is_admin or session_id in _admin_sessions:
                        target_client = data.get("target_client_id", "")
                        if target_client and unban_client(target_client):
                            await ws.send_json({"type": "unban_ok", "client_id": target_client})
                        else:
                            await ws.send_json({"type": "error", "message": "未找到该封禁记录"})
                    else:
                        await ws.send_json({"type": "error", "message": "你不是管理员"})

                elif msg_type == "admin_ban_list":
                    if is_admin or session_id in _admin_sessions:
                        await ws.send_json({
                            "type": "admin_ban_list",
                            "bans": get_banned_clients(),
                        })
                    else:
                        await ws.send_json({"type": "error", "message": "你不是管理员"})

                elif msg_type == "admin_load_messages":
                    if is_admin or session_id in _admin_sessions:
                        all_msgs = []
                        for room_id, room in room_manager.rooms.items():
                            for msg in room.messages:
                                m = dict(msg)
                                m["_room"] = room_id
                                m["_room_name"] = room.name
                                all_msgs.append(m)
                        all_msgs.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
                        await ws.send_json({
                            "type": "admin_message_history",
                            "messages": all_msgs,
                        })
                    else:
                        await ws.send_json({"type": "error", "message": "你不是管理员"})

                elif msg_type == "admin_search_messages":
                    if is_admin or session_id in _admin_sessions:
                        keyword = data.get("keyword", "").strip().lower()
                        if not keyword or len(keyword) < 1:
                            await ws.send_json({"type": "admin_search_result", "messages": [], "keyword": keyword})
                        else:
                            results = []
                            for room_id, room in room_manager.rooms.items():
                                for msg in room.messages:
                                    content = msg.get("content", "").lower()
                                    username = msg.get("username", "").lower()
                                    if keyword in content or keyword in username:
                                        m = dict(msg)
                                        m["_room"] = room_id
                                        m["_room_name"] = room.name
                                        results.append(m)
                            results.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
                            await ws.send_json({
                                "type": "admin_search_result",
                                "messages": results,
                                "keyword": keyword,
                            })
                    else:
                        await ws.send_json({"type": "error", "message": "你不是管理员"})

                elif msg_type == "ping":
                    await ws.send_json({"type": "pong"})

            elif msg.type == WSMsgType.ERROR:
                print(f"WS Error: {ws.exception()}")

    finally:
        _ws_clients.pop(session_id, None)
        _admin_sessions.discard(session_id)
        unregister_nickname(username)
        room_manager.user_left(session_id)
        if current_room is not None:
            await broadcast_to_room(current_room, {
                "type": "system",
                "message": f"{username} 离开了房间",
                "timestamp": int(time.time()),
            })
        await broadcast_room_list()

    return ws


async def broadcast_to_room(room_id: str, data: dict):
    room = room_manager.get_room(room_id)
    if not room:
        return

    disconnected = []
    for sid, user in room.users.items():
        try:
            await user["ws"].send_json(data)
        except (ConnectionResetError, ConnectionAbortedError):
            disconnected.append(sid)

    for sid in disconnected:
        room.users.pop(sid, None)

    # Also relay to admin sessions
    if data.get("type") == "chat":
        admin_data = dict(data)
        admin_data["_room"] = room_id
        # Attach client/session info from room user data
        if data.get("session_id"):
            for suid, su in room.users.items():
                if su.get("session_id") == data["session_id"]:
                    admin_data["client_id"] = su.get("client_id", "")
                    break
        for sid in list(_admin_sessions):
            aws = _ws_clients.get(sid)
            if aws and not aws.closed:
                try:
                    await aws.send_json(admin_data)
                except Exception:
                    _admin_sessions.discard(sid)


async def broadcast_room_list():
    data = {
        "type": "room_list",
        "rooms": room_manager.get_all_rooms(),
        "online_total": room_manager.get_online_count(),
    }

    for room in room_manager.rooms.values():
        disconnected = []
        for sid, user in room.users.items():
            try:
                await user["ws"].send_json(data)
            except (ConnectionResetError, ConnectionAbortedError):
                disconnected.append(sid)
        for sid in disconnected:
            room.users.pop(sid, None)


async def index_handler(request):
    return web.FileResponse(STATIC_DIR / "index.html")


async def admin_handler(request):
    return web.FileResponse(STATIC_DIR / "admin.html")


async def health_handler(request):
    """健康检查端点 - Cloudflare / 监控用"""
    return web.json_response({
        "status": "ok",
        "online": room_manager.get_online_count(),
        "rooms": len(room_manager.rooms),
        "timestamp": int(time.time()),
    })


def main():
    app = web.Application(client_max_size=65536)
    app.router.add_get("/", index_handler)
    app.router.add_get("/admin", admin_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/ws", websocket_handler)

    static_path = STATIC_DIR
    if static_path.exists():
        app.router.add_static("/", path=static_path, show_index=False)

    print(f"""
╔══════════════════════════════════════════╗
║     awishr Chatroom - Local Server      ║
║──────────────────────────────────────────║
║  Address:  http://localhost:{PORT}       ║
║  Admin password: {ADMIN_PASSWORD}        ║
║                                          ║
║  Press Ctrl+C to stop                    ║
╚══════════════════════════════════════════╝
    """)

    web.run_app(app, host=HOST, port=PORT,
                handle_signals=True,
                shutdown_timeout=10,
                keepalive_timeout=75,
                tcp_keepalive=True)


if __name__ == "__main__":
    main()
