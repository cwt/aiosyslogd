---
type: directory_index
title: Web Server Bug & Improvement Tracking Index
description: Semantic index and registry of all identified bugs, security vulnerabilities, and proposed architectural/UI improvements in the aiosyslogd web server component.
tags: [bugs, improvements, index, web-server, security, ui]
timestamp: 2026-08-28T16:56:00Z
---

# Web Server Bug & Improvement Tracking Index

This directory serves as the local tracking registry for defects and architectural improvements in the `aiosyslogd` web interface, following the Google Open Knowledge Format (OKF v0.1) standard.

---

## Registry Index

| ID | Title | Type | Component | Severity | Status | Document |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **001** | CSRF Protection Bypass via Content-Type Filtering | Bug | Python / Middleware | High | Fixed | [001.md](./001.md) |
| **002** | Open Redirect Vulnerability on Login Endpoint | Bug | Python / Auth | High | Fixed | [002.md](./002.md) |
| **003** | Arbitrary Database File Access in /api/activity | Bug | Python / API | High | Fixed | [003.md](./003.md) |
| **004** | Unhandled OperationalError on Malformed FTS5 Query in Activity Module | Bug | Python / Database | Medium | Fixed | [004.md](./004.md) |
| **005** | Admin Self-Demotion and Self-Lockout in User Editing | Bug | Python / Auth | Medium | Fixed | [005.md](./005.md) |
| **006** | Fragile admin_required Decorator Missing User Verification | Bug | Python / Decorators | Medium | Fixed | [006.md](./006.md) |
| **007** | Non-Atomic File Writes and User File Corruption in AuthManager | Bug | Python / Storage | Medium | Fixed | [007.md](./007.md) |
| **008** | Uncaught Null Reference Crash on Profile Page when Gemini is Disabled | Bug | JavaScript / Profile | Medium | Fixed | [008.md](./008.md) |
| **009** | Regex Substring False Positives and Missing Word Boundaries in Dynamic Highlighter | Bug | JavaScript / Highlighter | Medium | Fixed | [009.md](./009.md) |
| **010** | Bootstrap Modal Multiple Instantiation and Memory Leaks | Bug | JavaScript / Modals | Low | Fixed | [010.md](./010.md) |
| **011** | CSRF Token Leakage in Search and Filter GET Forms | Bug | HTML / Templates | Low | Fixed | [011.md](./011.md) |
| **012** | Unstyled Error Flash Messages (alert-error vs alert-danger) | Bug | HTML / CSS | Low | Fixed | [012.md](./012.md) |
| **013** | Tailwind CSS Utility Classes Used in Bootstrap 5 Environment | Bug | HTML / CSS | Low | Fixed | [013.md](./013.md) |
| **014** | Missing Margin Between Floating Form Controls on Login View | Bug | HTML / CSS | Low | Fixed | [014.md](./014.md) |
| **015** | CSRF Protection Exemption on State-Changing API Endpoints | Bug | Python / Middleware | Medium | Fixed | [015.md](./015.md) |
| **016** | Remove Dynamic JavaScript HTML Injection for Gemini Modals | Improvement | Jinja2 / JavaScript | Medium | Implemented | [016.md](./016.md) |
| **017** | Add Template Extension Blocks for Styles and Scripts in base.html | Improvement | Jinja2 / Templates | Low | Implemented | [017.md](./017.md) |
| **018** | Add Active Route Highlighting in Top Navigation Bar | Improvement | Navigation / UI | Low | Implemented | [018.md](./018.md) |
| **019** | Add Password Confirmation Field on User Management and Profile Forms | Improvement | Auth / Forms | Medium | Implemented | [019.md](./019.md) |
| **020** | Remove Inline Event Handlers for Strict Content Security Policy (CSP) | Improvement | Security / JS | Low | Implemented | [020.md](./020.md) |
| **021** | Optimize Dynamic Highlighter In-Place Reset without Page Reload | Improvement | JavaScript / Perf | Low | Implemented | [021.md](./021.md) |
| **022** | Consolidate API Fetch Helper and CSRF Token Handling | Improvement | JavaScript / API | Low | Implemented | [022.md](./022.md) |
| **023** | Monospace Typography and Word Breaking for Syslog Message Cells | Improvement | CSS / Readability | Low | Implemented | [023.md](./023.md) |
| **024** | Remove Brittle Inline Hover Style Event Handlers on Action Buttons | Improvement | CSS / UI | Low | Implemented | [024.md](./024.md) |
| **025** | Upgrade User Form Checkboxes to Bootstrap 5 Switches | Improvement | UI / Forms | Low | Implemented | [025.md](./025.md) |
| **026** | Add Dark Mode Color Scheme Support via Bootstrap 5.3+ | Improvement | CSS / Dark Mode | Low | Implemented | [026.md](./026.md) |
| **027** | Add Accessibility Improvements (A11y, ARIA Labels, and Table Scopes) | Improvement | Accessibility / A11y | Low | Implemented | [027.md](./027.md) |
| **028** | Add Subresource Integrity (SRI) Hashes to CDN Assets and Add Default Favicon | Improvement | Security / Assets | Low | Proposed | [028.md](./028.md) |

