"""Synthetic file ingestion and extraction regression tests."""

import hashlib
import io
import subprocess
import sys
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_profile_drafts import ProfileDraftTests

from app.documents.models import Document, DocumentChunk
from app.documents.routes import extract_file


def docx(body, *, encoding="utf-8", extra=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        archive.writestr("word/document.xml", body.encode(encoding))
        if extra:
            archive.writestr(*extra)
    return output.getvalue()


DOCUMENT = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>one</w:t><w:tab/><w:t>two</w:t><w:br/><w:t>three</w:t></w:r></w:p></w:body></w:document>'


class ExtractionTests(unittest.TestCase):
    def test_docx_nonbreaking_hyphen(self):
        body = DOCUMENT.replace(
            "<w:t>one</w:t><w:tab/><w:t>two</w:t><w:br/><w:t>three</w:t>",
            "<w:t>client</w:t><w:noBreakHyphen/><w:t>server</w:t>",
        )
        result = extract_file(docx(body), "docx")
        self.assertEqual(result["text"], "client\u2011server")
        self.assertEqual(
            result["chunks"], [["client\u2011server", "docx:paragraph:0:chars:0:13"]]
        )

    def test_docx_content_and_security(self):
        result = extract_file(docx(DOCUMENT), "docx")
        self.assertEqual(result["text"], "one\ttwo\nthree")
        self.assertEqual(result["chunks"][0][1], "docx:paragraph:0:chars:0:13")
        malicious = '<!DOCTYPE w:document [<!ENTITY x "private">]>' + DOCUMENT
        self.assertEqual(
            extract_file(docx(malicious, encoding="utf-16"), "docx")["status"], "failed"
        )
        for encoding in ("utf-16-le", "utf-16-be"):
            self.assertEqual(
                extract_file(docx(malicious, encoding=encoding), "docx")["status"],
                "failed",
            )
        self.assertEqual(
            extract_file(docx(DOCUMENT, extra=("../escape", "bad")), "docx")["status"],
            "failed",
        )
        invalid = io.BytesIO()
        with zipfile.ZipFile(invalid, "w") as archive:
            archive.writestr("word/document.xml", DOCUMENT)
        self.assertEqual(extract_file(invalid.getvalue(), "docx")["status"], "failed")
        self.assertEqual(
            extract_file(docx(DOCUMENT, extra=("word/header1.xml", "header")), "docx")[
                "status"
            ],
            "failed",
        )
        textbox = DOCUMENT.replace(
            "<w:tab/>",
            "<w:pict><w:txbxContent><w:p><w:r><w:t>hidden</w:t></w:r></w:p></w:txbxContent></w:pict>",
        )
        self.assertEqual(extract_file(docx(textbox), "docx")["status"], "failed")

    def test_pdf_text_blank_and_page_limit(self):
        from pypdf import PdfWriter
        from pypdf.generic import (
            ArrayObject,
            DecodedStreamObject,
            DictionaryObject,
            NameObject,
            TextStringObject,
        )

        writer = PdfWriter()
        page = writer.add_blank_page(width=200, height=200)
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 10 100 Td (Synthetic PDF) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {
                        NameObject("/F1"): DictionaryObject(
                            {
                                NameObject("/Type"): NameObject("/Font"),
                                NameObject("/Subtype"): NameObject("/Type1"),
                                NameObject("/BaseFont"): NameObject("/Helvetica"),
                            }
                        )
                    }
                )
            }
        )
        output = io.BytesIO()
        writer.write(output)
        result = extract_file(output.getvalue(), "pdf")
        self.assertIn("Synthetic PDF", result["text"])
        self.assertTrue(result["chunks"][0][1].startswith("pdf:page:1:"))
        widget = DictionaryObject(
            {
                NameObject("/Subtype"): NameObject("/Widget"),
                NameObject("/FT"): NameObject("/Tx"),
                NameObject("/T"): TextStringObject("field"),
                NameObject("/V"): TextStringObject("populated value"),
            }
        )
        widget_ref = writer._add_object(widget)
        page[NameObject("/Annots")] = ArrayObject([widget_ref])
        writer._root_object[NameObject("/AcroForm")] = DictionaryObject(
            {NameObject("/Fields"): ArrayObject([widget_ref])}
        )
        output = io.BytesIO()
        writer.write(output)
        self.assertEqual(extract_file(output.getvalue(), "pdf")["status"], "failed")
        del writer._root_object[NameObject("/AcroForm")]
        output = io.BytesIO()
        writer.write(output)
        self.assertEqual(extract_file(output.getvalue(), "pdf")["status"], "failed")
        del page[NameObject("/Annots")]
        stream.set_data(
            b"BT /F1 12 Tf 10 100 Td (Synthetic PDF) Tj ET BI /W 1 /H 1 /CS /DeviceGray /BPC 8 ID \x00 EI"
        )
        output = io.BytesIO()
        writer.write(output)
        self.assertEqual(extract_file(output.getvalue(), "pdf")["status"], "failed")
        blank = PdfWriter()
        blank.add_blank_page(width=200, height=200)
        output = io.BytesIO()
        blank.write(output)
        self.assertEqual(extract_file(output.getvalue(), "pdf")["status"], "failed")
        encrypted = PdfWriter()
        encrypted.add_blank_page(width=200, height=200)
        encrypted.encrypt("synthetic")
        output = io.BytesIO()
        encrypted.write(output)
        self.assertEqual(extract_file(output.getvalue(), "pdf")["status"], "failed")
        oversized = PdfWriter()
        for _ in range(51):
            oversized.add_blank_page(width=200, height=200)
        output = io.BytesIO()
        oversized.write(output)
        from app.documents.service import DocumentError

        with self.assertRaises(DocumentError) as error:
            extract_file(output.getvalue(), "pdf")
        self.assertEqual(error.exception.status, 413)

    def test_monitor_kills_and_reaps_child(self):
        real_popen = subprocess.Popen
        processes = []

        def sleeping(*args, **kwargs):
            child = real_popen(
                [sys.executable, "-c", "import time; time.sleep(30)"], **kwargs
            )
            processes.append(child)
            return child

        with (
            patch("app.documents.routes.subprocess.Popen", side_effect=sleeping),
            patch("app.documents.routes.psutil.Process") as monitor,
        ):
            monitor.return_value.memory_info.return_value = SimpleNamespace(
                rss=513 * 1024 * 1024
            )
            self.assertEqual(extract_file(b"synthetic", "pdf")["status"], "failed")
        self.assertIsNotNone(processes[0].poll())
        with (
            patch("app.documents.routes.subprocess.Popen", side_effect=sleeping),
            patch("app.documents.routes.time.monotonic", side_effect=[0, 11]),
        ):
            self.assertEqual(extract_file(b"synthetic", "pdf")["status"], "failed")
        self.assertIsNotNone(processes[1].poll())

    def test_txt_strict_utf8_and_limit(self):
        self.assertEqual(extract_file("한글".encode(), "txt")["text"], "한글")
        self.assertEqual(extract_file(b"\xff", "txt")["status"], "failed")
        from app.documents.service import DocumentError

        with self.assertRaises(DocumentError) as error:
            extract_file(b"a" * 100001, "txt")
        self.assertEqual(error.exception.status, 413)


