# SP1 — Foundations (config + seed + abbr) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build dext's foundation layer — a cached `Settings` object (pydantic-settings), a validated seed-manifest loader for `entrances.yaml`, and stable English-abbreviation derivation for per-university DB filenames — with zero I/O side effects beyond reading env/YAML.

**Architecture:** Two small, focused modules under a new `src/dext/` package. `dext.config` exposes a single `Settings` model and a `functools.lru_cache`-d `get_settings()`. `dext.seed` holds the pydantic seed models, a `SeedError`, `load_manifest`/`get_university`, and pure abbr helpers (`resolve_abbr`/`db_filename`). No DB, network, or LLM. Missing `DEEPSEEK_API_KEY` never raises here (validated later by SP5/SP7).

**Tech Stack:** Python 3.11, pydantic v2, pydantic-settings v2, PyYAML, pytest, uv. (All already installed in `.venv`; `uv` 0.11 available.)

**Spec:** `docs/superpowers/specs/2026-06-13-dext-01-foundations-design.md` (and SP0 overview §7 repo structure).

---

## Pre-verified facts (don't re-derive)

These were validated against the real repo before writing this plan:

- `entrances.yaml` has **38 universities**, validates cleanly against the models below (35 use `org_unit_listing_urls`, 3 — 西安交通大学/厦门大学/中山大学 — use `org_units[].faculty_urls`). No university has an explicit `abbr:` field.
- The abbr algorithm below yields **38 distinct, non-empty, slug-clean abbrs with zero collisions**: `bit bnu buaa cau cqu csu dlut ecnu fudan hit hnu hust jlu lzu muc nankai neu nju nudt nwafu nwpu ouc pku scu scut sdu seu sjtu sysu tju tongji tsinghua uestc ustc whu xjtu xmu zju`.
- `.venv\Scripts\python.exe` has pydantic 2.13, pydantic-settings 2.14, PyYAML 6.0, pytest 9.0. (sqlalchemy/aiohttp/openai/click/tiktoken are NOT yet installed — not needed for SP1.)
- Repo is **not** a git repo yet and has no `src/` or `tests/`.

## File Structure

```
dext/
├─ .gitignore                 # CREATE — python/venv/data/.env/node_modules
├─ pyproject.toml             # EXISTS — no change in SP1 (deps already declared)
├─ entrances.yaml             # EXISTS — real seed (read-only here)
├─ src/dext/
│  ├─ __init__.py             # CREATE — package marker + __version__
│  ├─ config.py               # CREATE — Settings + get_settings()
│  └─ seed.py                 # CREATE — models, SeedError, load_manifest, get_university, resolve_abbr, db_filename
└─ tests/
   ├─ test_package.py         # CREATE — import smoke test
   ├─ test_config.py          # CREATE — Settings behavior
   ├─ test_seed.py            # CREATE — models + manifest loading + get_university
   └─ test_abbr.py            # CREATE — resolve_abbr + db_filename
```

Module boundaries: `config.py` knows nothing about seeds; `seed.py` imports `get_settings` only for the default seed path. Both are pure (read env/YAML only). Tests import via `pythonpath = ["src"]` (already set in `pyproject.toml`), so no install step is required.

**Test command used throughout:** run from the repo root `D:\pyprj\dext`:
```
uv run pytest <path> -v
```
`uv run` auto-syncs the environment to the lockfile on first use, then runs pytest.

---

### Task 1: Project scaffolding & version control

**Files:**
- Create: `.gitignore`
- Create: `src/dext/__init__.py`
- Create: `tests/test_package.py`

- [ ] **Step 1: Initialize git and the default branch**

Run (from `D:\pyprj\dext`):
```bash
git init -b main
```
Expected: `Initialized empty Git repository in D:/pyprj/dext/.git/`

- [ ] **Step 2: Create `.gitignore`**

