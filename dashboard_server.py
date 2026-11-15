# -*- coding: utf-8 -*-
# ============================================================
#  dashboard_server.py — Glory Engine Dashboard Backend (v6.3)
#  FIXED: /api/groups no longer crashes
#  FIXED: Added /api/settings, /api/admin_login, /api/logs
#  FIXED: restart_group function added
# ============================================================
import asyncio
import json
import os
import time
import traceback
import aiohttp
from aiohttp import web

# ==================== INJECTED GLOBALS ====================
bot_state = None
BOT_STATE = None
ACTIVE_GROUPS = None
CREATE_ACCOUNTS_FUNC = None
FETCH_GUILD_INFO = None
RUN_GROUP_CREATION = None
JOIN_GUILD_FUNC = None

DEFAULT_PASSWORD = "NGRAXX__"
DEFAULT_COUNT = 4

GUILD_API_BASE = "https://danger-guild-management-web.vercel.app"
_GUILD_CACHE: dict = {}
_GUILD_CACHE_TTL = 30.0
_sync_task = None
_score_task = None

# ==================== NEW: SETTINGS & STATE ====================
SETTINGS_FILE = "dashboard_settings.json"
DASHBOARD_SETTINGS = {
    "maintenance": False,
    "admin": {"username": "admin", "password": "Glory@2026!"},
    "launch_cost": 1,       
    "credit_price": 10,     
    "servers": ["IND", "BD", "PK", "ID", "BR", "ME"]
}

if os.path.exists(SETTINGS_FILE):
    try:
        with open(SETTINGS_FILE, "r") as f:
            saved = json.load(f)
            DASHBOARD_SETTINGS.update(saved)
    except Exception:
        pass

def save_settings():
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(DASHBOARD_SETTINGS, f, indent=4)
    except Exception:
        pass

# ==================== GUILD INFO NORMALIZER ====================
def _normalize_guild_info(raw: dict) -> dict:
    if not isinstance(raw, dict):
        return None
    leader = raw.get("GuildLeader") or {}
    if not isinstance(leader, dict):
        leader = {}
    officers = raw.get("Officers") or []
    if not isinstance(officers, list):
        officers = []
    gid = raw.get("GuildId")

    score = int(raw.get("TotalActivityPoints", 0) or raw.get("score", 0) or 0)
    weekly = int(raw.get("WeeklyActivityPoints", 0) or 0)

    return {
        "status": str(raw.get("status", "success")),
        "raw": raw,
        "clan_name": str(raw.get("GuildName") or f"Guild {gid or '?'}"),
        "guild_id": str(gid or ""),
        "guild_level": int(raw.get("GuildLevel", 1) or 1),
        "region": str(raw.get("GuildRegion", "") or ""),
        "leader_id": str(leader.get("Uid", "") or ""),
        "max_members": int(raw.get("MaxMembers", 0) or 0),
        "current_members": int(raw.get("CurrentMembers", 0) or 0),
        "score": score,
        "weekly_activity": weekly,
        "leader_name": str(leader.get("Name", "") or ""),
        "leader_level": int(leader.get("Level", 1) or 1),
        "leader_likes": int(leader.get("Likes", 0) or 0),
        "leader_br_points": int(leader.get("BrPoints", 0) or 0),
        "leader_br_rank": str(leader.get("BrRank", "") or ""),
        "leader_cs_rank": str(leader.get("CsRank", "") or ""),
        "slogan": str(raw.get("GuildSlogan", "") or ""),
        "frame_name": str(raw.get("FrameName", "") or ""),
        "avatar_name": str(raw.get("AvatarName", "") or ""),
        "creation_time": str(raw.get("CreationTime", "") or ""),
        "auto_approval": str(raw.get("AutoApprovalStatus", "OFF") or "OFF"),
        "officers_count": len(officers),
        "officers": officers,
        "last_updated": str(raw.get("last_updated", "") or ""),
    }

async def _ensure_normalized(info) -> dict:
    if not info or not isinstance(info, dict):
        return None
    if "clan_name" in info and "guild_level" in info:
        return info
    if str(info.get("status", "")).lower() == "success" or "GuildName" in info:
        return _normalize_guild_info(info)
    return None

