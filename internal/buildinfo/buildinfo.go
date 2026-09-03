package buildinfo

import "runtime"

// These values are intentionally overridable with -ldflags at release time.
var (
	Version   = "go-migration-dev"
	BuildTime = "unknown"
	Commit    = "unknown"
)

type Info struct {
	Version   string `json:"version"`
	BuildTime string `json:"build_time"`
	Commit    string `json:"commit"`
	GoVersion string `json:"go_version"`
}

func Current() Info {
	return Info{
		Version:   Version,
		BuildTime: BuildTime,
		Commit:    Commit,
		GoVersion: runtime.Version(),
	}
}
