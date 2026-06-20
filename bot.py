import os
import re
import io
import json
import asyncio
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncpg
import aiohttp

try:
    from anthropic import AsyncAnthropic
except Exception:  # package not installed -> AI intake simply stays disabled
    AsyncAnthropic = None

# Load a local .env if present (does not override real env vars on the host).
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ============================================================
# CONFIG / ENV
# ============================================================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

GUILD_ID = int(os.getenv("GUILD_ID", "0") or "0")
STAFF_ROLE_ID = int(os.getenv("STAFF_ROLE_ID", "0") or "0")

CLAIM_CATEGORY_ID = int(os.getenv("CLAIM_CATEGORY_ID", "0") or "0")
CUSTOM_CATEGORY_ID = int(os.getenv("CUSTOM_CATEGORY_ID", "0") or "0")
SUPPORT_CATEGORY_ID = int(os.getenv("SUPPORT_CATEGORY_ID", "0") or "0")

TICKET_LOG_CHANNEL_ID = int(os.getenv("TICKET_LOG_CHANNEL_ID", "0") or "0")

STATUS_ROTATE_SECONDS = int(os.getenv("STATUS_ROTATE_SECONDS", "15") or "15")
DELETE_COUNTDOWN_SECONDS = int(os.getenv("DELETE_COUNTDOWN_SECONDS", "5") or "5")

AF_BLUE = 0x1E90FF

# Image assets are bundled with the bot and attached to each message so Discord
# hosts them itself. External links (Imgur, etc.) often refuse to embed in
# Discord, so we ship the PNGs next to bot.py and reference them via
# attachment://. A direct image URL can still override each via env var.
LOGO_FILENAME = "af_logo_black.png"
BANNER_FILENAME = "af_tickets.png"

# Resolve asset paths absolutely (relative to this file), so the files are found
# regardless of the process working directory — which often differs from the
# project folder on hosts / worker processes.
ASSET_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(ASSET_DIR, LOGO_FILENAME)
BANNER_PATH = os.path.join(ASSET_DIR, BANNER_FILENAME)

AF_LOGO_URL = os.getenv("AF_LOGO_URL", "")     # optional direct-URL override
AF_BANNER_URL = os.getenv("AF_BANNER_URL", "")  # optional direct-URL override

# ============================================================
# AUTO-DELIVERY / AI / INTEGRATIONS CONFIG
# Every feature below is OFF until its env vars are set, so the bot keeps
# behaving exactly as before until you opt in.
# ============================================================
# --- Claude (Anthropic) AI intake assistant ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "claude-sonnet-4-6")  # set claude-opus-4-8 for max quality
AI_ENABLED = bool(ANTHROPIC_API_KEY) and AsyncAnthropic is not None

# --- SellAuth (payment verification — the trusted proof) ---
SELLAUTH_API_KEY = os.getenv("SELLAUTH_API_KEY", "")
SELLAUTH_SHOP_ID = os.getenv("SELLAUTH_SHOP_ID", "")
SELLAUTH_API_BASE = os.getenv("SELLAUTH_API_BASE", "https://api.sellauth.com/v1").rstrip("/")
# Invoice statuses that count as "paid". SellAuth uses "completed" for a finished order.
SELLAUTH_PAID_STATUSES = {
    s.strip().lower()
    for s in os.getenv("SELLAUTH_PAID_STATUSES", "completed,paid").split(",")
    if s.strip()
}
SELLAUTH_ENABLED = bool(SELLAUTH_API_KEY and SELLAUTH_SHOP_ID)

# --- LZT.market (your account stock source) ---
LZT_API_TOKEN = os.getenv("LZT_API_TOKEN", "")
LZT_USER_ID = os.getenv("LZT_USER_ID", "")  # your numeric lzt.market user id (for /user/{id}/orders)
LZT_API_BASE = os.getenv("LZT_API_BASE", "https://api.lzt.market").rstrip("/")
LZT_ENABLED = bool(LZT_API_TOKEN)

# Customer-facing category name -> LZT.market category_id (verified against the live API).
# Valorant accounts live under the "riot" category (13).
LZT_CATEGORY_IDS = {
    "fortnite": 9,
    "valorant": 13,
    "riot": 13,
    "steam": 1,
    "socialclub": 7,
    "llm": 6,
}
# Owned-account tag titles on LZT.market that mean "do NOT deliver this".
LZT_BAD_TAGS = {"invalid", "resold"}

# Customer-facing category -> LZT.market search URL slug (for browsing listings to resell).
LZT_MARKET_SLUGS = {
    "valorant": "riot",   # riot category (13) holds Valorant + LoL
    "fortnite": "fortnite",
}
# Resale markup: we sell to the customer at >= this multiple of the LZT source price.
RESALE_MULTIPLIER = float(os.getenv("RESALE_MULTIPLIER", "2.5") or "2.5")
# Where "buy this now to restock" alerts go. If unset, the alert DMs the staff who triggered it.
RESTOCK_CHANNEL_ID = int(os.getenv("RESTOCK_CHANNEL_ID", "0") or "0")

# --- NowPayments (crypto checkout for custom orders) ---
NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY", "")
NOWPAYMENTS_API_BASE = os.getenv("NOWPAYMENTS_API_BASE", "https://api.nowpayments.io/v1").rstrip("/")
NOWPAYMENTS_PRICE_CURRENCY = os.getenv("NOWPAYMENTS_PRICE_CURRENCY", "eur").lower()
NOWPAYMENTS_ENABLED = bool(NOWPAYMENTS_API_KEY)
# Coins customers can pay with -> NowPayments pay_currency ticker.
CRYPTO_CHOICES = {"LTC": "ltc", "SOL": "sol", "BTC": "btc", "ETH": "eth"}
# NowPayments statuses that mean the customer has paid.
NP_PAID_STATUSES = {"confirmed", "sending", "finished", "partially_paid"}

# --- Delivery policy ---
# You chose "AI gathers + staff approves": payment is verified automatically but a
# human clicks Approve before any account leaves stock. Set AUTO_DELIVER=1 to skip
# the human step once SellAuth confirms payment.
AUTO_DELIVER = os.getenv("AUTO_DELIVER", "0") not in ("0", "false", "False", "")
# Require a verified SellAuth order before delivery is even offered to staff.
REQUIRE_SELLAUTH = os.getenv("REQUIRE_SELLAUTH", "1") not in ("0", "false", "False", "")

# --- Card payment example images (shown in /card) ---
# Bundled locally and attached so they always render (Imgur often refuses to embed
# directly in Discord). Env vars can override with direct image URLs if preferred.
CARD_PROOF_PAYMENT_FILENAME = "card_proof_payment.png"
CARD_PROOF_EMAIL_FILENAME = "card_proof_email.png"
CARD_PROOF_PAYMENT_PATH = os.path.join(ASSET_DIR, CARD_PROOF_PAYMENT_FILENAME)
CARD_PROOF_EMAIL_PATH = os.path.join(ASSET_DIR, CARD_PROOF_EMAIL_FILENAME)
CARD_PROOF_PAYMENT_URL = os.getenv("CARD_PROOF_PAYMENT_URL", "")
CARD_PROOF_EMAIL_URL = os.getenv("CARD_PROOF_EMAIL_URL", "")

anthropic_client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY) if AI_ENABLED else None
# Channels currently being processed by the AI, so two quick messages don't double-fire.
ai_locks: set[int] = set()


def logo_ref() -> str:
    return AF_LOGO_URL or f"attachment://{LOGO_FILENAME}"


def banner_ref() -> str:
    return AF_BANNER_URL or f"attachment://{BANNER_FILENAME}"


def embed_files(include_banner: bool = False) -> list[discord.File]:
    """Fresh File objects to attach alongside an embed. Single-use, so build new
    ones for every send. Skipped when an env URL override is set or file missing."""
    files: list[discord.File] = []
    if not AF_LOGO_URL and os.path.exists(LOGO_PATH):
        files.append(discord.File(LOGO_PATH, filename=LOGO_FILENAME))
    if include_banner and not AF_BANNER_URL and os.path.exists(BANNER_PATH):
        files.append(discord.File(BANNER_PATH, filename=BANNER_FILENAME))
    return files

if not DISCORD_TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN")
if not DATABASE_URL:
    raise RuntimeError("Missing DATABASE_URL")
if not GUILD_ID:
    raise RuntimeError("Missing GUILD_ID")


# ============================================================
# DISCORD BOT
# ============================================================
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

db_pool: asyncpg.Pool | None = None


