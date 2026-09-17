package storage

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	_ "modernc.org/sqlite"
)

var ErrNotFound = errors.New("not found")

type User struct {
	ID           int64
	Username     string
	PasswordHash string
}

type Account struct {
	ID                int64
	Name              string
	ProviderAccountID string
	Region            string
	UsernameEncrypted string
	PasswordEncrypted string
	TOTPEncrypted     string
	AKEncrypted       string
	CookieEncrypted   string
	Status            string
	Notes             string
	CreatedAt         string
	UpdatedAt         string
}

type AccountRecord struct {
	Account
	SKEncrypted string
}

type AccountWrite struct {
	Name, Region, UsernameEncrypted, PasswordEncrypted, TOTPEncrypted, AKEncrypted, SKEncrypted, CookieEncrypted, Notes string
}

type Finance struct {
	AccountID int64    `json:"account_id"`
	Available *float64 `json:"available"`
	Owe       *float64 `json:"owe"`
	Status    *string  `json:"status"`
	Message   *string  `json:"message"`
	UpdatedAt *string  `json:"updated_at"`
}

type Resource struct {
	ID            int64
	AccountID     int64
	ResourceType  string
	ProviderID    string
	Name          string
	Region        string
	Status        string
	BillingMode   string
	PayloadJSON   string
	SyncedAt      string
	SyncState     string
	LastSeenAt    *string
	LastSuccessAt *string
	SyncError     string
}

type ResourceWrite struct {
	ProviderID, Name, Region, Status, BillingMode, PayloadJSON string
}

type RegionSync struct {
	AccountID    int64
	ResourceType string
	Region       string
	Status       string
	ItemCount    int
	Error        string
}

type ActionJob struct {
	ID, ResourceType, ResourceID, Action, Region, PayloadJSON, ResultJSON, Status, LastError string
	AccountID                                                                                int64
	Attempts                                                                                 int
	NextAttemptAt                                                                            int64
}

func (s *Store) ResourceByProvider(ctx context.Context, accountID int64, resourceType, providerID string) (Resource, error) {
	var row Resource
	err := s.db.QueryRowContext(ctx, `select id,account_id,resource_type,provider_id,name,region,status,
		billing_mode,payload_json,synced_at,sync_state,last_seen_at,last_success_at,sync_error
		from resources where account_id=? and resource_type=? and provider_id=?`,
		accountID, resourceType, providerID).Scan(&row.ID, &row.AccountID, &row.ResourceType, &row.ProviderID,
		&row.Name, &row.Region, &row.Status, &row.BillingMode, &row.PayloadJSON, &row.SyncedAt,
		&row.SyncState, &row.LastSeenAt, &row.LastSuccessAt, &row.SyncError)
	if errors.Is(err, sql.ErrNoRows) {
		return Resource{}, ErrNotFound
	}
	if err != nil {
		return Resource{}, fmt.Errorf("query resource: %w", err)
	}
	return row, nil
}

func (s *Store) RecordOperation(ctx context.Context, accountID *int64, resourceType, resourceID, action, status, message string) error {
	_, err := s.db.ExecContext(ctx, `insert into operations(account_id,resource_type,resource_id,action,status,message)
		values(?,?,?,?,?,?)`, accountID, nullableText(resourceType), nullableText(resourceID), action, status, message)
	if err != nil {
		return fmt.Errorf("record operation: %w", err)
	}
	return nil
}

func nullableText(value string) any {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	return value
}

type Operation struct {
	ID           int64   `json:"id"`
	AccountID    *int64  `json:"account_id"`
	ResourceType *string `json:"resource_type"`
	ResourceID   *string `json:"resource_id"`
	Action       string  `json:"action"`
	Status       string  `json:"status"`
	Message      string  `json:"message"`
	CreatedAt    string  `json:"created_at"`
}

type DashboardSummary struct {
	AccountCount        int64            `json:"account_count"`
	ResourceCounts      map[string]int64 `json:"resource_counts"`
	StaleResourceCounts map[string]int64 `json:"stale_resource_counts"`
	Finance             []Finance        `json:"finance"`
}

type Store struct {
	db *sql.DB
}

func OpenReadOnly(path string) (*Store, error) {
	return open(path, true)
}

func OpenReadWrite(path string) (*Store, error) {
	store, err := open(path, false)
	if err != nil {
		return nil, err
	}
	if err := store.Migrate(context.Background()); err != nil {
		store.Close()
		return nil, err
	}
	return store, nil
}

