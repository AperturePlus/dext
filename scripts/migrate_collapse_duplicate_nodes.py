#!/usr/bin/env python
"""
One-time migration: collapse legacy node_key duplicates.

Background:
  Commit 210a66a changed node_key_for from "<type>:org:<id>:url:<url>" to "url:<url>".
  No data migration was run, so URLs discovered before the change have a legacy-key
  node AND a new-key node for the same page. Each duplicate causes a redundant LLM
  extraction (the page cache prevents HTTP re-fetch, but the LLM is re-invoked).

What this does:
  1. For each URL with multiple detail_url nodes:
     - Pick the canonical node (prefer done; if none, lowest id).
     - Re-point inbound edges of the duplicates to the canonical node.
     - Mark duplicates as skipped with last_error="duplicate_url_migrated:<canonical_id>".
     - Rename skipped nodes' keys to avoid the UNIQUE constraint collision.
  2. Normalize the canonical node's node_key to "url:<url>".

Usage:
  python scripts/migrate_collapse_duplicate_nodes.py <db_path> [--dry-run] [--no-backup]

The script backs up the DB to <db_path>.pre-migration before making changes unless
--no-backup is passed. With --dry-run it only prints what it would do.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

SQL_DUPLICATE_GROUPS = """
SELECT
  replace(replace(url,'https://',''),'http://','') AS schemeless_key,
  COUNT(*) AS n
FROM crawl_graph_nodes
WHERE type = 'detail_url'
GROUP BY replace(replace(url,'https://',''),'http://','')
HAVING COUNT(*) > 1
ORDER BY schemeless_key
"""

SQL_NODES_FOR_SCHEMELESS_KEY = """
SELECT id, node_key, status, org_unit_id, url
FROM crawl_graph_nodes
WHERE type = 'detail_url'
  AND replace(replace(url,'https://',''),'http://','') = ?
ORDER BY id
"""

SQL_EDGES_TO_NODE = "SELECT id FROM crawl_graph_edges WHERE to_node_id = ?"

SQL_UPDATE_EDGE_TO = "UPDATE crawl_graph_edges SET to_node_id = ? WHERE id = ?"

SQL_UPDATE_NODE_KEY = "UPDATE crawl_graph_nodes SET node_key = ? WHERE id = ?"

SQL_SKIP_AND_RENAME = """
UPDATE crawl_graph_nodes
SET status = 'skipped',
    last_error = ?,
    completed_at = datetime('now'),
    node_key = ?
