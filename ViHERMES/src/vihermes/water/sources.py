"""Registry of official source documents, with checksum-verified downloads."""

import hashlib
import json
import ssl
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceFile:
    path: str
    url: str
    gazette: str = ""
    sha256: str = ""


@dataclass(frozen=True)
class SourceDocument:
    id: str
    slug: str
    short_name: str
    title: str
    document_type: str
    issuer: str
    signer: str
    issued_date: str
    effective_date: str
    source_url: str
    files: tuple[SourceFile, ...]
    aliases: tuple[str, ...] = ()
    include_articles: tuple[str, ...] | None = None
    gazette_url: str = ""
    notes: str = ""

    def metadata(self):
        """Provenance attached to every graph node of this document."""
        return {
            "document_number": self.id,
            "short_name": self.short_name,
            "document_type": self.document_type,
            "issuer": self.issuer,
            "issued_date": self.issued_date,
            "effective_date": self.effective_date,
            "source_url": self.source_url,
            "gazette": "; ".join(f.gazette for f in self.files if f.gazette),
        }


def load_sources(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    documents = []
    for row in payload["documents"]:
        files = tuple(SourceFile(**f) for f in row["files"])
        if not files:
            raise ValueError(f"{row['id']} has no source files")
        include = row.get("include_articles")
        documents.append(
            SourceDocument(
                **{
                    k: v
                    for k, v in row.items()
                    if k not in {"files", "aliases", "include_articles"}
                },
                files=files,
                aliases=tuple(row.get("aliases", [])),
                include_articles=tuple(include) if include else None,
            )
        )
    ids = [d.id for d in documents]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate document IDs in source registry")
    return documents


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def tls_context():
    """Verified TLS using the certifi CA bundle when present; some Python builds ship none."""
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def download(documents, raw_dir, force=False, timeout=120):
    """Fetch missing files with a plain HTTP client and verify their registered checksums."""
    raw_dir = Path(raw_dir)
    context = tls_context()
    report = []
    for document in documents:
        for item in document.files:
            target = raw_dir / item.path
            if target.exists() and not force and (not item.sha256 or sha256(target) == item.sha256):
                report.append({"path": item.path, "status": "present"})
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_suffix(target.suffix + ".part")
            with urllib.request.urlopen(item.url, timeout=timeout, context=context) as response:
                partial.write_bytes(response.read())
            digest = sha256(partial)
            if item.sha256 and digest != item.sha256:
                partial.unlink()
                raise ValueError(f"Checksum mismatch for {item.url}: {digest}")
            partial.replace(target)
            report.append({"path": item.path, "status": "downloaded", "sha256": digest})
    return report
