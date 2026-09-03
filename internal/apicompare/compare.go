package apicompare

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"sort"
	"strings"
	"time"
)

const maxResponseBytes = 256 << 20

var DefaultEndpoints = []string{
	"/api/version",
	"/api/me",
	"/api/accounts",
	"/api/finance",
	"/api/dashboard/summary",
	"/api/resources/vpc",
	"/api/resources/subnet",
	"/api/resources/vip",
	"/api/resources/ecs",
	"/api/resources/eip",
	"/api/resources/security_group",
	"/api/resources/image",
	"/api/operations",
	"/api/runtime/status",
}

type Config struct {
	PythonURL string
	GoURL     string
	Username  string
	Password  string
	Endpoints []string
	Timeout   time.Duration
}

type Report struct {
	GeneratedAt string   `json:"generated_at"`
	PythonURL   string   `json:"python_url"`
	GoURL       string   `json:"go_url"`
	Matched     int      `json:"matched"`
	Different   int      `json:"different"`
	Errors      int      `json:"errors"`
	Results     []Result `json:"results"`
}

type Result struct {
	Endpoint    string       `json:"endpoint"`
	Outcome     string       `json:"outcome"`
	Python      *Snapshot    `json:"python,omitempty"`
	Go          *Snapshot    `json:"go,omitempty"`
	Differences []Difference `json:"differences,omitempty"`
	Error       string       `json:"error,omitempty"`
}

type Snapshot struct {
	Status int `json:"status"`
	Body   any `json:"-"`
}

type Difference struct {
	Path   string `json:"path"`
	Python any    `json:"python,omitempty"`
	Go     any    `json:"go,omitempty"`
	Reason string `json:"reason"`
}

type serviceClient struct {
	baseURL string
	client  *http.Client
}

func Run(ctx context.Context, cfg Config) (Report, error) {
	if strings.TrimSpace(cfg.Username) == "" || cfg.Password == "" {
		return Report{}, errors.New("CTYUN_COMPARE_USERNAME and CTYUN_COMPARE_PASSWORD are required")
	}
	if cfg.Timeout <= 0 {
		cfg.Timeout = 30 * time.Second
	}
	if len(cfg.Endpoints) == 0 {
		cfg.Endpoints = append([]string(nil), DefaultEndpoints...)
	}
	for _, endpoint := range cfg.Endpoints {
		if err := ValidateEndpoint(endpoint); err != nil {
			return Report{}, err
		}
	}
	pythonClient, err := newServiceClient(cfg.PythonURL, cfg.Timeout)
	if err != nil {
		return Report{}, fmt.Errorf("Python URL: %w", err)
	}
	goClient, err := newServiceClient(cfg.GoURL, cfg.Timeout)
	if err != nil {
		return Report{}, fmt.Errorf("Go URL: %w", err)
	}
	if err := pythonClient.login(ctx, cfg.Username, cfg.Password); err != nil {
		return Report{}, fmt.Errorf("Python login failed: %w", err)
	}
	if err := goClient.login(ctx, cfg.Username, cfg.Password); err != nil {
		return Report{}, fmt.Errorf("Go login failed: %w", err)
	}

	report := Report{
		GeneratedAt: time.Now().UTC().Format(time.RFC3339),
		PythonURL:   pythonClient.baseURL,
		GoURL:       goClient.baseURL,
		Results:     make([]Result, 0, len(cfg.Endpoints)),
	}
	for _, endpoint := range cfg.Endpoints {
		result := compareEndpoint(ctx, pythonClient, goClient, endpoint)
		report.Results = append(report.Results, result)
		switch result.Outcome {
		case "match":
			report.Matched++
		case "different":
			report.Different++
		default:
			report.Errors++
		}
	}
	return report, nil
}

