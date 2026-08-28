---
type: directory_index
title: Web Server Bug Tracking Index
description: Semantic index and registry of all identified bugs, vulnerabilities, and defects in the aiosyslogd web server component.
tags: [bugs, index, web-server, security, ui]
timestamp: 2026-08-28T10:12:00Z
---

# Web Server Bug Tracking Index

This directory serves as the local bug tracking registry for the `aiosyslogd` web interface, following the Google Open Knowledge Format (OKF v0.1) standard.

---

## Active Bug Registry

| ID | Title | Component | Severity | Status | Document |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **001** | CSRF Protection Bypass via Content-Type Filtering | Python / Middleware | High | Fixed | [001.md](./001.md) |
| **002** | Open Redirect Vulnerability on Login Endpoint | Python / Auth | High | Fixed | [002.md](./002.md) |
| **003** | Arbitrary Database File Access in /api/activity | Python / API | High | Fixed | [003.md](./003.md) |
| **004** | Unhandled OperationalError on Malformed FTS5 Query in Activity Module | Python / Database | Medium | Fixed | [004.md](./004.md) |
| **005** | Admin Self-Demotion and Self-Lockout in User Editing | Python / Auth | Medium | Fixed | [005.md](./005.md) |
| **006** | Fragile admin_required Decorator Missing User Verification | Python / Decorators | Medium | Fixed | [006.md](./006.md) |
| **007** | Non-Atomic File Writes and User File Corruption in AuthManager | Python / Storage | Medium | Fixed | [007.md](./007.md) |
| **008** | Uncaught Null Reference Crash on Profile Page when Gemini is Disabled | JavaScript / Profile | Medium | Fixed | [008.md](./008.md) |
| **009** | Regex Substring False Positives and Missing Word Boundaries in Dynamic Highlighter | JavaScript / Highlighter | Medium | Fixed | [009.md](./009.md) |
| **010** | Bootstrap Modal Multiple Instantiation and Memory Leaks | JavaScript / Modals | Low | Confirmed | [010.md](./010.md) |
| **011** | CSRF Token Leakage in Search and Filter GET Forms | HTML / Templates | Low | Confirmed | [011.md](./011.md) |
| **012** | Unstyled Error Flash Messages (alert-error vs alert-danger) | HTML / CSS | Low | Confirmed | [012.md](./012.md) |
| **013** | Tailwind CSS Utility Classes Used in Bootstrap 5 Environment | HTML / CSS | Low | Confirmed | [013.md](./013.md) |
| **014** | Missing Margin Between Floating Form Controls on Login View | HTML / CSS | Low | Confirmed | [014.md](./014.md) |
| **015** | CSRF Protection Exemption on State-Changing API Endpoints | Python / Middleware | Medium | Confirmed | [015.md](./015.md) |

---

## Bugs by Severity

### High Severity (Security Vulnerabilities)
- [001. CSRF Protection Bypass via Content-Type Filtering](./001.md)
- [002. Open Redirect Vulnerability on Login Endpoint](./002.md)
- [003. Arbitrary Database File Access in /api/activity](./003.md)

### Medium Severity (Logic & Client-Side Errors)
- [004. Unhandled OperationalError on Malformed FTS5 Query in Activity Module](./004.md)
- [005. Admin Self-Demotion and Self-Lockout in User Editing](./005.md)
- [006. Fragile admin_required Decorator Missing User Verification](./006.md)
- [007. Non-Atomic File Writes and User File Corruption in AuthManager](./007.md)
- [008. Uncaught Null Reference Crash on Profile Page when Gemini is Disabled](./008.md)
- [009. Regex Substring False Positives and Missing Word Boundaries in Dynamic Highlighter](./009.md)
- [015. CSRF Protection Exemption on State-Changing API Endpoints](./015.md)

### Low Severity (UI Defects & Code Quality)
- [010. Bootstrap Modal Multiple Instantiation and Memory Leaks](./010.md)
- [011. CSRF Token Leakage in Search and Filter GET Forms](./011.md)
- [012. Unstyled Error Flash Messages (alert-error vs alert-danger)](./012.md)
- [013. Tailwind CSS Utility Classes Used in Bootstrap 5 Environment](./013.md)
- [014. Missing Margin Between Floating Form Controls on Login View](./014.md)

---

## Validation Log

### 2026-08-28 — Source Code Validation

All 15 reports were cross-checked against the current source code (`aiosyslogd/web.py`, `aiosyslogd/auth.py`, `aiosyslogd/activity/__init__.py`, and the referenced templates).

- **Result:** 14 of 15 reports confirmed exactly as documented. [006](./006.md) was confirmed as **latent-only**: the code matches the report, but the `KeyError` / `AttributeError` failure paths are not currently reachable because every admin route stacks `@login_required` first (which already guards missing, deleted, and disabled users). Treated as defensive hardening rather than an active defect.
- **Severity notes:** [008](./008.md)'s impact is console noise plus a dead (already hidden) UI section — arguably Low rather than Medium. [007](./007.md)'s concurrent-write claim is weak under the default single-worker deployment, but the crash-mid-write corruption path is real.

---

## References
- [Main Documentation Index](../index.md)
- [Web Server Implementation](../../aiosyslogd/web.py)
- [Auth Manager Implementation](../../aiosyslogd/auth.py)