func open(path string, readOnly bool) (*Store, error) {
	info, err := os.Stat(path)
	if err != nil {
		return nil, fmt.Errorf("stat database: %w", err)
	}
	if info.IsDir() {
		return nil, fmt.Errorf("database path is a directory: %s", path)
	}
	absolute, err := filepath.Abs(path)
	if err != nil {
		return nil, fmt.Errorf("resolve database path: %w", err)
	}
	mode := "rw"
	if readOnly {
		mode = "ro"
	}
	dsn := (&url.URL{Scheme: "file", Opaque: filepath.ToSlash(absolute), RawQuery: "mode=" + mode}).String()
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("open SQLite database: %w", err)
	}
	// SQLite permits many readers but only one writer. Resource synchronization
	// deliberately fetches several resource types in parallel and each result is
	// committed in a short transaction. Keeping a single pooled connection makes
	// those commits queue in database/sql instead of racing into SQLITE_BUSY.
	// It also ensures connection-scoped pragmas below apply to every operation.
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	store := &Store{db: db}
	if err := store.Ping(context.Background()); err != nil {
		db.Close()
		return nil, err
	}
	if _, err := db.Exec("pragma busy_timeout=30000"); err != nil {
		db.Close()
		return nil, fmt.Errorf("configure SQLite busy timeout: %w", err)
	}
	return store, nil
}

func (s *Store) Close() error { return s.db.Close() }

func (s *Store) Ping(ctx context.Context) error {
	if err := s.db.PingContext(ctx); err != nil {
		return fmt.Errorf("ping SQLite database: %w", err)
	}
	return nil
}

func (s *Store) UserByUsername(ctx context.Context, username string) (User, error) {
	var user User
	err := s.db.QueryRowContext(ctx, "select id, username, password_hash from users where username = ?", username).
		Scan(&user.ID, &user.Username, &user.PasswordHash)
	if errors.Is(err, sql.ErrNoRows) {
		return User{}, ErrNotFound
	}
	if err != nil {
		return User{}, fmt.Errorf("query user: %w", err)
	}
	return user, nil
}

func (s *Store) Accounts(ctx context.Context) ([]Account, error) {
	rows, err := s.db.QueryContext(ctx, `
		select id, name, provider_account_id, region,
		       coalesce(username_enc, ''), coalesce(password_enc, ''),
		       coalesce(totp_secret_enc, ''), coalesce(ak_enc, ''),
		       coalesce(cookie_state_enc, ''), status, notes, created_at, updated_at
		from ctyun_accounts order by id desc`)
	if err != nil {
		return nil, fmt.Errorf("query accounts: %w", err)
	}
	defer rows.Close()
	accounts := []Account{}
	for rows.Next() {
		var account Account
		if err := rows.Scan(
			&account.ID, &account.Name, &account.ProviderAccountID, &account.Region,
			&account.UsernameEncrypted, &account.PasswordEncrypted, &account.TOTPEncrypted,
			&account.AKEncrypted, &account.CookieEncrypted, &account.Status, &account.Notes,
			&account.CreatedAt, &account.UpdatedAt,
		); err != nil {
			return nil, fmt.Errorf("scan account: %w", err)
		}
		accounts = append(accounts, account)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate accounts: %w", err)
	}
	return accounts, nil
}

func (s *Store) AccountByID(ctx context.Context, id int64) (AccountRecord, error) {
	var row AccountRecord
	err := s.db.QueryRowContext(ctx, `select id,name,provider_account_id,region,
		coalesce(username_enc,''),coalesce(password_enc,''),coalesce(totp_secret_enc,''),
		coalesce(ak_enc,''),coalesce(sk_enc,''),coalesce(cookie_state_enc,''),status,notes,created_at,updated_at
		from ctyun_accounts where id=?`, id).Scan(
		&row.ID, &row.Name, &row.ProviderAccountID, &row.Region, &row.UsernameEncrypted,
		&row.PasswordEncrypted, &row.TOTPEncrypted, &row.AKEncrypted, &row.SKEncrypted,
		&row.CookieEncrypted, &row.Status, &row.Notes, &row.CreatedAt, &row.UpdatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return AccountRecord{}, ErrNotFound
	}
	if err != nil {
		return AccountRecord{}, fmt.Errorf("query account: %w", err)
	}
	return row, nil
}

