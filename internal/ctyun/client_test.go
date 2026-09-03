package ctyun

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestEncodeQueryMatchesPythonQuote(t *testing.T) {
	t.Parallel()
	got := EncodeQuery(map[string]any{"z": "+", "empty": "", "a": "hello world", "slash": "a/b", "enabled": true})
	want := "a=hello%20world&enabled=True&slash=a%2Fb&z=%2B"
	if got != want {
		t.Fatalf("got %q want %q", got, want)
	}
}

func TestSignDeterministic(t *testing.T) {
	t.Parallel()
	headers := Sign("test-ak", "test-sk", []byte(`{"regionID": "cn-test", "pageNo": 1}`), map[string]any{"z": "+", "a": "hello world"}, "20260903T000000Z", "00112233-4455-6677-8899-aabbccddeeff")
	want := "test-ak Headers=ctyun-eop-request-id;eop-date Signature=fx0B0FVncBPcfouaiOQU58EqYxNAtQN4iz11ZvEVT/E="
	if headers["Eop-Authorization"] != want {
		t.Fatalf("bad authorization: %q", headers["Eop-Authorization"])
	}
}

func TestRequestAndResponseParsing(t *testing.T) {
	t.Parallel()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Eop-Authorization") == "" || r.Header.Get("ctyun-eop-request-id") == "" {
			t.Error("missing signature headers")
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"statusCode":800,"returnObj":{"regionList":[{"regionID":"r1"}],"totalPages":2}}`))
	}))
	defer server.Close()
	client := New("ak", "sk", time.Second)
	data, err := client.Request(context.Background(), server.URL, "/regions", http.MethodGet, map[string]any{"name": "hello world"})
	if err != nil {
		t.Fatal(err)
	}
	if len(Items(data)) != 1 || TotalPages(data) != 2 {
		t.Fatalf("unexpected data: %#v", data)
	}
}

func TestParseRegionIDs(t *testing.T) {
	t.Parallel()
	got := ParseRegionIDs("a； b\nc，d")
	if len(got) != 4 || got[2] != "c" {
		t.Fatalf("unexpected regions: %#v", got)
	}
}

func TestItemsFlattensNestedLists(t *testing.T) {
	t.Parallel()
	items := Items(map[string]any{"returnObj": []any{map[string]any{"vpcs": []any{map[string]any{"id": "v1"}}}}})
	if len(items) != 1 || items[0]["id"] != "v1" {
		t.Fatalf("items=%#v", items)
	}
}