class FileDocumentsTests(ProfileDraftTests):
    def upload(self, profile, data, name="test.txt", mime="text/plain", key=None):
        headers = {"X-CSRF-Token": self.csrf}
        if key:
            headers["Idempotency-Key"] = key
        return self.client.post(
            "/api/profiles/" + profile["id"] + "/documents/files",
            headers=headers,
            data={"expected_revision": str(profile["revision"]), "kind": "other"},
            files={"file": (name, data, mime)},
        )

    def test_file_storage_replay_and_failure(self):
        profile = self.create()
        original = "private 한글\r\n".encode()
        response = self.upload(profile, original, key="file-key")
        self.assertEqual(response.status_code, 202, response.text)
        document = response.json()["document"]
        self.assertEqual(
            document["content_sha256"], hashlib.sha256(original).hexdigest()
        )
        self.assertEqual(document["status"], "queued")
        self.assertIsNone(document["job_id"])
        self.assertNotIn("private", response.text)
        other, csrf, _, _ = self.login()
        self.assertEqual(other.get("/api/documents/" + document["id"]).status_code, 404)
        self.assertEqual(
            other.post(
                "/api/profiles/" + profile["id"] + "/documents/files",
                headers={"X-CSRF-Token": csrf},
                data={"expected_revision": "0", "kind": "other"},
                files={"file": ("test.txt", b"other", "text/plain")},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.upload(profile, original, key="file-key").json(), response.json()
        )
        self.assertEqual(
            self.upload(profile, b"different", key="file-key").status_code, 409
        )
        with Session(self.engine) as db:
            row = db.get(Document, UUID(document["id"]))
            self.assertEqual(row.raw_content, original)
            chunk = db.scalar(
                select(DocumentChunk).where(DocumentChunk.document_id == row.id)
            )
            self.assertEqual(chunk.text_content, original.decode())
        current = self.client.get("/api/profiles/" + profile["id"]).json()
        failure = self.upload(current, b"\xff")
        self.assertEqual(failure.status_code, 202, failure.text)
        self.assertEqual(failure.json()["document"]["status"], "failed")
        self.assertEqual(failure.json()["document"]["error_code"], "extraction_failed")

    def test_rollback_after_invalidation(self):
        profile = self.create()
        with patch(
            "app.documents.service.invalidate", side_effect=SQLAlchemyError("synthetic")
        ):
            response = self.upload(profile, b"private", key="rollback")
        self.assertEqual(response.status_code, 503)
        with Session(self.engine) as db:
            self.assertIsNone(
                db.scalar(
                    select(Document).where(Document.profile_id == UUID(profile["id"]))
                )
            )
        self.assertEqual(
            self.client.get("/api/profiles/" + profile["id"]).json()["revision"],
            profile["revision"],
        )

    def test_reject_mismatch_and_oversize(self):
        profile = self.create()
        self.assertEqual(
            self.upload(profile, b"%PDF-fake", "file.txt").status_code, 415
        )
        self.assertEqual(
            self.upload(profile, b"a" * (5 * 1024 * 1024 + 1)).status_code, 413
        )
        self.assertEqual(
            self.upload(profile, b"x", "file.pdf", "application/pdf").status_code, 415
        )
