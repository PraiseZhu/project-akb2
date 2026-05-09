"""AKB2.0 Phase C-1 read-only Workspace Toolbox Scanner.

Discovers real workspace tool assets (Codex skills, Claude skills, Claude
scripts and hooks) and emits a paired JSONL manifest + Markdown report.

Read-only by design. Never executes a candidate, never writes outside the
two AKB2.0 allowed directories, never imports the AKB pipeline.
"""

__version__ = "0.1.0"
