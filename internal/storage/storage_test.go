package storage

import (
	"context"
	"database/sql"
	"path/filepath"
	"testing"
	"time"
)

func TestOpenReadOnlyAndQueries(t *testing.T) {
	t.Parallel()
	path := filepath.Join(t.TempDir(), "compatibility.db")
	db, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec(`
		create table users (id integer primary key, username text, password_hash text);
		create table ctyun_accounts (
			id integer primary key, name text, provider_account_id text, region text,
			username_enc text, password_enc text, totp_secret_enc text, ak_enc text, sk_enc text,
			cookie_state_enc text, status text default 'enabled', notes text default '',
			created_at text default current_timestamp, updated_at text default current_timestamp
		);
		create table account_finance (
			account_id integer primary key, available real, owe real, status text,
			message text, updated_at text
		);
		create table resources (
			id integer primary key, account_id integer, resource_type text, provider_id text,
			name text, region text, status text, billing_mode text, payload_json text, synced_at text,
			sync_state text not null default 'stale', last_seen_at text, last_success_at text,
			sync_error text not null default '',
			unique(account_id, resource_type, provider_id)
		);
		create table operations (
			id integer primary key, account_id integer, resource_type text, resource_id text,
			action text, status text, message text, created_at text
		);
		insert into users values (1, 'admin', 'hash');
		insert into ctyun_accounts values (2, 'account', 'provider', 'region', null, 'password', null, null, null, null, 'enabled', '', 'created', 'updated');
		insert into ctyun_accounts values (3, 'empty-finance', '', '', null, null, null, null, null, null, 'enabled', '', 'created', 'updated');
		insert into account_finance values (2, 10.5, 1.25, 'ok', 'fresh', 'finance-updated');
		insert into resources(id,account_id,resource_type,provider_id,name,region,status,billing_mode,payload_json,synced_at,sync_state)
		values (4, 2, 'ecs', 'server-4', 'server', 'region', 'running', 'monthly', '{"cpu":2}', 'synced', 'fresh');
		insert into operations values (5, null, null, null, 'sync', 'success', 'done', 'operation-created');
	`)
	if err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}

	store, err := OpenReadOnly(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	if got := store.db.Stats().MaxOpenConnections; got != 1 {
		t.Fatalf("SQLite pool max connections = %d, want 1", got)
	}
	user, err := store.UserByUsername(context.Background(), "admin")
	if err != nil || user.PasswordHash != "hash" {
		t.Fatalf("user = %#v, err = %v", user, err)
	}
	accounts, err := store.Accounts(context.Background())
	if err != nil || len(accounts) != 2 || accounts[0].ID != 3 {
		t.Fatalf("accounts = %#v, err = %v", accounts, err)
	}
	finance, err := store.Finance(context.Background())
	if err != nil || len(finance) != 2 || finance[0].AccountID != 3 || finance[0].Available != nil || finance[1].Available == nil || *finance[1].Available != 10.5 {
		t.Fatalf("finance = %#v, err = %v", finance, err)
	}
	resources, err := store.Resources(context.Background(), "ecs", nil)
	if err != nil || len(resources) != 1 || resources[0].ProviderID != "server-4" {
		t.Fatalf("resources = %#v, err = %v", resources, err)
	}
	accountID := int64(99)
	resources, err = store.Resources(context.Background(), "ecs", &accountID)
	if err != nil || len(resources) != 0 {
		t.Fatalf("filtered resources = %#v, err = %v", resources, err)
	}
	operations, err := store.Operations(context.Background(), 200)
	if err != nil || len(operations) != 1 || operations[0].AccountID != nil {
		t.Fatalf("operations = %#v, err = %v", operations, err)
	}
	summary, err := store.DashboardSummary(context.Background())
	if err != nil || summary.AccountCount != 2 || summary.ResourceCounts["ecs"] != 1 || len(summary.Finance) != 2 {
		t.Fatalf("summary = %#v, err = %v", summary, err)
	}
	if _, err := store.db.Exec("insert into users values (3, 'write', 'blocked')"); err == nil {
		t.Fatal("read-only database unexpectedly accepted a write")
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	writable, err := OpenReadWrite(path)
	if err != nil {
		t.Fatal(err)
	}
	defer writable.Close()
	if _, err := writable.db.Exec("insert into users values (3, 'write', 'allowed')"); err != nil {
		t.Fatalf("read-write database rejected a write: %v", err)
	}
	if err := writable.ReplaceResources(context.Background(), 2, "ecs", []string{"region"}, []ResourceWrite{{ProviderID: "new-1", Name: "new", Region: "region", Status: "running", PayloadJSON: `{"id":"new-1"}`}}); err != nil {
		t.Fatal(err)
	}
	resourceRows, err := writable.Resources(context.Background(), "ecs", nil)
	if err != nil || len(resourceRows) != 1 || resourceRows[0].ProviderID != "new-1" || resourceRows[0].SyncState != "fresh" {
		t.Fatalf("replaced resources=%#v err=%v", resourceRows, err)
	}
	if err := writable.RecordRegionSync(context.Background(), RegionSync{AccountID: 2, ResourceType: "ecs", Region: "region", Status: "failed", Error: "temporary provider error"}); err != nil {
		t.Fatal(err)
	}
	resourceRows, err = writable.Resources(context.Background(), "ecs", nil)
	if err != nil || resourceRows[0].SyncState != "stale" || resourceRows[0].SyncError != "temporary provider error" {
		t.Fatalf("stale resources=%#v err=%v", resourceRows, err)
	}
	summary, err = writable.DashboardSummary(context.Background())
	if err != nil || summary.ResourceCounts["ecs"] != 0 || summary.StaleResourceCounts["ecs"] != 1 {
		t.Fatalf("stale summary=%#v err=%v", summary, err)
	}
	job := ActionJob{ID: "job-1", AccountID: 2, ResourceType: "ecs", ResourceID: "new-1", Action: "stop", Region: "region", Status: "pending", NextAttemptAt: time.Now().Add(-time.Second).Unix()}
	if err := writable.CreateActionJob(context.Background(), job); err != nil {
		t.Fatal(err)
	}
	jobs, err := writable.DueActionJobs(context.Background(), time.Now().Unix(), 10)
	if err != nil || len(jobs) != 1 || jobs[0].ID != "job-1" {
		t.Fatalf("due jobs=%#v err=%v", jobs, err)
	}
	if err := writable.UpdateActionJob(context.Background(), "job-1", "completed", "", 1, 0); err != nil {
		t.Fatal(err)
	}
	jobs, err = writable.DueActionJobs(context.Background(), time.Now().Unix(), 10)
	if err != nil || len(jobs) != 0 {
		t.Fatalf("completed jobs still due=%#v err=%v", jobs, err)
	}
	id, err := writable.CreateAccount(context.Background(), AccountWrite{Name: "created", Region: "region", UsernameEncrypted: "encrypted", Notes: "notes"})
	if err != nil {
		t.Fatal(err)
	}
	row, err := writable.AccountByID(context.Background(), id)
	if err != nil || row.Name != "created" || row.UsernameEncrypted != "encrypted" {
		t.Fatalf("created row=%#v err=%v", row, err)
	}
	err = writable.UpdateAccount(context.Background(), id, AccountWrite{Name: "updated", Region: "new-region", UsernameEncrypted: "encrypted", Notes: "new-notes"})
	if err != nil {
		t.Fatal(err)
	}
	row, _ = writable.AccountByID(context.Background(), id)
	if row.Name != "updated" {
		t.Fatalf("updated row=%#v", row)
	}
	if err := writable.DeleteAccount(context.Background(), id); err != nil {
		t.Fatal(err)
	}
	if _, err := writable.AccountByID(context.Background(), id); err != ErrNotFound {
		t.Fatalf("deleted account err=%v", err)
	}
}

func TestOpenReadWriteMigratesLegacyResourceSchema(t *testing.T) {
	t.Parallel()
	path := filepath.Join(t.TempDir(), "legacy.db")
	db, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec(`
		create table resources (
			id integer primary key, account_id integer, resource_type text, provider_id text,
			name text, region text, status text, billing_mode text, payload_json text, synced_at text,
			unique(account_id, resource_type, provider_id)
		);
		create table operations (
			id integer primary key, account_id integer, resource_type text, resource_id text,
			action text, status text, message text, created_at text
		);
		insert into resources values (1, 7, 'eip', 'eip-1', 'address', 'r1', 'active', '', '{}', 'old');
	`)
	if err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	store, err := OpenReadWrite(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	rows, err := store.Resources(context.Background(), "eip", nil)
	if err != nil || len(rows) != 1 || rows[0].SyncState != "stale" {
		t.Fatalf("migrated rows=%#v err=%v", rows, err)
	}
	if _, err := store.db.Exec(`insert into resource_sync_regions(account_id,resource_type,region,status) values(7,'eip','r1','success')`); err != nil {
		t.Fatalf("resource_sync_regions missing: %v", err)
	}
	if _, err := store.db.Exec(`insert into resource_action_jobs(id,account_id,resource_type,action,next_attempt_at) values('job',7,'eip','release',0)`); err != nil {
		t.Fatalf("resource_action_jobs missing: %v", err)
	}
}
