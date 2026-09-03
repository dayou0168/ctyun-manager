package httpserver

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"math/big"
	"net/http"
	"regexp"
	"strings"
	"time"

	"github.com/dayou0168/ctyun-manager/internal/security"
	"github.com/dayou0168/ctyun-manager/internal/storage"
)

var rechargeAmountPattern = regexp.MustCompile(`^\+?(?:(\d+)(?:\.(\d{0,2}))?|\.(\d{1,2}))$`)

type rechargeBody struct {
	Amount        string `json:"amount"`
	PaymentMethod string `json:"payment_method"`
}

func normalizeRechargeAmount(raw string) (string, bool) {
	match := rechargeAmountPattern.FindStringSubmatch(strings.TrimSpace(raw))
	if match == nil {
		return "", false
	}
	whole, fraction := match[1], match[2]
	if whole == "" {
		whole, fraction = "0", match[3]
	}
	whole = strings.TrimLeft(whole, "0")
	if whole == "" {
		whole = "0"
	}
	fraction += strings.Repeat("0", 2-len(fraction))
	cents := new(big.Int)
	if _, ok := cents.SetString(whole+fraction, 10); !ok || cents.Sign() <= 0 {
		return "", false
	}
	limit := new(big.Int).Mul(big.NewInt(100_000_000), big.NewInt(100))
	if cents.Cmp(limit) >= 0 {
		return "", false
	}
	return whole + "." + fraction, true
}

func validPaymentMethod(method string) bool {
	switch strings.ToLower(strings.TrimSpace(method)) {
	case "alipay", "bestpay", "wechat":
		return true
	default:
		return false
	}
}

func (s *Server) rechargeOrder(w http.ResponseWriter, r *http.Request, _ security.Session) {
	var body rechargeBody
	if json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body) != nil {
		writeDetail(w, 422, "invalid_request")
		return
	}
	amount, ok := normalizeRechargeAmount(body.Amount)
	if !ok {
		writeDetail(w, 422, "充值金额必须大于 0、少于 1 亿元且最多保留两位小数")
		return
	}
	if !validPaymentMethod(body.PaymentMethod) {
		writeDetail(w, 422, "不支持的支付方式")
		return
	}
	id, account, ok := s.actionAccount(w, r)
	if !ok {
		return
	}
	started := time.Now()
	result, err := s.callBrowserWorker(r.Context(), "/v1/recharge/order", account, map[string]any{"amount": amount, "payment_method": strings.ToLower(body.PaymentMethod)})
	if err != nil {
		s.recordOperation(r, id, "recharge", "", "create_recharge_order", "failed", err.Error())
		writeDetail(w, 502, err.Error())
		return
	}
	s.persistBrowserResult(r.Context(), id, result, false)
	delete(result, "storage_state")
	result["elapsed_ms"] = time.Since(started).Milliseconds()
	status := firstText(result, "status")
	s.recordOperation(r, id, "recharge", firstText(result, "order_no"), "create_recharge_order", status, firstText(result, "message"))
	if status != "ready" {
		writeDetail(w, 502, firstText(result, "message"))
		return
	}
	writeJSON(w, 200, result)
}

func (s *Server) rechargePayment(w http.ResponseWriter, r *http.Request, _ security.Session) {
	var body rechargeBody
	if json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body) != nil || !validPaymentMethod(body.PaymentMethod) {
		writeDetail(w, 422, "不支持的支付方式")
		return
	}
	id, account, ok := s.actionAccount(w, r)
	if !ok {
		return
	}
	result, err := s.callBrowserWorker(r.Context(), "/v1/recharge/payment", account, map[string]any{"payment_method": strings.ToLower(body.PaymentMethod)})
	if err != nil {
		s.recordOperation(r, id, "recharge", "", "activate_payment", "failed", err.Error())
		writeDetail(w, 502, err.Error())
		return
	}
	s.persistBrowserResult(r.Context(), id, result, false)
	delete(result, "storage_state")
	status := firstText(result, "status")
	s.recordOperation(r, id, "recharge", "", "activate_payment", status, firstText(result, "message"))
	if status != "ready" {
		writeDetail(w, 502, firstText(result, "message"))
		return
	}
	writeJSON(w, 200, result)
}

