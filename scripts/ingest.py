#!/usr/bin/env python3
"""
Document ingestion CLI for PAI-chatbot RAG system.

Usage:
    python scripts/ingest.py --db general --file docs/manual.txt
    python scripts/ingest.py --db general --dir docs/
    python scripts/ingest.py --db general --url "https://docs.google.com/document/d/XXX"
    python scripts/ingest.py --db general --url "https://docs.google.com/spreadsheets/d/XXX"
    python scripts/ingest.py --db general --url "https://example.com/page"
    python scripts/ingest.py --db general --stats
    python scripts/ingest.py --db general --clear

Supported formats: .txt  .md  .pdf  .json
URL sources: Google Docs, Google Sheets (公開設定のもの), 一般Webページ
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import rag_manager
from core.ingest_helpers import SUPPORTED_EXTENSIONS, fetch_url, read_file


def _ingest_url(db_name: str, url: str, source: str = "") -> None:
    try:
        text = fetch_url(url)
    except RuntimeError as e:
        print(f"  [error] {e}")
        return
    label = source or url
    count = rag_manager.ingest_text(db_name, text, source=label)
    print(f"  [ok] {url} → {count} chunks stored")


def _ingest_file(db_name: str, path: Path, source: str = "") -> None:
    try:
        text = read_file(path)
    except RuntimeError as e:
        print(f"  [skip] {e}")
        return
    if text is None:
        return
    label = source or str(path)
    count = rag_manager.ingest_text(db_name, text, source=label)
    print(f"  [ok] {path.name} → {count} chunks stored")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest documents into PAI-chatbot RAG system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--db", required=True, help="Target database name (e.g. general)")
    parser.add_argument("--file", type=Path, help="Single file to ingest")
    parser.add_argument("--dir", type=Path, help="Directory to ingest recursively")
    parser.add_argument("--url", help="URL to fetch and ingest (Google Docs/Sheets/Web)")
    parser.add_argument("--source", default="", help="Override source label in metadata")
    parser.add_argument("--stats", action="store_true", help="Show collection stats and exit")
    parser.add_argument("--clear", action="store_true", help="Delete all documents from collection")
    args = parser.parse_args()

    if args.stats:
        stats = rag_manager.collection_stats(args.db)
        print(f"DB           : {args.db}")
        print(f"RAG enabled  : {stats['enabled']}")
        print(f"Backend      : {stats.get('vector_backend', 'chroma')}")
        print(f"Model        : {stats['embedding_model']}")
        print(f"Chunks stored: {stats['document_count']}")
        return

    if args.clear:
        count = rag_manager.clear_collection(args.db)
        print(f"Cleared {count} chunks from '{args.db}'")
        return

    if args.file:
        if not args.file.exists():
            print(f"Error: file not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        print(f"Ingesting {args.file} → DB '{args.db}'")
        _ingest_file(args.db, args.file, args.source)
        return

    if args.dir:
        if not args.dir.exists():
            print(f"Error: directory not found: {args.dir}", file=sys.stderr)
            sys.exit(1)
        files = sorted(f for f in args.dir.rglob("*") if f.suffix.lower() in SUPPORTED_EXTENSIONS)
        print(f"Ingesting {len(files)} file(s) from '{args.dir}' → DB '{args.db}'")
        for f in files:
            _ingest_file(args.db, f, args.source)
        return

    if args.url:
        print(f"Fetching {args.url} → DB '{args.db}'")
        _ingest_url(args.db, args.url, args.source)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
