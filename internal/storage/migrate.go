package storage

import (
	"context"
	"database/sql"
	"fmt"
)

// Migrate upgrades the live SQLite database in-place. Every statement is
// additive so older releases can still read the database during rollback.
func (s *Store) Migrate(ctx context.Context) error {
	if err := s.addColumnIfMissing(ctx, "resources", "sync_state", "text not null default 'stale'"); err != nil {
		return err
	}
	if err := s.addColumnIfMissing(ctx, "resources", "last_seen_at", "text"); err != nil {
		return err
	}
	if err := s.addColumnIfMissing(ctx, "resources", "last_success_at", "text"); err != nil {
		return err
	}
	if err := s.addColumnIfMissing(ctx, "resources", "sync_error", "text not null default ''"); err != nil {
		return err
	}
	statements := []string{
		`create table if not exists resource_sync_regions (
			account_id integer not null,
			resource_type text not null,
			region text not null,
			status text not null,
			item_count integer not null default 0,
			error text not null default '',
			last_attempt_at text not null default current_timestamp,
			last_success_at text,
			primary key(account_id, resource_type, region)
		)`,
		`create table if not exists resource_action_jobs (
			id text primary key,
			account_id integer not null,
			resource_type text not null,
			resource_id text not null default '',
			action text not null,
			region text not null default '',
			payload_json text not null default '{}',
			result_json text not null default '{}',
			status text not null default 'pending',
			attempts integer not null default 0,
			next_attempt_at integer not null,
			last_error text not null default '',
			created_at text not null default current_timestamp,
			updated_at text not null default current_timestamp
		)`,
		`create index if not exists idx_resources_type_account_region_sync on resources(resource_type, account_id, region, sync_state, synced_at)`,
		`create index if not exists idx_resource_jobs_due on resource_action_jobs(status, next_attempt_at)`,
		`create index if not exists idx_operations_action_account_id on operations(action, account_id, id)`,
	}
	for _, statement := range statements {
		if _, err := s.db.ExecContext(ctx, statement); err != nil {
			return fmt.Errorf("apply storage migration: %w", err)
		}
	}
	return nil
}

func (s *Store) addColumnIfMissing(ctx context.Context, table, column, definition string) error {
	rows, err := s.db.QueryContext(ctx, "pragma table_info("+table+")")
	if err != nil {
		return fmt.Errorf("inspect %s schema: %w", table, err)
	}
	found := false
	for rows.Next() {
		var cid int
		var name, dataType string
		var notNull, primaryKey int
		var defaultValue sql.NullString
		if err := rows.Scan(&cid, &name, &dataType, &notNull, &defaultValue, &primaryKey); err != nil {
			rows.Close()
			return fmt.Errorf("scan %s schema: %w", table, err)
		}
		if name == column {
			found = true
		}
	}
	if err := rows.Close(); err != nil {
		return err
	}
	if found {
		return nil
	}
	if _, err := s.db.ExecContext(ctx, "alter table "+table+" add column "+column+" "+definition); err != nil {
		return fmt.Errorf("add %s.%s: %w", table, column, err)
	}
	return nil
}
