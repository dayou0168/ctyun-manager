package ctyun

import "testing"

func TestNormalizeResourceFields(t *testing.T) {
	t.Parallel()
	item := map[string]any{"instanceID": "ecs-1", "instanceName": "node", "instanceStatus": "running", "regionID": "r1", "flavor": map[string]any{"flavorName": "s2"}, "networkCardList": []any{map[string]any{"IPv4Address": "192.0.2.1", "networkCardID": "nic-1"}}, "addresses": []any{map[string]any{"addressList": []any{map[string]any{"type": "floating", "addr": "198.51.100.1"}}}}}
	got := Normalize(item, "ecs", "fallback", map[string]string{"r1": "Region One"})
	for key, want := range map[string]any{"id": "ecs-1", "name": "node", "status": "running", "region": "r1", "region_name": "Region One", "spec": "s2", "private_ip": "192.0.2.1", "public_ip": "198.51.100.1", "network_card_id": "nic-1"} {
		if got[key] != want {
			t.Errorf("%s=%#v want %#v", key, got[key], want)
		}
	}
}

func TestNormalizeImageVisibility(t *testing.T) {
	t.Parallel()
	got := Normalize(map[string]any{"imageID": "image-1", "imageVisibility": "private"}, "image", "r1", nil)
	if got["visibility"] != "0" {
		t.Fatalf("visibility=%#v", got["visibility"])
	}
}
