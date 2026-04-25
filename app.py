import json
import os
import re
import sqlite3
import time
import threading
import uuid
from html import escape
from contextlib import contextmanager
from functools import wraps
from flask import Flask, render_template, request, jsonify, Response, send_file
from werkzeug.middleware.proxy_fix import ProxyFix
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
from dotenv import load_dotenv
from names_linking import (
    DEFAULT_ENABLED_TAG_TYPES,
    DEFAULT_GEMINI_MODEL,
    diagnostic_log,
    extract_years_from_text,
    get_editable_dictionary_definitions,
    get_editable_dictionary_snapshot,
    link_entity,
    normalize_gemini_model_name,
    normalize_enabled_tag_types,
    normalize_whitespace,
    reset_current_gemini_model,
    set_current_gemini_model,
    start_diagnostic_session,
    stop_diagnostic_session,
    tag_entities_with_gemini,
    validate_editable_dictionary_entries,
    write_editable_dictionary_config,
)


load_dotenv()
app = Flask(__name__)
app.wsgi_app = ProxyFix(
    app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
APP_PASSWORD = os.environ.get('APP_PASSWORD')
APP_USER = os.environ.get('APP_USER')

client = genai.Client(api_key=GEMINI_API_KEY)
PROGRESS_SESSIONS = {}
PROGRESS_LOCK = threading.Lock()
PROGRESS_RETENTION_SECONDS = 30 * 60
PROGRESS_SESSION_DIR = os.environ.get(
    "TEXT2NER_PROGRESS_SESSION_DIR",
    "/tmp/text2ner_progress",
)
IDENTIFY_JOB_DB_PATH = os.environ.get(
    "TEXT2NER_IDENTIFY_JOB_DB_PATH",
    "/tmp/text2ner_identify_jobs.sqlite3",
)
IDENTIFY_JOB_RETENTION_SECONDS = int(os.environ.get(
    "TEXT2NER_IDENTIFY_JOB_RETENTION_SECONDS",
    str(48 * 60 * 60),
))
IDENTIFY_JOB_STALE_RUNNING_SECONDS = int(os.environ.get(
    "TEXT2NER_IDENTIFY_JOB_STALE_RUNNING_SECONDS",
    str(60 * 60),
))
IDENTIFY_WORKER_POLL_SECONDS = float(os.environ.get(
    "TEXT2NER_IDENTIFY_WORKER_POLL_SECONDS",
    "1.0",
))
IDENTIFY_WORKER_LOCK = threading.Lock()
IDENTIFY_WORKER_STARTED = False

# szablon nagłówka dokumentu TEI
TEI_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt> <title>Dokument z automatyczną identyfikacją encji</title> </titleStmt>
      <publicationStmt> <p>PHC IHPAN</p> </publicationStmt>
      <sourceDesc> <p>Dokument źródłowy</p> </sourceDesc>
    </fileDesc>
  </teiHeader>
  <text>
  <body>
"""

TEI_FOOTER = """
    </body>
  </text>
</TEI>"""


# -------------------------------- FUNCTIONS ----------------------------------
def compress_inline_tags(xml_text):
    """Usuwa zbędne odstępy wewnątrz krótkich tagów inline TEI."""
    return re.sub(
        r'<(persName|placeName|orgName|date|roleName)(.*?)>\s*(.*?)\s*</\1>',
        r'<\1\2>\3</\1>',
        xml_text,
        flags=re.DOTALL,
    )


def serialize_inner_xml(soup):
    """Serializuje roboczy XML dokumentu do postaci gotowej do osadzenia w TEI."""
    inner_xml = soup.prettify(formatter="minimal")
    inner_xml = compress_inline_tags(inner_xml)
    inner_xml = re.sub(r'<\?xml.*?\?>', '', inner_xml).strip()
    return inner_xml


def build_full_tei_xml(inner_xml):
    """Składa pełny dokument TEI z wewnętrznej treści body."""
    return f"{TEI_HEADER}\n{inner_xml}\n{TEI_FOOTER}"


PDF_PREVIEW_STYLES = """
@page {
  size: A4;
  margin: 18mm 16mm;
}
body {
  margin: 0;
  color: #111827;
  background: #ffffff;
  font-family: DejaVu Serif, Georgia, Cambria, "Times New Roman", serif;
  font-size: 12pt;
  line-height: 1.6;
}
header {
  border-bottom: 1px solid #d1d5db;
  margin-bottom: 16px;
  padding-bottom: 8px;
}
h1 {
  font-family: DejaVu Sans, Arial, sans-serif;
  font-size: 15pt;
  margin: 0;
}
p {
  margin: 0 0 10px;
}
.tei-tag {
  border-bottom-width: 1.5px;
  border-bottom-style: solid;
  padding: 0 2px;
}
.entity-label {
  border-radius: 2px;
  color: #374151;
  font-family: DejaVu Sans, Arial, sans-serif;
  font-size: 6.5pt;
  font-weight: 700;
  margin-left: 2px;
  padding: 0 2px;
  vertical-align: 18%;
}
.entity-pers {
  background-color: #e0e7ff;
  border-bottom-color: #4338ca;
}
.entity-place {
  background-color: #ffedd5;
  border-bottom-color: #ea580c;
}
.entity-date {
  background-color: #dcfce7;
  border-bottom-color: #16a34a;
}
.entity-role {
  background-color: #fce7f3;
  border-bottom-color: #db2777;
}
.entity-org {
  background-color: #ede9fe;
  border-bottom-color: #7c3aed;
}
.pdf-legend {
  border-bottom: 1px solid #e5e7eb;
  margin-bottom: 14px;
  padding-bottom: 10px;
}
.legend-title,
.entity-section h2 {
  font-family: DejaVu Sans, Arial, sans-serif;
  font-size: 11pt;
  margin: 0 0 7px;
}
.legend-items {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}
.legend-item {
  display: inline-block;
  font-family: DejaVu Sans, Arial, sans-serif;
  font-size: 8pt;
  padding: 1px 5px;
}
.entity-sections {
  border-top: 1px solid #d1d5db;
  margin-top: 18px;
  padding-top: 14px;
}
.entity-section {
  break-inside: avoid;
  margin-top: 12px;
}
.entity-list {
  margin: 0;
  padding-left: 18px;
}
.entity-list li {
  margin-bottom: 5px;
}
.entity-meta {
  color: #4b5563;
  font-family: DejaVu Sans, Arial, sans-serif;
  font-size: 8pt;
}
.entity-url {
  color: #1d4ed8;
  font-family: DejaVu Sans, Arial, sans-serif;
  font-size: 8pt;
  text-decoration: underline;
}
"""


PDF_TAG_CLASSES = {
    "persName": "entity-pers",
    "placeName": "entity-place",
    "date": "entity-date",
    "roleName": "entity-role",
    "orgName": "entity-org",
}

PDF_TAG_LABELS = {
    "persName": "osoba",
    "placeName": "miejsce",
    "date": "data",
    "roleName": "funkcja",
    "orgName": "instytucja",
}


def get_diagnostic_log_dir():
    """Zwraca bezpieczną, absolutną ścieżkę do katalogu z logami diagnostycznymi."""
    return os.path.abspath(os.path.join(app.root_path, "log"))


def resolve_safe_diagnostic_log_path(log_path):
    """Waliduje ścieżkę logu i dopuszcza wyłącznie pliki z katalogu `log/` aplikacji."""
    normalized_input = str(log_path or "").strip()
    if not normalized_input:
        raise ValueError("Brak ścieżki pliku logu.")

    log_dir = get_diagnostic_log_dir()
    candidate_path = normalized_input
    if not os.path.isabs(candidate_path):
        normalized_relative = os.path.normpath(candidate_path).lstrip(os.sep)
        log_dir_name = os.path.basename(log_dir)
        if normalized_relative == log_dir_name:
            raise ValueError("Nieprawidłowa ścieżka pliku logu.")
        log_prefix = f"{log_dir_name}{os.sep}"
        if normalized_relative.startswith(log_prefix):
            normalized_relative = normalized_relative[len(log_prefix):]
        candidate_path = os.path.join(log_dir, normalized_relative)

    resolved_path = os.path.abspath(candidate_path)
    if not (resolved_path == log_dir or resolved_path.startswith(log_dir + os.sep)):
        raise ValueError("Nieprawidłowa ścieżka pliku logu.")
    if not os.path.isfile(resolved_path):
        raise FileNotFoundError("Nie znaleziono wskazanego pliku logu.")
    return resolved_path


def extract_entity_log_snippet(log_path, entity_surface, entity_type):
    """Wyciąga z pliku logu ciągły fragment dotyczący wskazanej encji."""
    resolved_path = resolve_safe_diagnostic_log_path(log_path)
    with open(resolved_path, "r", encoding="utf-8") as log_file:
        lines = [line.rstrip("\n") for line in log_file]

    entity_surface = normalize_whitespace(entity_surface)
    entity_type = normalize_whitespace(entity_type)
    if not entity_surface or entity_type not in {"persName", "placeName"}:
        raise ValueError("Nieprawidłowe dane encji do podglądu logu.")

    block_start_pattern = re.compile(
        r"^\[TEXT2NER-DIAG\] (Analiza encji|Fallback analizy encji|Użyto cache dla encji) '(.+)' \((persName|placeName)\)"
    )
    generic_entity_pattern = re.compile(
        r"^\[TEXT2NER-DIAG\] .*'(.+)' \((persName|placeName)\)"
    )
    validation_pattern = re.compile(
        r"^\[TEXT2NER-DIAG\] Walidacja (persName|placeName) '(.+)':"
    )
    entity_reference = f"'{entity_surface}' ({entity_type})"
    target_entity_key = (entity_surface, entity_type)

    def extract_entity_key(line):
        """Zwraca (surface, type) dla linii jawnie przypisanej do encji albo `None`."""
        generic_match = generic_entity_pattern.match(line)
        if generic_match:
            return (
                normalize_whitespace(generic_match.group(1)),
                normalize_whitespace(generic_match.group(2)),
            )

        validation_match = validation_pattern.match(line)
        if validation_match:
            return (
                normalize_whitespace(validation_match.group(2)),
                normalize_whitespace(validation_match.group(1)),
            )
        return None

    start_index = None
    for index, line in enumerate(lines):
        if entity_reference in line and block_start_pattern.match(line):
            start_index = index
            break

    if start_index is None:
        matching_lines = [line for line in lines if entity_reference in line]
        if matching_lines:
            return "\n".join(matching_lines)
        raise ValueError("Nie znaleziono fragmentu logu dla wskazanej encji.")

    end_index = len(lines)
    for index in range(start_index + 1, len(lines)):
        entity_key = extract_entity_key(lines[index])
        if entity_key and entity_key != target_entity_key:
            end_index = index
            break

    return "\n".join(lines[start_index:end_index]).strip()


def extract_inner_xml_from_payload(xml_payload):
    """Wyciąga zawartość sekcji body z pełnego TEI albo zwraca podany XML roboczy."""
    xml_payload = str(xml_payload or "").strip()
    if not xml_payload:
        raise ValueError("Brak XML do identyfikacji.")

    if "<TEI" not in xml_payload:
        return xml_payload

    body_match = re.search(r"<body>\s*(.*?)\s*</body>", xml_payload, flags=re.DOTALL)
    if not body_match:
        raise ValueError("Nie udało się odnaleźć sekcji <body> w przesłanym TEI-XML.")
    return body_match.group(1).strip()


def render_preview_pdf_node(node):
    """Renderuje fragment roboczego XML-a TEI jako bezpieczny HTML do PDF."""
    if getattr(node, "name", None) is None:
        return escape(str(node))

    node_name = node.name
    children_html = "".join(render_preview_pdf_node(child) for child in node.children)
    if node_name == "p":
        return f"<p>{children_html}</p>"
    if node_name in {"div", "body"}:
        return children_html
    if node_name in PDF_TAG_CLASSES:
        tag_class = PDF_TAG_CLASSES[node_name]
        label = PDF_TAG_LABELS.get(node_name, node_name)
        return (
            f'<span class="tei-tag {tag_class}">{children_html}'
            f'<span class="entity-label {tag_class}">{escape(label)}</span></span>'
        )
    return children_html


def render_pdf_legend():
    """Buduje legendę kolorów użytych w PDF."""
    items = []
    for tag_name, tag_class in PDF_TAG_CLASSES.items():
        label = PDF_TAG_LABELS.get(tag_name, tag_name)
        items.append(
            f'<span class="legend-item tei-tag {tag_class}">{escape(label)}</span>'
        )
    return (
        '<section class="pdf-legend">'
        '<h2 class="legend-title">Legenda oznaczeń</h2>'
        f'<div class="legend-items">{"".join(items)}</div>'
        '</section>'
    )


def normalize_pdf_entity_items(items):
    """Przygotowuje listę encji z payloadu frontendu do bezpiecznego renderowania."""
    if not isinstance(items, list):
        return []
    normalized_items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized_items.append({
            "name": normalize_whitespace(item.get("name", "")),
            "surface": normalize_whitespace(item.get("surface", "")),
            "type": normalize_whitespace(item.get("type", "")),
            "url": normalize_whitespace(item.get("url", "")),
            "reason": normalize_whitespace(item.get("reason", "")),
        })
    return normalized_items


def render_pdf_entity_list(title, items, empty_text):
    """Renderuje jedną sekcję listy encji do PDF."""
    if not items:
        return (
            '<section class="entity-section">'
            f'<h2>{escape(title)}</h2>'
            f'<p class="entity-meta">{escape(empty_text)}</p>'
            '</section>'
        )

    rows = []
    for item in items:
        display_name = item["name"] or item["surface"] or "bez nazwy"
        surface = f' <span class="entity-meta">({escape(item["surface"])})</span>' if item["surface"] and item["surface"] != item["name"] else ""
        type_text = f' <span class="entity-meta">[{escape(item["type"])}]</span>' if item["type"] else ""
        url_text = f'<div class="entity-url">{escape(item["url"])}</div>' if item["url"] else ""
        reason_text = f'<div class="entity-meta">Powód: {escape(item["reason"])}</div>' if item["reason"] else ""
        rows.append(
            f'<li><strong>{escape(display_name)}</strong>{surface}{type_text}{url_text}{reason_text}</li>'
        )
    return (
        '<section class="entity-section">'
        f'<h2>{escape(title)}</h2>'
        f'<ol class="entity-list">{"".join(rows)}</ol>'
        '</section>'
    )


def render_pdf_entity_sections(entities=None, unresolved_entities=None, identification_performed=False):
    """Buduje sekcje encji dołączane do PDF po wykonanej identyfikacji."""
    if not identification_performed:
        return ""
    resolved_items = normalize_pdf_entity_items(entities)
    unresolved_items = normalize_pdf_entity_items(unresolved_entities)
    return (
        '<section class="entity-sections">'
        f'{render_pdf_entity_list("Zidentyfikowane encje", resolved_items, "Brak zidentyfikowanych encji.")}'
        f'{render_pdf_entity_list("Niezidentyfikowane encje", unresolved_items, "Brak niezidentyfikowanych encji.")}'
        '</section>'
    )


def build_preview_pdf_html(xml_payload, entities=None, unresolved_entities=None, identification_performed=False):
    """Buduje kompletny dokument HTML z kolorowym podglądem tekstu do eksportu PDF."""
    inner_xml = extract_inner_xml_from_payload(xml_payload)
    soup = BeautifulSoup(inner_xml, "xml")
    body_node = soup.find("body") or soup.find("div") or soup
    preview_html = "".join(render_preview_pdf_node(child) for child in body_node.children)
    if not normalize_whitespace(BeautifulSoup(preview_html, "html.parser").get_text(" ")):
        raise ValueError("Brak treści podglądu do eksportu PDF.")

    return f"""<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="UTF-8">
  <title>TEXT2NER - analiza tekstu</title>
  <style>{PDF_PREVIEW_STYLES}</style>
</head>
<body>
  <header>
    <h1>TEXT2NER - analiza tekstu</h1>
  </header>
  {render_pdf_legend()}
  <main>{preview_html}</main>
  {render_pdf_entity_sections(entities, unresolved_entities, identification_performed)}
</body>
</html>"""


def render_preview_pdf_bytes(xml_payload, entities=None, unresolved_entities=None, identification_performed=False):
    """Generuje PDF z kolorowego podglądu tekstu."""
    try:
        from weasyprint import HTML
    except Exception as exc:
        raise RuntimeError(
            "Eksport PDF wymaga biblioteki WeasyPrint. "
            "Zainstaluj zależności z requirements.txt oraz biblioteki systemowe WeasyPrint."
        ) from exc

    html = build_preview_pdf_html(
        xml_payload,
        entities=entities,
        unresolved_entities=unresolved_entities,
        identification_performed=identification_performed,
    )
    return HTML(string=html, base_url=app.root_path).write_pdf()


def purge_expired_progress_sessions():
    """Czyści stare wpisy postępu, żeby pamięć i katalog stanu nie rosły bez końca."""
    cutoff = time.time() - PROGRESS_RETENTION_SECONDS
    with PROGRESS_LOCK:
        expired_ids = [
            progress_id for progress_id, data in PROGRESS_SESSIONS.items()
            if data.get("updated_at", 0) < cutoff
        ]
        for progress_id in expired_ids:
            PROGRESS_SESSIONS.pop(progress_id, None)
    try:
        os.makedirs(PROGRESS_SESSION_DIR, exist_ok=True)
        for filename in os.listdir(PROGRESS_SESSION_DIR):
            if not filename.endswith(".json"):
                continue
            progress_path = os.path.join(PROGRESS_SESSION_DIR, filename)
            if os.path.getmtime(progress_path) < cutoff:
                os.remove(progress_path)
    except OSError as exc:
        diagnostic_log(f"Nie udało się wyczyścić plików postępu: {exc}")


def normalize_progress_id(progress_id):
    """Dopuszcza wyłącznie bezpieczne identyfikatory postępu generowane przez frontend."""
    progress_id = normalize_whitespace(progress_id)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", progress_id):
        return ""
    return progress_id


def get_progress_session_path(progress_id):
    """Zwraca ścieżkę pliku stanu dla danego identyfikatora postępu."""
    safe_progress_id = normalize_progress_id(progress_id)
    if not safe_progress_id:
        return ""
    return os.path.join(PROGRESS_SESSION_DIR, f"{safe_progress_id}.json")


def write_progress_session_file(progress_id, progress_data):
    """Zapisuje stan postępu tak, aby był widoczny dla wielu workerów WSGI."""
    progress_path = get_progress_session_path(progress_id)
    if not progress_path:
        return
    try:
        os.makedirs(PROGRESS_SESSION_DIR, exist_ok=True)
        tmp_path = f"{progress_path}.{os.getpid()}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as progress_file:
            json.dump(progress_data, progress_file, ensure_ascii=False)
        os.replace(tmp_path, progress_path)
    except OSError as exc:
        diagnostic_log(f"Nie udało się zapisać pliku postępu: {exc}")


def read_progress_session_file(progress_id):
    """Czyta współdzielony stan postępu zapisany przez dowolny worker."""
    progress_path = get_progress_session_path(progress_id)
    if not progress_path or not os.path.isfile(progress_path):
        return {}
    try:
        with open(progress_path, "r", encoding="utf-8") as progress_file:
            progress_data = json.load(progress_file)
        if isinstance(progress_data, dict):
            return progress_data
    except (OSError, json.JSONDecodeError) as exc:
        diagnostic_log(f"Nie udało się odczytać pliku postępu: {exc}")
    return {}


def update_progress(progress_id, **updates):
    """Aktualizuje stan długiej operacji widoczny dla frontendu."""
    progress_id = normalize_progress_id(progress_id)
    if not progress_id:
        return
    now = time.time()
    with PROGRESS_LOCK:
        current = PROGRESS_SESSIONS.get(progress_id, {})
        current.update(updates)
        current["updated_at"] = now
        PROGRESS_SESSIONS[progress_id] = current
        progress_snapshot = dict(current)
    write_progress_session_file(progress_id, progress_snapshot)
    update_identify_job(progress_id, **updates)


def get_progress(progress_id):
    """Zwraca kopię stanu postępu dla wskazanego identyfikatora."""
    progress_id = normalize_progress_id(progress_id)
    if not progress_id:
        return {}
    purge_expired_progress_sessions()
    with PROGRESS_LOCK:
        progress = dict(PROGRESS_SESSIONS.get(progress_id, {}))
    if progress:
        return progress
    return read_progress_session_file(progress_id)


def get_identify_job_connection():
    """Otwiera połączenie z lokalną bazą SQLite przechowującą zadania identyfikacji."""
    db_dir = os.path.dirname(os.path.abspath(IDENTIFY_JOB_DB_PATH))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    connection = sqlite3.connect(IDENTIFY_JOB_DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


@contextmanager
def identify_job_connection():
    """Udostępnia połączenie SQLite i zawsze je zamyka po zakończeniu operacji."""
    connection = get_identify_job_connection()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_identify_job_store():
    """Tworzy tabelę jobów, jeśli nie istnieje."""
    with identify_job_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS identify_jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                input_xml TEXT NOT NULL,
                result_json TEXT,
                error TEXT,
                diagnostic_log_file TEXT,
                model_name TEXT,
                current INTEGER DEFAULT 0,
                total INTEGER DEFAULT 0,
                message TEXT DEFAULT '',
                entity TEXT DEFAULT '',
                entity_type TEXT DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                finished_at REAL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_identify_jobs_status_created "
            "ON identify_jobs(status, created_at)"
        )


def cleanup_identify_jobs():
    """Usuwa stare zakończone joby i oznacza bardzo stare uruchomione joby jako błędne."""
    now = time.time()
    stale_before = now - IDENTIFY_JOB_STALE_RUNNING_SECONDS
    expired_before = now - IDENTIFY_JOB_RETENTION_SECONDS
    try:
        init_identify_job_store()
        with identify_job_connection() as connection:
            connection.execute(
                """
                UPDATE identify_jobs
                SET status = 'error',
                    error = 'Zadanie zostało przerwane przed zakończeniem.',
                    message = 'Zadanie zostało przerwane przed zakończeniem.',
                    finished_at = ?,
                    updated_at = ?
                WHERE status = 'running' AND updated_at < ?
                """,
                (now, now, stale_before),
            )
            connection.execute(
                """
                DELETE FROM identify_jobs
                WHERE status IN ('done', 'error', 'expired')
                  AND COALESCE(finished_at, updated_at, created_at) < ?
                """,
                (expired_before,),
            )
    except sqlite3.Error as exc:
        diagnostic_log(f"Nie udało się wyczyścić jobów identyfikacji: {exc}")


def row_to_identify_job(row):
    """Zamienia wiersz SQLite na słownik bez dużych pól roboczych."""
    if not row:
        return {}
    return {
        "id": row["id"],
        "status": row["status"],
        "current": row["current"] or 0,
        "total": row["total"] or 0,
        "message": row["message"] or "",
        "entity": row["entity"] or "",
        "entity_type": row["entity_type"] or "",
        "error": row["error"] or "",
        "model_name": row["model_name"] or DEFAULT_GEMINI_MODEL,
        "diagnostic_log_file": row["diagnostic_log_file"] or "",
        "result_available": bool(row["result_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "finished_at": row["finished_at"],
    }


def create_identify_job(xml_payload, model_name):
    """Zapisuje nowe zadanie identyfikacji i zwraca jego identyfikator."""
    cleanup_identify_jobs()
    job_id = str(uuid.uuid4())
    now = time.time()
    init_identify_job_store()
    with identify_job_connection() as connection:
        connection.execute(
            """
            INSERT INTO identify_jobs (
                id, status, input_xml, model_name, current, total, message,
                created_at, updated_at
            )
            VALUES (?, 'queued', ?, ?, 0, 0, ?, ?, ?)
            """,
            (
                job_id,
                xml_payload,
                model_name,
                "Zadanie identyfikacji czeka na rozpoczęcie.",
                now,
                now,
            ),
        )
    ensure_identify_worker_started()
    return job_id


def get_identify_job(job_id, include_input=False):
    """Pobiera metadane zadania identyfikacji."""
    job_id = normalize_progress_id(job_id)
    if not job_id:
        return {}
    init_identify_job_store()
    with identify_job_connection() as connection:
        row = connection.execute(
            "SELECT * FROM identify_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    if not row:
        return {}
    job = row_to_identify_job(row)
    if include_input:
        job["input_xml"] = row["input_xml"]
    return job


def get_identify_job_result(job_id):
    """Zwraca wynik zakończonego zadania identyfikacji."""
    job_id = normalize_progress_id(job_id)
    if not job_id:
        return {}
    init_identify_job_store()
    with identify_job_connection() as connection:
        row = connection.execute(
            "SELECT status, result_json, error FROM identify_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    if not row:
        return {}
    if row["status"] != "done" or not row["result_json"]:
        return {
            "status": row["status"],
            "error": row["error"] or "",
        }
    result = json.loads(row["result_json"])
    result["status"] = "done"
    return result


def update_identify_job(job_id, **updates):
    """Aktualizuje wybrane pola joba identyfikacji."""
    job_id = normalize_progress_id(job_id)
    if not job_id:
        return
    allowed_fields = {
        "status",
        "current",
        "total",
        "message",
        "entity",
        "entity_type",
        "error",
        "diagnostic_log_file",
        "model_name",
        "result_json",
        "finished_at",
    }
    normalized_updates = {
        field: value for field, value in updates.items()
        if field in allowed_fields
    }
    if not normalized_updates:
        return
    normalized_updates["updated_at"] = time.time()
    assignments = ", ".join(f"{field} = ?" for field in normalized_updates)
    values = list(normalized_updates.values()) + [job_id]
    try:
        init_identify_job_store()
        with identify_job_connection() as connection:
            connection.execute(
                f"UPDATE identify_jobs SET {assignments} WHERE id = ?",
                values,
            )
    except sqlite3.Error as exc:
        diagnostic_log(f"Nie udało się zaktualizować joba identyfikacji: {exc}")


def claim_next_identify_job():
    """Atomowo przejmuje najstarsze zadanie oczekujące."""
    init_identify_job_store()
    now = time.time()
    connection = get_identify_job_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT * FROM identify_jobs
            WHERE status = 'queued'
            ORDER BY created_at
            LIMIT 1
            """
        ).fetchone()
        if not row:
            connection.commit()
            return {}
        connection.execute(
            """
            UPDATE identify_jobs
            SET status = 'running',
                message = ?,
                updated_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            ("Rozpoczynam identyfikację encji.", now, row["id"]),
        )
        connection.commit()
        job = row_to_identify_job(row)
        job["input_xml"] = row["input_xml"]
        return job
    except sqlite3.Error as exc:
        connection.rollback()
        diagnostic_log(f"Nie udało się przejąć joba identyfikacji: {exc}")
        return {}
    finally:
        connection.close()


def run_identify_job(job):
    """Wykonuje identyfikację encji dla zadania pobranego z kolejki SQLite."""
    job_id = job["id"]
    diagnostic_log_path = None
    model_token = None
    try:
        diagnostic_log_path = start_diagnostic_session(log_dir="log")
        selected_model = normalize_gemini_model_name(
            job.get("model_name") or DEFAULT_GEMINI_MODEL
        )
        model_token = set_current_gemini_model(selected_model)
        update_identify_job(
            job_id,
            status="running",
            model_name=selected_model,
            diagnostic_log_file=diagnostic_log_path,
            message="Rozpoczynam identyfikację encji.",
        )
        update_progress(
            job_id,
            status="running",
            current=0,
            total=0,
            message="Rozpoczynam identyfikację encji.",
        )
        diagnostic_log(f"Uruchomiono job identyfikacji {job_id}.")
        diagnostic_log(f"Model Gemini dla joba identyfikacji: {selected_model}")

        full_tei_xml, entities, unresolved_entities = identify_entities_in_tei(
            job.get("input_xml", ""),
            progress_id=job_id,
        )
        result = {
            "xml": full_tei_xml,
            "entities": entities,
            "unresolved_entities": unresolved_entities,
            "identification_performed": True,
            "model_name": selected_model,
            "diagnostic_log_file": diagnostic_log_path,
        }
        now = time.time()
        update_identify_job(
            job_id,
            status="done",
            message="Identyfikacja zakończona.",
            result_json=json.dumps(result, ensure_ascii=False),
            diagnostic_log_file=diagnostic_log_path,
            finished_at=now,
        )
        update_progress(
            job_id,
            status="done",
            message="Identyfikacja zakończona.",
        )
    except Exception as exc:
        diagnostic_log(f"Błąd krytyczny w jobie identyfikacji {job_id}: {exc}")
        update_identify_job(
            job_id,
            status="error",
            error=str(exc),
            message=str(exc),
            diagnostic_log_file=diagnostic_log_path or "",
            finished_at=time.time(),
        )
        update_progress(
            job_id,
            status="error",
            message=str(exc),
        )
    finally:
        reset_current_gemini_model(model_token)
        stop_diagnostic_session()


def identify_worker_loop():
    """Prosta kolejka robocza dla identyfikacji encji oparta o SQLite."""
    cleanup_identify_jobs()
    while True:
        job = claim_next_identify_job()
        if job:
            run_identify_job(job)
            continue
        time.sleep(IDENTIFY_WORKER_POLL_SECONDS)


def ensure_identify_worker_started():
    """Uruchamia lokalny wątek workerowy w bieżącym procesie aplikacji."""
    global IDENTIFY_WORKER_STARTED
    with IDENTIFY_WORKER_LOCK:
        if IDENTIFY_WORKER_STARTED:
            return
        worker = threading.Thread(
            target=identify_worker_loop,
            name="text2ner-identify-worker",
            daemon=True,
        )
        worker.start()
        IDENTIFY_WORKER_STARTED = True


def check_auth(username, password):
    """Sprawdza, czy nazwa użytkownika i hasło są poprawne."""
    return username == APP_USER and password == APP_PASSWORD

def authenticate():
    """Wysyła odpowiedź 401, która wyzwala okno logowania w przeglądarce."""
    return Response(
        'Proszę się zalogować.\n'
        'Dostęp tylko dla pracowników IH PAN.', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'}
    )

def requires_auth(f):
    """Opakowuje widok Flaska prostym uwierzytelnianiem HTTP Basic."""
    @wraps(f)
    def decorated(*args, **kwargs):
        """Przepuszcza żądanie tylko dla poprawnych danych logowania."""
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated


def get_entity_context(tag):
    """Zwraca możliwie pełny kontekst encji z najbliższego bloku tekstu."""
    parent_block = tag.find_parent(['p', 'ab', 'note', 'head'])
    if parent_block:
        raw_text = parent_block.get_text()
    else:
        raw_text = tag.parent.get_text() if tag.parent else tag.get_text()
    return re.sub(r'\s+', ' ', raw_text).strip()


def build_entity_resolution_cache_key(tag_type, name, context):
    """Buduje kontekstowy klucz cache dla wyniku identyfikacji pojedynczej encji."""
    return (
        normalize_whitespace(tag_type),
        normalize_whitespace(name).casefold(),
        normalize_whitespace(context).casefold(),
    )


def identify_entities_in_soup(soup, document_years, progress_id=None):
    """Wykonuje linking dla `persName` i `placeName` na już otagowanym XML-u."""
    entities = []
    unresolved_entities = []
    seen_entities = set()
    seen_unresolved_entities = set()
    resolved_entity_cache = {}

    tags = soup.find_all(['persName', 'placeName'])
    total_tags = len(tags)
    update_progress(
        progress_id,
        status="running",
        current=0,
        total=total_tags,
        message=f"Znaleziono {total_tags} encji do identyfikacji.",
    )
    for index, tag in enumerate(tags, start=1):
        name = tag.get_text()
        tag_type = tag.name
        update_progress(
            progress_id,
            status="running",
            current=index,
            total=total_tags,
            entity=name,
            entity_type=tag_type,
            message=f"Identyfikuję {index}/{total_tags}: {name} ({tag_type})",
        )
        if tag.has_attr('key'):
            del tag['key']
        if tag.has_attr('ref'):
            del tag['ref']

        context = get_entity_context(tag)
        cache_key = build_entity_resolution_cache_key(tag_type, name, context)
        if cache_key in resolved_entity_cache:
            link_result = resolved_entity_cache[cache_key]
            diagnostic_log(
                f"Użyto cache kontekstowego dla encji '{name}' ({tag_type}) -> "
                f"{link_result['decision'].get('selected_url')}"
            )
        else:
            link_result = link_entity(
                name,
                context,
                tag_type,
                document_years=document_years,
            )
            if link_result["decision"].get("status") == "selected":
                resolved_entity_cache[cache_key] = link_result

        entity_analysis = link_result["entity_analysis"]
        norm_name = link_result["normalized_name"]
        decision = link_result["decision"]
        tag['key'] = norm_name

        selected_url = decision.get("selected_url")

        if decision.get("status") == "selected" and selected_url:
            tag['ref'] = selected_url
            entity_key = (norm_name, selected_url)
            if entity_key not in seen_entities:
                selected_candidate = decision.get("selected_candidate") or {}
                entities.append({
                    "name": norm_name,
                    "surface": name,
                    "type": tag_type,
                    "url": selected_url,
                    "facts": {
                        "document": {
                            "surface": name,
                            "normalized_name": norm_name,
                            "context_clues": entity_analysis.get("context_clues", []),
                            "context_years": entity_analysis.get("context_years", []),
                            "posthumous_context": entity_analysis.get("posthumous_context", False),
                        },
                        "candidate": {
                            "label": selected_candidate.get("name", norm_name),
                            "url": selected_url,
                            "source": selected_candidate.get("source", ""),
                            "id": selected_candidate.get("id", ""),
                            "description": selected_candidate.get("description", ""),
                            "key_facts": selected_candidate.get("key_facts", []),
                        },
                    },
                })
                seen_entities.add(entity_key)
        else:
            unresolved_key = (tag_type, norm_name, normalize_whitespace(name))
            if unresolved_key not in seen_unresolved_entities:
                unresolved_entities.append({
                    "name": norm_name,
                    "surface": name,
                    "type": tag_type,
                    "reason": decision.get("reason", "not_selected"),
                    "candidate_suggestions": link_result.get("candidate_suggestions", []),
                    "facts": {
                        "document": {
                            "surface": name,
                            "normalized_name": norm_name,
                            "context_clues": entity_analysis.get("context_clues", []),
                            "context_years": entity_analysis.get("context_years", []),
                            "posthumous_context": entity_analysis.get("posthumous_context", False),
                        },
                    },
                })
                seen_unresolved_entities.add(unresolved_key)

        update_progress(
            progress_id,
            status="running",
            current=index,
            total=total_tags,
            entity=name,
            entity_type=tag_type,
            message=f"Zakończono {index}/{total_tags}: {name} ({tag_type})",
        )

    entities.sort(key=lambda x: x['name'])
    unresolved_entities.sort(key=lambda x: (x['type'], x['name'], x['surface']))
    update_progress(
        progress_id,
        status="finishing",
        current=total_tags,
        total=total_tags,
        message="Składam wynik TEI-XML i listy encji.",
    )
    return entities, unresolved_entities


def recognize_text_to_tei(raw_text, enabled_tag_types=None):
    """Rozpoznaje encje i zwraca pełny TEI-XML bez identyfikacji referencyjnej."""
    tagged_xml = tag_entities_with_gemini(raw_text, enabled_tag_types=enabled_tag_types)
    if tagged_xml is None:
        raise ValueError("Błąd tagowania tekstu przez model Gemini. Sprawdź połączenie lub klucz API.")

    soup = BeautifulSoup(tagged_xml, 'xml')
    inner_xml = serialize_inner_xml(soup)
    return build_full_tei_xml(inner_xml)


def identify_entities_in_tei(xml_payload, progress_id=None):
    """Identyfikuje encje w dostarczonym TEI-XML lub XML-u roboczym."""
    inner_xml = extract_inner_xml_from_payload(xml_payload)
    soup = BeautifulSoup(inner_xml, 'xml')
    document_years = extract_years_from_text(soup.get_text(" "))
    diagnostic_log(f"Wykryte lata dokumentu dla identyfikacji: {document_years}")

    entities, unresolved_entities = identify_entities_in_soup(soup, document_years, progress_id=progress_id)
    normalized_inner_xml = serialize_inner_xml(soup)
    full_tei_xml = build_full_tei_xml(normalized_inner_xml)
    return full_tei_xml, entities, unresolved_entities


# -------------------------------- ROUTES -------------------------------------
@app.route('/')
@requires_auth
def index():
    """Renderuje główny interfejs aplikacji."""
    return render_template('index.html')

@app.route('/recognize', methods=['POST'])
@requires_auth
def recognize():
    """Rozpoznaje encje i zwraca TEI-XML bez identyfikacji referencyjnej."""
    diagnostic_log_path = None
    model_token = None
    try:
      diagnostic_log_path = start_diagnostic_session(log_dir="log")
      raw_text = request.json.get('text', '')[:5000]
      enabled_tag_types = normalize_enabled_tag_types(
          request.json.get('tag_types', list(DEFAULT_ENABLED_TAG_TYPES))
      )
      selected_model = normalize_gemini_model_name(
          request.json.get('model_name', DEFAULT_GEMINI_MODEL)
      )
      model_token = set_current_gemini_model(selected_model)
      if not enabled_tag_types:
          raise ValueError("Wybierz co najmniej jeden typ tagu do rozpoznawania encji.")
      diagnostic_log(f"Uruchomiono /recognize dla tekstu o długości {len(raw_text)} znaków.")
      diagnostic_log(f"Włączone tagi rozpoznawania: {enabled_tag_types}")
      diagnostic_log(f"Model Gemini dla /recognize: {selected_model}")
      full_tei_xml = recognize_text_to_tei(raw_text, enabled_tag_types=enabled_tag_types)

      return jsonify({
          "xml": full_tei_xml,
          "entities": [],
          "unresolved_entities": [],
          "identification_performed": False,
          "model_name": selected_model,
          "recognized_tag_types": enabled_tag_types,
          "diagnostic_log_file": diagnostic_log_path
      })
    
    except Exception as e:
        diagnostic_log(f"Błąd krytyczny w /recognize: {e}")
        print(f"Błąd krytyczny w /recognize: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        reset_current_gemini_model(model_token)
        stop_diagnostic_session()


@app.route('/identify', methods=['POST'])
@requires_auth
def identify():
    """Identyfikuje `persName` i `placeName` w istniejącym TEI-XML."""
    diagnostic_log_path = None
    model_token = None
    try:
      diagnostic_log_path = start_diagnostic_session(log_dir="log")
      xml_payload = request.json.get('xml', '')
      progress_id = normalize_whitespace(request.json.get('progress_id', ''))
      update_progress(
          progress_id,
          status="running",
          current=0,
          total=0,
          message="Rozpoczynam identyfikację encji.",
      )
      selected_model = normalize_gemini_model_name(
          request.json.get('model_name', DEFAULT_GEMINI_MODEL)
      )
      model_token = set_current_gemini_model(selected_model)
      diagnostic_log(f"Uruchomiono /identify dla XML o długości {len(xml_payload)} znaków.")
      diagnostic_log(f"Model Gemini dla /identify: {selected_model}")

      full_tei_xml, entities, unresolved_entities = identify_entities_in_tei(
          xml_payload,
          progress_id=progress_id,
      )
      update_progress(
          progress_id,
          status="done",
          message="Identyfikacja zakończona.",
      )

      return jsonify({
          "xml": full_tei_xml,
          "entities": entities,
          "unresolved_entities": unresolved_entities,
          "identification_performed": True,
          "model_name": selected_model,
          "diagnostic_log_file": diagnostic_log_path
      })

    except Exception as e:
        diagnostic_log(f"Błąd krytyczny w /identify: {e}")
        print(f"Błąd krytyczny w /identify: {e}")
        update_progress(
            normalize_whitespace((request.json or {}).get('progress_id', '')),
            status="error",
            message=str(e),
        )
        return jsonify({"error": str(e)}), 500
    finally:
        reset_current_gemini_model(model_token)
        stop_diagnostic_session()


@app.route('/identify/jobs', methods=['POST'])
@requires_auth
def create_identify_job_route():
    """Uruchamia identyfikację w tle i od razu zwraca identyfikator zadania."""
    try:
        payload = request.get_json(silent=True) or {}
        xml_payload = payload.get('xml', '')
        if not normalize_whitespace(xml_payload):
            raise ValueError("Brak XML do identyfikacji.")
        selected_model = normalize_gemini_model_name(
            payload.get('model_name', DEFAULT_GEMINI_MODEL)
        )
        job_id = create_identify_job(xml_payload, selected_model)
        return jsonify({
            "job_id": job_id,
            "status": "queued",
            "message": "Zadanie identyfikacji zostało przyjęte.",
        }), 202
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route('/identify/jobs/<job_id>', methods=['GET'])
@requires_auth
def get_identify_job_route(job_id):
    """Zwraca bieżący stan zadania identyfikacji."""
    ensure_identify_worker_started()
    job = get_identify_job(job_id)
    if not job:
        return jsonify({"error": "Nie znaleziono zadania identyfikacji."}), 404
    return jsonify(job)


@app.route('/identify/jobs/<job_id>/result', methods=['GET'])
@requires_auth
def get_identify_job_result_route(job_id):
    """Zwraca wynik zakończonego zadania identyfikacji."""
    result = get_identify_job_result(job_id)
    if not result:
        return jsonify({"error": "Nie znaleziono zadania identyfikacji."}), 404
    if result.get("status") != "done":
        return jsonify({
            "error": result.get("error") or "Zadanie identyfikacji nie zostało jeszcze zakończone.",
            "status": result.get("status", "unknown"),
        }), 409
    return jsonify(result)


@app.route('/identify/progress/<progress_id>', methods=['GET'])
@requires_auth
def identify_progress(progress_id):
    """Zwraca bieżący postęp identyfikacji encji."""
    progress = get_progress(progress_id)
    if not progress:
        return jsonify({
            "status": "pending",
            "current": 0,
            "total": 0,
            "message": "Oczekuję na rozpoczęcie identyfikacji.",
        })
    return jsonify(progress)


@app.route('/process', methods=['POST'])
@requires_auth
def process():
    """Zachowuje zgodność wsteczną: rozpoznaje i od razu identyfikuje encje."""
    diagnostic_log_path = None
    model_token = None
    try:
      diagnostic_log_path = start_diagnostic_session(log_dir="log")
      raw_text = request.json.get('text', '')[:5000]
      enabled_tag_types = normalize_enabled_tag_types(
          request.json.get('tag_types', list(DEFAULT_ENABLED_TAG_TYPES))
      )
      selected_model = normalize_gemini_model_name(
          request.json.get('model_name', DEFAULT_GEMINI_MODEL)
      )
      model_token = set_current_gemini_model(selected_model)
      if not enabled_tag_types:
          raise ValueError("Wybierz co najmniej jeden typ tagu do rozpoznawania encji.")
      diagnostic_log(f"Uruchomiono /process dla tekstu o długości {len(raw_text)} znaków.")
      diagnostic_log(f"Włączone tagi rozpoznawania w /process: {enabled_tag_types}")
      diagnostic_log(f"Model Gemini dla /process: {selected_model}")

      full_tei_xml = recognize_text_to_tei(raw_text, enabled_tag_types=enabled_tag_types)
      full_tei_xml, entities, unresolved_entities = identify_entities_in_tei(full_tei_xml)

      return jsonify({
          "xml": full_tei_xml,
          "entities": entities,
          "unresolved_entities": unresolved_entities,
          "identification_performed": True,
          "model_name": selected_model,
          "diagnostic_log_file": diagnostic_log_path
      })

    except Exception as e:
        diagnostic_log(f"Błąd krytyczny w /process: {e}")
        print(f"Błąd krytyczny w /process: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        reset_current_gemini_model(model_token)
        stop_diagnostic_session()


@app.route('/diagnostic-log/download', methods=['GET'])
@requires_auth
def download_diagnostic_log():
    """Udostępnia do pobrania cały plik logu diagnostycznego dla bieżącej analizy."""
    try:
        log_path = resolve_safe_diagnostic_log_path(request.args.get('path', ''))
        return send_file(
            log_path,
            mimetype='text/plain; charset=utf-8',
            as_attachment=True,
            download_name=os.path.basename(log_path),
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route('/preview-pdf', methods=['POST'])
@requires_auth
def preview_pdf():
    """Generuje PDF z kolorowego podglądu aktualnego TEI-XML."""
    try:
        payload = request.get_json(silent=True) or {}
        pdf_bytes = render_preview_pdf_bytes(
            payload.get("xml", ""),
            entities=payload.get("entities", []),
            unresolved_entities=payload.get("unresolved_entities", []),
            identification_performed=bool(payload.get("identification_performed", False)),
        )
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={
                "Content-Disposition": 'attachment; filename="text2ner_preview.pdf"',
            },
        )
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route('/dictionary-configs', methods=['GET'])
@requires_auth
def get_dictionary_configs():
    """Zwraca słowniki konfiguracyjne dostępne do edycji w interfejsie WWW."""
    return jsonify({
        "definitions": get_editable_dictionary_definitions(),
        "dictionaries": get_editable_dictionary_snapshot(),
    })


@app.route('/dictionary-configs', methods=['POST'])
@requires_auth
def save_dictionary_configs():
    """Zapisuje słowniki konfiguracyjne edytowane z poziomu aplikacji."""
    try:
        payload = request.get_json(silent=True) or {}
        dictionaries = payload.get('dictionaries', {})
        if not isinstance(dictionaries, dict) or not dictionaries:
            raise ValueError("Brak danych słowników do zapisu.")

        normalized_payload = {}
        for definition in get_editable_dictionary_definitions():
            dictionary_key = definition["key"]
            if dictionary_key not in dictionaries:
                raise ValueError(f"Brak słownika '{dictionary_key}' w żądaniu.")
            normalized_payload[dictionary_key] = validate_editable_dictionary_entries(
                dictionaries[dictionary_key]
            )

        for dictionary_key, normalized_mapping in normalized_payload.items():
            write_editable_dictionary_config(dictionary_key, normalized_mapping)

        return jsonify({
            "message": "Słowniki zostały zapisane.",
            "definitions": get_editable_dictionary_definitions(),
            "dictionaries": get_editable_dictionary_snapshot(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route('/diagnostic-log/entity-snippet', methods=['POST'])
@requires_auth
def get_entity_diagnostic_log_snippet():
    """Zwraca fragment logu dotyczący wskazanej encji zidentyfikowanej lub niezidentyfikowanej."""
    try:
        payload = request.json or {}
        snippet = extract_entity_log_snippet(
            payload.get('log_path', ''),
            payload.get('entity_surface', ''),
            payload.get('entity_type', ''),
        )
        return jsonify({"snippet": snippet})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# -------------------------------- MAIN ---------------------------------------
if __name__ == '__main__':
    app.run(debug=True)