# ==================== DEFAULT GUILD INFO FETCH ====================
async def _default_fetch_guild_info(guild_id: str, region: str):
    gid = str(guild_id or "").strip()
    if not gid.isdigit():
        return None
    reg = str(region or "IND").strip().upper()

    cache_key = f"{gid}:{reg}"
    now = time.time()
    hit = _GUILD_CACHE.get(cache_key)
    if hit and (now - hit["ts"]) < _GUILD_CACHE_TTL:
        return hit["data"]

    url = f"{GUILD_API_BASE}/guild?guild_id={gid}&region={reg}"
    headers = {"accept": "application/json", "user-agent": "Mozilla/5.0"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get(url, headers=headers) as r:
                if r.status != 200:
                    return None
                raw = await r.json()
                if not isinstance(raw, dict) or str(raw.get("status", "")).lower() != "success":
                    return None
                norm = _normalize_guild_info(raw)
                _GUILD_CACHE[cache_key] = {"ts": now, "data": norm}
                return norm
    except Exception:
        return None

async def _default_join_guild(guild_id, guest_uid, guest_password, region="IND", token=None):
    gid = str(guild_id or "").strip()
    gu = str(guest_uid or "").strip()
    gp = str(guest_password or "").strip()
    if not (gid.isdigit() and gu and gp):
        return False
    try:
        url = f"{GUILD_API_BASE}/join?guild_id={gid}&uid={gu}&password={gp}"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as s:
            async with s.get(url) as r:
                return r.status == 200
    except Exception:
        return False

# ==================== ACCOUNT SNAPSHOT ====================
def _account_snapshot(auth_uid: str, fallback: dict = None) -> dict:
    auth_uid = str(auth_uid)
    acc = None
    if BOT_STATE:
        game_id = BOT_STATE.auth_to_game_id.get(auth_uid)
        if game_id:
            acc = BOT_STATE.accounts.get(str(game_id))
    if acc:
        return {
            "uid": str(acc.get("uid", auth_uid)),
            "auth_uid": auth_uid,
            "nickname": acc.get("nickname", "Generating..."),
            "level": int(acc.get("level", 1) or 1),
            "gained_exp": int(acc.get("gained_exp", 0) or 0),
            "status": acc.get("status", "ONLINE"),
        }
    if fallback:
        return dict(fallback)
    return {
        "uid": auth_uid, "auth_uid": auth_uid, "nickname": "Generating...",
        "level": 1, "gained_exp": 0, "status": "GENERATING",
    }

def _refresh_group_accounts(group: dict) -> None:
    auth_uids = group.get("auth_uids", []) or []
    initial_map = {a["auth_uid"]: a for a in group.get("initial_accounts", [])}
    accounts = [_account_snapshot(au, fallback=initial_map.get(str(au))) for au in auth_uids]
    group["accounts"] = accounts

    if group.get("status") == "GENERATING" and accounts:
        if any(a.get("status") != "GENERATING" for a in accounts):
            group["status"] = "RUNNING"

# ==================== DEFAULT GROUP CREATION ====================
async def _default_run_group_creation(group_key: str, region: str, count: int, guild_id: str = None, password: str = None):
    group = ACTIVE_GROUPS.get(group_key)
    if not group or not isinstance(group, dict):
        return
    if not CREATE_ACCOUNTS_FUNC:
        group["status"] = "FAILED"
        group["error"] = "create_accounts function not configured"
        return

    pwd = password or DEFAULT_PASSWORD
    guild = guild_id or group.get("guild_id")
    try:
        # NOTE: Yahan count fix kiya gaya hai taaki backend par exact 4 hi bots banein
        created = await CREATE_ACCOUNTS_FUNC(region, guild, 4, pwd)
        if not created:
            group["status"] = "FAILED"
            group["error"] = "No accounts created"
            return

        auth_uids, initial = [], []
        for a in created:
            au = str(a.get("auth_uid", "")).strip()
            if not au: continue
            auth_uids.append(au)
            initial.append({
                "uid": str(a.get("account_id", au)),
                "auth_uid": au,
                "nickname": a.get("nickname", "Generating..."),
                "level": int(a.get("level", 1) or 1),
                "gained_exp": 0,
                "status": "ONLINE",
            })

        group["auth_uids"] = auth_uids
        group["initial_accounts"] = initial
        group["status"] = "RUNNING" if auth_uids else "FAILED"
    except Exception as e:
        traceback.print_exc()
        group["status"] = "FAILED"
        group["error"] = str(e)

# ==================== WORKER CANCELLATION ====================
def _cancel_group_workers(group: dict) -> None:
    if not BOT_STATE or not isinstance(group, dict):
        return
    for auth_uid in list(group.get("auth_uids", [])):
        au = str(auth_uid)
        game_id = BOT_STATE.auth_to_game_id.get(au)
        for key in (au, str(game_id) if game_id else ""):
            if not key: continue
            task = BOT_STATE.account_workers.pop(key, None)
            if task:
                try: task.cancel()
                except Exception: pass

# ==================== SYNC LOOPS ====================
async def _sync_loop():
    try:
        while True:
            if ACTIVE_GROUPS:
                for g in list(ACTIVE_GROUPS.values()):
                    if not isinstance(g, dict): continue
                    try: _refresh_group_accounts(g)
                    except Exception: pass
            await asyncio.sleep(3.0)
    except asyncio.CancelledError:
        pass

async def _score_updater_loop():
    try:
        while True:
            for g in list(ACTIVE_GROUPS.values()):
                if not isinstance(g, dict): continue
                if g.get("status") not in ("RUNNING", "GENERATING", "STOPPED"): continue
                try:
                    info = await FETCH_GUILD_INFO(g["guild_id"], g.get("region", "IND"))
                    if not info: continue
                    current_score = int(info.get("score", 0) or 0)
                    current_weekly = int(info.get("weekly_activity", 0) or 0)
                    initial_weekly = int(g.get("initial_weekly", current_weekly))
                    if initial_weekly == 0 and current_weekly > 0:
                        initial_weekly = current_weekly
                        g["initial_weekly"] = current_weekly

                    old_weekly = g.get("current_weekly", initial_weekly)
                    if current_weekly < old_weekly:
                        initial_weekly = 0
                        g["initial_weekly"] = 0

                    g["current_score"] = current_score
                    g["current_weekly"] = current_weekly
                    g["glory_pushed"] = max(0, current_weekly - initial_weekly)
                    g["current_members"] = int(info.get("current_members", 0) or 0)
                    g["max_members"] = int(info.get("max_members", 35) or 35)
                except Exception: pass
            await asyncio.sleep(30.0)
    except asyncio.CancelledError:
        pass

# ==================== ROUTE HANDLERS ====================
async def handle_index(request):
    base = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(base, "templates", "index.html"), os.path.join(base, "index.html")):
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return web.Response(text=f.read(), content_type="text/html", charset="utf-8")
            except Exception: continue
    return web.Response(text="<h1>Glory Engine</h1><p>index.html not found.</p>", content_type="text/html")

