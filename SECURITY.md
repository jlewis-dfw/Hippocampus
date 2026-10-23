# Security

Please report security problems **privately**, not in a public issue: use the repository's "Report a vulnerability" button
(Security tab), or contact the maintainer directly.

Only the latest version on `main` is supported.

Hippocampus is meant to run on your own machine or a trusted network. By default it listens on `127.0.0.1` only and requires
a token for writes. If you expose it more widely, put a reverse proxy with TLS and authentication in front of it, keep
`HIPPO_REQUIRE_AUTH=true`, and treat access tokens like passwords.