# ============================================================
# DATABASE SCHEMA
# ============================================================
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ticket_counters (
  kind TEXT PRIMARY KEY,
  next_num INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
  channel_id BIGINT PRIMARY KEY,
  guild_id BIGINT NOT NULL,
  owner_id BIGINT NOT NULL,
  kind TEXT NOT NULL,
  ticket_num INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_activity TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  claimed_by BIGINT NULL,
  first_staff_response_seconds INTEGER NULL,
  control_message_id BIGINT NULL,
  last_footer_text TEXT NULL,
  last_topic_text TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_open_owner_kind
ON tickets (guild_id, owner_id, kind)
WHERE status='open';

CREATE INDEX IF NOT EXISTS idx_status
ON tickets (status);

ALTER TABLE tickets ADD COLUMN IF NOT EXISTS claimed_by BIGINT NULL;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS first_staff_response_seconds INTEGER NULL;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS control_message_id BIGINT NULL;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS last_footer_text TEXT NULL;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS last_topic_text TEXT NULL;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS ai_handled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS verified_order_id TEXT NULL;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS verified_product TEXT NULL;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS reserved_market_item_id BIGINT NULL;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS restock_alerted BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS crypto_payment_id TEXT NULL;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS crypto_amount NUMERIC NULL;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS crypto_paid BOOLEAN NOT NULL DEFAULT FALSE;

-- Record of every account released, so a SellAuth order can never be used twice.
CREATE TABLE IF NOT EXISTS deliveries (
  id SERIAL PRIMARY KEY,
  channel_id BIGINT NOT NULL,
  owner_id BIGINT NOT NULL,
  product TEXT NULL,
  order_id TEXT NULL,
  lzt_item_id BIGINT NULL,
  delivered_by BIGINT NULL,
  delivered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_delivery_order
ON deliveries (order_id) WHERE order_id IS NOT NULL;
"""


async def ensure_db() -> None:
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        async with db_pool.acquire() as con:
            await con.execute(SCHEMA_SQL)
            await con.execute("ALTER TABLE tickets DROP COLUMN IF EXISTS priority")


async def db_fetchrow(q: str, *args):
    assert db_pool is not None
    async with db_pool.acquire() as con:
        return await con.fetchrow(q, *args)


async def db_fetch(q: str, *args):
    assert db_pool is not None
    async with db_pool.acquire() as con:
        return await con.fetch(q, *args)


async def db_execute(q: str, *args):
    assert db_pool is not None
    async with db_pool.acquire() as con:
        return await con.execute(q, *args)


async def get_next_ticket_num(kind: str) -> int:
    assert db_pool is not None
    async with db_pool.acquire() as con:
        row = await con.fetchrow(
            """
            INSERT INTO ticket_counters(kind, next_num)
            VALUES ($1, 1)
            ON CONFLICT (kind) DO UPDATE
            SET next_num = ticket_counters.next_num + 1
            RETURNING next_num;
            """,
            kind
        )
        return int(row["next_num"])


# ============================================================
# SELLAUTH — payment verification
# ============================================================
async def sellauth_get_invoice(order_id: str) -> dict:
    """Look up a SellAuth invoice by its id. Returns a normalized dict:
    {ok, found, paid, status, amount, email, items, error}."""
    out = {
        "ok": False, "found": False, "paid": False,
        "status": None, "amount": None, "email": None, "items": [], "error": None,
    }
    if not SELLAUTH_ENABLED:
        out["error"] = "SellAuth not configured"
        return out

    oid = re.sub(r"[^A-Za-z0-9_\-]", "", (order_id or "").strip())
    if not oid:
        out["error"] = "empty order id"
        return out

    url = f"{SELLAUTH_API_BASE}/shops/{SELLAUTH_SHOP_ID}/invoices/{oid}"
    headers = {"Authorization": f"Bearer {SELLAUTH_API_KEY}", "Accept": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get(url, headers=headers) as resp:
                text = await resp.text()
                if resp.status == 404:
                    out["ok"] = True  # request succeeded; the invoice just doesn't exist
                    out["error"] = "invoice not found"
                    return out
                if resp.status >= 400:
                    out["error"] = f"HTTP {resp.status}: {text[:200]}"
                    return out
                data = json.loads(text)
    except Exception as e:
        out["error"] = f"request failed: {e}"
        return out

    # SellAuth may wrap the object as {"invoice": {...}} or return it flat.
    inv = data.get("invoice", data) if isinstance(data, dict) else {}
    status = str(inv.get("status", "")).lower()
    paid_usd = inv.get("paid_usd")
    completed = bool(inv.get("completed_at"))

    out.update(
        ok=True,
        found=True,
        status=status or None,
        amount=inv.get("price_usd") or inv.get("price") or inv.get("total"),
        email=inv.get("email"),
        items=inv.get("items") or [],
    )
    # "paid" if status is in the allow-list, or SellAuth already marked it completed/paid_usd.
    out["paid"] = (
        status in SELLAUTH_PAID_STATUSES
        or completed
        or (paid_usd is not None and float(paid_usd or 0) > 0)
    )
    return out


# ============================================================
# LZT.MARKET — your account stock
# ============================================================
def _lzt_headers() -> dict:
    return {
        "Authorization": f"Bearer {LZT_API_TOKEN}",
        "Accept": "application/json",
    }


async def lzt_list_owned(page: int = 1, category_id: int | None = None) -> dict:
    """List accounts you've purchased on LZT.market (your sellable stock).
    Pass category_id to filter server-side (e.g. 9=Fortnite, 13=Riot/Valorant).
    Returns {ok, items: [{item_id, title, price, item_state, category, tags}], total, has_next, error}."""
    out = {"ok": False, "items": [], "total": None, "has_next": False, "error": None}
    if not LZT_ENABLED:
        out["error"] = "stock source not configured"
        return out
    if not LZT_USER_ID:
        out["error"] = "LZT_USER_ID not set (your numeric lzt.market user id)"
        return out

    url = f"{LZT_API_BASE}/user/{LZT_USER_ID}/orders"
    params: dict = {"page": page}
    if category_id is not None:
        params["category_id"] = category_id
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(url, headers=_lzt_headers(), params=params) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    out["error"] = f"HTTP {resp.status}: {text[:200]}"
                    return out
                data = json.loads(text)
    except Exception as e:
        out["error"] = f"request failed: {e}"
        return out

    items = data.get("items") or data.get("orders") or []
    out.update(
        ok=True,
        items=[
            {
                "item_id": it.get("item_id") or it.get("id"),
                "title": it.get("title") or it.get("title_en") or "Account",
                "price": it.get("price"),
                "item_state": it.get("item_state") or it.get("status"),
                "category": (it.get("category") or {}).get("category_name"),
                "tags": [t.get("title") for t in (it.get("tags") or {}).values() if isinstance(t, dict)],
            }
            for it in items if isinstance(it, dict)
        ],
        total=data.get("totalItems"),
        has_next=bool(data.get("hasNextPage")),
    )
    return out


def _item_is_valid(item: dict) -> bool:
    """True when an owned item has no 'invalid'/'resold' tag — i.e. safe to deliver."""
    tags = {str(t).lower() for t in (item.get("tags") or [])}
    return not (tags & LZT_BAD_TAGS)


async def _delivered_item_ids() -> set[int]:
    """LZT item IDs already delivered to a customer (never hand the same account out twice)."""
    rows = await db_fetch("SELECT lzt_item_id FROM deliveries WHERE lzt_item_id IS NOT NULL")
    return {int(r["lzt_item_id"]) for r in rows if r["lzt_item_id"]}


async def lzt_find_valid(category: str, max_pages: int = 10) -> dict:
    """Walk your purchases for one category, keeping only VALID accounts
    (excludes the 'invalid' and 'resold' tags). `category` is a name like
    'fortnite' or 'valorant'. Returns {ok, items, error}."""
    out = {"ok": False, "items": [], "error": None}
    cid = LZT_CATEGORY_IDS.get(category.lower())
    if cid is None:
        out["error"] = f"unknown category '{category}'"
        return out
    valid: list[dict] = []
    for pg in range(1, max_pages + 1):
        res = await lzt_list_owned(page=pg, category_id=cid)
        if not res["ok"]:
            out["error"] = res["error"]
            return out
        valid.extend(it for it in res["items"] if _item_is_valid(it))
        if not res.get("has_next"):
            break
    out.update(ok=True, items=valid)
    return out


# ============================================================
# LZT.MARKET — BROWSE LISTINGS TO BUY & RESELL (dropshipping)
# ============================================================
LZT_SITE = "https://lzt.market"


async def _lzt_get(path: str, params: dict | None = None) -> dict:
    """Raw authenticated GET against the LZT.market API. Returns {ok, data, error}."""
    out = {"ok": False, "data": None, "error": None}
    if not LZT_ENABLED:
        out["error"] = "stock source not configured"
        return out
    url = f"{LZT_API_BASE}{path}"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(url, headers=_lzt_headers(), params=params or {}) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    out["error"] = f"HTTP {resp.status}: {text[:200]}"
                    return out
                out.update(ok=True, data=json.loads(text))
    except Exception as e:
        out["error"] = f"request failed: {e}"
    return out


async def lzt_search_market(category: str, budget: float | None = None,
                            count: int = 3) -> dict:
    """Search live LZT.market listings we can buy & resell, richest-first within budget.
    `category` is 'valorant' or 'fortnite'. `budget` is the customer's max spend (EUR);
    we cap the source price at budget / RESALE_MULTIPLIER. Returns {ok, items, error}."""
    out = {"ok": False, "items": [], "error": None}
    slug = LZT_MARKET_SLUGS.get(category.lower())
    if not slug:
        out["error"] = f"unknown category '{category}'"
        return out
    params: dict = {"order_by": "price_to_down"}  # most expensive (richest) first
    if budget and budget > 0:
        params["pmax"] = round(budget / RESALE_MULTIPLIER, 2)
    res = await _lzt_get(f"/{slug}", params)
    if not res["ok"]:
        out["error"] = res["error"]
        return out
    items = (res["data"] or {}).get("items") or []
    out.update(ok=True, items=items[:max(1, min(count, 5))])
    return out


async def lzt_item_detail(item_id: str | int) -> dict:
    """Full listing detail (skins, image preview links, etc). Returns {ok, item, error}."""
    iid = re.sub(r"[^0-9]", "", str(item_id or "").strip())
    if not iid:
        return {"ok": False, "item": None, "error": "invalid item id"}
    res = await _lzt_get(f"/{iid}")
    if not res["ok"]:
        return {"ok": False, "item": None, "error": res["error"]}
    data = res["data"] or {}
    return {"ok": True, "item": data.get("item", data), "error": None}


def _resale_price(item: dict) -> tuple[float, float]:
    """(source_price, resale_price) in EUR, resale rounded up to the next whole euro."""
    src = float(item.get("price") or 0)
    resale = src * RESALE_MULTIPLIER
    resale = float(int(resale) + (1 if resale > int(resale) else 0)) if resale else 0.0
    return src, resale


def _preview_image(item: dict, kind: str) -> str | None:
    """Direct (JWT-signed, embeddable) composite image URL for skins/weapons, if present."""
    links = (item.get("imagePreviewLinks") or {}).get("direct") or {}
    return links.get(kind)


_FN_RARITY = {
    "legendary": "🟠", "epic": "🟣", "rare": "🔵", "uncommon": "🟢",
    "common": "⚪", "marvel": "🔴", "dc": "🔷", "icon": "🟦", "gaming": "🟦",
    "starwars": "🟡", "frozen": "🧊", "lava": "🔥", "shadow": "⬛", "slurp": "💧",
}


def market_account_embed(category: str, item: dict, image_name: str | None = None) -> discord.Embed:
    """Customer-facing embed describing one account: stats + skins image + price.
    Never exposes the sourcing marketplace. `image_name` is an attachment:// filename."""
    src, resale = _resale_price(item)

    if category.lower() == "valorant":
        skins = item.get("riot_valorant_skin_count") or 0
        vp_inv = item.get("riot_valorant_inventory_value") or 0
        vp_wallet = item.get("riot_valorant_wallet_vp") or 0
        rank = item.get("valorantRankTitle") or "Unrated"
        level = item.get("riot_valorant_level") or 0
        region = item.get("valorantRegionPhrase") or item.get("riot_valorant_region") or "—"
        agents = item.get("riot_valorant_agent_count") or 0
        knives = item.get("riot_valorant_knife_count") or 0
        e = discord.Embed(
            title=f"🔫  Valorant Account — {skins} Skins",
            description=f"**Rank:** {rank}  •  **Level:** {level}  •  **Region:** {region}",
            color=GUIDE_RIOT_COLOR,
        )
        e.add_field(name="🎨 Skins", value=str(skins), inline=True)
        e.add_field(name="💎 Skin Value", value=f"{vp_inv:,} VP", inline=True)
        e.add_field(name="🪙 VP Balance", value=f"{vp_wallet:,} VP", inline=True)
        e.add_field(name="🧍 Agents", value=str(agents), inline=True)
        e.add_field(name="🔪 Knives", value=str(knives), inline=True)
    else:  # fortnite
        skins = item.get("fortnite_skin_count") or 0
        vbucks = item.get("fortnite_balance") or 0
        spent = sum(int(item.get(f"fortnite_shop_{k}_cost") or 0)
                    for k in ("skins", "pickaxes", "dances", "gliders"))
        fn_skins = item.get("fortniteSkins") or []
        names = []
        for s in fn_skins[:18]:
            emoji = _FN_RARITY.get(str(s.get("rarity", "")).lower(), "▫️")
            names.append(f"{emoji} {s.get('title')}")
        more = len(fn_skins) - len(names)
        skin_list = "\n".join(names) if names else "—"
        if more > 0:
            skin_list += f"\n…and **{more}** more"
        e = discord.Embed(
            title=f"🎮  Fortnite Account — {skins} Skins",
            description=f"**V-Bucks:** {vbucks:,}  •  **V-Bucks spent in shop:** {spent:,}",
            color=GUIDE_EPIC_COLOR,
        )
        e.add_field(name="🎨 Skins", value=skin_list[:1024], inline=False)

    e.add_field(name="💶 Price", value=f"**€{resale:.0f}**", inline=True)
    if image_name:
        e.set_image(url=f"attachment://{image_name}")
    e.set_footer(text="AF SERVICES • Prices include warranty & setup support")
    return e


async def _fetch_bytes(url: str) -> bytes | None:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
            async with s.get(url) as r:
                return await r.read() if r.status == 200 else None
    except Exception:
        return None


async def build_account_message(category: str, item: dict, idx: int = 0):
    """(embed, file|None) for one account. The skin preview is re-hosted through Discord
    so the customer never sees the source marketplace URL."""
    kind = "weapons" if category.lower() == "valorant" else "skins"
    url = _preview_image(item, kind)
    file = None
    image_name = None
    if url:
        data = await _fetch_bytes(url)
        if data:
            image_name = f"account_{idx}.png"
            file = discord.File(io.BytesIO(data), filename=image_name)
    return market_account_embed(category, item, image_name=image_name), file


async def notify_restock(triggered_by: discord.abc.User | None, guild: discord.Guild,
                         item: dict, ticket: discord.TextChannel | None) -> str:
    """Alert the owner that a sold account must be re-bought on LZT ASAP. Returns a status string."""
    src, resale = _resale_price(item)
    iid = item.get("item_id") or item.get("id")
    listing = f"{LZT_SITE}/{iid}/"
    title = item.get("title") or item.get("title_en") or "Account"
    e = discord.Embed(
        title="🛒  RESTOCK NOW — Account Sold",
        description=(
            f"A customer paid for this account. **Buy the exact listing below ASAP** before it's gone.\n\n"
            f"**{str(title)[:200]}**"
        ),
        color=0xE74C3C,
        url=listing,
    )
    e.add_field(name="💸 Your cost", value=f"€{src:.2f}", inline=True)
    e.add_field(name="💶 Sold for", value=f"€{resale:.0f}", inline=True)
    e.add_field(name="🆔 Item ID", value=f"`{iid}`", inline=True)
    e.add_field(name="🔗 Buy now", value=f"[Open listing]({listing})", inline=False)
    if ticket:
        e.add_field(name="🎟️ Ticket", value=ticket.mention, inline=False)
    e.set_footer(text="AF SERVICES • Restock alert")

    target = guild.get_channel(RESTOCK_CHANNEL_ID) if RESTOCK_CHANNEL_ID else None
    if isinstance(target, discord.TextChannel):
        await target.send(embed=e)
        return f"Restock alert posted in {target.mention}."
    if triggered_by is not None:
        try:
            await triggered_by.send(embed=e)
            return "Restock alert sent to your DMs."
        except discord.Forbidden:
            return "⚠️ Couldn't DM you and no RESTOCK_CHANNEL_ID is set — enable DMs or set the channel."
    return "⚠️ No RESTOCK_CHANNEL_ID set — restock alert could not be delivered."


# ============================================================
# NOWPAYMENTS — crypto checkout (LTC / SOL / BTC / ETH)
# ============================================================
def _np_headers() -> dict:
    return {"x-api-key": NOWPAYMENTS_API_KEY, "Content-Type": "application/json"}


async def np_create_payment(amount: float, pay_currency: str, order_id: str,
                            description: str) -> dict:
    """Create a NowPayments crypto payment. Returns {ok, payment_id, pay_address,
    pay_amount, pay_currency, error}."""
    out = {"ok": False, "payment_id": None, "pay_address": None,
           "pay_amount": None, "pay_currency": pay_currency, "error": None}
    if not NOWPAYMENTS_ENABLED:
        out["error"] = "crypto payments not configured"
        return out
    body = {
        "price_amount": round(float(amount), 2),
        "price_currency": NOWPAYMENTS_PRICE_CURRENCY,
        "pay_currency": pay_currency,
        "order_id": order_id,
        "order_description": description[:200],
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as s:
            async with s.post(f"{NOWPAYMENTS_API_BASE}/payment",
                              headers=_np_headers(), json=body) as r:
                text = await r.text()
                if r.status >= 400:
                    out["error"] = f"HTTP {r.status}: {text[:200]}"
                    return out
                data = json.loads(text)
    except Exception as e:
        out["error"] = f"request failed: {e}"
        return out
    out.update(
        ok=True,
        payment_id=str(data.get("payment_id")),
        pay_address=data.get("pay_address"),
        pay_amount=data.get("pay_amount"),
        pay_currency=(data.get("pay_currency") or pay_currency),
    )
    return out


async def np_get_payment(payment_id: str) -> dict:
    """Look up a NowPayments payment. Returns {ok, status, paid, error}."""
    out = {"ok": False, "status": None, "paid": False, "error": None}
    if not NOWPAYMENTS_ENABLED:
        out["error"] = "crypto payments not configured"
        return out
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
            async with s.get(f"{NOWPAYMENTS_API_BASE}/payment/{payment_id}",
                             headers=_np_headers()) as r:
                text = await r.text()
                if r.status >= 400:
                    out["error"] = f"HTTP {r.status}: {text[:200]}"
                    return out
                data = json.loads(text)
    except Exception as e:
        out["error"] = f"request failed: {e}"
        return out
    status = str(data.get("payment_status") or "").lower()
    out.update(ok=True, status=status, paid=status in NP_PAID_STATUSES)
    return out


async def lzt_get_credentials(item_id: str | int) -> dict:
    """Fetch the login credentials for one owned LZT item.
    Returns {ok, login, password, raw, title, error}."""
    out = {"ok": False, "login": None, "password": None, "raw": None, "title": None, "error": None}
    if not LZT_ENABLED:
        out["error"] = "stock source not configured"
        return out

    iid = re.sub(r"[^0-9]", "", str(item_id or "").strip())
    if not iid:
        out["error"] = "invalid item id"
        return out

    url = f"{LZT_API_BASE}/{iid}"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(url, headers=_lzt_headers()) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    out["error"] = f"HTTP {resp.status}: {text[:200]}"
                    return out
                data = json.loads(text)
    except Exception as e:
        out["error"] = f"request failed: {e}"
        return out

    item = data.get("item", data) if isinstance(data, dict) else {}
    login_data = item.get("loginData") or item.get("login_data") or {}
    login = login_data.get("login") or item.get("account_login")
    password = login_data.get("password") or item.get("account_password")
    raw = login_data.get("raw") or (f"{login}:{password}" if login and password else None)

    if not (login or raw):
        out["error"] = "no credentials returned (item may not be owned, or token lacks scope)"
        return out

    out.update(ok=True, login=login, password=password, raw=raw,
               title=item.get("title") or item.get("title_en"))
    return out


# ============================================================
# DELIVERY — idempotent account release into a ticket
# ============================================================
async def order_already_delivered(order_id: str | None) -> bool:
    if not order_id:
        return False
    row = await db_fetchrow("SELECT 1 FROM deliveries WHERE order_id=$1", order_id)
    return row is not None


def credentials_embed(product: str | None, order_id: str | None,
                      login: str | None, password: str | None, raw: str | None) -> discord.Embed:
    body = "Here is your account. **Tap the hidden field to reveal it**, then secure it immediately.\n\n"
    if login:
        body += f"**Login:** ||{login}||\n"
    if password:
        body += f"**Password:** ||{password}||\n"
    if raw and not (login and password):
        body += f"**Account:** ||{raw}||\n"
    body += "\nFollow **/guide** to lock the account to you (change email/password, enable 2FA)."

    e = discord.Embed(title="📦  Your Account — Delivered", description=body, color=0x2ECC71)
    if product:
        e.add_field(name="Product", value=product, inline=True)
    if order_id:
        e.add_field(name="Order", value=f"`{order_id}`", inline=True)
    e.set_thumbnail(url=logo_ref())
    e.set_footer(text="AF SERVICES • Thank you for your purchase")
    return e


async def deliver_account(
    channel: discord.TextChannel,
    owner: discord.abc.User,
    lzt_item_id: str | int,
    product: str | None,
    order_id: str | None,
    delivered_by: int | None,
) -> bool:
    """Pull credentials from LZT for the given item and deliver them into the ticket.
    Guards against re-using an order. Returns True on success."""
    if order_id and await order_already_delivered(order_id):
        await channel.send("⚠️ That order has already been used for a delivery — staff will review.")
        return False

    creds = await lzt_get_credentials(lzt_item_id)
    if not creds["ok"]:
        await channel.send(f"⚠️ Couldn't load that account from stock: `{creds['error']}`")
        return False

    # Record first (unique index on order_id makes a double-deliver fail loudly).
    try:
        await db_execute(
            """INSERT INTO deliveries(channel_id, owner_id, product, order_id, lzt_item_id, delivered_by)
               VALUES ($1,$2,$3,$4,$5,$6)""",
            channel.id, owner.id, product, order_id, int(re.sub(r"[^0-9]", "", str(lzt_item_id)) or 0),
            delivered_by,
        )
    except asyncpg.UniqueViolationError:
        await channel.send("⚠️ That order was already delivered (duplicate blocked).")
        return False

    embed = credentials_embed(product or creds.get("title"), order_id,
                              creds["login"], creds["password"], creds["raw"])
    await channel.send(content=owner.mention, embed=embed, files=embed_files())
    try:
        await owner.send(embed=embed)  # also DM the buyer as a backup copy
    except Exception:
        pass

    await db_execute("UPDATE tickets SET ai_handled=TRUE WHERE channel_id=$1", channel.id)

    log_ch = await get_log_channel(channel.guild)
    if log_ch:
        le = discord.Embed(title="✅ Account Delivered", color=0x2ECC71)
        le.add_field(name="Buyer", value=f"{owner.mention} (`{owner.id}`)", inline=False)
        le.add_field(name="Product", value=product or creds.get("title") or "—", inline=True)
        le.add_field(name="Item", value=str(lzt_item_id), inline=True)
        le.add_field(name="Order", value=f"`{order_id}`" if order_id else "—", inline=True)
        if delivered_by:
            le.add_field(name="Released by", value=f"<@{delivered_by}>", inline=False)
        le.add_field(name="Channel", value=channel.mention, inline=False)
        await log_ch.send(embed=le)

    # Confirmed purchase → send the security guide + replacement policy so the buyer
    # knows how to secure the account and how replacements work.
    try:
        await post_purchase_followup(channel, owner, product or creds.get("title"), creds.get("title"))
    except Exception as e:
        print("post-purchase guide failed:", e)
    return True


# ============================================================
# HELPERS
# ============================================================
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def safe_name(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = re.sub(r"-{2,}", "-", name).strip("-")
    return name[:40] if name else "ticket"


def kind_label(kind: str) -> str:
    return {
        "claim": "Claim Order",
        "custom": "Custom Order",
        "support": "Issues/Help",
    }.get(kind, "Support")


def kind_prefix(kind: str) -> str:
    return {
        "claim": "claim",
        "custom": "custom",
        "support": "support",
    }.get(kind, "support")


def kind_emoji(kind: str) -> str:
    return {
        "claim": "🛒",
        "custom": "🛍️",
        "support": "🎫",
    }.get(kind, "🎫")


def category_for_kind(kind: str) -> int:
    if kind == "claim":
        return CLAIM_CATEGORY_ID
    if kind == "custom":
        return CUSTOM_CATEGORY_ID
    return SUPPORT_CATEGORY_ID


def get_staff_role(guild: discord.Guild) -> discord.Role | None:
    return guild.get_role(STAFF_ROLE_ID) if STAFF_ROLE_ID else None


def is_staff(member: discord.Member) -> bool:
    # Server owner and administrators always count as staff so the bot
    # owner is never locked out, even without the explicit staff role.
    if member.guild and member.id == member.guild.owner_id:
        return True
    if member.guild_permissions.administrator:
        return True
    role = get_staff_role(member.guild)
    return (role in member.roles) if role else False


async def get_log_channel(guild: discord.Guild) -> discord.TextChannel | None:
    if not TICKET_LOG_CHANNEL_ID:
        return None
    ch = guild.get_channel(TICKET_LOG_CHANNEL_ID)
    if isinstance(ch, discord.TextChannel):
        return ch
    try:
        fetched = await bot.fetch_channel(TICKET_LOG_CHANNEL_ID)
        return fetched if isinstance(fetched, discord.TextChannel) else None
    except Exception:
        return None


def make_channel_name(kind: str, owner: discord.abc.User, num: int) -> str:
    return f"{kind_emoji(kind)}-{kind_prefix(kind)}-{safe_name(owner.name)}-{num:04d}"


def sanitize_channel_rename(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^\w\-\u0080-\uffff]", "", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:90] if text else "renamed-ticket"


async def is_ticket_channel(channel_id: int) -> bool:
    row = await db_fetchrow("SELECT 1 FROM tickets WHERE channel_id=$1", channel_id)
    return row is not None


async def hide_ticket_from_other_staff(channel: discord.TextChannel, claimer: discord.Member) -> None:
    staff_role = get_staff_role(channel.guild)
    if staff_role:
        await channel.set_permissions(staff_role, overwrite=discord.PermissionOverwrite(view_channel=False))

    await channel.set_permissions(
        claimer,
        overwrite=discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    )


# ============================================================
# EMBEDS
# ============================================================
def panel_embed() -> discord.Embed:
    e = discord.Embed(
        title="AF SERVICES Tickets",
        description=(
            "Do you require assistance with anything? If so,\n"
            "please open a ticket and our support team will answer your queries.\n\n"
            "**What can we help with?**\n"
            "• Claim Order\n"
            "• Custom Order\n"
            "• Issues/Help\n\n"
            "Please be precise and straight forward with your query."
        ),
        color=AF_BLUE,
    )
    e.set_author(name="AF SERVICES Support System")
    e.set_thumbnail(url=logo_ref())
    e.set_image(url=banner_ref())
    e.set_footer(text="Support Team | AF SERVICES")
    return e


def ticket_embed(
    kind: str,
    owner_mention: str,
    claimed_by_mention: str | None,
    first_staff_seconds: int | None,
    footer_text: str | None
) -> discord.Embed:
    title = kind_label(kind)
    claimed_line = claimed_by_mention or "This ticket has not been claimed."

    e = discord.Embed(
        title=title,
        description=(
            "Thank you for contacting us.\n"
            "Please describe your request clearly.\n\n"
            "**Claimed by**\n"
            f"{claimed_line}\n\n"
            f"**Owner:** {owner_mention}"
        ),
        color=AF_BLUE,
    )
    if first_staff_seconds is not None:
        mins = first_staff_seconds // 60
        secs = first_staff_seconds % 60
        e.add_field(name="First staff response", value=f"{mins}m {secs}s", inline=False)

    e.set_author(name="AF SERVICES Tickets")
    e.set_thumbnail(url=logo_ref())
    e.set_footer(text=footer_text or "AF SERVICES")
    return e


# ============================================================
# TRANSCRIPT
# ============================================================
async def build_formatted_transcript(channel: discord.TextChannel) -> bytes:
    lines: list[str] = []

    ticket_row = await db_fetchrow(
        "SELECT owner_id, kind, ticket_num, claimed_by, created_at FROM tickets WHERE channel_id=$1",
        channel.id,
    )

    claimed_by_text = "Unclaimed"
    if ticket_row and ticket_row["claimed_by"]:
        claimed_member = channel.guild.get_member(int(ticket_row["claimed_by"]))
        claimed_by_text = (
            f"{claimed_member} ({claimed_member.id})"
            if claimed_member else
            f"{int(ticket_row['claimed_by'])}"
        )

    lines.append(f"Transcript for: {channel.name}")
    lines.append(f"Channel ID: {channel.id}")
    if ticket_row:
        lines.append(f"Ticket kind: {ticket_row['kind']}")
        lines.append(f"Ticket number: {ticket_row['ticket_num']}")
        lines.append(f"Ticket owner ID: {ticket_row['owner_id']}")
        lines.append(f"Claimed by: {claimed_by_text}")
        lines.append(f"Created at: {ticket_row['created_at'].isoformat()}")
    lines.append(f"Generated: {utcnow().isoformat()}")
    lines.append("=" * 80)

    async for msg in channel.history(limit=None, oldest_first=True):
        ts = msg.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        author = f"{msg.author} ({msg.author.id})"
        content = (msg.content or "").replace("\r", "")

        lines.append(f"[{ts}] {author}")
        if content.strip():
            for line in content.split("\n"):
                lines.append(f"  {line}")

        if msg.attachments:
            lines.append("  Attachments:")
            for a in msg.attachments:
                lines.append(f"    - {a.url}")

        if msg.embeds:
            lines.append(f"  Embeds: {len(msg.embeds)}")

        lines.append("-" * 80)

    lines.append("END OF TRANSCRIPT")
    return ("\n".join(lines)).encode("utf-8", errors="replace")


async def send_transcript_txt(
    guild: discord.Guild,
    ticket_channel: discord.TextChannel,
    close_reason: str,
    closed_by: str,
    claimed_by: str,
) -> bool:
    log_ch = await get_log_channel(guild)
    if not log_ch:
        return False

    try:
        data = await build_formatted_transcript(ticket_channel)
        f = discord.File(io.BytesIO(data), filename=f"{ticket_channel.name}.txt")
        await log_ch.send(
            content=(
                f"🧾 **Ticket Transcript**\n"
                f"Channel: `{ticket_channel.name}`\n"
                f"Closed by: {closed_by}\n"
                f"Claimed by: {claimed_by}\n"
                f"Reason: {close_reason}"
            ),
            file=f
        )
        return True
    except Exception as e:
        print("Transcript send failed:", e)
        return False


# ============================================================
# CUSTOM IDS
# ============================================================
PANEL_SELECT_CID = "af_panel_select"


def cid_close(channel_id: int) -> str:
    return f"af_close:{channel_id}"


def cid_claim(channel_id: int) -> str:
    return f"af_claim:{channel_id}"


# ============================================================
# CLOSE MODAL
# ============================================================
class CloseReasonModal(discord.ui.Modal, title="Close Ticket"):
    reason = discord.ui.TextInput(
        label="Close reason",
        style=discord.TextStyle.long,
        required=True,
        max_length=400,
        placeholder="Example: Delivered / Resolved / Duplicate / etc."
    )

    def __init__(self, channel_id: int):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Invalid context.", ephemeral=True)
            return
        if not is_staff(interaction.user):
            await interaction.response.send_message("Only staff can close tickets.", ephemeral=True)
            return

        ch = interaction.guild.get_channel(self.channel_id)
        if not isinstance(ch, discord.TextChannel):
            await interaction.response.send_message("Ticket channel not found.", ephemeral=True)
            return

        await interaction.response.send_message("Closing ticket...", ephemeral=True)
        await close_ticket_flow(
            channel=ch,
            closed_by=f"{interaction.user} ({interaction.user.id})",
            reason=str(self.reason.value)
        )


# ============================================================
# TICKET CONTROLS VIEW
# ============================================================
class TicketControlView(discord.ui.View):
    def __init__(self, channel_id: int):
        super().__init__(timeout=None)
        self.channel_id = channel_id

        close_btn = discord.ui.Button(
            label="Close Ticket",
            style=discord.ButtonStyle.danger,
            emoji="🔒",
            custom_id=cid_close(channel_id),
        )
        close_btn.callback = self._close_callback
        self.add_item(close_btn)

        claim_btn = discord.ui.Button(
            label="Claim",
            style=discord.ButtonStyle.success,
            emoji="✋",
            custom_id=cid_claim(channel_id),
        )
        claim_btn.callback = self._claim_callback
        self.add_item(claim_btn)

    async def _close_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CloseReasonModal(self.channel_id))

    async def _claim_callback(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Invalid context.", ephemeral=True)
            return
        if not is_staff(interaction.user):
            await interaction.response.send_message("Only staff can claim tickets.", ephemeral=True)
            return

        row = await db_fetchrow(
            "SELECT owner_id, kind, ticket_num, claimed_by, status FROM tickets WHERE channel_id=$1",
            self.channel_id
        )
        if not row or row["status"] != "open":
            await interaction.response.send_message("Ticket not found or not open.", ephemeral=True)
            return
        if row["claimed_by"] is not None:
            await interaction.response.send_message("This ticket is already claimed.", ephemeral=True)
            return

        await db_execute(
            "UPDATE tickets SET claimed_by=$1, last_activity=NOW() WHERE channel_id=$2",
            interaction.user.id, self.channel_id
        )

        ch = interaction.guild.get_channel(self.channel_id)
        if isinstance(ch, discord.TextChannel):
            try:
                await hide_ticket_from_other_staff(ch, interaction.user)
            except Exception as e:
                print("Permission update failed:", e)

            await ch.send(f"✅ Ticket claimed by {interaction.user.mention}.")
            await refresh_ticket_control_message(ch)

        await interaction.response.send_message("✅ Ticket claimed.", ephemeral=True)


# ============================================================
# PANEL SELECT VIEW
# ============================================================
class TicketPanelSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Claim Order", value="claim", description="Claim your order", emoji="🛒"),
            discord.SelectOption(label="Custom Order", value="custom", description="Browse & buy an account", emoji="🛍️"),
            discord.SelectOption(label="Issues/Help", value="support", description="Get help", emoji="🎫"),
        ]
        super().__init__(
            placeholder="Select a category...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=PANEL_SELECT_CID,
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return

        guild = interaction.guild
        user = interaction.user
        kind = self.values[0]

        existing = await db_fetchrow(
            "SELECT channel_id FROM tickets WHERE guild_id=$1 AND owner_id=$2 AND kind=$3 AND status='open'",
            guild.id, user.id, kind
        )
        if existing:
            ch = guild.get_channel(int(existing["channel_id"]))
            if isinstance(ch, discord.TextChannel):
                await interaction.response.send_message(f"You already have an open ticket: {ch.mention}", ephemeral=True)
            else:
                await interaction.response.send_message("You already have an open ticket.", ephemeral=True)
            return

        cat_id = category_for_kind(kind)
        category = guild.get_channel(cat_id)
        if not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message("Ticket category not configured correctly.", ephemeral=True)
            return

        num = await get_next_ticket_num(kind)
        channel_name = make_channel_name(kind, user, num)

        staff_role = get_staff_role(guild)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            reason="Ticket created"
        )

        await db_execute(
            """
            INSERT INTO tickets(channel_id, guild_id, owner_id, kind, ticket_num, status)
            VALUES ($1,$2,$3,$4,$5,'open')
            """,
            channel.id, guild.id, user.id, kind, num
        )

        embed = ticket_embed(
            kind=kind,
            owner_mention=user.mention,
            claimed_by_mention=None,
            first_staff_seconds=None,
            footer_text="AF SERVICES • Status: Waiting for staff"
        )
        msg = await channel.send(content=user.mention, embed=embed, view=TicketControlView(channel.id), files=embed_files())

        await db_execute(
            "UPDATE tickets SET control_message_id=$1 WHERE channel_id=$2",
            msg.id, channel.id
        )

        bot.add_view(TicketControlView(channel.id), message_id=msg.id)

        if kind == "custom" and AI_ENABLED:
            await channel.send(embed=make_buy_intro_embed())

        await interaction.response.send_message(f"✅ Ticket created: {channel.mention}", ephemeral=True)


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketPanelSelect())


# ============================================================
# STAFF CHECK + COMMANDS
# ============================================================
def staff_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return False
        return is_staff(interaction.user)
    return app_commands.check(predicate)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    # Without this handler, a failed check (e.g. staff-only) silently drops the
    # interaction and Discord shows "Application did not respond".
    if isinstance(error, app_commands.CheckFailure):
        msg = "🚫 You don't have permission to use this command — staff only."
    else:
        msg = f"⚠️ Something went wrong while running this command:\n```{error}```"
        print("App command error:", repr(error))

    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception as e:
        print("Failed to deliver error message:", e)


@bot.tree.command(name="ticket_panel", description="Post the AF SERVICES ticket panel.")
@staff_only()
async def ticket_panel(interaction: discord.Interaction):
    await interaction.response.send_message(embed=panel_embed(), view=TicketPanelView(), files=embed_files(include_banner=True))


@bot.tree.command(name="close", description="Close the current ticket.")
@staff_only()
@app_commands.describe(reason="Reason for closing this ticket")
async def close_command(interaction: discord.Interaction, reason: str):
    if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Use this in a ticket channel.", ephemeral=True)
        return

    if not await is_ticket_channel(interaction.channel.id):
        await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)
        return

    await interaction.response.send_message("Closing ticket...", ephemeral=True)
    await close_ticket_flow(
        channel=interaction.channel,
        closed_by=f"{interaction.user} ({interaction.user.id})",
        reason=reason,
    )


@bot.tree.command(name="rename", description="Rename the current ticket channel.")
@staff_only()
@app_commands.describe(text="New channel name")
async def rename_command(interaction: discord.Interaction, text: str):
    if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Use this in a ticket channel.", ephemeral=True)
        return

    if not await is_ticket_channel(interaction.channel.id):
        await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)
        return

    new_name = sanitize_channel_rename(text)
    try:
        await interaction.channel.edit(name=new_name)
        await interaction.response.send_message(f"✅ Channel renamed to `{new_name}`.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Rename failed: {e}", ephemeral=True)