async def api_ping(request):
    return web.json_response({"pong": True, "ts": time.time()})

async def api_stats(request):
    accs = list(BOT_STATE.accounts.values()) if BOT_STATE else []
    return web.json_response({
        "total_accounts": len(accs),
        "online": sum(1 for a in accs if a.get("status") not in ("OFFLINE", "PAUSED")),
        "total_matches": BOT_STATE.total_matches if BOT_STATE else 0,
        "matches_started": BOT_STATE.matches_started if BOT_STATE else 0,
        "total_gained_exp": BOT_STATE.total_exp_gained if BOT_STATE else 0,
        "uptime": int(time.time() - BOT_STATE.start_time) if BOT_STATE else 0,
        "groups": len(ACTIVE_GROUPS or {}),
        "creating": bool(BOT_STATE.creating) if BOT_STATE else False,
    })

async def api_get_logs(request):
    logs = BOT_STATE.logs[-100:] if BOT_STATE else []
    return web.json_response({"logs": logs})

async def api_get_settings(request):
    return web.json_response(DASHBOARD_SETTINGS)

async def api_update_settings(request):
    try:
        data = await request.json()
        if "maintenance" in data: DASHBOARD_SETTINGS["maintenance"] = bool(data["maintenance"])
        if "launch_cost" in data: DASHBOARD_SETTINGS["launch_cost"] = int(data["launch_cost"])
        if "credit_price" in data: DASHBOARD_SETTINGS["credit_price"] = int(data["credit_price"])
        if "servers" in data: DASHBOARD_SETTINGS["servers"] = data["servers"]
        save_settings()
        return web.json_response({"ok": True, "settings": DASHBOARD_SETTINGS})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def api_admin_login(request):
    try:
        data = await request.json()
        admin = DASHBOARD_SETTINGS.get("admin", {})
        if data.get("username") == admin.get("username") and data.get("password") == admin.get("password"):
            return web.json_response({"ok": True})
        return web.json_response({"ok": False, "error": "Invalid credentials"})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def api_change_admin_pass(request):
    try:
        data = await request.json()
        new_user = data.get("username", "").strip()
        new_pass = data.get("password", "").strip()
        if not new_user or not new_pass:
            return web.json_response({"ok": False, "error": "Username and password required"})
        DASHBOARD_SETTINGS["admin"]["username"] = new_user
        DASHBOARD_SETTINGS["admin"]["password"] = new_pass
        save_settings()
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def api_get_groups(request):
    raw = list(ACTIVE_GROUPS.values()) if ACTIVE_GROUPS else []
    groups = []
    for g in raw:
        if not isinstance(g, dict): continue
        try: _refresh_group_accounts(g)
        except Exception: pass
        groups.append(g)

    groups.sort(key=lambda x: x.get("started_at", 0) or 0, reverse=True)
    out = []
    for g in groups:
        view = {k: v for k, v in g.items() if k not in ("initial_accounts", "auth_uids")}
        out.append(view)
    return web.json_response({"groups": out})

