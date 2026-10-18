# Integration maintenance

This repository is an independent, source-only integration of AiCA. See README.md and docs/INTEGRATION_SOURCES.md before editing. Preserve original author attribution and third-party notices.

- Keep parser Runtime and compat/execution-worker isolated; they expose different packages under the same Python names.
- Do not introduce runtime dependencies on private upstream Git objects.
- Never commit recording state, credentials, API keys, SQLite files, model weights, user content or installed dependencies.
- Use synthetic fixtures and mock providers for automated tests; paid model calls require separate user authorization.
- Verify frontend tests/build, Gateway/contract tests and affected Worker tests. Record actual failures and limits in docs/INTEGRATION_VALIDATION.md.
- Keep platform account authorization on all user artifacts; the independent B semantic-edit review server is loopback-only and not a production endpoint.
- Do not alter unrelated user work or upstream repositories.
