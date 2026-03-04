"""storage.py のユニットテスト"""

import csv
import io

import pytest

from models import ScoringSession
import storage


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """DATA_DIR と OUTPUT_DIR を一時ディレクトリに切り替える"""
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "output"
    data_dir.mkdir()
    output_dir.mkdir()
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "OUTPUT_DIR", output_dir)
    return data_dir


class TestSaveAndLoad:
    def test_round_trip(self, sample_session, tmp_data_dir):
        storage.save_session(sample_session)
        loaded = storage.load_session(sample_session.session_id)

        assert loaded is not None
        assert loaded.session_id == sample_session.session_id
        assert len(loaded.students) == 2
        assert loaded.students[0].total_score == sample_session.students[0].total_score

    def test_load_nonexistent(self, tmp_data_dir):
        result = storage.load_session("nonexistent_id")
        assert result is None


class TestListSessions:
    def test_list_multiple(self, sample_session, tmp_data_dir):
        storage.save_session(sample_session)

        session2 = ScoringSession(session_id="second", rubric_title="Test 2")
        storage.save_session(session2)

        sessions = storage.list_sessions()
        assert len(sessions) == 2


class TestExportCsv:
    def test_csv_headers(self, sample_session):
        csv_str = storage.export_csv(sample_session)
        reader = csv.reader(io.StringIO(csv_str))
        headers = next(reader)
        assert "学生番号" in headers
        assert "合計点" in headers

    def test_csv_data_rows(self, sample_session):
        csv_str = storage.export_csv(sample_session)
        reader = csv.reader(io.StringIO(csv_str))
        next(reader)  # skip headers
        rows = list(reader)
        assert len(rows) == 2
        # 最初の学生のID
        assert rows[0][0] == "S001"