async def api_create_team(request):
    try:
        data = await request.json()
        region = str(data.get("region", "IND")).upper()
        guild_id = str(data.get("guild_id") or data.get("clan_id") or "").strip()
        count = int(data.get("count", DEFAULT_COUNT))
        password = str(data.get("password", DEFAULT_PASSWORD)).strip() or DEFAULT_PASSWORD

        if not guild_id or not guild_id.isdigit():
            return web.json_response({"ok": False, "error": "Valid numeric Guild ID required"})
        if BOT_STATE and BOT_STATE.creating:
            return web.json_response({"ok": False, "error": "Another creation in progress"})

        group_key = f"{guild_id}_{region}"
        existing = ACTIVE_GROUPS.get(group_key) if ACTIVE_GROUPS else None
        if existing and isinstance(existing, dict) and existing.get("status") in ("RUNNING", "GENERATING"):
            return web.json_response({"ok": False, "error": "Group already running"})

        info = await _default_fetch_guild_info(guild_id, region)
        
        if info:
            clan_name = info.get("clan_name") or f"Guild {guild_id}"
            level = int(info.get("guild_level", 1) or 1)
            leader_id = str(info.get("leader_id", "") or "")
            max_members = int(info.get("max_members", 35) or 35)
            current_members = int(info.get("current_members", 0) or 0)
            initial_score = int(info.get("score", 0) or 0)
            initial_weekly = int(info.get("weekly_activity", 0) or 0)
        else:
            clan_name = f"Guild {guild_id}"
            level, leader_id = 1, ""
            max_members, current_members = 35, 0
            initial_score = 0
            initial_weekly = 0

        if ACTIVE_GROUPS is None: return web.json_response({"ok": False, "error": "ACTIVE_GROUPS not bound"})

        ACTIVE_GROUPS[group_key] = {
            "group_key": group_key, "guild_id": guild_id, "group_name": clan_name,
            "clan_name": clan_name, "level": level, "leader_id": leader_id,
            "max_members": max_members, "current_members": current_members,
            "region": region, "initial_score": initial_score, "current_score": initial_score,
            "initial_weekly": initial_weekly, "current_weekly": initial_weekly, "glory_pushed": 0,
            "status": "GENERATING", "started_at": time.time(), "auth_uids": [],
            "initial_accounts": [], "accounts": [], "guild_info": info,
        }

        runner = RUN_GROUP_CREATION or _default_run_group_creation
        asyncio.create_task(runner(group_key, region, count, guild_id, password))

        return web.json_response({"ok": True, "msg": f"Launched for {clan_name}", "group_key": group_key, "guild_info": info})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def api_stop_group(request):
    try:
        data = await request.json()
        group_key = str(data.get("group_key", ""))
        g = ACTIVE_GROUPS.get(group_key) if ACTIVE_GROUPS else None
        if not g or not isinstance(g, dict):
            return web.json_response({"ok": False, "error": "Group not found"})

        _cancel_group_workers(g)
        g["status"] = "STOPPED"
        for a in g.get("accounts", []): a["status"] = "STOPPED"
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def api_restart_group(request):
    try:
        data = await request.json()
        group_key = str(data.get("group_key", ""))
        g = ACTIVE_GROUPS.get(group_key) if ACTIVE_GROUPS else None
        if not g or not isinstance(g, dict):
            return web.json_response({"ok": False, "error": "Group not found"})

        _cancel_group_workers(g)
        g["status"] = "GENERATING"
        g["started_at"] = time.time()
        g["accounts"] = []
        g["auth_uids"] = []
        g["initial_accounts"] = []

        g["initial_score"] = g.get("current_score", 0)
        g["initial_weekly"] = g.get("current_weekly", 0)
        g["glory_pushed"] = 0
        g.pop("error", None)

        region = g.get("region", "IND")
        guild_id = g.get("guild_id")

        runner = RUN_GROUP_CREATION or _default_run_group_creation
        asyncio.create_task(runner(group_key, region, DEFAULT_COUNT, guild_id, DEFAULT_PASSWORD))
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