func (s *Store) CreateAccount(ctx context.Context, value AccountWrite) (int64, error) {
	result, err := s.db.ExecContext(ctx, `insert into ctyun_accounts
		(name,provider_account_id,region,username_enc,password_enc,totp_secret_enc,ak_enc,sk_enc,cookie_state_enc,notes)
		values(?,'',?,?,?,?,?,?,?,?)`, value.Name, value.Region, value.UsernameEncrypted,
		value.PasswordEncrypted, value.TOTPEncrypted, value.AKEncrypted, value.SKEncrypted, value.CookieEncrypted, value.Notes)
	if err != nil {
		return 0, fmt.Errorf("create account: %w", err)
	}
	id, err := result.LastInsertId()
	if err != nil {
		return 0, fmt.Errorf("read account id: %w", err)
	}
	_, err = s.db.ExecContext(ctx, `insert into operations(account_id,resource_type,resource_id,action,status,message)
		values(?,'account',?,'create_account','success','')`, id, fmt.Sprint(id))
	return id, err
}

func (s *Store) UpdateAccount(ctx context.Context, id int64, value AccountWrite) error {
	result, err := s.db.ExecContext(ctx, `update ctyun_accounts set name=?,region=?,username_enc=?,password_enc=?,
		totp_secret_enc=?,ak_enc=?,sk_enc=?,cookie_state_enc=?,notes=?,updated_at=current_timestamp where id=?`,
		value.Name, value.Region, value.UsernameEncrypted, value.PasswordEncrypted, value.TOTPEncrypted,
		value.AKEncrypted, value.SKEncrypted, value.CookieEncrypted, value.Notes, id)
	if err != nil {
		return fmt.Errorf("update account: %w", err)
	}
	count, _ := result.RowsAffected()
	if count == 0 {
		return ErrNotFound
	}
	_, err = s.db.ExecContext(ctx, `insert into operations(account_id,resource_type,resource_id,action,status,message)
		values(?,'account',?,'update_account','success','')`, id, fmt.Sprint(id))
	return err
}

func (s *Store) DeleteAccount(ctx context.Context, id int64) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	result, err := tx.ExecContext(ctx, "delete from ctyun_accounts where id=?", id)
	if err != nil {
		return err
	}
	count, _ := result.RowsAffected()
	if count == 0 {
		return ErrNotFound
	}
	if _, err = tx.ExecContext(ctx, "delete from resources where account_id=?", id); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, "delete from resource_sync_regions where account_id=?", id); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, "delete from resource_action_jobs where account_id=?", id); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `insert into operations(account_id,resource_type,resource_id,action,status,message)
		values(?,'account',?,'delete_account','success','')`, id, fmt.Sprint(id)); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *Store) Finance(ctx context.Context) ([]Finance, error) {
	rows, err := s.db.QueryContext(ctx, `
		select a.id, f.available, f.owe, f.status, f.message, f.updated_at
		from ctyun_accounts a
		left join account_finance f on f.account_id = a.id
		order by a.id desc`)
	if err != nil {
		return nil, fmt.Errorf("query finance: %w", err)
	}
	defer rows.Close()
	result := []Finance{}
	for rows.Next() {
		var row Finance
		if err := rows.Scan(&row.AccountID, &row.Available, &row.Owe, &row.Status, &row.Message, &row.UpdatedAt); err != nil {
			return nil, fmt.Errorf("scan finance: %w", err)
		}
		result = append(result, row)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate finance: %w", err)
	}
	return result, nil
}

func (s *Store) UpdateAccountBrowserState(ctx context.Context, id int64, cookieEncrypted, providerAccountID string) error {
	_, err := s.db.ExecContext(ctx, `update ctyun_accounts set cookie_state_enc=?,provider_account_id=case when ?='' then provider_account_id else ? end,updated_at=current_timestamp where id=?`, cookieEncrypted, providerAccountID, providerAccountID, id)
	return err
}

func (s *Store) UpsertFinance(ctx context.Context, accountID int64, available, owe *float64, status, message string) error {
	_, err := s.db.ExecContext(ctx, `insert into account_finance(account_id,available,owe,status,message,updated_at) values(?,?,?,?,?,current_timestamp)
		on conflict(account_id) do update set available=excluded.available,owe=excluded.owe,status=excluded.status,message=excluded.message,updated_at=current_timestamp`, accountID, available, owe, status, message)
	return err
}

