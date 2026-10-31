# Threat Model

EcoFac is a local demonstration API. The primary assets are data integrity and reproducibility.

| STRIDE Category | Threat | Mitigation |
|---|---|---|
| **Spoofing** | Caller submits readings claiming a meter they don't own. | No auth in initial scope. Documented limitation. |
| **Tampering** | Modifying a CSV to hide an anomaly. | Evidence records reference immutable reading IDs. |
| **Repudiation** | User denies acknowledging an incident. | Incident transitions are timestamped and audited. |
| **Information Disclosure** | Synthetic data leaks. | All data is synthetically generated. |
| **Denial of Service** | Oversized CSV upload. | 5MB file size limit enforced at the API boundary. |
| **Elevation of Privilege** | Low-priv user closes incidents. | Out of scope initially (single-user model). |

**Non-Threat:** Because there is no physical control path and no BMS integration, the risk of commanding equipment is structurally eliminated.
