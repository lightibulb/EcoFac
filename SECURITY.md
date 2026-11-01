# Security Policy

## Supported Versions
Currently, only the `main` branch is supported for security updates.

## Threat Model & Security Review
EcoFac is a local demonstration API handling synthetic data. It is **not** designed for public internet exposure without significant hardening.

### What we have implemented:
- **Input Validation:** All API inputs are validated via Pydantic.
- **File-Size Limits:** CSV uploads are restricted to 5MB; the server reads at most 5MB plus one byte before rejecting, so oversized bodies are not buffered.
- **Import Hygiene:** source and batch ids are pattern-validated, a batch id can be imported only once, and a single CSV can auto-create at most 500 meters.
- **Append-only Evidence and Audit:** database triggers reject UPDATE and DELETE on `evidence_records` and `audit_events`.
- **SQL Injection Protection:** All database queries use parameterized statements (`?` placeholders).
- **No Secrets:** The codebase requires no secrets, API keys, or credentials.
- **No Equipment Control:** The system has no write-access to physical systems.

### What is NOT implemented (Known Risks):
- **Authentication / Authorization:** There is no user login, no RBAC, and no tenant isolation.
- **Rate Limiting:** The API is susceptible to denial-of-service via request flooding.
- **Path Traversal:** File uploads are read into memory (not saved to disk), mitigating traditional path traversal, but large payloads are still a risk.
- **Audit Integrity:** Append-only is enforced by SQLite triggers, but a user with direct file access could still drop the triggers or replace the file.

## Reporting a Vulnerability
If you discover a security vulnerability, please do not open a public issue. Contact the repository maintainer directly.