@bot.tree.command(name="purge", description="Close tickets in bulk.")
@staff_only()
@app_commands.describe(target="Use 'all' to close all open tickets")
@app_commands.choices(target=[app_commands.Choice(name="all", value="all")])
async def purge_command(interaction: discord.Interaction, target: app_commands.Choice[str]):
    if not interaction.guild:
        await interaction.response.send_message("Use this in a server.", ephemeral=True)
        return

    if target.value != "all":
        await interaction.response.send_message("Invalid purge target.", ephemeral=True)
        return

    rows = await db_fetch(
        "SELECT channel_id FROM tickets WHERE guild_id=$1 AND status='open'",
        interaction.guild.id,
    )
    if not rows:
        await interaction.response.send_message("No open tickets found.", ephemeral=True)
        return

    await interaction.response.send_message(f"Closing {len(rows)} open ticket(s)...", ephemeral=True)

    for row in rows:
        ch = interaction.guild.get_channel(int(row["channel_id"]))
        if isinstance(ch, discord.TextChannel):
            try:
                await close_ticket_flow(
                    channel=ch,
                    closed_by=f"{interaction.user} ({interaction.user.id})",
                    reason="Bulk purge: all open tickets",
                )
                await asyncio.sleep(1)
            except Exception as e:
                print(f"Failed to purge channel {ch.id}: {e}")