# ==================== MIDDLEWARE & STARTUP ====================
@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        return web.Response(status=204, headers={
            "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type, Authorization",
        })
    try:
        response = await handler(request)
    except web.HTTPException as e:
        response = web.json_response({"ok": False, "error": e.reason or str(e)}, status=e.status)
    except Exception as e:
        traceback.print_exc()
        response = web.json_response({"ok": False, "error": str(e)}, status=500)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response

async def start_web_dashboard(host: str, port: int, bot_state_arg, active_groups: dict = None, create_accounts_func=None, fetch_info_func=None, run_creation_func=None, join_guild_func=None):
    global bot_state, BOT_STATE, ACTIVE_GROUPS, CREATE_ACCOUNTS_FUNC, FETCH_GUILD_INFO, RUN_GROUP_CREATION, JOIN_GUILD_FUNC, _sync_task, _score_task

    bot_state = bot_state_arg
    BOT_STATE = bot_state_arg
    ACTIVE_GROUPS = active_groups if active_groups is not None else {}
    CREATE_ACCOUNTS_FUNC = create_accounts_func
    FETCH_GUILD_INFO = fetch_info_func or _default_fetch_guild_info
    RUN_GROUP_CREATION = run_creation_func
    JOIN_GUILD_FUNC = join_guild_func or _default_join_guild

    app = web.Application(middlewares=[cors_middleware])

    # HTML Routes
    app.router.add_get("/", handle_index)
    app.router.add_get("/index.html", handle_index)
    
    # Bot & Stats APIs
    app.router.add_get("/api/ping", api_ping)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/api/logs", api_get_logs)
    app.router.add_get("/api/groups", api_get_groups)
    app.router.add_post("/api/create_team", api_create_team)
    app.router.add_post("/api/groups/stop", api_stop_group)
    app.router.add_post("/api/groups/restart", api_restart_group)
    
    # Admin & Settings APIs
    app.router.add_get("/api/settings", api_get_settings)
    app.router.add_post("/api/settings", api_update_settings)
    app.router.add_post("/api/admin_login", api_admin_login)
    app.router.add_post("/api/change_admin", api_change_admin_pass)

    if _sync_task is None or _sync_task.done():
        _sync_task = asyncio.create_task(_sync_loop())
    if _score_task is None or _score_task.done():
        _score_task = asyncio.create_task(_score_updater_loop())

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    print(f"[WEB] 🌐 Dashboard ready → http://{host}:{port}")
    return runner