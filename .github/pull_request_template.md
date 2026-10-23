## What

## Why

## Assumptions
<!-- Ambiguous requirements and the assumption taken. -->

## Review questions
1. **Which endpoint or job is new or changed, and is `require_scope` on it?**
2. **Which input comes from outside, and where is it validated?**
3. **Can PII reach a log, error message, URL or external payload here?**
4. **Which test would fail if this change were wrong?**
5. **Is a dependency added, and why?**

## Security review
<!-- Paste the findings of the security-review skill. -->

## How tested

## Checklist
- [ ] `make check` green
- [ ] Screenshots for UI changes
- [ ] i18n checked (nl/en)
- [ ] Migration reversible: yes / no / n.a.
- [ ] Docs updated (threat model, dataflow, runbook, ADR) where a flow changed
