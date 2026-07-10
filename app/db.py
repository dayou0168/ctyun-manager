import sqlite3
from pathlib import Path
from typing import Iterable

from .config import settings
from .security import hash_password


def connect() -> sqlite3.Connection:
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma busy_timeout=30000")
    return conn


def migrate() -> None:
    with connect() as conn:
        conn.execute("pragma journal_mode=WAL")
        conn.executescript(
            """
            create table if not exists users (
              id integer primary key autoincrement,
              username text not null unique,
              password_hash text not null,
              created_at text not null default current_timestamp
            );

            create table if not exists ctyun_accounts (
              id integer primary key autoincrement,
              name text not null,
              provider_account_id text not null default '',
              region text not null default '',
              username_enc text,
              password_enc text,
              totp_secret_enc text,
              ak_enc text,
              sk_enc text,
              cookie_state_enc text,
              status text not null default 'enabled',
              notes text not null default '',
              created_at text not null default current_timestamp,
              updated_at text not null default current_timestamp
            );

            create table if not exists resources (
              id integer primary key autoincrement,
              account_id integer not null,
              resource_type text not null,
              provider_id text not null,
              name text not null,
              region text not null default '',
              status text not null default '',
              billing_mode text not null default '',
              payload_json text not null default '{}',
              synced_at text not null default current_timestamp,
              unique(account_id, resource_type, provider_id)
            );

            create table if not exists operations (
              id integer primary key autoincrement,
              account_id integer,
              resource_type text,
              resource_id text,
              action text not null,
              status text not null,
              message text not null default '',
              created_at text not null default current_timestamp
            );

            create table if not exists account_finance (
              account_id integer primary key,
              available real,
              owe real,
              status text not null default '',
              message text not null default '',
              updated_at text not null default current_timestamp
            );

            create table if not exists option_cache (
              cache_key text primary key,
              kind text not null,
              payload_json text not null default '[]',
              expires_at real not null,
              updated_at text not null default current_timestamp
            );

            create table if not exists ikuai_gateways (
              id integer primary key autoincrement,
              name text not null,
              base_url text not null,
              username_enc text,
              password_enc text,
              status text not null default 'enabled',
              notes text not null default '',
              last_status text not null default '',
              payload_json text not null default '{}',
              created_at text not null default current_timestamp,
              updated_at text not null default current_timestamp
            );

            create table if not exists linux_servers (
              id integer primary key autoincrement,
              name text not null,
              host text not null,
              port integer not null default 22,
              username_enc text,
              password_enc text,
              private_key_enc text,
              private_key_passphrase_enc text,
              status text not null default 'enabled',
              last_status text not null default '',
              last_message text not null default '',
              fingerprint text not null default '',
              notes text not null default '',
              created_at text not null default current_timestamp,
              updated_at text not null default current_timestamp
            );

            create table if not exists rustdesk_jobs (
              id text primary key,
              status text not null,
              message text not null default '',
              payload_json text not null default '{}',
              logs_json text not null default '[]',
              result_json text,
              error text not null default '',
              created_at text not null,
              updated_at text not null
            );
            """
        )
        columns = [row["name"] for row in conn.execute("pragma table_info(ctyun_accounts)").fetchall()]
        if "provider_account_id" not in columns:
            conn.execute("alter table ctyun_accounts add column provider_account_id text not null default ''")
        existing = conn.execute("select id from users where username = ?", (settings.admin_user,)).fetchone()
        if not existing:
            conn.execute(
                "insert into users(username, password_hash) values(?, ?)",
                (settings.admin_user, hash_password(settings.admin_password)),
            )
        else:
            conn.execute(
                "update users set password_hash=? where username=?",
                (hash_password(settings.admin_password), settings.admin_user),
            )


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict]:
    return [dict(row) for row in rows]
