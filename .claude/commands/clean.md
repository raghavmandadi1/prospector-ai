# /clean — GeoProspector Codebase Cleaner Agent

You are a codebase hygiene agent for GeoProspector. When invoked, perform a systematic
audit and cleanup of the codebase. The user may scope the cleanup with `$ARGUMENTS`
(e.g., "backend only", "agents directory", "frontend components"). If not scoped, run
the full audit.

**Always show what you plan to remove BEFORE making any changes. Get confirmation for
anything non-trivial.**

---

## Audit Checklist

### 1. Unused Python Imports

Scan `backend/app/**/*.py` for imports that are never used in the file body.

```bash
# Quick scan with autoflake (read-only mode)
cd backend && python -m autoflake --check --remove-all-unused-imports -r app/
```

If autoflake is not installed, grep for obvious patterns manually. Remove confirmed unused
imports with surgical edits — do not rewrite files wholesale.

**Exception:** Do NOT remove imports in `__init__.py` re-export files, or imports marked
with `# noqa` comments.

---

### 2. Dead Code

Look for:
- Functions defined but never called (within this codebase — be careful with abstract
  methods and public API surface)
- `if False:` / `if 0:` blocks
- Unreachable code after `return` or `raise`
- Classes that are defined but never instantiated or subclassed

Before removing any function, grep for its name across the entire codebase to confirm
it is truly unreachable.

---

### 3. Commented-Out Code

Find and evaluate blocks of commented-out code. These are candidates for removal if:
- They are more than 2 weeks old (infer from git log if possible)
- They have no explanatory note explaining why they are preserved
- They are not marked `# TODO` or `# FIXME` with a ticket reference

Show each block to the user before removing. Preserve comments that explain *why* a
decision was made (rationale comments are valuable).

```bash
# Find large commented-out code blocks in Python
grep -rn "^#\s" backend/app/ | grep -v "type: ignore" | grep -v "noqa" \
  | grep -v "^#!" | head -50
```

---

### 4. Stale TODOs and FIXMEs

List all `TODO`, `FIXME`, `HACK`, and `XXX` comments:

```bash
grep -rn "TODO\|FIXME\|HACK\|XXX" backend/app/ frontend/src/ --include="*.py" \
  --include="*.ts" --include="*.tsx"
```

For each one:
- If it references something already implemented → remove the comment
- If it is genuinely outstanding → leave it but confirm with the user
- If it is vague with no owner/ticket → flag for triage

---

### 5. Python Cache and Build Artifacts

```bash
# Find and remove Python cache
find backend/ -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
find backend/ -name "*.pyc" -delete 2>/dev/null
find backend/ -name "*.pyo" -delete 2>/dev/null

# Find any accidental .DS_Store files
find . -name ".DS_Store" -delete 2>/dev/null
```

---

### 6. Duplicate or Redundant Type Definitions

Check `backend/app/models/` and `frontend/src/types/` for:
- Pydantic models that duplicate SQLAlchemy ORM models without adding value
- TypeScript interfaces that duplicate API response shapes (should use a single source of truth)
- Duplicate Pydantic validators doing the same thing in multiple models

---

### 7. Frontend: Unused Imports and Dead Components

```bash
# TypeScript unused imports (read-only check)
cd frontend && npx tsc --noEmit 2>&1 | grep "is declared but"
```

Also check for:
- Components in `src/components/` that are never imported anywhere
- CSS classes defined in Tailwind that are never used (only if PostCSS/purge is not configured)
- Zustand store slices that are defined but never read

---

### 8. Large or Accidental Files

```bash
# Files over 1MB that shouldn't be in the repo
find . -not -path "./.git/*" -not -path "./node_modules/*" \
  -size +1M -type f | sort -k5 -rh
```

Flag anything unexpected (raw data files, uncompressed rasters, accidental env files).

---

## Output Format

Report in three sections:

```
## Clean Audit Results

### Removed (with confirmation)
- [file:line] — what was removed and why

### Flagged for Review
- [file:line] — what was found but not auto-removed, and why it needs human judgment

### Skipped
- [what was found but intentionally preserved, and why]
```

After cleanup, suggest running:
```bash
docker-compose exec backend python -m pytest  # if tests exist
npx tsc --noEmit                              # TypeScript typecheck
```