---

## Items by Category

### Resolved Bugs
- [001. CSRF Protection Bypass via Content-Type Filtering](./001.md)
- [002. Open Redirect Vulnerability on Login Endpoint](./002.md)
- [003. Arbitrary Database File Access in /api/activity](./003.md)
- [004. Unhandled OperationalError on Malformed FTS5 Query in Activity Module](./004.md)
- [005. Admin Self-Demotion and Self-Lockout in User Editing](./005.md)
- [006. Fragile admin_required Decorator Missing User Verification](./006.md)
- [007. Non-Atomic File Writes and User File Corruption in AuthManager](./007.md)
- [008. Uncaught Null Reference Crash on Profile Page when Gemini is Disabled](./008.md)
- [009. Regex Substring False Positives and Missing Word Boundaries in Dynamic Highlighter](./009.md)
- [010. Bootstrap Modal Multiple Instantiation and Memory Leaks](./010.md)
- [011. CSRF Token Leakage in Search and Filter GET Forms](./011.md)
- [012. Unstyled Error Flash Messages (alert-error vs alert-danger)](./012.md)
- [013. Tailwind CSS Utility Classes Used in Bootstrap 5 Environment](./013.md)
- [014. Missing Margin Between Floating Form Controls on Login View](./014.md)
- [015. CSRF Protection Exemption on State-Changing API Endpoints](./015.md)

### Proposed Architectural & UI Improvements
- **Template Architecture**: [016](./016.md), [017](./017.md), [018](./018.md)
- **Security & Forms**: [019](./019.md), [020](./020.md), [022](./022.md), [028](./028.md)
- **Performance & JavaScript**: [021](./021.md), [022](./022.md)
- **Visuals & Typography**: [023](./023.md), [024](./024.md), [025](./025.md), [026](./026.md)
- **Accessibility (A11y)**: [027](./027.md)

---

## Validation Log

### 2026-08-28 — Frontend Improvement Proposals Recorded
Registered 13 improvement proposals ([016](./016.md)–[028](./028.md)) covering template architecture, CSP readiness, performance, typography, accessibility, and visual modernizations.

### 2026-08-28 — Bug Remediation & Resolution
All 15 reported bugs have been addressed, verified with automated unit tests, and resolved across backend and frontend modules.

### 2026-08-28 — Source Code Validation
All 15 reports were cross-checked against the current source code (`aiosyslogd/web.py`, `aiosyslogd/auth.py`, `aiosyslogd/activity/__init__.py`, and the referenced templates).

---

## References
- [Main Documentation Index](../index.md)
- [Web Server Implementation](../../aiosyslogd/web.py)
- [Auth Manager Implementation](../../aiosyslogd/auth.py)