@bot.tree.command(name="ticket_stats", description="Show ticket stats overview (server).")
@staff_only()
async def ticket_stats(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Use in a server.", ephemeral=True)
        return

    totals = await db_fetchrow(
        """
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open,
          SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) AS closed,
          SUM(CASE WHEN status='deleted' THEN 1 ELSE 0 END) AS deleted
        FROM tickets
        WHERE guild_id=$1
        """,
        guild.id
    )

    by_kind = await db_fetch(
        """
        SELECT kind,
               COUNT(*) AS total,
               SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open
        FROM tickets
        WHERE guild_id=$1
        GROUP BY kind
        ORDER BY kind
        """,
        guild.id
    )

    avg_resp = await db_fetchrow(
        """
        SELECT AVG(first_staff_response_seconds)::float AS avg_first_response
        FROM tickets
        WHERE guild_id=$1 AND first_staff_response_seconds IS NOT NULL
        """,
        guild.id
    )

    avg_seconds = avg_resp["avg_first_response"]
    avg_text = "N/A" if avg_seconds is None else f"{int(avg_seconds) // 60}m {int(avg_seconds) % 60}s"

    e = discord.Embed(title="AF SERVICES • Ticket Stats", color=AF_BLUE)
    e.set_thumbnail(url=logo_ref())
    e.add_field(name="Total tickets", value=str(totals["total"]), inline=True)
    e.add_field(name="Open", value=str(totals["open"] or 0), inline=True)
    e.add_field(name="Closed", value=str(totals["closed"] or 0), inline=True)
    e.add_field(name="Deleted", value=str(totals["deleted"] or 0), inline=True)
    e.add_field(name="Avg first staff response", value=avg_text, inline=False)

    lines = []
    for r in by_kind:
        lines.append(f"**{kind_label(r['kind'])}**: total {r['total']}, open {r['open'] or 0}")
    e.add_field(name="By category", value="\n".join(lines) if lines else "No data", inline=False)

    await interaction.response.send_message(embed=e, ephemeral=True, files=embed_files())


# ============================================================
# STOCK / DELIVERY / VERIFICATION COMMANDS
# ============================================================
@bot.tree.command(name="stock", description="List the accounts in your stock.")
@staff_only()
@app_commands.describe(page="Page number (default 1)")
async def lzt_stock_command(interaction: discord.Interaction, page: int = 1):
    await interaction.response.defer(ephemeral=True)
    res = await lzt_list_owned(page=max(1, page))
    if not res["ok"]:
        await interaction.followup.send(f"⚠️ Stock error: `{res['error']}`", ephemeral=True)
        return
    if not res["items"]:
        await interaction.followup.send("No owned accounts found on this page.", ephemeral=True)
        return

    lines = []
    for it in res["items"][:25]:
        price = f" • {it['price']}" if it.get("price") is not None else ""
        state = f" • `{it['item_state']}`" if it.get("item_state") else ""
        lines.append(f"`{it['item_id']}` — {str(it['title'])[:60]}{price}{state}")

    e = discord.Embed(
        title="📦  Your Stock",
        description="\n".join(lines),
        color=AF_BLUE,
    )
    total = f" • total {res['total']}" if res.get("total") is not None else ""
    e.set_footer(text=f"AF SERVICES • Page {max(1, page)}{total} • Use the item ID with /deliver")
    await interaction.followup.send(embed=e, ephemeral=True)


LZT_CATEGORY_CHOICES = [
    app_commands.Choice(name="Fortnite", value="fortnite"),
    app_commands.Choice(name="Valorant", value="valorant"),
    app_commands.Choice(name="Steam", value="steam"),
    app_commands.Choice(name="Social Club", value="socialclub"),
]


@bot.tree.command(name="find_account", description="List VALID accounts you own in a category (excludes Invalid & Resold).")
@staff_only()
@app_commands.describe(category="Game category the customer wants")
@app_commands.choices(category=LZT_CATEGORY_CHOICES)
async def find_account_command(interaction: discord.Interaction, category: app_commands.Choice[str]):
    await interaction.response.defer(ephemeral=True)
    res = await lzt_find_valid(category.value)
    if not res["ok"]:
        await interaction.followup.send(f"⚠️ Stock error: `{res['error']}`", ephemeral=True)
        return

    used = await _delivered_item_ids()
    items = [it for it in res["items"]
             if int(re.sub(r"[^0-9]", "", str(it["item_id"])) or 0) not in used]
    if not items:
        await interaction.followup.send(
            f"No **valid** {category.name} accounts available in your purchases "
            f"(all are tagged Invalid/Resold or already delivered).", ephemeral=True)
        return

    lines = []
    for it in items[:25]:
        price = f" • {it['price']}" if it.get("price") is not None else ""
        lines.append(f"`{it['item_id']}` — {str(it['title'])[:60]}{price}")
    e = discord.Embed(
        title=f"✅  Valid {category.name} Stock",
        description="\n".join(lines),
        color=0x2ECC71,
    )
    e.set_footer(text=f"AF SERVICES • {len(items)} valid • excludes Invalid/Resold/already-delivered "
                      f"• deliver with /deliver_next or /deliver")
    await interaction.followup.send(embed=e, ephemeral=True)


@bot.tree.command(name="deliver_next",
                  description="Auto-pick the next VALID account in a category and deliver it into this ticket.")
@staff_only()
@app_commands.describe(category="Game category the customer wants")
@app_commands.choices(category=LZT_CATEGORY_CHOICES)
async def deliver_next_command(interaction: discord.Interaction, category: app_commands.Choice[str]):
    if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Use this in a ticket channel.", ephemeral=True)
        return
    row = await db_fetchrow(
        "SELECT owner_id, verified_order_id, verified_product FROM tickets WHERE channel_id=$1",
        interaction.channel.id,
    )
    if not row:
        await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    res = await lzt_find_valid(category.value)
    if not res["ok"]:
        await interaction.followup.send(f"⚠️ Stock error: `{res['error']}`", ephemeral=True)
        return

    used = await _delivered_item_ids()
    candidates = [it for it in res["items"]
                  if int(re.sub(r"[^0-9]", "", str(it["item_id"])) or 0) not in used]
    if not candidates:
        await interaction.followup.send(
            f"❌ No **valid** {category.name} accounts left to deliver "
            f"(all tagged Invalid/Resold or already delivered).", ephemeral=True)
        return

    pick = candidates[0]
    owner = interaction.guild.get_member(int(row["owner_id"])) or await bot.fetch_user(int(row["owner_id"]))
    await interaction.followup.send(
        f"📦 Delivering valid {category.name} account `{pick['item_id']}` — "
        f"{str(pick['title'])[:60]}...", ephemeral=True)
    await deliver_account(
        channel=interaction.channel,
        owner=owner,
        lzt_item_id=pick["item_id"],
        product=row["verified_product"] or category.name,
        order_id=row["verified_order_id"],
        delivered_by=interaction.user.id,
    )


MARKET_CATEGORY_CHOICES = [
    app_commands.Choice(name="Valorant", value="valorant"),
    app_commands.Choice(name="Fortnite", value="fortnite"),
]


@bot.tree.command(name="market",
                  description="Browse Valorant/Fortnite accounts available to buy, within a budget.")
@app_commands.describe(
    category="Game the customer wants",
    budget="Customer's max budget in EUR (optional — we find accounts that fit)",
    count="How many accounts to show (1-5, default 3)",
)
@app_commands.choices(category=MARKET_CATEGORY_CHOICES)
async def market_command(interaction: discord.Interaction, category: app_commands.Choice[str],
                         budget: float | None = None, count: int = 3):
    await interaction.response.defer()
    res = await lzt_search_market(category.value, budget=budget, count=count)
    if not res["ok"]:
        await interaction.followup.send(f"⚠️ Stock error: `{res['error']}`", ephemeral=True)
        return
    if not res["items"]:
        await interaction.followup.send(
            f"No {category.name} accounts found"
            + (f" under €{budget:.0f}." if budget else "."), ephemeral=True)
        return

    embeds, files = [], []
    for i, it in enumerate(res["items"]):
        det = await lzt_item_detail(it.get("item_id") or it.get("id"))
        embed, file = await build_account_message(category.value, det["item"] if det["ok"] else it, i)
        embeds.append(embed)
        if file:
            files.append(file)

    header = f"🛍️ **{category.name} accounts available**"
    if budget:
        header += f" — within a **€{budget:.0f}** budget"
    await interaction.followup.send(content=header, embeds=embeds[:10], files=files)


@bot.tree.command(name="account_info",
                  description="Show full details (skins, value, price) for one account.")
@app_commands.describe(item_id="Account listing/item ID")
async def account_info_command(interaction: discord.Interaction, item_id: str):
    await interaction.response.defer()
    det = await lzt_item_detail(item_id)
    if not det["ok"]:
        await interaction.followup.send(f"⚠️ Stock error: `{det['error']}`", ephemeral=True)
        return
    item = det["item"] or {}
    cid = (item.get("category") or {}).get("category_id") or item.get("category_id")
    category = "valorant" if cid == 13 else "fortnite" if cid == 9 else None
    if category is None:
        await interaction.followup.send(
            "ℹ️ That account isn't a Valorant or Fortnite account, so I can't render its stats.",
            ephemeral=True)
        return
    embed, file = await build_account_message(category, item, 0)
    await interaction.followup.send(embed=embed, files=[file] if file else [])


@bot.tree.command(name="reserve",
                  description="Reserve an account for this ticket — auto-alerts you to buy it once paid.")
@staff_only()
@app_commands.describe(item_id="The account listing the customer chose")
async def reserve_command(interaction: discord.Interaction, item_id: str):
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Use this in a ticket channel.", ephemeral=True)
        return
    row = await db_fetchrow("SELECT 1 FROM tickets WHERE channel_id=$1", interaction.channel.id)
    if not row:
        await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    det = await lzt_item_detail(item_id)
    if not det["ok"]:
        await interaction.followup.send(f"⚠️ Stock error: `{det['error']}`", ephemeral=True)
        return
    item = det["item"] or {}
    iid = int(re.sub(r"[^0-9]", "", str(item.get("item_id") or item_id)) or 0)
    src, resale = _resale_price(item)
    await db_execute(
        "UPDATE tickets SET reserved_market_item_id=$1, restock_alerted=FALSE WHERE channel_id=$2",
        iid, interaction.channel.id,
    )
    await interaction.followup.send(
        f"📌 Reserved listing `{iid}` for this ticket — **{str(item.get('title') or 'Account')[:80]}**\n"
        f"Cost €{src:.2f} → sells for €{resale:.0f}. "
        f"You'll get a restock alert automatically once the buyer's payment is verified.",
        ephemeral=True,
    )


@bot.tree.command(name="restock",
                  description="Alert yourself to re-buy a sold account ASAP.")
@staff_only()
@app_commands.describe(item_id="The account item ID the customer bought")
async def restock_command(interaction: discord.Interaction, item_id: str):
    if not interaction.guild:
        await interaction.response.send_message("Use this inside the server.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    det = await lzt_item_detail(item_id)
    if not det["ok"]:
        await interaction.followup.send(f"⚠️ Stock error: `{det['error']}`", ephemeral=True)
        return
    ticket = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
    status = await notify_restock(interaction.user, interaction.guild, det["item"] or {}, ticket)
    await interaction.followup.send(f"✅ {status}", ephemeral=True)


# ============================================================
# CRYPTO CHECKOUT VIEWS (NowPayments) — posted into a ticket
# ============================================================
class CryptoSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Select a cryptocurrency to pay with…",
            min_values=1, max_values=1, custom_id="af_crypto_select",
            options=[discord.SelectOption(label=lbl, value=tk) for lbl, tk in CRYPTO_CHOICES.items()],
        )

    async def callback(self, interaction: discord.Interaction):
        ch = interaction.channel
        if not isinstance(ch, discord.TextChannel):
            await interaction.response.send_message("Use this inside the ticket.", ephemeral=True)
            return
        row = await db_fetchrow("SELECT crypto_amount FROM tickets WHERE channel_id=$1", ch.id)
        if not row or row["crypto_amount"] is None:
            await interaction.response.send_message(
                "No price is set yet — staff must run `/crypto` first.", ephemeral=True)
            return
        amount = float(row["crypto_amount"])
        coin = self.values[0]
        await interaction.response.defer()
        pay = await np_create_payment(amount, coin, order_id=f"ticket-{ch.id}",
                                      description=f"AF SERVICES order ({ch.name})")
        if not pay["ok"]:
            await interaction.followup.send(
                f"⚠️ Couldn't create the {coin.upper()} payment: `{pay['error']}`", ephemeral=True)
            return
        await db_execute(
            "UPDATE tickets SET crypto_payment_id=$1, crypto_paid=FALSE WHERE channel_id=$2",
            pay["payment_id"], ch.id)
        e = discord.Embed(
            title=f"💎  Send {str(pay['pay_currency']).upper()} to Pay €{amount:.2f}",
            description=("Send **exactly** the amount below to this address. "
                         f"Sending less will not confirm.\n{DIVIDER}"),
            color=AF_BLUE,
        )
        e.add_field(name="Amount to send",
                    value=f"`{pay['pay_amount']} {str(pay['pay_currency']).upper()}`", inline=False)
        e.add_field(name="Address", value=f"`{pay['pay_address']}`", inline=False)
        e.set_footer(text="After sending, click ‘I’ve Paid — Check’ below.")
        await interaction.followup.send(embed=e, view=CryptoCheckView())


class CryptoPayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CryptoSelect())