Create `.gitignore`:
```gitignore
# Python
__pycache__/
*.py[cod]
.pytest_cache/
*.egg-info/

# Virtual env / tooling
.venv/

# Runtime data (per-university SQLite DBs + backups)
/data/

# Secrets
.env

# Node (userscripts build)
**/node_modules/
```

- [ ] **Step 3: Baseline commit of existing files**

Run:
```bash
git add -A
git commit -m "chore: initial commit (specs, seed, userscripts, pyproject)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```
Expected: a commit is created; `git status` is clean afterward (verify `.venv/` and `node_modules/` are NOT listed by `git status`).

- [ ] **Step 4: Branch for SP1 work**

Run:
```bash
git checkout -b sp1-foundations
```
Expected: `Switched to a new branch 'sp1-foundations'`

- [ ] **Step 5: Create the package marker**

Create `src/dext/__init__.py`:
```python
"""dext — graph-driven, human-assisted university faculty crawler."""

__version__ = "0.1.0"
```

- [ ] **Step 6: Write the import smoke test**

Create `tests/test_package.py`:
```python
def test_dext_package_imports():
    import dext

    assert dext.__version__ == "0.1.0"
```

- [ ] **Step 7: Run the smoke test to verify the harness works**

Run:
```bash
uv run pytest tests/test_package.py -v
```
Expected: `1 passed`. (If `uv run` triggers a sync, that is expected on first run.)

- [ ] **Step 8: Commit**

```bash
git add .gitignore src/dext/__init__.py tests/test_package.py
git commit -m "feat(sp1): scaffold dext package + import smoke test" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `dext.config` — Settings + get_settings()

**Files:**
- Create: `src/dext/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config.py`:
```python
from pathlib import Path

from dext.config import Settings, get_settings


def test_defaults_are_sane():
    s = Settings(_env_file=None)
    assert s.data_dir == Path("data/universities")
    assert s.seed_path == Path("entrances.yaml")
    assert s.bridge_host == "127.0.0.1"
    assert s.bridge_port == 21520
    assert s.fetch_timeout_seconds == 60
    assert s.llm_base_url == "https://api.deepseek.com"
    assert s.llm_model == "deepseek-chat"
    assert s.llm_model_retry == "deepseek-reasoner"
    assert s.llm_enable_thinking is False
    assert s.llm_workers == 4
    assert s.invalid_json_max_retry == 2
    assert s.max_depth == 4
    assert s.max_attempts == 3
    assert s.followup_page_limit == 36
    assert s.attempt_penalty == 5.0
    assert s.log_level == "INFO"