func (s *Store) Resources(ctx context.Context, resourceType string, accountID *int64) ([]Resource, error) {
	query := `select id, account_id, resource_type, provider_id, name, region, status,
	                 billing_mode, payload_json, synced_at, sync_state, last_seen_at, last_success_at, sync_error
	          from resources where resource_type = ?`
	args := []any{resourceType}
	if accountID != nil {
		query += " and account_id = ?"
		args = append(args, *accountID)
	}
	query += " order by synced_at desc"
	rows, err := s.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, fmt.Errorf("query resources: %w", err)
	}
	defer rows.Close()
	result := []Resource{}
	for rows.Next() {
		var row Resource
		if err := rows.Scan(&row.ID, &row.AccountID, &row.ResourceType, &row.ProviderID, &row.Name,
			&row.Region, &row.Status, &row.BillingMode, &row.PayloadJSON, &row.SyncedAt,
			&row.SyncState, &row.LastSeenAt, &row.LastSuccessAt, &row.SyncError); err != nil {
			return nil, fmt.Errorf("scan resource: %w", err)
		}
		result = append(result, row)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate resources: %w", err)
	}
	return result, nil
}

func (s *Store) ReplaceResources(ctx context.Context, accountID int64, resourceType string, targetRegions []string, items []ResourceWrite) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin resource replacement: %w", err)
	}
	defer tx.Rollback()
	if len(targetRegions) == 0 {
		if _, err = tx.ExecContext(ctx, "delete from resources where account_id=? and resource_type=?", accountID, resourceType); err != nil {
			return err
		}
	} else {
		placeholders := strings.TrimRight(strings.Repeat("?,", len(targetRegions)), ",")
		args := []any{accountID, resourceType}
		for _, region := range targetRegions {
			args = append(args, region)
		}
		if _, err = tx.ExecContext(ctx, "delete from resources where account_id=? and resource_type=? and region in ("+placeholders+")", args...); err != nil {
			return err
		}
	}
	statement := `insert into resources(account_id,resource_type,provider_id,name,region,status,billing_mode,payload_json,synced_at)
		values(?,?,?,?,?,?,?,?,current_timestamp) on conflict(account_id,resource_type,provider_id) do update set
		name=excluded.name,region=excluded.region,status=excluded.status,billing_mode=excluded.billing_mode,payload_json=excluded.payload_json,synced_at=current_timestamp`
	for _, item := range items {
		if item.ProviderID == "" {
			continue
		}
		if _, err = tx.ExecContext(ctx, statement, accountID, resourceType, item.ProviderID, item.Name, item.Region, item.Status, item.BillingMode, item.PayloadJSON); err != nil {
			return fmt.Errorf("upsert resource %s: %w", item.ProviderID, err)
		}
	}
	if len(targetRegions) > 0 {
		if _, err = tx.ExecContext(ctx, `update resources set sync_state='fresh',last_seen_at=current_timestamp,
			last_success_at=current_timestamp,sync_error='' where account_id=? and resource_type=? and region in (`+
			strings.TrimRight(strings.Repeat("?,", len(targetRegions)), ",")+")", append([]any{accountID, resourceType}, stringArgs(targetRegions)...)...); err != nil {
			return fmt.Errorf("mark resources fresh: %w", err)
		}
	} else if _, err = tx.ExecContext(ctx, `update resources set sync_state='fresh',last_seen_at=current_timestamp,
		last_success_at=current_timestamp,sync_error='' where account_id=? and resource_type=?`, accountID, resourceType); err != nil {
		return fmt.Errorf("mark resources fresh: %w", err)
	}
	return tx.Commit()
}

func stringArgs(values []string) []any {
	result := make([]any, 0, len(values))
	for _, value := range values {
		result = append(result, value)
	}
	return result
}

func (s *Store) RecordRegionSync(ctx context.Context, value RegionSync) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	statement := `insert into resource_sync_regions(account_id,resource_type,region,status,item_count,error,last_attempt_at,last_success_at)
		values(?,?,?,?,?,?,current_timestamp,case when ?='success' then current_timestamp else null end) on conflict(account_id,resource_type,region) do update set
		status=excluded.status,item_count=excluded.item_count,error=excluded.error,last_attempt_at=current_timestamp,
		last_success_at=case when excluded.status='success' then current_timestamp else resource_sync_regions.last_success_at end`
	if _, err = tx.ExecContext(ctx, statement, value.AccountID, value.ResourceType, value.Region, value.Status, value.ItemCount, value.Error, value.Status); err != nil {
		return fmt.Errorf("record region sync: %w", err)
	}
	if value.Status != "success" {
		if _, err = tx.ExecContext(ctx, `update resources set sync_state='stale',sync_error=?
			where account_id=? and resource_type=? and region=?`, value.Error, value.AccountID, value.ResourceType, value.Region); err != nil {
			return fmt.Errorf("mark region resources stale: %w", err)
		}
	}
	return tx.Commit()
}