class CryptoCheckView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="I've Paid — Check", style=discord.ButtonStyle.success,
                       emoji="✅", custom_id="af_crypto_check")
    async def check(self, interaction: discord.Interaction, button: discord.ui.Button):
        ch = interaction.channel
        if not isinstance(ch, discord.TextChannel):
            await interaction.response.send_message("Use this inside the ticket.", ephemeral=True)
            return
        row = await db_fetchrow(
            "SELECT crypto_payment_id, crypto_paid, owner_id FROM tickets WHERE channel_id=$1", ch.id)
        if not row or not row["crypto_payment_id"]:
            await interaction.response.send_message("No crypto payment is in progress here.", ephemeral=True)
            return
        if row["crypto_paid"]:
            await interaction.response.send_message("✅ This order is already marked paid.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        st = await np_get_payment(row["crypto_payment_id"])
        if not st["ok"]:
            await interaction.followup.send(f"⚠️ Couldn't check the payment: `{st['error']}`", ephemeral=True)
            return
        if not st["paid"]:
            await interaction.followup.send(
                f"⏳ Not confirmed yet (status: `{st['status']}`). Give it a minute and check again.",
                ephemeral=True)
            return
        await db_execute("UPDATE tickets SET crypto_paid=TRUE WHERE channel_id=$1", ch.id)
        await interaction.followup.send("✅ Payment confirmed — thank you!", ephemeral=True)
        owner = ch.guild.get_member(int(row["owner_id"])) or await bot.fetch_user(int(row["owner_id"]))
        staff_role = get_staff_role(ch.guild)
        await ch.send(
            content=(staff_role.mention if staff_role else None),
            embed=discord.Embed(
                title="✅  Crypto Payment Confirmed",
                description=f"{owner.mention}'s crypto payment is confirmed. Staff will deliver shortly.",
                color=0x2ECC71),
        )
        await maybe_fire_restock(ch)


@bot.tree.command(name="crypto",
                  description="Post a crypto (LTC/SOL/BTC/ETH) payment option in this ticket.")
@staff_only()
@app_commands.describe(amount="Price in EUR (optional — defaults to the reserved account's price)")
async def crypto_command(interaction: discord.Interaction, amount: float | None = None):
    if not NOWPAYMENTS_ENABLED:
        await interaction.response.send_message(
            "⚠️ Crypto payments aren't configured (set `NOWPAYMENTS_API_KEY`).", ephemeral=True)
        return
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Use this in a ticket channel.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    row = await db_fetchrow(
        "SELECT reserved_market_item_id FROM tickets WHERE channel_id=$1", interaction.channel.id)
    if not row:
        await interaction.followup.send("This is not a ticket channel.", ephemeral=True)
        return
    price = amount
    if price is None:
        if not row["reserved_market_item_id"]:
            await interaction.followup.send(
                "No account reserved — run `/reserve` first, or pass an amount: `/crypto 25`.",
                ephemeral=True)
            return
        det = await lzt_item_detail(row["reserved_market_item_id"])
        if not det["ok"]:
            await interaction.followup.send(
                f"⚠️ Couldn't load the reserved account: `{det['error']}`", ephemeral=True)
            return
        _, price = _resale_price(det["item"] or {})
    if not price or price <= 0:
        await interaction.followup.send("Couldn't determine a price.", ephemeral=True)
        return
    await db_execute(
        "UPDATE tickets SET crypto_amount=$1, crypto_payment_id=NULL, crypto_paid=FALSE WHERE channel_id=$2",
        price, interaction.channel.id)
    e = discord.Embed(
        title="💳  Pay with Crypto",
        description=(f"**Total: €{price:.2f}**\n\nChoose a coin below to get your payment address.\n"
                     f"We accept **LTC, SOL, BTC, ETH**.\n{DIVIDER}"),
        color=AF_BLUE,
    )
    e.set_thumbnail(url=logo_ref())
    e.set_footer(text="AF SERVICES • Crypto Checkout")
    await interaction.channel.send(embed=e, view=CryptoPayView(), files=embed_files())
    await interaction.followup.send("✅ Crypto checkout posted.", ephemeral=True)


@bot.tree.command(name="verify_order", description="Check a SellAuth order/invoice and its payment status.")
@staff_only()
@app_commands.describe(order_id="The SellAuth invoice / order ID")
async def verify_order_command(interaction: discord.Interaction, order_id: str):
    await interaction.response.defer(ephemeral=True)
    res = await sellauth_get_invoice(order_id)
    if not res["ok"]:
        await interaction.followup.send(f"⚠️ SellAuth error: `{res['error']}`", ephemeral=True)
        return
    if not res["found"]:
        await interaction.followup.send(f"❌ Order `{order_id}` not found on SellAuth.", ephemeral=True)
        return

    already = await order_already_delivered(order_id)
    e = discord.Embed(
        title="🔎  SellAuth Order",
        color=0x2ECC71 if res["paid"] else 0xE67E22,
    )
    e.add_field(name="Order", value=f"`{order_id}`", inline=True)
    e.add_field(name="Status", value=f"`{res.get('status')}`", inline=True)
    e.add_field(name="Paid", value="✅ Yes" if res["paid"] else "❌ Not yet", inline=True)
    if res.get("amount") is not None:
        e.add_field(name="Amount", value=str(res["amount"]), inline=True)
    if res.get("email"):
        e.add_field(name="Email", value=str(res["email"]), inline=True)
    if already:
        e.add_field(name="⚠️ Already delivered", value="This order was already used.", inline=False)
    await interaction.followup.send(embed=e, ephemeral=True)


@bot.tree.command(name="deliver", description="Manually release an account into this ticket.")
@staff_only()
@app_commands.describe(
    item_id="Item ID to deliver (see /stock)",
    order_id="Optional SellAuth order ID (for the record / duplicate-protection)",
    product="Optional product label",
)
async def deliver_command(interaction: discord.Interaction, item_id: str,
                          order_id: str | None = None, product: str | None = None):
    if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Use this in a ticket channel.", ephemeral=True)
        return

    row = await db_fetchrow(
        "SELECT owner_id, verified_order_id, verified_product FROM tickets WHERE channel_id=$1",
        interaction.channel.id,
    )
    if not row:
        await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)
        return

    owner = interaction.guild.get_member(int(row["owner_id"]))
    if owner is None:
        owner = await bot.fetch_user(int(row["owner_id"]))

    await interaction.response.send_message("📦 Delivering account...", ephemeral=True)
    await deliver_account(
        channel=interaction.channel,
        owner=owner,
        lzt_item_id=item_id,
        product=product or row["verified_product"],
        order_id=order_id or row["verified_order_id"],
        delivered_by=interaction.user.id,
    )


