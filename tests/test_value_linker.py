from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.agent.question_analyzer import QuestionAnalyzer
from askdata.retrieval.value_linker import ValueLinker


def build_db(tmp_path: Path) -> str:
    path = tmp_path / "demo.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE molecule(molecule_id TEXT, label TEXT);
        CREATE TABLE atom(atom_id TEXT, molecule_id TEXT, element TEXT);
        CREATE TABLE transactions_1k(Date TEXT, Price REAL, CustomerID INTEGER);
        CREATE TABLE schools(County TEXT, City TEXT);
        INSERT INTO molecule VALUES ('TR060', '-');
        INSERT INTO atom VALUES ('a1', 'TR060', 'c');
        INSERT INTO transactions_1k VALUES ('2012-08-25', 634.8, 6718);
        INSERT INTO schools VALUES ('Monterey', 'Salinas');
        """
    )
    conn.commit()
    conn.close()
    return str(path)


def test_value_linker_links_identifier_number_date_and_text(tmp_path):
    database_path = build_db(tmp_path)
    question = "For molecule TR060 in Monterey, who paid 634.8 in 2012/8/25?"
    schema = {
        "molecule": ["molecule_id", "label"],
        "atom": ["atom_id", "molecule_id", "element"],
        "transactions_1k": ["Date", "Price", "CustomerID"],
        "schools": ["County", "City"],
    }
    analysis = QuestionAnalyzer().Analyze(question, schema, "")
    retrieval = {
        "database_path": database_path,
        "schema": schema,
        "matched_tables": [{"table_name": name} for name in schema],
    }

    links = ValueLinker().Link(question, retrieval, analysis)
    pairs = {(link.value, link.table, link.column) for link in links}

    assert ("TR060", "molecule", "molecule_id") in pairs
    assert ("634.8", "transactions_1k", "Price") in pairs
    assert ("2012/8/25", "transactions_1k", "Date") in pairs
    assert ("Monterey", "schools", "County") in pairs