func (s *Store) Operations(ctx context.Context, limit int) ([]Operation, error) {
	rows, err := s.db.QueryContext(ctx, `
		select id, account_id, resource_type, resource_id, action, status, message, created_at
		from operations order by id desc limit ?`, limit)
	if err != nil {
		return nil, fmt.Errorf("query operations: %w", err)
	}
	defer rows.Close()
	result := []Operation{}
	for rows.Next() {
		var row Operation
		if err := rows.Scan(&row.ID, &row.AccountID, &row.ResourceType, &row.ResourceID,
			&row.Action, &row.Status, &row.Message, &row.CreatedAt); err != nil {
			return nil, fmt.Errorf("scan operation: %w", err)
		}
		result = append(result, row)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate operations: %w", err)
	}
	return result, nil
}

func (s *Store) DashboardSummary(ctx context.Context) (DashboardSummary, error) {
	var result DashboardSummary
	result.ResourceCounts = map[string]int64{}
	result.StaleResourceCounts = map[string]int64{}
	if err := s.db.QueryRowContext(ctx, "select count(*) from ctyun_accounts").Scan(&result.AccountCount); err != nil {
		return DashboardSummary{}, fmt.Errorf("count accounts: %w", err)
	}
	rows, err := s.db.QueryContext(ctx, `select resource_type,
		sum(case when sync_state='fresh' then 1 else 0 end),
		sum(case when sync_state<>'fresh' then 1 else 0 end)
		from resources group by resource_type`)
	if err != nil {
		return DashboardSummary{}, fmt.Errorf("count resources: %w", err)
	}
	for rows.Next() {
		var resourceType string
		var count, stale int64
		if err := rows.Scan(&resourceType, &count, &stale); err != nil {
			rows.Close()
			return DashboardSummary{}, fmt.Errorf("scan resource count: %w", err)
		}
		result.ResourceCounts[resourceType] = count
		result.StaleResourceCounts[resourceType] = stale
	}
	if err := rows.Close(); err != nil {
		return DashboardSummary{}, fmt.Errorf("close resource counts: %w", err)
	}
	if err := rows.Err(); err != nil {
		return DashboardSummary{}, fmt.Errorf("iterate resource counts: %w", err)
	}
	result.Finance, err = s.Finance(ctx)
	if err != nil {
		return DashboardSummary{}, err
	}
	return result, nil
}

func (s *Store) CreateActionJob(ctx context.Context, value ActionJob) error {
	_, err := s.db.ExecContext(ctx, `insert into resource_action_jobs
		(id,account_id,resource_type,resource_id,action,region,payload_json,result_json,status,attempts,next_attempt_at,last_error)
		values(?,?,?,?,?,?,?,?,?,0,?,'')`, value.ID, value.AccountID, value.ResourceType, value.ResourceID,
		value.Action, value.Region, value.PayloadJSON, value.ResultJSON, value.Status, value.NextAttemptAt)
	if err != nil {
		return fmt.Errorf("create resource action job: %w", err)
	}
	return nil
}

func (s *Store) DueActionJobs(ctx context.Context, now int64, limit int) ([]ActionJob, error) {
	rows, err := s.db.QueryContext(ctx, `select id,account_id,resource_type,resource_id,action,region,payload_json,
		result_json,status,attempts,next_attempt_at,last_error from resource_action_jobs
		where status in ('pending','retrying') and next_attempt_at<=? order by next_attempt_at,id limit ?`, now, limit)
	if err != nil {
		return nil, fmt.Errorf("query due resource jobs: %w", err)
	}
	defer rows.Close()
	result := []ActionJob{}
	for rows.Next() {
		var item ActionJob
		if err := rows.Scan(&item.ID, &item.AccountID, &item.ResourceType, &item.ResourceID, &item.Action,
			&item.Region, &item.PayloadJSON, &item.ResultJSON, &item.Status, &item.Attempts,
			&item.NextAttemptAt, &item.LastError); err != nil {
			return nil, fmt.Errorf("scan resource job: %w", err)
		}
		result = append(result, item)
	}
	return result, rows.Err()
}

func (s *Store) UpdateActionJob(ctx context.Context, id, status, lastError string, attempts int, nextAttemptAt int64) error {
	_, err := s.db.ExecContext(ctx, `update resource_action_jobs set status=?,last_error=?,attempts=?,next_attempt_at=?,updated_at=current_timestamp where id=?`,
		status, lastError, attempts, nextAttemptAt, id)
	return err
}
