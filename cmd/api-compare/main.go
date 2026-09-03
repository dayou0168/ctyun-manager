package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/dayou0168/ctyun-manager/internal/apicompare"
)

func main() {
	pythonURL := flag.String("python-url", envOr("CTYUN_COMPARE_PYTHON_URL", "http://127.0.0.1:8000"), "Python service base URL")
	goURL := flag.String("go-url", envOr("CTYUN_COMPARE_GO_URL", "http://127.0.0.1:18000"), "Go service base URL")
	endpointList := flag.String("endpoints", os.Getenv("CTYUN_COMPARE_ENDPOINTS"), "comma-separated read-only API paths")
	reportPath := flag.String("report", "", "optional JSON report file")
	timeout := flag.Duration("timeout", 30*time.Second, "timeout for each HTTP request")
	flag.Parse()

	endpoints := apicompare.DefaultEndpoints
	if strings.TrimSpace(*endpointList) != "" {
		endpoints = splitEndpoints(*endpointList)
	}
	report, err := apicompare.Run(context.Background(), apicompare.Config{
		PythonURL: *pythonURL,
		GoURL:     *goURL,
		Username:  os.Getenv("CTYUN_COMPARE_USERNAME"),
		Password:  os.Getenv("CTYUN_COMPARE_PASSWORD"),
		Endpoints: endpoints,
		Timeout:   *timeout,
	})
	if err != nil {
		fmt.Fprintln(os.Stderr, "comparison failed:", err)
		os.Exit(2)
	}
	encoded, err := json.MarshalIndent(report, "", "  ")
	if err != nil {
		fmt.Fprintln(os.Stderr, "encode report:", err)
		os.Exit(2)
	}
	encoded = append(encoded, '\n')
	if *reportPath != "" {
		if err := os.WriteFile(*reportPath, encoded, 0o600); err != nil {
			fmt.Fprintln(os.Stderr, "write report:", err)
			os.Exit(2)
		}
	}
	_, _ = os.Stdout.Write(encoded)
	if report.Different > 0 || report.Errors > 0 {
		os.Exit(1)
	}
}

func splitEndpoints(value string) []string {
	result := []string{}
	for _, item := range strings.Split(value, ",") {
		if endpoint := strings.TrimSpace(item); endpoint != "" {
			result = append(result, endpoint)
		}
	}
	return result
}

func envOr(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}