# ============================================================
# GUIDE COMMAND
# ============================================================
GUIDE_STEAM_COLOR = 0x00ADEF
GUIDE_RIOT_COLOR  = 0xFF4655
GUIDE_EPIC_COLOR  = AF_BLUE

DIVIDER = "━━━━━━━━━━━━━━━━━━━━━━━━"


def make_steam_guide_embed() -> discord.Embed:
    e = discord.Embed(
        title="🎮  Steam — Account Security Guide",
        description=f"Follow these steps to properly secure your Steam account.\n{DIVIDER}",
        color=GUIDE_STEAM_COLOR,
    )
    steps = [
        ("1️⃣  Change Profile Info",
         "Update your **username**, **profile picture**, **display name**, and **country**."),
        ("2️⃣  Link Phone Number (2FA)",
         "Go to account settings and **link your phone number** to enable Two-Factor Authentication."),
        ("3️⃣  Enable Steam Guard",
         "Activate **Steam Guard Mobile Authenticator** for maximum account protection."),
        ("4️⃣  Block All Friends",
         "**Block every existing friend** on the account to cut off the previous owner's access."),
        ("5️⃣  Make Profile Private",
         "Go to **Privacy Settings** and set your profile to **Private** across all categories."),
    ]
    for name, value in steps:
        e.add_field(name=name, value=value, inline=False)
    e.set_author(name="AF SERVICES • Account Guides")
    e.set_thumbnail(url=logo_ref())
    e.set_footer(text="AF SERVICES | Steam Guide")
    return e


def make_riot_guide_embed() -> discord.Embed:
    e = discord.Embed(
        title="⚔️  Riot Games / Valorant — Account Security Guide",
        description=f"Follow these steps to properly secure your Riot Games / Valorant account.\n{DIVIDER}",
        color=GUIDE_RIOT_COLOR,
    )
    steps = [
        ("1️⃣  Change Name & Username",
         "Update both the **in-game display name** and your **Riot username** in account settings."),
        ("2️⃣  Add a Google Account (Required!)",
         "🔗 **Add a Google account to the Valorant/Riot account** in your sign-in settings.\n"
         "Use a **Google account you fully control** — this locks the account to you, and it's "
         "**the proof we require for a replacement**: if anything goes wrong you must show that you "
         "can **no longer log in** with that Google account."),
        ("3️⃣  Block All Friends",
         "Go through your **friends list** and **block every contact**."),
        ("4️⃣  Wait 2–3 Days",
         "⏳ **Do not change the password yet.** Wait **2–3 days** before doing so."),
        ("5️⃣  Add 2FA (If Needed for Ranked)",
         "Enable **Two-Factor Authentication** if it is required to participate in ranked modes."),
        ("6️⃣  Check Riot Support — Close Active Tickets",
         "Visit **Riot Support** and check for **active tickets**.\n"
         "If any are open, reply with:\n"
         "> *\"I have dealt with the problem, you can close, I won't need it for now.\"*"),
        ("♻️  Need a Replacement Later?",
         "Always **log in with the same email / Google account** you linked here.\n"
         "Tap **♻️ Need a replacement?** below to see exactly what to record as proof."),
    ]
    for name, value in steps:
        e.add_field(name=name, value=value, inline=False)
    e.set_author(name="AF SERVICES • Account Guides")
    e.set_thumbnail(url=logo_ref())
    e.set_footer(text="AF SERVICES | Riot Games Guide")
    return e


def make_riot_replacement_embed() -> discord.Embed:
    """Shown when a buyer taps 'Need a replacement?' — the proof their video must contain."""
    e = discord.Embed(
        title="♻️  Riot / Valorant — How to Claim a Replacement",
        description=(
            "To receive a replacement you **must** provide **one uncut video** as proof.\n"
            "Make sure every item below is clearly visible in that single recording.\n"
            f"{DIVIDER}"
        ),
        color=GUIDE_RIOT_COLOR,
    )
    e.add_field(
        name="🔑  Use the SAME Google account",
        value="On camera, attempt to sign in using the **exact Google account you added** to the "
              "Valorant account when you secured it. Show that **Google email on screen** first.",
        inline=False,
    )
    e.add_field(
        name="🚫  Prove the Google login no longer works",
        value="The key proof: show that signing in with **that Google account no longer gives you "
              "access** to the Valorant account — it's been removed / changed and you're **locked out**.",
        inline=False,
    )
    e.add_field(
        name="🎥  One continuous, uncut video",
        value="• **No cuts, no edits, no pauses** — a single take.\n"
              "• Record your **full screen** (not a cropped/phone-camera clip).\n"
              "• Show the **current date** on screen (e.g. open a clock/date site).",
        inline=False,
    )
    e.add_field(
        name="📨  Submit it",
        value="Open a **ticket** here and attach the video. Staff will verify and issue your replacement.",
        inline=False,
    )
    e.set_author(name="AF SERVICES • Replacement Policy")
    e.set_thumbnail(url=logo_ref())
    e.set_footer(text="AF SERVICES | Riot Games Replacement")
    return e


class RiotGuideView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Need a replacement?",
        style=discord.ButtonStyle.danger,
        custom_id="af_riot_replacement",
        emoji="♻️",
    )
    async def replacement_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=make_riot_replacement_embed(),
            ephemeral=True,
        )


def make_replacement_policy_embed() -> discord.Embed:
    """Game-agnostic replacement policy, sent to every buyer after a confirmed purchase."""
    e = discord.Embed(
        title="♻️  Replacement Policy — Please Read",
        description=(
            "If something is wrong with your account, you're covered — here's how it works.\n"
            f"{DIVIDER}"
        ),
        color=0x2ECC71,
    )
    e.add_field(
        name="✅ You qualify for a replacement if",
        value="The account is **dead on arrival** or stops working before you've changed the "
              "login details — as long as you followed the security guide.",
        inline=False,
    )
    e.add_field(
        name="🎥 What you must provide",
        value="**One uncut video** (no cuts/edits) that:\n"
              "• Logs in **live** using the **same email / login** we delivered\n"
              "• Shows the **error or lockout** clearly\n"
              "• Shows your **full screen** and the **current date**",
        inline=False,
    )
    e.add_field(
        name="🚫 Not covered",
        value="Issues caused **after** you changed the email/password without following the guide, "
              "or claims made without the video proof above.",
        inline=False,
    )
    e.add_field(
        name="📨 How to claim",
        value="Open a ticket here with your video and order ID — staff will verify and replace it fast.",
        inline=False,
    )
    e.set_author(name="AF SERVICES • Replacement Policy")
    e.set_thumbnail(url=logo_ref())
    e.set_footer(text="AF SERVICES | Warranty & Replacements")
    return e


def detect_game(*texts: str | None) -> str | None:
    """Best-effort game detection from a product label / account title."""
    blob = " ".join(t for t in texts if t).lower()
    if any(k in blob for k in ("valorant", "riot", "league")):
        return "valorant"
    if any(k in blob for k in ("fortnite", "epic")):
        return "fortnite"
    if "steam" in blob:
        return "steam"
    return None


async def post_purchase_followup(channel: discord.TextChannel, owner: discord.abc.User,
                                 product: str | None, title: str | None) -> None:
    """After a confirmed delivery, send the matching security guide + replacement policy."""
    game = detect_game(product, title)
    if game == "valorant":
        guides = [make_riot_guide_embed()]
    elif game == "fortnite":
        guides = [make_epic_guide_embed()]
    elif game == "steam":
        guides = [make_steam_guide_embed()]
    else:
        guides = []
    embeds = guides + [make_replacement_policy_embed()]

    view = RiotGuideView() if game == "valorant" else None
    await channel.send(
        content=(f"{owner.mention} ✅ **Purchase confirmed!** Please read the security guide and our "
                 f"replacement policy below — securing the account correctly keeps your warranty valid."),
        embeds=embeds[:10],
        view=view,
        files=embed_files(),
    )


def make_epic_guide_embed() -> discord.Embed:
    e = discord.Embed(
        title="🎯  Epic Games — Account Security Guide",
        description=f"Follow these steps to properly secure an Epic Games / Fortnite account.\n{DIVIDER}",
        color=GUIDE_EPIC_COLOR,
    )
    steps = [
        ("1️⃣  Change User ID",
         "Update the **account display name / user ID** if the option is available in settings."),
        ("2️⃣  Create a Fake Recovery Ticket",
         "Submit a recovery ticket via [Epic ID Recovery](https://www.epicgames.com/id/login/recovery/help) "
         "to lock in account recovery access."),
        ("3️⃣  Block All Friends & Connections",
         "**Block every friend and linked connection** on the account.\n"
         "You can use [FishStick FN](https://t.me/fishstickfn) for assistance."),
        ("4️⃣  Download Account PDF",
         "Download the **PDF with your account information** from "
         "[Account Settings](https://www.epicgames.com/id/login?redirect_uri=https%3A%2F%2Fwww.epicgames.com%2Faccount%2Fpersonal&prompt=select_account&display=guided)."),
        ("5️⃣  Waiting Periods",
         "⏳ Wait at least **8 days** before any event/tournament *(if different country or region)*.\n"
         "⏳ Wait **2 weeks** before making **ANY purchases**."),
        ("6️⃣  V-Bucks Accounts — Use All Refund Credits",
         "💰 **USE ALL REFUND CREDITS** — earn more V-Bucks and greatly reduce the risk of a skin revert."),
        ("7️⃣  No 2FA on Ramblers",
         "🚫 **DO NOT add 2FA** to any ramblers!"),
    ]
    for name, value in steps:
        e.add_field(name=name, value=value, inline=False)
    e.set_author(name="AF SERVICES • Account Guides")
    e.set_thumbnail(url=logo_ref())
    e.set_footer(text="AF SERVICES | Epic Games Guide")
    return e


@bot.tree.command(name="guide", description="View the account security guide for a game platform.")
@app_commands.describe(game="Select the game platform")
@app_commands.choices(game=[
    app_commands.Choice(name="Steam", value="steam"),
    app_commands.Choice(name="Riot Games", value="riot_game"),
    app_commands.Choice(name="Epic Games", value="epic_games"),
])
async def guide_command(interaction: discord.Interaction, game: app_commands.Choice[str]):
    if game.value == "steam":
        embed = make_steam_guide_embed()
        await interaction.response.send_message(embed=embed, files=embed_files())
    elif game.value == "riot_game":
        embed = make_riot_guide_embed()
        await interaction.response.send_message(
            embed=embed, view=RiotGuideView(), files=embed_files()
        )
    else:
        embed = make_epic_guide_embed()
        await interaction.response.send_message(embed=embed, files=embed_files())


# ============================================================
# FORTNITE FAKE TICKET GUIDE
# ============================================================
def make_fortnite_faketicket_embed() -> discord.Embed:
    e = discord.Embed(
        title="🎟️  Fortnite — How to Make a Recovery Ticket",
        description=(
            "**⚠️ There can only be 1 recovery ticket per account — be quick!**\n"
            "Submit before the previous owner gets the chance.\n\n"
            f"🔗 **Recovery Form:** [epicgames.com/id/login/recovery/help](https://www.epicgames.com/id/login/recovery/help)\n\n"
            "**Before you start:**\n"
            "🌐 Open a **VPN**\n"
            "📧 Get a temp email at **[tempmail.ninja](https://tempmail.ninja/)**\n"
            f"{DIVIDER}"
        ),
        color=0x00D4FF,
    )
    steps = [
        ("1️⃣  New Email Address",
         "Enter a **new email address** that has **no existing Epic ID** linked — "
         "this becomes the new address for the account."),
        ("2️⃣  Account ID",
         "Get the **Account ID** from account settings.\n"
         "*(PDF access is **not** required for this step.)*"),
        ("3️⃣  Current Email on the Account",
         "Enter the **current email address** linked to the account *(the one you already have access to)*."),
        ("4️⃣  Display Name",
         "Enter the **display name** exactly as shown in account settings."),
        ("5️⃣  Personal Details",
         "• **Name:** Guess using **first and last letters** — *accuracy not required, just be fast*\n"
         "• **Country:** Shown in account settings\n"
         "• **City:** Any guess works *(doesn't need to be accurate)*"),
        ("6️⃣  Connected Accounts",
         "Select that you **haven't connected any accounts**. *(Skip this question)*"),
        ("7️⃣  Payment Methods",
         "Select that you **haven't used a card**. *(Skip this question)*"),
        ("8️⃣  Support Message",
         "Use the following message:\n"
         "> *\"Hello, I recently moved to [your country] and saw that I no longer have access to my email. "
         "I am making this request so I can regain access.\"*"),
    ]
    for name, value in steps:
        e.add_field(name=name, value=value, inline=False)
    e.add_field(
        name="🔄  Ticket Declined?",
        value="**Redo the ticket immediately** — resubmit after every decline.",
        inline=False,
    )
    e.set_author(name="AF SERVICES • Account Guides")
    e.set_thumbnail(url=logo_ref())
    e.set_footer(text="AF SERVICES | Fortnite Recovery Guide")
    return e


@bot.tree.command(name="fortnite_faketicketguide", description="How to make a recovery ticket for a Fortnite account.")
async def fortnite_faketicketguide_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=make_fortnite_faketicket_embed(), files=embed_files())


