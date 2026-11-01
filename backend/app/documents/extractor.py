"""Run untrusted document parsers in a resource-limited child process."""

import io
import json
import resource
import sys
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree

LIMIT = 100000
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ExtractionError(Exception):
    pass


def extract(data: bytes, kind: str) -> list[tuple[str, str]]:
    if kind == "txt":
        value = data.decode("utf-8", errors="strict")
        return [(value, f"text:chars:0:{len(value)}")]
    if kind == "pdf":
        from pypdf import PdfReader
        from pypdf.generic import ContentStream

        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted or len(reader.pages) > 50:
            raise ExtractionError("limit" if not reader.is_encrypted else "invalid")
        if "/AcroForm" in reader.trailer["/Root"]:
            raise ExtractionError("invalid")
        result = []
        for index, page in enumerate(reader.pages):
            if any(
                annotation.get_object().get("/Subtype") == "/Widget"
                for annotation in page.get("/Annots", [])
            ):
                raise ExtractionError("invalid")
            resources = page.get("/Resources")
            if resources:
                objects = resources.get_object().get("/XObject")
                if objects and any(
                    obj.get_object().get("/Subtype") in ("/Image", "/Form")
                    for obj in objects.get_object().values()
                ):
                    raise ExtractionError("invalid")
            contents = page.get_contents()
            if contents is not None:
                if len(contents.get_data()) > 20_000_000:
                    raise ExtractionError("limit")
                if any(
                    operator == b"INLINE IMAGE"
                    for _, operator in ContentStream(contents, reader).operations
                ):
                    raise ExtractionError("invalid")
            value = page.extract_text()
            if not value.strip():
                raise ExtractionError("invalid")
            result.append((value, f"pdf:page:{index + 1}:chars:0:{len(value)}"))
        return result
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) > 1000 or sum(m.file_size for m in members) > 20_000_000:
            raise ExtractionError("limit")
        names = set()
        for member in members:
            name = member.filename
            path = PurePosixPath(name)
            if (
                name in names
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in name
                or member.flag_bits & 1
                or (
                    member.file_size > 100_000
                    and member.file_size > member.compress_size * 100
                )
            ):
                raise ExtractionError("invalid")
            names.add(name)

        def safe_xml(name: str):
            value = archive.read(name).decode("utf-8-sig", errors="strict")
            if (
                "\x00" in value
                or "<!DOCTYPE" in value.upper()
                or "<!ENTITY" in value.upper()
            ):
                raise ExtractionError("invalid")
            return ElementTree.fromstring(value)

        types = safe_xml("[Content_Types].xml")
        ct = "{http://schemas.openxmlformats.org/package/2006/content-types}"
        if not any(
            node.tag == ct + "Override"
            and node.get("PartName") == "/word/document.xml"
            and node.get("ContentType")
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
            for node in types
        ):
            raise ExtractionError("invalid")
        relationships = safe_xml("_rels/.rels")
        rel = "{http://schemas.openxmlformats.org/package/2006/relationships}"
        if not any(
            node.tag == rel + "Relationship"
            and node.get("Type")
            == "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
            and node.get("Target") in ("word/document.xml", "/word/document.xml")
            and node.get("TargetMode") != "External"
            for node in relationships
        ):
            raise ExtractionError("invalid")
        if any(
            name.startswith(
                (
                    "word/header",
                    "word/footer",
                    "word/footnotes",
                    "word/endnotes",
                    "word/comments",
                )
            )
            and name.endswith(".xml")
            for name in names
        ):
            raise ExtractionError("invalid")
        root = safe_xml("word/document.xml")
        if root.tag != W + "document" or root.find(W + "body") is None:
            raise ExtractionError("invalid")
        if any(
            next(root.iter(W + tag), None) is not None
            for tag in (
                "delText",
                "altChunk",
                "drawing",
                "object",
                "sym",
                "instrText",
                "fldSimple",
                "pict",
                "txbxContent",
            )
        ):
            raise ExtractionError("invalid")
        math = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
        if (
            next(root.iter(math + "oMath"), None) is not None
            or next(root.iter(math + "oMathPara"), None) is not None
        ):
            raise ExtractionError("invalid")
        result = []
        for index, paragraph in enumerate(root.iter(W + "p")):
            value = "".join(
                (node.text or "")
                if node.tag == W + "t"
                else "\t"
                if node.tag == W + "tab"
                else "\u2011"
                if node.tag == W + "noBreakHyphen"
                else "\n"
                for node in paragraph.iter()
                if node.tag
                in {W + "t", W + "tab", W + "br", W + "cr", W + "noBreakHyphen"}
            )
            if value:
                result.append((value, f"docx:paragraph:{index}:chars:0:{len(value)}"))
        return result


def main() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    if sys.platform != "darwin":
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    try:
        chunks = extract(sys.stdin.buffer.read(5 * 1024 * 1024 + 1), sys.argv[1])
        text = "\n".join(item[0] for item in chunks)
        if len(text.encode("utf-8")) > LIMIT:
            raise ExtractionError("limit")
        if not text.strip() or "\x00" in text:
            raise ExtractionError("invalid")
        print(json.dumps({"chunks": chunks, "text": text}, ensure_ascii=True))
    except ExtractionError as error:
        print(json.dumps({"error": str(error)}))
    except Exception:
        print(json.dumps({"error": "invalid"}))


if __name__ == "__main__":
    main()
