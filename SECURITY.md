# Security Policy

## Supported Versions
Currently, only the `main` branch is supported for security updates.

## Threat Model & Security Review
EcoFac is a local demonstration API handling synthetic data. It is **not** designed for public internet exposure without significant hardening.

### What we have implemented:
- **Input Validation:** All API inputs are validated via Pydantic.
- **File-Size Limits:** CSV uploads are restricted to 5MB to prevent memory exhaustion.
- **SQL Injection Protection:** All database queries use parameterized statements (`?` placeholders).
- **No Secrets:** The codebase requires no secrets, API keys, or credentials.
- **No Equipment Control:** The system has no write-access to physical systems.

### What is NOT implemented (Known Risks):
- **Authentication / Authorization:** There is no user login, no RBAC, and no tenant isolation.
- **Rate Limiting:** The API is susceptible to denial-of-service via request flooding.
- **Path Traversal:** File uploads are read into memory (not saved to disk), mitigating traditional path traversal, but large payloads are still a risk.
- **Audit Integrity:** Audit logs are append-only at the application layer, but a user with direct SQLite access could modify them.

## Reporting a Vulnerability
If you discover a security vulnerability, please do not open a public issue. Contact the repository maintainer directly.