func (s *Server) rechargeQR(w http.ResponseWriter, r *http.Request, _ security.Session) {
	_, account, ok := s.actionAccount(w, r)
	if !ok {
		return
	}
	result, err := s.callBrowserWorker(r.Context(), "/v1/recharge/qr", account, nil)
	if err != nil || firstText(result, "status") != "ready" {
		if err != nil {
			writeDetail(w, 502, err.Error())
		} else {
			writeDetail(w, 404, firstText(result, "message"))
		}
		return
	}
	png, err := base64.StdEncoding.DecodeString(firstText(result, "png_base64"))
	if err != nil || len(png) < 8 {
		writeDetail(w, 502, "浏览器工作进程返回了无效二维码")
		return
	}
	setNoCache(w.Header())
	w.Header().Set("Content-Type", "image/png")
	w.WriteHeader(200)
	_, _ = w.Write(png)
}

func (s *Server) rechargeQRRefresh(w http.ResponseWriter, r *http.Request, _ security.Session) {
	id, account, ok := s.actionAccount(w, r)
	if !ok {
		return
	}
	result, err := s.callBrowserWorker(r.Context(), "/v1/recharge/refresh", account, nil)
	if err != nil {
		s.recordOperation(r, id, "recharge", "", "refresh_payment_qr", "failed", err.Error())
		writeDetail(w, 502, err.Error())
		return
	}
	s.persistBrowserResult(r.Context(), id, result, false)
	delete(result, "storage_state")
	status := firstText(result, "status")
	s.recordOperation(r, id, "recharge", "", "refresh_payment_qr", status, firstText(result, "message"))
	if status != "ready" {
		writeDetail(w, 409, firstText(result, "message"))
		return
	}
	writeJSON(w, 200, result)
}

func (s *Server) rechargeStatus(w http.ResponseWriter, r *http.Request, _ security.Session) {
	id, account, ok := s.actionAccount(w, r)
	if !ok {
		return
	}
	result, err := s.callBrowserWorker(r.Context(), "/v1/recharge/status", account, nil)
	if err != nil {
		writeDetail(w, 502, err.Error())
		return
	}
	s.persistBrowserResult(r.Context(), id, result, false)
	delete(result, "storage_state")
	if status := firstText(result, "status"); status == "paid" || status == "success" || status == "completed" {
		go s.refreshFinanceAfterPayment(id, account)
	}
	writeJSON(w, 200, result)
}

func (s *Server) refreshFinanceAfterPayment(id int64, account storage.AccountRecord) {
	for _, delay := range []time.Duration{0, 3 * time.Second} {
		if delay > 0 {
			time.Sleep(delay)
		}
		ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
		result, err := s.callBrowserWorker(ctx, "/v1/finance", account, nil)
		if err == nil {
			s.persistBrowserResult(ctx, id, result, true)
		}
		cancel()
	}
}

func (s *Server) rechargePrewarm(w http.ResponseWriter, r *http.Request, _ security.Session) {
	if !s.cfg.RechargePrewarmEnabled || strings.TrimSpace(s.cfg.BrowserWorkerToken) == "" || s.store == nil {
		writeJSON(w, 200, map[string]any{"status": "disabled", "message": "充值页预热未启用"})
		return
	}
	accounts, err := s.store.Accounts(r.Context())
	if err != nil {
		s.internalStoreError(w, "recharge_prewarm_accounts_failed", err)
		return
	}
	go func(rows []storage.Account) {
		for _, row := range rows {
			if row.Status != "" && !strings.EqualFold(row.Status, "enabled") {
				continue
			}
			ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
			account, err := s.store.AccountByID(ctx, row.ID)
			if err == nil {
				if result, callErr := s.callBrowserWorker(ctx, "/v1/prewarm", account, nil); callErr == nil {
					s.persistBrowserResult(ctx, row.ID, result, false)
				}
			}
			cancel()
		}
	}(append([]storage.Account(nil), accounts...))
	writeJSON(w, 200, map[string]any{"status": "queued", "message": "充值页后台预热已排队"})
}