# ============================================================
# CARD PAYMENT GUIDE
# ============================================================
class PaidProofModal(discord.ui.Modal, title="Submit Payment Proof"):
    screenshot = discord.ui.TextInput(
        label="Payment Page Screenshot URL",
        style=discord.TextStyle.short,
        required=True,
        placeholder="https://i.imgur.com/... or any image URL",
        max_length=500,
    )
    email_proof = discord.ui.TextInput(
        label="Email Confirmation Screenshot URL",
        style=discord.TextStyle.short,
        required=False,
        placeholder="https://i.imgur.com/... (optional but recommended)",
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Submit this inside the server.", ephemeral=True)
            return

        log_ch = await get_log_channel(interaction.guild)

        e = discord.Embed(
            title="💳  Payment Proof Received",
            color=0x2ECC71,
        )
        e.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        e.add_field(
            name="Submitted by",
            value=f"{interaction.user.mention} (`{interaction.user.id}`)",
            inline=False,
        )
        e.add_field(name="📸  Payment Screenshot", value=self.screenshot.value, inline=False)
        if self.email_proof.value:
            e.add_field(name="📧  Email Confirmation", value=self.email_proof.value, inline=False)
        e.set_footer(text=f"AF SERVICES | {utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

        if log_ch:
            await log_ch.send(embed=e)

        await interaction.response.send_message(
            "✅ **Payment proof submitted!** A staff member will review it shortly.",
            ephemeral=True,
        )


class CardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Paid",
        style=discord.ButtonStyle.success,
        custom_id="af_card_paid",
        emoji="✅",
    )
    async def paid_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PaidProofModal())


def card_proof_payment_ref() -> str:
    return CARD_PROOF_PAYMENT_URL or f"attachment://{CARD_PROOF_PAYMENT_FILENAME}"


def card_proof_email_ref() -> str:
    return CARD_PROOF_EMAIL_URL or f"attachment://{CARD_PROOF_EMAIL_FILENAME}"


def card_proof_files() -> list[discord.File]:
    """Fresh File objects for the two card example screenshots. Single-use."""
    files: list[discord.File] = []
    if not CARD_PROOF_PAYMENT_URL and os.path.exists(CARD_PROOF_PAYMENT_PATH):
        files.append(discord.File(CARD_PROOF_PAYMENT_PATH, filename=CARD_PROOF_PAYMENT_FILENAME))
    if not CARD_PROOF_EMAIL_URL and os.path.exists(CARD_PROOF_EMAIL_PATH):
        files.append(discord.File(CARD_PROOF_EMAIL_PATH, filename=CARD_PROOF_EMAIL_FILENAME))
    return files


def make_card_guide_embed() -> discord.Embed:
    e = discord.Embed(
        title="💳  How to Purchase Using Card",
        description=f"Follow the steps below to complete your purchase.\n{DIVIDER}",
        color=0x2ECC71,
    )
    steps = [
        ("1️⃣  Visit the Store",
         "Head over to **[accsforge.fun](https://www.accsforge.fun/)**."),
        ("2️⃣  Select the 1€ Option",
         "Choose the **1 EURO** option — it is the first product listed."),
        ("3️⃣  Set the Quantity",
         "Set the quantity to the **price of your product**, then add **20% on top** to cover taxes.\n"
         "*(Example: product costs €10 → set quantity to **12**)*"),
        ("4️⃣  Add to Cart & Checkout",
         "Add the product to your cart, click the **cart icon**, and proceed through to payment."),
        ("5️⃣  Save Your Payment Proof",
         "📸 Take a screenshot of:\n"
         "• The **payment confirmation page**\n"
         "• The **email confirmation** received after payment\n\n"
         "Upload both to an image host *(e.g. Imgur)* and click **✅ Paid** below to submit."),
    ]
    for name, value in steps:
        e.add_field(name=name, value=value, inline=False)
    e.set_author(name="AF SERVICES • Payment Guide")
    e.set_thumbnail(url=logo_ref())
    e.set_footer(text="AF SERVICES | Card Payment Guide")
    return e


def make_card_embeds() -> list[discord.Embed]:
    """The card guide plus the two example screenshots buyers should reproduce."""
    main = make_card_guide_embed()

    ex_pay = discord.Embed(
        title="📸  Example — Payment Confirmation Page",
        description="Your payment-page screenshot should look like this:",
        color=0x2ECC71,
    )
    ex_pay.set_image(url=card_proof_payment_ref())

    ex_email = discord.Embed(
        title="📧  Example — Email Confirmation",
        description="And your confirmation email should look like this:",
        color=0x2ECC71,
    )
    ex_email.set_image(url=card_proof_email_ref())

    return [main, ex_pay, ex_email]


@bot.tree.command(name="card", description="How to purchase using a card payment method.")
async def card_command(interaction: discord.Interaction):
    await interaction.response.send_message(
        embeds=make_card_embeds(),
        view=CardView(),
        files=embed_files() + card_proof_files(),
    )


# ============================================================
# STAFF APPROVAL / DELIVERY VIEW
# Posted in a claim ticket once payment is (or could not be) verified. Staff
# enters the LZT item id to release. custom_ids are static and the action reads
# the ticket channel directly, so the buttons survive bot restarts.
# ============================================================
APPROVE_DELIVER_CID = "af_approve_deliver"
REJECT_DELIVER_CID = "af_reject_deliver"


class ApproveDeliverModal(discord.ui.Modal, title="Approve & Deliver"):
    item_id = discord.ui.TextInput(
        label="Account item ID to deliver",
        style=discord.TextStyle.short,
        required=True,
        max_length=30,
        placeholder="e.g. 12345678  (see /stock)",
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        ch = interaction.channel
        if not isinstance(ch, discord.TextChannel):
            await interaction.response.send_message("Use this in a ticket channel.", ephemeral=True)
            return

        row = await db_fetchrow(
            "SELECT owner_id, verified_order_id, verified_product FROM tickets WHERE channel_id=$1",
            ch.id,
        )
        if not row:
            await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)
            return

        owner = ch.guild.get_member(int(row["owner_id"]))
        if owner is None:
            owner = await bot.fetch_user(int(row["owner_id"]))

        await interaction.response.send_message("📦 Delivering account...", ephemeral=True)
        await deliver_account(
            channel=ch,
            owner=owner,
            lzt_item_id=str(self.item_id.value),
            product=row["verified_product"],
            order_id=row["verified_order_id"],
            delivered_by=interaction.user.id,
        )


class DeliveryApprovalView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Approve & Deliver", style=discord.ButtonStyle.success,
                       emoji="✅", custom_id=APPROVE_DELIVER_CID)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        await interaction.response.send_modal(ApproveDeliverModal())

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger,
                       emoji="🚫", custom_id=REJECT_DELIVER_CID)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        await interaction.response.send_message("Marked as rejected — no account released.", ephemeral=True)
        if isinstance(interaction.channel, discord.TextChannel):
            await interaction.channel.send(f"🚫 Delivery rejected by {interaction.user.mention}.")


async def post_delivery_approval(channel: discord.TextChannel, owner: discord.abc.User,
                                 product: str | None, order_id: str | None,
                                 verification: dict | None) -> None:
    """Drop the staff Approve/Reject panel into a claim ticket."""
    staff_role = get_staff_role(channel.guild)
    if verification and verification.get("paid"):
        status_line = f"✅ **Payment verified** via SellAuth (status: `{verification.get('status')}`)."
    elif verification and verification.get("ok") and not verification.get("found"):
        status_line = f"⚠️ Order `{order_id}` **not found** on SellAuth — verify manually."
    elif verification and verification.get("error"):
        status_line = f"⚠️ Could not auto-verify (`{verification.get('error')}`) — verify manually."
    else:
        status_line = "ℹ️ Manual verification (SellAuth not configured)."

    e = discord.Embed(
        title="🛒  Ready to Deliver — Staff Approval",
        description=(
            f"{status_line}\n\n"
            f"**Buyer:** {owner.mention}\n"
            f"**Product:** {product or 'unspecified'}\n"
            f"**Order ID:** {f'`{order_id}`' if order_id else 'not provided'}\n\n"
            "Click **Approve & Deliver** and enter the account item ID to release the account."
        ),
        color=AF_BLUE,
    )
    e.set_thumbnail(url=logo_ref())
    e.set_footer(text="AF SERVICES • Staff action required")
    content = staff_role.mention if staff_role else None
    await channel.send(content=content, embed=e, view=DeliveryApprovalView())

    # Auto restock alert: if this ticket reserved a market account and payment is verified,
    # tell us to re-buy that exact listing ASAP (fires once per ticket).
    if verification and verification.get("paid"):
        await maybe_fire_restock(channel)


async def maybe_fire_restock(channel: discord.TextChannel) -> None:
    """Fire the restock alert once if the ticket has a reserved, not-yet-alerted market item."""
    row = await db_fetchrow(
        "SELECT reserved_market_item_id, restock_alerted FROM tickets WHERE channel_id=$1",
        channel.id,
    )
    if not row or not row["reserved_market_item_id"] or row["restock_alerted"]:
        return
    det = await lzt_item_detail(row["reserved_market_item_id"])
    if not det["ok"]:
        await channel.send(f"⚠️ Reserved account verified, but I couldn't load it for the "
                           f"restock alert: `{det['error']}`")
        return
    await notify_restock(None, channel.guild, det["item"] or {}, channel)
    await db_execute("UPDATE tickets SET restock_alerted=TRUE WHERE channel_id=$1", channel.id)


# ============================================================
# AI INTAKE (Claude) — claim tickets
# ============================================================
AI_SYSTEM_PROMPT = (
    "You are the intake assistant for AF SERVICES, a Discord shop that resells gaming "
    "accounts. You are talking to a buyer inside a 'Claim Order' ticket — someone claiming "
    "an order they already paid for. Your goals, in order:\n"
    "1. Find out WHICH product/account they purchased (a short product name or key).\n"
    "2. Get their SellAuth ORDER ID / invoice ID. This is the only proof we trust — "
    "screenshots are easy to fake, so politely insist on the order ID.\n"
    "Be warm, concise, and human. Never reveal or promise specific account credentials "
    "yourself — once payment is confirmed, a staff member releases the account.\n\n"
    "Respond with ONLY a JSON object, no prose around it:\n"
    '{"reply": "<your message to the buyer>", "product": <string|null>, '
    '"order_id": <string|null>, "ready": <true only when you have BOTH a product and an order_id>}'
)


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


