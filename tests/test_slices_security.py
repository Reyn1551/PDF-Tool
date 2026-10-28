#!/usr/bin/env python3
"""Regression tests for the security slice (``pdf_ops_security.py``)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz

from pdf_ops_security import (
    add_attachment, delete_attachment, extract_attachment, find_matches, inspect,
    list_attachments, redact, redact_areas, sanitize,
)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def build_pdf(secret="SECRET-TOKEN", meta_title="Confidential draft"):
    doc = fitz.open()
    page = doc.new_page(width=400, height=500)
    page.insert_text((40, 60), "Public introduction paragraph.", fontsize=12)
    page.insert_text((40, 100), f"Account {secret} must never leak.", fontsize=12)
    page.insert_text((40, 140), "Third line of ordinary content.", fontsize=12)
    second = doc.new_page(width=400, height=500)
    second.insert_text((40, 60), f"Second page mentions {secret} again.", fontsize=12)
    doc.set_metadata({"title": meta_title, "author": "Tester"})
    data = doc.tobytes()
    doc.close()
    return data


def text_of(data):
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        return "\n".join(doc.load_page(i).get_text() for i in range(doc.page_count))
    finally:
        doc.close()


def main():
    tmp = tempfile.mkdtemp(prefix="sec_slice_")
    blob = build_pdf()

    print("== 1. find_matches ==")
    hits = find_matches(blob, terms=["SECRET-TOKEN"])
    check("finds both occurrences", len(hits) == 2, len(hits))
    check("reports 0-based pages", {h["page"] for h in hits} == {0, 1},
          {h["page"] for h in hits})
    check("hit carries matched text", all(h["text"] == "SECRET-TOKEN" for h in hits))
    check("page filter applied", len(find_matches(blob, terms=["SECRET-TOKEN"],
                                                  pages=[1])) == 1)
    check("matching is case-insensitive by default",
          len(find_matches(blob, terms=["secret-token"])) == 2)
    check("case_sensitive=True narrows the match",
          find_matches(blob, terms=["secret-token"], case_sensitive=True) == [])
    check("regex pattern supported",
          len(find_matches(blob, pattern=r"account\s+\S+")) == 1)
    check("whole word does not match inside a word",
          find_matches(blob, terms=["ECRE"], whole_word=True) == [])
    check("substring matched without whole word",
          len(find_matches(blob, terms=["ECRE"])) == 2)
    check("whole word matches a standalone word",
          len(find_matches(blob, terms=["Account"], whole_word=True)) == 1,
          find_matches(blob, terms=["Account"], whole_word=True))
    check("partial match without whole word",
          len(find_matches(blob, terms=["Account"])) == 1)

    print("== 2. redact ==")
    redacted, report = redact(blob, terms=["SECRET-TOKEN"])
    check("report counts hits", report["hits"] == 2, report)
    check("report counts pages", report["pages"] == 2, report)
    cleaned = text_of(redacted)
    check("token gone from text", "SECRET-TOKEN" not in cleaned)
    check("other content preserved", "Public introduction paragraph." in cleaned,
          cleaned[:120])
    check("redacted file is a valid PDF", redacted[:4] == b"%PDF")
    untouched, empty_report = redact(blob, terms=["NOT-IN-THIS-DOCUMENT"])
    check("no matches -> zero hits", empty_report["hits"] == 0, empty_report)
    check("no matches leaves text intact", "SECRET-TOKEN" in text_of(untouched))
    progress = []
    redact(blob, terms=["SECRET-TOKEN"], progress=lambda d, t: progress.append((d, t)))
    check("progress reports every page", progress == [(1, 2), (2, 2)], progress)

    print("== 3. redact_areas ==")
    area_hits = find_matches(blob, terms=["Public introduction paragraph."])
    check("area fixture located", bool(area_hits))
    if area_hits:
        page_no = area_hits[0]["page"]
        rect = area_hits[0]["rect"]
        area_bytes, area_report = redact_areas(blob, {page_no: [rect]})
        check("area redaction reports a count", area_report >= 1, area_report)
        remaining = text_of(area_bytes)
        check("area content removed", "Public introduction paragraph." not in remaining)
        check("other page untouched", "SECRET-TOKEN" in remaining)

    print("== 4. inspect vs sanitize ==")
    info = inspect(blob)
    check("inspect counts pages", info["pages"] == 2, info["pages"])
    check("inspect shows metadata title", info["metadata"].get("title") ==
          "Confidential draft", info["metadata"])
    check("inspect reports no attachments", info["attachments"] == [])
    check("inspect not encrypted", info["encrypted"] is False)
    sanitized, sreport = sanitize(blob)
    check("sanitize returns PDF bytes", sanitized[:4] == b"%PDF")
    check("sanitize reports the metadata it stripped",
          sreport["metadata"].get("title") == "Confidential draft", sreport["metadata"])
    doc = fitz.open(stream=sanitized, filetype="pdf")
    check("sanitized metadata cleared", not (doc.metadata or {}).get("title"),
          doc.metadata)
    doc.close()
    kept, _ = sanitize(blob, metadata=False)
    doc = fitz.open(stream=kept, filetype="pdf")
    check("metadata=False keeps title",
          (doc.metadata or {}).get("title") == "Confidential draft", doc.metadata)
    doc.close()

    print("== 5. attachments round-trip ==")
    payload = os.path.join(tmp, "notes.txt")
    with open(payload, "w", encoding="utf-8") as fh:
        fh.write("attachment payload \u2713\n")
    attached, att_name = add_attachment(blob, payload, description="unit test")
    listing = list_attachments(attached)
    check("attachment listed once", len(listing) == 1, listing)
    check("attachment name reported", listing[0]["name"] == att_name, listing[0])
    check("attachment size matches file",
          listing[0]["size"] == os.path.getsize(payload), listing[0]["size"])
    check("description stored", listing[0]["description"] == "unit test",
          listing[0]["description"])
    restored = os.path.join(tmp, "restored.txt")
    extract_attachment(attached, att_name, restored)
    with open(restored, "rb") as fh:
        check("extracted bytes identical", fh.read() == open(payload, "rb").read())
    detached = delete_attachment(attached, att_name)
    check("delete removes attachment", list_attachments(detached) == [])
    try:
        add_attachment(attached, payload)
    except ValueError:
        check("duplicate name rejected", True)
    else:
        check("duplicate name rejected", False)
    try:
        delete_attachment(blob, "missing.txt")
    except ValueError:
        check("deleting a missing attachment raises", True)
    else:
        check("deleting a missing attachment raises", False)
    try:
        add_attachment(blob, os.path.join(tmp, "nope.bin"))
    except ValueError:
        check("missing attachment source raises", True)
    else:
        check("missing attachment source raises", False)

    print("== 6. sanitize drops attachments ==")
    stripped, _rep = sanitize(attached)
    check("sanitized file drops embedded attachment",
          list_attachments(stripped) == [], list_attachments(stripped))

    print(f"\nRESULT: {PASS} PASS, {FAIL} FAIL")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()