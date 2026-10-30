# Knowledge documents

Drop Markdown, text or PDF files here (PDF needs `pip install pypdf`). They are split by heading and
retrieved per caller turn so the assistant can answer from them.

Two tiers, on purpose:

- `../facts.md` - approved facts the assistant may **assert** (prices, stats, guarantees).
- `docs/` - material the assistant may **answer from** (how-tos, policies, descriptions). A price or
  statistic that appears only here is still blocked by the guardrail until it is added to `facts.md`.

This folder is read-only inside the OpenShell sandbox; change it through git.
