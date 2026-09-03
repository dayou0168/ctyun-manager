# Bundled xl2tpd source

`xl2tpd-v1.3.20.tar.gz` is the unmodified upstream v1.3.20 source archive from:

`https://github.com/xelerance/xl2tpd/archive/refs/tags/v1.3.20.tar.gz`

SHA-256:

`3db95450c5e1efaeea7547af344b5621f4453af3c227f26ec43bcbc79087b045`

The management platform uploads this archive to a target server before running the L2TP installer. This lets CTyunOS systems whose vendor repositories do not contain `xl2tpd` build it without requiring the target server to access GitHub. The installer verifies the SHA-256 before extraction. The upstream license files are included in the archive.