WHERE id = ?
"""


def pick_canonical(nodes: list[tuple]) -> int:
    """Prefer a 'done' node (lowest id among done); else lowest id.
    `nodes` rows are (id, node_key, status, org_unit_id, url)."""
    done = [n for n in nodes if n[2] == "done"]
    pool = done if done else nodes
    return min(n[0] for n in pool)


def main():
    parser = argparse.ArgumentParser(description="Collapse legacy node_key duplicates")
    parser.add_argument("db_path", type=Path, help="Path to the SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without executing")
    parser.add_argument("--no-backup", action="store_true", help="Skip creating .pre-migration backup")
    args = parser.parse_args()

    db_path: Path = args.db_path.resolve()
    if not db_path.exists():
        sys.exit(f"DB not found: {db_path}")

    if not args.no_backup and not args.dry_run:
        backup_path = db_path.with_name(db_path.name + ".pre-migration")
        if backup_path.exists():
            sys.exit(f"Backup already exists: {backup_path} (remove it or use --no-backup)")
        print(f"Backing up {db_path} -> {backup_path}")
        shutil.copy2(db_path, backup_path)

    con = sqlite3.connect(db_path)
    cur = con.cursor()

    groups = cur.execute(SQL_DUPLICATE_GROUPS).fetchall()
    print(f"Found {len(groups)} page groups with >1 detail_url node (scheme-insensitive)")

    total_duplicate_nodes = 0
    total_edges_moved = 0
    total_keys_normalized = 0

    for schemeless_key, count in groups:
        nodes = cur.execute(SQL_NODES_FOR_SCHEMELESS_KEY, (schemeless_key,)).fetchall()
        # nodes: list of (id, node_key, status, org_unit_id, url)
        canonical_id = pick_canonical(nodes)
        # Canonical URL: prefer the https variant if any node uses it (modern default),
        # else the first node's url.
        https_nodes = [n for n in nodes if n[4].startswith("https://")]
        canonical_url = (https_nodes[0][4] if https_nodes else nodes[0][4])
        expected_key = f"url:{canonical_url}"
        duplicates = [n for n in nodes if n[0] != canonical_id]

        # 1. Move edges pointing to duplicates -> canonical. A (from,type) may already
        #    have an edge to the canonical (same parent discovered both twins); in that
        #    case the duplicate edge is just dropped — the relationship is preserved by
        #    the surviving edge.
        for dup_id, _key, _status, _org, _url in duplicates:
            for (edge_id, from_id, edge_type) in cur.execute(
                "SELECT id, from_node_id, edge_type FROM crawl_graph_edges WHERE to_node_id = ?", (dup_id,)
            ).fetchall():
                exists = cur.execute(
                    "SELECT 1 FROM crawl_graph_edges WHERE from_node_id=? AND to_node_id=? AND edge_type=? AND id!=? LIMIT 1",
                    (from_id, canonical_id, edge_type, edge_id),
                ).fetchone()
                if args.dry_run:
                    verb = "drop" if exists else "repoint"
                    print(f"  [dry] edge {edge_id} ({from_id}->{dup_id}) would {verb} to {canonical_id}")
                else:
                    if exists:
                        cur.execute("DELETE FROM crawl_graph_edges WHERE id = ?", (edge_id,))
                    else:
                        cur.execute(SQL_UPDATE_EDGE_TO, (canonical_id, edge_id))
                total_edges_moved += 1

        # 2. Skip + rename duplicate nodes (rename avoids UNIQUE collision with canonical)
        for dup_id, _key, _status, _org, _url in duplicates:
            if args.dry_run:
                print(f"  [dry] node {dup_id} ({_url[:50]}) would skip+rename (canonical={canonical_id})")
            else:
                cur.execute(
                    SQL_SKIP_AND_RENAME,
                    (f"duplicate_url_migrated:{canonical_id}", f"migrated:skipped:{dup_id}", dup_id),
                )
            total_duplicate_nodes += 1

        # 3. Normalize canonical node_key to url:<canonical_url>
        canonical_row = next(n for n in nodes if n[0] == canonical_id)
        if canonical_row[1] != expected_key:
            if args.dry_run:
                print(f"  [dry] canonical {canonical_id} key {canonical_row[1]!r} -> {expected_key!r}")
            else:
                # If the canonical already has the https key shape but a skipped dup
                # held it before, the rename above freed it. Verify no clash.
                clash = cur.execute(
                    "SELECT 1 FROM crawl_graph_nodes WHERE node_key=? AND id!=? LIMIT 1",
                    (expected_key, canonical_id),
                ).fetchone()
                if clash:
                    print(f"  WARN: cannot set canonical {canonical_id} key to {expected_key!r} (clash); leaving as-is")
                else:
                    cur.execute(SQL_UPDATE_NODE_KEY, (expected_key, canonical_id))
                    total_keys_normalized += 1
        else:
            # already correct shape; but if canonical url is http and an https twin
            # existed, leave canonical on http (it's the done one). key stays url:<http>.
            pass

    if args.dry_run:
        print("\n[dry-run] Summary:")
        print(f"  URL groups with duplicates: {len(groups)}")
        print(f"  Duplicate nodes to skip+rename: {total_duplicate_nodes}")
        print(f"  Edges to re-point: {total_edges_moved}")
        print(f"  Canonical keys to normalize: {total_keys_normalized}")
    else:
        con.commit()

    # Phase 2: normalize remaining legacy-key nodes whose URL has NO duplicate.
    # These are single-node-per-url legacy nodes; renaming their key to "url:<url>"
    # is safe (no collision — verified by the group query above) and prevents future
    # re-discoveries from creating duplicates.
    orphan_legacy = cur.execute(
        "SELECT id, url FROM crawl_graph_nodes "
        "WHERE type='detail_url' AND node_key LIKE 'detail_url:org:%'"
    ).fetchall()
    orphan_normalized = 0
    for nid, url in orphan_legacy:
        target_key = f"url:{url}"
        clash = cur.execute(
            "SELECT 1 FROM crawl_graph_nodes WHERE node_key = ? AND id != ? LIMIT 1", (target_key, nid)
        ).fetchone()
        if clash:
            print(f"  WARN: node {nid} url={url[:60]} cannot normalize to {target_key!r} (clash); skipping")
            continue
        if args.dry_run:
            print(f"  [dry] orphan node {nid} key -> {target_key!r}")
        else:
            cur.execute(SQL_UPDATE_NODE_KEY, (target_key, nid))
        orphan_normalized += 1

    if args.dry_run:
        print(f"  Orphan legacy keys to normalize: {orphan_normalized}")
        print("No changes made.")
    else:
        con.commit()
        print("\nMigration complete:")
        print(f"  URL groups processed: {len(groups)}")
        print(f"  Duplicate nodes skipped+renamed: {total_duplicate_nodes}")
        print(f"  Edges re-pointed: {total_edges_moved}")
        print(f"  Canonical keys normalized: {total_keys_normalized}")
        print(f"  Orphan legacy keys normalized: {orphan_normalized}")

    con.close()


if __name__ == "__main__":
    main()