async def run_ai_intake(channel: discord.TextChannel, owner: discord.abc.User) -> None:
    if not (AI_ENABLED and anthropic_client):
        return

    # Build the conversation so far (buyer + bot), mapped to Anthropic roles.
    history: list[dict] = []
    async for m in channel.history(limit=25, oldest_first=True):
        if not m.content:
            continue
        role = "assistant" if (bot.user and m.author.id == bot.user.id) else "user"
        history.append({"role": role, "content": m.content[:1500]})
    while history and history[0]["role"] != "user":
        history.pop(0)
    if not history:
        return
    # Anthropic requires alternating roles — merge consecutive same-role turns.
    merged: list[dict] = []
    for h in history:
        if merged and merged[-1]["role"] == h["role"]:
            merged[-1]["content"] += "\n" + h["content"]
        else:
            merged.append(dict(h))

    try:
        resp = await anthropic_client.messages.create(
            model=AI_MODEL, max_tokens=600, system=AI_SYSTEM_PROMPT, messages=merged
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    except Exception as e:
        print("AI intake error:", e)
        return

    data = _extract_json(raw) or {}
    reply = data.get("reply") or "Could you tell me which product you bought and your order ID?"
    await channel.send(reply)

    product = (data.get("product") or None)
    order_id = (data.get("order_id") or None)
    if not (data.get("ready") and order_id):
        return

    # We have enough to attempt verification.
    if order_id and await order_already_delivered(order_id):
        await channel.send("⚠️ That order has already been delivered. A staff member will check.")
        return

    verification = None
    if REQUIRE_SELLAUTH and SELLAUTH_ENABLED:
        verification = await sellauth_get_invoice(order_id)
        if verification["ok"] and verification["found"] and not verification["paid"]:
            await channel.send(
                f"I found order `{order_id}` but it isn't marked paid yet "
                f"(status: `{verification.get('status')}`). Ping me once the payment completes."
            )
            return

    # Persist what we verified so the staff approval action can read it.
    await db_execute(
        "UPDATE tickets SET verified_order_id=$1, verified_product=$2, ai_handled=TRUE WHERE channel_id=$3",
        order_id, product, channel.id,
    )
    await post_delivery_approval(channel, owner, product, order_id, verification)


# ============================================================
# AI SHOPPING ASSISTANT — "Buy Account" tickets
# ============================================================
def make_buy_intro_embed() -> discord.Embed:
    e = discord.Embed(
        title="🛍️  What are you looking for?",
        description=(
            "Tell me what you'd like to buy and your **budget**, and I'll pull up matching "
            "accounts right away.\n\n"
            "We currently stock **Valorant** and **Fortnite** accounts.\n\n"
            "**For example:**\n"
            "> *\"A Valorant account with some skins, around €25\"*\n"
            "> *\"Fortnite account with OG skins, budget 40 euros\"*\n\n"
            f"{DIVIDER}\nJust type your request below 👇"
        ),
        color=AF_BLUE,
    )
    e.set_thumbnail(url=logo_ref())
    e.set_footer(text="AF SERVICES • Account Shop")
    return e


AI_SHOP_PROMPT = (
    "You are the shopping assistant for AF SERVICES, a Discord shop that sells gaming accounts. "
    "You are talking to a customer in a ticket who wants to browse and buy an account. "
    "We ONLY stock two games: Valorant and Fortnite. "
    "Your job: figure out (1) which game they want and (2) their budget in EUR. "
    "If they mention a game we don't carry, politely say we only have Valorant and Fortnite. "
    "If you don't yet know the game or budget, ask for the missing piece in a warm, brief way, "
    "and give a quick example of how to phrase it. "
    "When you have BOTH a game (valorant or fortnite) and a numeric EUR budget, set ready=true — "
    "the system will then automatically show matching accounts, so your reply should tell them "
    "you're pulling up options now (do NOT invent account details or prices yourself).\n\n"
    "Respond with ONLY a JSON object, no prose around it:\n"
    '{"reply": "<your message>", "game": <"valorant"|"fortnite"|null>, '
    '"budget": <number|null>, "ready": <true only when you have BOTH game and budget>}'
)


async def run_ai_shopping(channel: discord.TextChannel, owner: discord.abc.User) -> None:
    if not (AI_ENABLED and anthropic_client):
        return

    history: list[dict] = []
    async for m in channel.history(limit=25, oldest_first=True):
        if not m.content:
            continue
        role = "assistant" if (bot.user and m.author.id == bot.user.id) else "user"
        history.append({"role": role, "content": m.content[:1500]})
    while history and history[0]["role"] != "user":
        history.pop(0)
    if not history:
        return
    merged: list[dict] = []
    for h in history:
        if merged and merged[-1]["role"] == h["role"]:
            merged[-1]["content"] += "\n" + h["content"]
        else:
            merged.append(dict(h))

    try:
        resp = await anthropic_client.messages.create(
            model=AI_MODEL, max_tokens=500, system=AI_SHOP_PROMPT, messages=merged
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    except Exception as e:
        print("AI shopping error:", e)
        return

    data = _extract_json(raw) or {}
    reply = data.get("reply") or "What game and budget are you looking for?"
    await channel.send(reply)

    game = (data.get("game") or "").lower()
    budget = data.get("budget")
    try:
        budget = float(budget) if budget is not None else None
    except (TypeError, ValueError):
        budget = None
    if not (data.get("ready") and game in LZT_MARKET_SLUGS and budget and budget > 0):
        return

    # Show matching accounts within budget (source price capped at budget / markup).
    res = await lzt_search_market(game, budget=budget, count=3)
    if not res["ok"]:
        await channel.send(f"⚠️ I couldn't reach the stock right now (`{res['error']}`). "
                           f"A staff member will help you shortly.")
        return
    if not res["items"]:
        await channel.send(
            f"I couldn't find a {game.title()} account within €{budget:.0f} right now. "
            f"Try a higher budget, or a staff member can source one for you.")
        return

    embeds, files = [], []
    for i, it in enumerate(res["items"]):
        det = await lzt_item_detail(it.get("item_id") or it.get("id"))
        embed, file = await build_account_message(game, det["item"] if det["ok"] else it, i)
        embeds.append(embed)
        if file:
            files.append(file)
    await channel.send(
        content=f"Here are the best **{game.title()}** accounts I found within **€{budget:.0f}** 👇",
        embeds=embeds[:10],
        files=files,
    )
    await channel.send("Let me know which one you'd like, or tell me to adjust the budget. "
                       "A staff member will finalize your purchase.")


# ============================================================
# REFRESH CONTROL MESSAGE
# ============================================================
async def refresh_ticket_control_message(channel: discord.TextChannel):
    row = await db_fetchrow(
        """
        SELECT owner_id, kind, status, claimed_by,
               first_staff_response_seconds, control_message_id, last_footer_text
        FROM tickets WHERE channel_id=$1
        """,
        channel.id
    )
    if not row:
        return

    owner = channel.guild.get_member(int(row["owner_id"]))
    owner_mention = owner.mention if owner else f"<@{int(row['owner_id'])}>"

    claimed_by_mention = None
    if row["claimed_by"]:
        claimer = channel.guild.get_member(int(row["claimed_by"]))
        claimed_by_mention = claimer.mention if claimer else f"<@{int(row['claimed_by'])}>"

    footer_text = row["last_footer_text"] or "AF SERVICES"
    embed = ticket_embed(
        kind=str(row["kind"]),
        owner_mention=owner_mention,
        claimed_by_mention=claimed_by_mention,
        first_staff_seconds=row["first_staff_response_seconds"],
        footer_text=footer_text,
    )

    mid = row["control_message_id"]
    if not mid:
        return

    try:
        msg = await channel.fetch_message(int(mid))
        if str(row["status"]) == "open":
            await msg.edit(embed=embed, view=TicketControlView(channel.id))
            bot.add_view(TicketControlView(channel.id), message_id=msg.id)
        else:
            await msg.edit(embed=embed, view=None)
    except Exception:
        pass


# ============================================================
# CLOSE FLOW
# ============================================================
async def close_ticket_flow(channel: discord.TextChannel, closed_by: str, reason: str):
    row = await db_fetchrow(
        "SELECT status, claimed_by FROM tickets WHERE channel_id=$1",
        channel.id,
    )
    if not row or row["status"] != "open":
        return

    await db_execute(
        "UPDATE tickets SET status='closed', last_activity=NOW() WHERE channel_id=$1",
        channel.id
    )

    claimed_by = "Unclaimed"
    if row["claimed_by"]:
        claimer = channel.guild.get_member(int(row["claimed_by"]))
        claimed_by = f"{claimer} ({claimer.id})" if claimer else str(int(row["claimed_by"]))

    transcript_sent = await send_transcript_txt(
        guild=channel.guild,
        ticket_channel=channel,
        close_reason=reason,
        closed_by=closed_by,
        claimed_by=claimed_by,
    )

    base = (
        f"🔒 **Ticket Closed**\n"
        f"Closed by: {closed_by}\n"
        f"Claimed by: {claimed_by}\n"
        f"Reason: {reason}\n"
        f"{'✅ Transcript saved.' if transcript_sent else '⚠️ Transcript failed.'}\n\n"
        f"🗑️ Deleting in {DELETE_COUNTDOWN_SECONDS} seconds..."
    )
    countdown_msg = await channel.send(base)

    for i in range(DELETE_COUNTDOWN_SECONDS - 1, 0, -1):
        await asyncio.sleep(1)
        try:
            await countdown_msg.edit(content=base.replace(
                f"Deleting in {DELETE_COUNTDOWN_SECONDS} seconds...",
                f"Deleting in {i} seconds..."
            ))
        except Exception:
            pass

    await asyncio.sleep(1)

    await db_execute("UPDATE tickets SET status='deleted' WHERE channel_id=$1", channel.id)
    try:
        await channel.delete(reason="Ticket closed and deleted.")
    except Exception as e:
        print("Channel delete failed:", e)


# ============================================================
# FIRST STAFF RESPONSE + ACTIVITY
# ============================================================
@bot.event
async def on_message(message: discord.Message):
    if not message.guild or message.author.bot:
        return
    if not isinstance(message.channel, discord.TextChannel):
        return

    row = await db_fetchrow(
        """SELECT created_at, first_staff_response_seconds, status, kind, owner_id, ai_handled, claimed_by
           FROM tickets WHERE channel_id=$1""",
        message.channel.id
    )
    if not row:
        await bot.process_commands(message)
        return

    await db_execute("UPDATE tickets SET last_activity=NOW() WHERE channel_id=$1", message.channel.id)

    is_member = isinstance(message.author, discord.Member)
    author_is_staff = is_member and is_staff(message.author)

    if row["status"] == "open" and author_is_staff:
        if row["first_staff_response_seconds"] is None:
            created_at: datetime = row["created_at"]
            seconds = int((utcnow() - created_at).total_seconds())
            await db_execute(
                "UPDATE tickets SET first_staff_response_seconds=$1 WHERE channel_id=$2",
                seconds, message.channel.id
            )
            await refresh_ticket_control_message(message.channel)

    # AI intake: only in open claim tickets, only for the buyer's own messages,
    # and only until staff takes over (claimed) or an account is delivered.
    if (
        AI_ENABLED
        and row["status"] == "open"
        and row["kind"] == "claim"
        and not row["ai_handled"]
        and is_member
        and not author_is_staff
        and message.author.id == int(row["owner_id"])
        and message.channel.id not in ai_locks
    ):
        ai_locks.add(message.channel.id)
        try:
            async with message.channel.typing():
                await run_ai_intake(message.channel, message.author)
        except Exception as e:
            print("AI intake failed:", e)
        finally:
            ai_locks.discard(message.channel.id)

    # AI shopping: in open "custom" tickets, help the buyer browse stock until staff claims it.
    if (
        AI_ENABLED
        and row["status"] == "open"
        and row["kind"] == "custom"
        and row["claimed_by"] is None
        and is_member
        and not author_is_staff
        and message.author.id == int(row["owner_id"])
        and message.channel.id not in ai_locks
    ):
        ai_locks.add(message.channel.id)
        try:
            async with message.channel.typing():
                await run_ai_shopping(message.channel, message.author)
        except Exception as e:
            print("AI shopping failed:", e)
        finally:
            ai_locks.discard(message.channel.id)

    await bot.process_commands(message)


# ============================================================
# STATUS ROTATOR
# ============================================================
def compute_status_strings(claimed_by: int | None, tick: int) -> tuple[str, str]:
    states = [
        "Waiting for staff",
        "Processing",
        "AF SERVICES Support",
        "Please provide details",
    ]
    state = states[tick % len(states)]
    claimed = "Claimed" if claimed_by else "Unclaimed"
    footer = f"AF SERVICES • Status: {state} • {claimed}"
    topic = f"AF SERVICES Ticket | {claimed} | {state}"
    return footer, topic


@tasks.loop(seconds=STATUS_ROTATE_SECONDS)
async def status_rotator():
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    rows = await db_fetch(
        """
        SELECT channel_id, claimed_by, last_footer_text, last_topic_text
        FROM tickets
        WHERE guild_id=$1 AND status='open'
        """,
        guild.id
    )

    tick = int(utcnow().timestamp() // STATUS_ROTATE_SECONDS)

    for r in rows:
        channel_id = int(r["channel_id"])
        ch = guild.get_channel(channel_id)
        if not isinstance(ch, discord.TextChannel):
            continue

        footer_text, topic_text = compute_status_strings(r["claimed_by"], tick)

        if (r["last_topic_text"] or "") != topic_text:
            try:
                await ch.edit(topic=topic_text)
                await db_execute("UPDATE tickets SET last_topic_text=$1 WHERE channel_id=$2", topic_text, channel_id)
            except Exception:
                pass

        if (r["last_footer_text"] or "") != footer_text:
            await db_execute("UPDATE tickets SET last_footer_text=$1 WHERE channel_id=$2", footer_text, channel_id)
            await refresh_ticket_control_message(ch)


# ============================================================
# READY
# ============================================================
@bot.event
async def on_ready():
    await ensure_db()

    try:
        gobj = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=gobj)
        await bot.tree.sync(guild=gobj)
    except Exception as e:
        print("Command sync error:", e)

    bot.add_view(TicketPanelView())
    bot.add_view(CardView())
    bot.add_view(RiotGuideView())
    bot.add_view(DeliveryApprovalView())
    bot.add_view(CryptoPayView())
    bot.add_view(CryptoCheckView())

    rows = await db_fetch(
        "SELECT channel_id, control_message_id FROM tickets WHERE guild_id=$1 AND status='open' AND control_message_id IS NOT NULL",
        GUILD_ID
    )
    for r in rows:
        try:
            bot.add_view(TicketControlView(int(r["channel_id"])), message_id=int(r["control_message_id"]))
        except Exception:
            pass

    if not status_rotator.is_running():
        status_rotator.start()

    print(f"✅ Ticket bot online as {bot.user}")
    print(f"🖼️  Asset dir: {ASSET_DIR}")
    print(f"🖼️  Logo found:   {os.path.exists(LOGO_PATH)}  ({LOGO_PATH})")
    print(f"🖼️  Banner found: {os.path.exists(BANNER_PATH)}  ({BANNER_PATH})")
    if AF_LOGO_URL or AF_BANNER_URL:
        print(f"🖼️  URL overrides -> logo:{AF_LOGO_URL or '(none)'} banner:{AF_BANNER_URL or '(none)'}")
    print(f"🖼️  Card proof imgs -> payment:{os.path.exists(CARD_PROOF_PAYMENT_PATH)} email:{os.path.exists(CARD_PROOF_EMAIL_PATH)}")
    print(f"🤖 AI intake:      {'ON ('+AI_MODEL+')' if AI_ENABLED else 'OFF (set ANTHROPIC_API_KEY)'}")
    print(f"💳 SellAuth:       {'ON (shop '+SELLAUTH_SHOP_ID+')' if SELLAUTH_ENABLED else 'OFF (set SELLAUTH_API_KEY + SELLAUTH_SHOP_ID)'}")
    print(f"📦 LZT.market:     {'ON' if LZT_ENABLED else 'OFF (set LZT_API_TOKEN)'}{'' if LZT_USER_ID else ' [LZT_USER_ID missing]'}")
    print(f"🚚 Delivery:       {'AUTO' if AUTO_DELIVER else 'staff-approve'} • SellAuth required: {REQUIRE_SELLAUTH}")
    print(f"🛒 Resale/restock: x{RESALE_MULTIPLIER} markup • restock alerts → "
          f"{'channel '+str(RESTOCK_CHANNEL_ID) if RESTOCK_CHANNEL_ID else 'DM fallback (set RESTOCK_CHANNEL_ID)'}")
    print(f"💎 Crypto pay:     {'ON ('+NOWPAYMENTS_PRICE_CURRENCY+', LTC/SOL/BTC/ETH)' if NOWPAYMENTS_ENABLED else 'OFF (set NOWPAYMENTS_API_KEY)'}")


bot.run(DISCORD_TOKEN)