def test_missing_api_key_does_not_raise(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    s = Settings(_env_file=None)
    assert s.deepseek_api_key == ""


def test_deepseek_api_key_read_from_unprefixed_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-123")
    s = Settings(_env_file=None)
    assert s.deepseek_api_key == "sk-test-123"


def test_dext_prefixed_env_overrides_defaults(monkeypatch):
    monkeypatch.setenv("DEXT_LLM_MODEL", "custom-model")
    monkeypatch.setenv("DEXT_LLM_WORKERS", "8")
    monkeypatch.setenv("DEXT_BRIDGE_PORT", "30000")
    s = Settings(_env_file=None)
    assert s.llm_model == "custom-model"
    assert s.llm_workers == 8
    assert s.bridge_port == 30000


def test_get_settings_is_cached():
    get_settings.cache_clear()
    a = get_settings()
    b = get_settings()
    assert a is b
    get_settings.cache_clear()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
uv run pytest tests/test_config.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'dext.config'`.

- [ ] **Step 3: Implement `config.py`**

Create `src/dext/config.py`:
```python
"""Runtime configuration loaded from env / .env / defaults.

Single source of truth for all tunables. Pure data: reading this never
touches the DB, network, or LLM. A missing DEEPSEEK_API_KEY is NOT an error
here — SP5/SP7 validate it right before the first LLM call.
"""

from __future__ import annotations

import functools
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DEXT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Paths
    data_dir: Path = Path("data/universities")
    seed_path: Path = Path("entrances.yaml")  # falls back to assets/entrances.yaml in dext.seed

    # Fetch bridge server (contract-fixed port, but host/port configurable)
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 21520
    fetch_timeout_seconds: int = 60

    # LLM (DeepSeek / OpenAI chat-completions format)
    deepseek_api_key: str = Field(default="", validation_alias="DEEPSEEK_API_KEY")
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_model_retry: str = "deepseek-reasoner"
    llm_enable_thinking: bool = False
    llm_workers: int = 4
    invalid_json_max_retry: int = 2

    # Scheduling / retry
    max_depth: int = 4
    max_attempts: int = 3
    followup_page_limit: int = 36
    attempt_penalty: float = 5.0

    # Logging
    log_level: str = "INFO"


@functools.lru_cache
def get_settings() -> Settings:
    """Return the cached process-wide Settings singleton."""
    return Settings()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
uv run pytest tests/test_config.py -v
```
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/dext/config.py tests/test_config.py
git commit -m "feat(sp1): add Settings + cached get_settings()" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `dext.seed` — models, SeedError, entry-point validator

**Files:**
- Create: `src/dext/seed.py`
- Test: `tests/test_seed.py`

- [ ] **Step 1: Write the failing model tests**

Create `tests/test_seed.py`:
```python
from pathlib import Path

import pytest
from pydantic import ValidationError

from dext.seed import (
    Manifest,
    OrgUnitSeed,
    SeedError,
    UniversitySeed,
    get_university,
    load_manifest,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_SEED = REPO_ROOT / "entrances.yaml"


def test_university_with_listing_urls_is_valid():
    u = UniversitySeed(
        name="北京航空航天大学",
        url="https://www.buaa.edu.cn/",
        org_unit_listing_urls=["https://www.buaa.edu.cn/jgsz/jxkyjg.htm"],
    )
    assert u.org_unit_listing_urls


def test_university_with_org_units_faculty_urls_is_valid():
    u = UniversitySeed(
        name="西安交通大学",
        url="https://www.xjtu.edu.cn/",
        org_units=[
            OrgUnitSeed(
                name="数学学院",
                kind="college",
                faculty_urls=["https://math.xjtu.edu.cn/szdw/jsml.htm"],
            )
        ],
    )
    assert u.org_units[0].faculty_urls


def test_university_with_org_unit_url_is_valid():
    u = UniversitySeed(
        name="某大学",
        url="https://x.edu.cn/",
        org_units=[OrgUnitSeed(name="某学院", url="https://col.x.edu.cn/")],
    )
    assert u.org_units[0].url


def test_university_without_any_entry_point_raises():
    with pytest.raises(ValidationError):
        UniversitySeed(name="空大学", url="https://empty.edu.cn/")


def test_org_unit_kind_defaults_to_college():
    assert OrgUnitSeed(name="某学院").kind == "college"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
uv run pytest tests/test_seed.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'dext.seed'`.

- [ ] **Step 3: Implement the models in `seed.py`**

Create `src/dext/seed.py`:
```python
"""Seed manifest models + loading + abbr derivation.

Mirrors the two entry shapes in entrances.yaml (source doc §2). Pure: reads
YAML/env only, no normalization (URL normalization belongs to SP3).
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError, model_validator


class SeedError(Exception):
    """Seed manifest is missing/malformed, or a requested university is absent."""


class OrgUnitSeed(BaseModel):
    name: str
    # Known kinds: college/department/institute/hospital; any string allowed.
    kind: str = "college"
    url: str | None = None
    faculty_urls: list[str] = []


class UniversitySeed(BaseModel):
    name: str
    url: str  # official site; abbr is derived from this host
    location: str | None = None
    abbr: str | None = None  # optional explicit override
    org_unit_listing_urls: list[str] = []
    org_units: list[OrgUnitSeed] = []

    @model_validator(mode="after")
    def _at_least_one_entry(self) -> "UniversitySeed":
        has_listing = bool(self.org_unit_listing_urls)
        has_unit_url = any(u.url for u in self.org_units)
        has_faculty = any(u.faculty_urls for u in self.org_units)
        if not (has_listing or has_unit_url or has_faculty):
            raise ValueError(
                f"university {self.name!r} has no entry point: needs at least one of "
                "org_unit_listing_urls, org_units[].url, or org_units[].faculty_urls"
            )
        return self


class Manifest(BaseModel):
    version: int = 1
    universities: list[UniversitySeed]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
uv run pytest tests/test_seed.py -v
```
Expected: `5 passed` (the `get_university`/`load_manifest` imports resolve because they are added next; if running only these 5, they pass once Task 4 lands. To keep this task green on its own, the imports of `load_manifest`/`get_university` at the top of the test will fail until Task 4.)

> **Note:** because `tests/test_seed.py` imports `load_manifest` and `get_university` (added in Task 4), run this task's verification with `-k` to scope to the model tests:
> ```bash
> uv run pytest tests/test_seed.py -v -k "valid or entry_point or kind"
> ```
> Expected: `5 passed`. The full file goes green at the end of Task 4.

- [ ] **Step 5: Commit**

```bash
git add src/dext/seed.py tests/test_seed.py
git commit -m "feat(sp1): add seed models + entry-point validator" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `load_manifest` + `get_university`

**Files:**
- Modify: `src/dext/seed.py` (append functions)
- Modify: `tests/test_seed.py` (append tests)

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_seed.py`:
```python
def test_load_real_manifest_succeeds():
    m = load_manifest(REAL_SEED)
    assert m.version == 1
    assert len(m.universities) == 38  # update if entrances.yaml grows
    names = {u.name for u in m.universities}
    assert "北京航空航天大学" in names


def test_load_manifest_uses_settings_default_path(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    from dext.config import get_settings

    get_settings.cache_clear()
    m = load_manifest()  # path=None -> settings.seed_path == Path("entrances.yaml")
    assert len(m.universities) == 38
    get_settings.cache_clear()


def test_load_manifest_missing_file_raises_seed_error(tmp_path):
    with pytest.raises(SeedError):
        load_manifest(tmp_path / "nope.yaml")


def test_load_manifest_invalid_entry_raises_seed_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "version: 1\nuniversities:\n  - name: 空\n    url: https://x.edu.cn/\n",
        encoding="utf-8",
    )
    with pytest.raises(SeedError):
        load_manifest(bad)


def test_get_university_hit():
    m = load_manifest(REAL_SEED)
    u = get_university(m, "北京航空航天大学")
    assert u.url == "https://www.buaa.edu.cn/"


def test_get_university_miss_lists_name(tmp_path):
    m = load_manifest(REAL_SEED)
    with pytest.raises(SeedError) as exc:
        get_university(m, "不存在大学")
    assert "不存在大学" in str(exc.value)
```

- [ ] **Step 2: Run to verify the new tests fail**

Run:
```bash
uv run pytest tests/test_seed.py -v -k "manifest or get_university"
```
Expected: FAIL — `ImportError: cannot import name 'load_manifest'` (or `AttributeError`), because the functions don't exist yet.

- [ ] **Step 3: Implement `load_manifest` + `get_university`**

Append to `src/dext/seed.py`:
```python
_ASSETS_FALLBACK = Path("assets/entrances.yaml")


def load_manifest(path: Path | None = None) -> Manifest:
    """Load and validate the seed manifest.

    path=None -> settings.seed_path, falling back to assets/entrances.yaml.
    Raises SeedError (with an actionable message) on missing file, bad YAML,
    or schema violation.
    """
    if path is None:
        from dext.config import get_settings

        configured = get_settings().seed_path
        path = configured if configured.exists() else _ASSETS_FALLBACK

    path = Path(path)
    if not path.exists():
        raise SeedError(
            f"seed manifest not found at {path} (and no {_ASSETS_FALLBACK} fallback)"
        )

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SeedError(f"failed to parse seed YAML at {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise SeedError(
            f"seed manifest at {path} must be a mapping with a 'universities' list"
        )

    try:
        return Manifest.model_validate(raw)
    except ValidationError as exc:
        raise SeedError(f"invalid seed manifest at {path}:\n{exc}") from exc


def get_university(manifest: Manifest, name: str) -> UniversitySeed:
    """Return the university with an exact name match, else raise SeedError."""
    for university in manifest.universities:
        if university.name == name:
            return university
    available = ", ".join(u.name for u in manifest.universities)
    raise SeedError(f"university {name!r} not found in seed. Available: {available}")
```

- [ ] **Step 4: Run the full seed test file to verify all pass**

Run:
```bash
uv run pytest tests/test_seed.py -v
```
Expected: `11 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/dext/seed.py tests/test_seed.py
git commit -m "feat(sp1): add load_manifest + get_university with SeedError" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `resolve_abbr` + `db_filename`

**Files:**
- Modify: `src/dext/seed.py` (append helpers + public functions)
- Test: `tests/test_abbr.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_abbr.py`:
```python
import re
from pathlib import Path

from dext.seed import (
    UniversitySeed,
    db_filename,
    load_manifest,
    resolve_abbr,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_SEED = REPO_ROOT / "entrances.yaml"


def _uni(url: str, abbr: str | None = None) -> UniversitySeed:
    return UniversitySeed(
        name="x", url=url, abbr=abbr, org_unit_listing_urls=["https://x.edu.cn/x.htm"]
    )


def test_resolve_abbr_from_hostname():
    assert resolve_abbr(_uni("https://www.buaa.edu.cn/")) == "buaa"


def test_resolve_abbr_strips_www_and_public_suffix():
    cases = {
        "https://www.pku.edu.cn/": "pku",
        "https://www.tsinghua.edu.cn/": "tsinghua",
        "https://www.dlut.edu.cn/": "dlut",
        "https://www.sjtu.edu.cn/": "sjtu",
        "https://www.sysu.edu.cn/": "sysu",
    }
    for url, expected in cases.items():
        assert resolve_abbr(_uni(url)) == expected


def test_explicit_abbr_overrides_hostname_and_is_slugified():
    assert resolve_abbr(_uni("https://www.buaa.edu.cn/", abbr="  BUAA-X ")) == "buaa-x"


def test_all_seed_abbrs_are_unique_nonempty_and_slug_clean():
    m = load_manifest(REAL_SEED)
    abbrs = [resolve_abbr(u) for u in m.universities]
    assert all(a for a in abbrs)  # non-empty
    assert all(re.fullmatch(r"[a-z0-9-]+", a) for a in abbrs)  # slug-clean
    assert len(abbrs) == 38
    assert len(set(abbrs)) == 38  # zero collisions


def test_db_filename():
    assert db_filename("buaa") == "buaa.db"
```

- [ ] **Step 2: Run to verify the tests fail**

Run:
```bash
uv run pytest tests/test_abbr.py -v
```
Expected: FAIL — `ImportError: cannot import name 'resolve_abbr'`.

- [ ] **Step 3: Implement abbr helpers + public functions**

Append to `src/dext/seed.py`:
```python
# Longest-first public-suffix set. We deliberately avoid tldextract (YAGNI):
# 38 universities resolve uniquely; on a future collision, set seed `abbr`.
_PUBLIC_SUFFIXES = sorted(
    ["edu.cn", "ac.cn", "edu", "com", "org", "net", "cn"],
    key=lambda s: s.count(".") + 1,
    reverse=True,
)


def _slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9-]", "-", value.strip().lower())
    return re.sub(r"-+", "-", value).strip("-")


def _label_left_of_suffix(host: str) -> str:
    """Return the label immediately left of the longest matching public suffix.

    'buaa.edu.cn' -> 'buaa'; 'pku.edu.cn' -> 'pku'. Falls back to the first
    label if no known suffix matches.
    """
    labels = host.split(".")
    for suffix in _PUBLIC_SUFFIXES:
        suffix_labels = suffix.split(".")
        n = len(suffix_labels)
        if len(labels) > n and labels[-n:] == suffix_labels:
            return labels[-(n + 1)]
    return labels[0] if labels else host


def resolve_abbr(university: UniversitySeed) -> str:
    """Stable, readable, lowercase English abbr for the DB filename.

    Uses an explicit seed `abbr` when present; otherwise derives it from the
    official URL's host (strip leading 'www.', take the label left of the
    public suffix). Result is slugified to [a-z0-9-].
    """
    if university.abbr:
        return _slugify(university.abbr)
    host = (urllib.parse.urlsplit(university.url).hostname or "").lower()
    if host.startswith("www."):
        host = host[len("www.") :]
    return _slugify(_label_left_of_suffix(host))


def db_filename(abbr: str) -> str:
    """DB file name for an abbr (path assembly lives in SP2)."""
    return f"{abbr}.db"
```

- [ ] **Step 4: Run to verify the tests pass**

Run:
```bash
uv run pytest tests/test_abbr.py -v
```
Expected: `5 passed`.

- [ ] **Step 5: Run the full SP1 suite**

Run:
```bash
uv run pytest -v
```
Expected: `22 passed` (1 package + 5 config + 11 seed + 5 abbr).

- [ ] **Step 6: Commit**

```bash
git add src/dext/seed.py tests/test_abbr.py
git commit -m "feat(sp1): add resolve_abbr + db_filename (collision-free over 38 schools)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Definition of Done

- `uv run pytest -v` → all green (target: 22 tests).
- Public interface available exactly as the spec lists:
  - `dext.config.get_settings() -> Settings`
  - `dext.seed.load_manifest(path=None) -> Manifest`
  - `dext.seed.get_university(manifest, name) -> UniversitySeed`
  - `dext.seed.resolve_abbr(university) -> str`
  - `dext.seed.db_filename(abbr) -> str`
- No DB/network/LLM imports anywhere in `config.py`/`seed.py`.
- Missing `DEEPSEEK_API_KEY` does not raise.
- All work committed on branch `sp1-foundations`.

## Self-Review (performed while writing this plan)

**1. Spec coverage** — every SP1 spec requirement maps to a task:
- Config model + all fields + `get_settings` lru_cache + missing-key-no-raise → Task 2.
- Seed models (`OrgUnitSeed`/`UniversitySeed`/`Manifest`) + `_at_least_one_entry` validator → Task 3.
- `load_manifest` (UTF-8, `safe_load`, settings→assets fallback, `SeedError`) + `get_university` (hit/miss) → Task 4.
- `resolve_abbr` (explicit override, host-derived, slugify, no tldextract) + `db_filename` → Task 5.
- Spec test list (all 38 abbrs unique/non-empty/expected; both seed forms; missing-entry fails; get_university hit/miss; env override; missing key OK) → covered across Tasks 2–5.
- Spec "不做" items (multi-manifest, remote/hot-reload seed, URL normalization, tldextract) → none introduced. ✓

**2. Placeholder scan** — no TBD/TODO/"handle edge cases"/"similar to Task N"; every code step shows complete code and every run step shows the exact command + expected result. ✓

**3. Type/name consistency** — `Settings`, `get_settings`, `Manifest`, `UniversitySeed`, `OrgUnitSeed`, `SeedError`, `load_manifest`, `get_university`, `resolve_abbr`, `db_filename`, `_slugify`, `_label_left_of_suffix`, `_PUBLIC_SUFFIXES`, `_ASSETS_FALLBACK` are used identically across tasks and tests. Test imports match exported names. ✓

**Note on Task 3 verification:** `tests/test_seed.py` imports `load_manifest`/`get_university` at module top, so the full file only goes green after Task 4. Task 3's verification is scoped with `-k` (documented in its Step 4). This is intentional to keep `seed.py` as one cohesive module rather than splitting models/loaders across files.
