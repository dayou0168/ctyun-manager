package storage

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
)

type LinuxServer struct {
	ID                  int64  `json:"id"`
	Name                string `json:"name"`
	Host                string `json:"host"`
	Port                int    `json:"port"`
	UsernameEncrypted   string `json:"-"`
	PasswordEncrypted   string `json:"-"`
	PrivateKeyEncrypted string `json:"-"`
	PassphraseEncrypted string `json:"-"`
	Status              string `json:"status"`
	LastStatus          string `json:"last_status"`
	LastMessage         string `json:"last_message"`
	Fingerprint         string `json:"fingerprint"`
	Notes               string `json:"notes"`
	CreatedAt           string `json:"created_at"`
	UpdatedAt           string `json:"updated_at"`
}

type LinuxServerWrite struct {
	Name, Host                                                                                         string
	Port                                                                                               int
	UsernameEncrypted, PasswordEncrypted, PrivateKeyEncrypted, PassphraseEncrypted, Fingerprint, Notes string
}

const linuxColumns = `id,name,host,port,coalesce(username_enc,''),coalesce(password_enc,''),coalesce(private_key_enc,''),coalesce(private_key_passphrase_enc,''),status,last_status,last_message,fingerprint,notes,created_at,updated_at`

func scanLinux(scanner interface{ Scan(...any) error }) (LinuxServer, error) {
	var v LinuxServer
	err := scanner.Scan(&v.ID, &v.Name, &v.Host, &v.Port, &v.UsernameEncrypted, &v.PasswordEncrypted, &v.PrivateKeyEncrypted, &v.PassphraseEncrypted, &v.Status, &v.LastStatus, &v.LastMessage, &v.Fingerprint, &v.Notes, &v.CreatedAt, &v.UpdatedAt)
	return v, err
}

func (s *Store) LinuxServers(ctx context.Context) ([]LinuxServer, error) {
	rows, err := s.db.QueryContext(ctx, "select "+linuxColumns+" from linux_servers order by id desc")
	if err != nil {
		return nil, fmt.Errorf("query linux servers: %w", err)
	}
	defer rows.Close()
	result := []LinuxServer{}
	for rows.Next() {
		v, err := scanLinux(rows)
		if err != nil {
			return nil, err
		}
		result = append(result, v)
	}
	return result, rows.Err()
}
func (s *Store) LinuxServerByID(ctx context.Context, id int64) (LinuxServer, error) {
	v, err := scanLinux(s.db.QueryRowContext(ctx, "select "+linuxColumns+" from linux_servers where id=?", id))
	if errors.Is(err, sql.ErrNoRows) {
		return LinuxServer{}, ErrNotFound
	}
	return v, err
}
func (s *Store) CreateLinuxServer(ctx context.Context, v LinuxServerWrite) (int64, error) {
	r, err := s.db.ExecContext(ctx, `insert into linux_servers(name,host,port,username_enc,password_enc,private_key_enc,private_key_passphrase_enc,fingerprint,notes) values(?,?,?,?,?,?,?,?,?)`, v.Name, v.Host, v.Port, v.UsernameEncrypted, v.PasswordEncrypted, v.PrivateKeyEncrypted, v.PassphraseEncrypted, v.Fingerprint, v.Notes)
	if err != nil {
		return 0, err
	}
	return r.LastInsertId()
}
func (s *Store) UpdateLinuxServer(ctx context.Context, id int64, v LinuxServerWrite) error {
	r, err := s.db.ExecContext(ctx, `update linux_servers set name=?,host=?,port=?,username_enc=?,password_enc=?,private_key_enc=?,private_key_passphrase_enc=?,fingerprint=?,notes=?,updated_at=current_timestamp where id=?`, v.Name, v.Host, v.Port, v.UsernameEncrypted, v.PasswordEncrypted, v.PrivateKeyEncrypted, v.PassphraseEncrypted, v.Fingerprint, v.Notes, id)
	if err != nil {
		return err
	}
	n, _ := r.RowsAffected()
	if n == 0 {
		return ErrNotFound
	}
	return nil
}
func (s *Store) DeleteLinuxServer(ctx context.Context, id int64) error {
	r, err := s.db.ExecContext(ctx, "delete from linux_servers where id=?", id)
	if err != nil {
		return err
	}
	n, _ := r.RowsAffected()
	if n == 0 {
		return ErrNotFound
	}
	return nil
}
func (s *Store) UpdateLinuxStatus(ctx context.Context, id int64, status, message, fingerprint string) error {
	_, err := s.db.ExecContext(ctx, `update linux_servers set last_status=?,last_message=?,fingerprint=case when ?='' then fingerprint else ? end,updated_at=current_timestamp where id=?`, status, message, fingerprint, fingerprint, id)
	return err
}