func ValidateEndpoint(endpoint string) error {
	parsed, err := url.Parse(endpoint)
	if err != nil || parsed.IsAbs() || parsed.Host != "" || !strings.HasPrefix(parsed.Path, "/api/") {
		return fmt.Errorf("endpoint must be a relative /api/ path: %q", endpoint)
	}
	allowedExact := map[string]bool{
		"/api/version": true, "/api/me": true, "/api/accounts": true,
		"/api/finance": true, "/api/dashboard/summary": true,
		"/api/operations": true, "/api/runtime/status": true,
	}
	if allowedExact[parsed.Path] {
		if parsed.RawQuery != "" {
			return fmt.Errorf("query parameters are not allowed for %q", endpoint)
		}
		return nil
	}
	if !strings.HasPrefix(parsed.Path, "/api/resources/") || strings.Contains(strings.TrimPrefix(parsed.Path, "/api/resources/"), "/") {
		return fmt.Errorf("endpoint is not in the read-only allowlist: %q", endpoint)
	}
	resourceType := strings.TrimPrefix(parsed.Path, "/api/resources/")
	if resourceType == "" {
		return fmt.Errorf("resource type is required: %q", endpoint)
	}
	query := parsed.Query()
	for key, values := range query {
		if key != "account_id" || len(values) != 1 || strings.TrimSpace(values[0]) == "" {
			return fmt.Errorf("unsupported resource query in %q", endpoint)
		}
	}
	return nil
}

func newServiceClient(rawURL string, timeout time.Duration) (*serviceClient, error) {
	parsed, err := url.Parse(strings.TrimSpace(rawURL))
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" || parsed.User != nil {
		return nil, errors.New("must be an HTTP(S) URL without embedded credentials")
	}
	if parsed.RawQuery != "" || parsed.Fragment != "" {
		return nil, errors.New("must not contain a query or fragment")
	}
	jar, err := cookiejar.New(nil)
	if err != nil {
		return nil, err
	}
	return &serviceClient{
		baseURL: strings.TrimRight(parsed.String(), "/"),
		client:  &http.Client{Jar: jar, Timeout: timeout},
	}, nil
}

func (s *serviceClient) login(ctx context.Context, username, password string) error {
	body, err := json.Marshal(map[string]string{"username": username, "password": password})
	if err != nil {
		return err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, s.baseURL+"/api/login", bytes.NewReader(body))
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := s.client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, maxResponseBytes))
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d", response.StatusCode)
	}
	return nil
}

func compareEndpoint(ctx context.Context, pythonClient, goClient *serviceClient, endpoint string) Result {
	type fetchResult struct {
		name     string
		snapshot *Snapshot
		err      error
	}
	results := make(chan fetchResult, 2)
	go func() {
		snapshot, err := pythonClient.get(ctx, endpoint)
		results <- fetchResult{name: "python", snapshot: snapshot, err: err}
	}()
	go func() {
		snapshot, err := goClient.get(ctx, endpoint)
		results <- fetchResult{name: "go", snapshot: snapshot, err: err}
	}()
	result := Result{Endpoint: endpoint}
	var fetchErrors []string
	for range 2 {
		fetched := <-results
		if fetched.name == "python" {
			result.Python = fetched.snapshot
		} else {
			result.Go = fetched.snapshot
		}
		if fetched.err != nil {
			fetchErrors = append(fetchErrors, fetched.name+": "+fetched.err.Error())
		}
	}
	if len(fetchErrors) > 0 {
		sort.Strings(fetchErrors)
		result.Outcome = "error"
		result.Error = strings.Join(fetchErrors, "; ")
		return result
	}
	if result.Python.Status < 200 || result.Python.Status >= 300 || result.Go.Status < 200 || result.Go.Status >= 300 {
		result.Outcome = "error"
		result.Error = fmt.Sprintf("non-success response: Python HTTP %d, Go HTTP %d", result.Python.Status, result.Go.Status)
		return result
	}
	if result.Python.Status != result.Go.Status {
		result.Differences = append(result.Differences, Difference{
			Path: "/status", Python: result.Python.Status, Go: result.Go.Status, Reason: "different HTTP status",
		})
	}
	pythonBody := cloneAndNormalize(endpoint, result.Python.Body)
	goBody := cloneAndNormalize(endpoint, result.Go.Body)
	compareValues("", pythonBody, goBody, &result.Differences)
	if len(result.Differences) == 0 {
		result.Outcome = "match"
	} else {
		result.Outcome = "different"
	}
	return result
}

func (s *serviceClient) get(ctx context.Context, endpoint string) (*Snapshot, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, s.baseURL+endpoint, nil)
	if err != nil {
		return nil, err
	}
	response, err := s.client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	limited := io.LimitReader(response.Body, maxResponseBytes+1)
	body, err := io.ReadAll(limited)
	if err != nil {
		return nil, err
	}
	if len(body) > maxResponseBytes {
		return nil, errors.New("response exceeds 256 MiB")
	}
	var value any
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	if err := decoder.Decode(&value); err != nil {
		return nil, fmt.Errorf("invalid JSON response (HTTP %d): %w", response.StatusCode, err)
	}
	return &Snapshot{Status: response.StatusCode, Body: value}, nil
}

func cloneAndNormalize(endpoint string, value any) any {
	object, ok := value.(map[string]any)
	if !ok {
		return value
	}
	cloned := make(map[string]any, len(object))
	for key, item := range object {
		cloned[key] = item
	}
	switch endpoint {
	case "/api/version":
		for _, key := range []string{"version", "build_time", "commit", "go_version", "migration_phase"} {
			delete(cloned, key)
		}
	case "/api/me":
		delete(cloned, "version")
	case "/api/runtime/status":
		for _, key := range []string{"finance_refresh_queue", "finance_refresh_pending", "cookie_keepalive_queue", "cookie_keepalive_pending", "browser"} {
			delete(cloned, key)
		}
	}
	return cloned
}

func compareValues(path string, python, goValue any, differences *[]Difference) {
	if numbersEqual(python, goValue) {
		return
	}
	pythonObject, pythonIsObject := python.(map[string]any)
	goObject, goIsObject := goValue.(map[string]any)
	if pythonIsObject && goIsObject {
		keys := map[string]bool{}
		for key := range pythonObject {
			keys[key] = true
		}
		for key := range goObject {
			keys[key] = true
		}
		sortedKeys := make([]string, 0, len(keys))
		for key := range keys {
			sortedKeys = append(sortedKeys, key)
		}
		sort.Strings(sortedKeys)
		for _, key := range sortedKeys {
			pythonItem, pythonOK := pythonObject[key]
			goItem, goOK := goObject[key]
			itemPath := path + "/" + escapePointer(key)
			if !pythonOK || !goOK {
				*differences = append(*differences, Difference{Path: itemPath, Python: pythonItem, Go: goItem, Reason: "missing field"})
				continue
			}
			compareValues(itemPath, pythonItem, goItem, differences)
		}
		return
	}
	pythonArray, pythonIsArray := python.([]any)
	goArray, goIsArray := goValue.([]any)
	if pythonIsArray && goIsArray {
		if len(pythonArray) != len(goArray) {
			*differences = append(*differences, Difference{Path: path, Python: len(pythonArray), Go: len(goArray), Reason: "different array length"})
		}
		limit := min(len(pythonArray), len(goArray))
		for index := range limit {
			compareValues(fmt.Sprintf("%s/%d", path, index), pythonArray[index], goArray[index], differences)
		}
		return
	}
	if python == nil && goValue == nil {
		return
	}
	if fmt.Sprintf("%T", python) != fmt.Sprintf("%T", goValue) || fmt.Sprint(python) != fmt.Sprint(goValue) {
		*differences = append(*differences, Difference{Path: pointerRoot(path), Python: python, Go: goValue, Reason: "different value"})
	}
}

func numbersEqual(left, right any) bool {
	leftNumber, leftOK := left.(json.Number)
	rightNumber, rightOK := right.(json.Number)
	if !leftOK || !rightOK {
		return false
	}
	leftRat, leftValid := new(big.Rat).SetString(leftNumber.String())
	rightRat, rightValid := new(big.Rat).SetString(rightNumber.String())
	return leftValid && rightValid && leftRat.Cmp(rightRat) == 0
}

func escapePointer(value string) string {
	return strings.ReplaceAll(strings.ReplaceAll(value, "~", "~0"), "/", "~1")
}

func pointerRoot(path string) string {
	if path == "" {
		return "/"
	}
	return path
}
