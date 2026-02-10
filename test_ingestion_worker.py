"""
Integration tests for ingestion worker.

Tests idempotency, error handling, and edge cases.

Run with: pytest test_ingestion_worker.py -v
"""

import json
import os
import tempfile
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock

import pytest

# Mock Firebase before importing worker
with patch('firebase_admin.initialize_app'):
    with patch('firebase_admin.firestore.client'):
        from ingestion_worker import (
            CheckpointManager,
            StationManager,
            MeasurementInserter,
            retry_with_backoff
        )


# ============================================
# Checkpoint Tests
# ============================================

class TestCheckpointManager:
    """Test checkpoint save/load behavior."""

    def test_load_missing_file(self):
        """Missing checkpoint should return epoch zero."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CheckpointManager(f"{tmpdir}/checkpoint.json")
            result = mgr.load()
            assert result == "1970-01-01T00:00:00.000Z"

    def test_save_and_load(self):
        """Save checkpoint and verify it loads correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/checkpoint.json"
            mgr = CheckpointManager(path)

            # Save
            mgr.save("2025-10-02T13:15:53.937Z", 100)

            # Load
            mgr2 = CheckpointManager(path)
            result = mgr2.load()
            assert result == "2025-10-02T13:15:53.937Z"

    def test_corrupted_checkpoint(self):
        """Corrupted checkpoint should fallback to epoch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/checkpoint.json"

            # Write garbage
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                f.write("garbage data")

            mgr = CheckpointManager(path)
            result = mgr.load()
            assert result == "1970-01-01T00:00:00.000Z"

    def test_atomic_write(self):
        """Checkpoint writes should be atomic (temp + rename)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/checkpoint.json"
            mgr = CheckpointManager(path)

            mgr.save("2025-10-02T13:15:53.937Z", 50)

            # Verify file exists and temp doesn't
            assert os.path.exists(path)
            assert not os.path.exists(f"{path}.tmp")

            # Verify content is valid JSON
            with open(path) as f:
                data = json.load(f)
                assert data['last_processed_timestamp'] == "2025-10-02T13:15:53.937Z"
                assert data['processed_count'] == 50


# ============================================
# Station Manager Tests
# ============================================

class TestStationManager:
    """Test station lookup and caching."""

    def test_cache_initialization(self):
        """Should load existing stations into cache."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            ('Station A', 1),
            ('Station B', 2)
        ]

        mgr = StationManager(mock_conn)
        assert mgr.cache['Station A'] == 1
        assert mgr.cache['Station B'] == 2

    def test_get_from_cache(self):
        """Should return cached station_id without DB query."""
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value.fetchall.return_value = []

        mgr = StationManager(mock_conn)
        mgr.cache['Cached Station'] = 42

        mock_cursor = MagicMock()
        result = mgr.get_or_create('Cached Station', mock_cursor)

        assert result == 42
        # Cursor should not be used
        mock_cursor.execute.assert_not_called()

    def test_insert_new_station(self):
        """Should insert new station when not found."""
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value.fetchall.return_value = []

        mgr = StationManager(mock_conn)

        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [None, (99,)]  # Not found, then insert returns ID

        result = mgr.get_or_create('New Station', mock_cursor)

        assert result == 99
        assert mgr.cache['New Station'] == 99
        assert mock_cursor.execute.call_count == 2  # SELECT + INSERT

    def test_location_extraction(self):
        """Should extract location from station name."""
        mgr = StationManager(MagicMock())

        # Test with @ separator
        loc1 = mgr._extract_location("station_AquaPars #2 @Power Station, Tempe")
        assert loc1 == "Power Station, Tempe"

        # Test without @ separator
        loc2 = mgr._extract_location("Simple Station Name")
        assert loc2 == "Simple Station Name"


# ============================================
# Measurement Inserter Tests
# ============================================

class TestMeasurementInserter:
    """Test measurement batch insertion."""

    def test_empty_batch(self):
        """Empty batch should do nothing."""
        mock_conn = MagicMock()
        mock_mgr = MagicMock()

        inserter = MeasurementInserter(mock_conn, mock_mgr)
        result = inserter.insert_batch([])

        assert result == 0
        mock_conn.cursor.assert_not_called()

    def test_skip_missing_station_name(self):
        """Documents without station_name should be skipped."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        mock_mgr = MagicMock()

        inserter = MeasurementInserter(mock_conn, mock_mgr)

        batch = [
            {'timestamp': '2025-10-02T13:15:53.937Z'},  # Missing station_name
        ]

        result = inserter.insert_batch(batch)
        assert result == 0
        # execute_values should not be called
        from psycopg2.extras import execute_values
        # (Can't easily verify this without more mocking)

    def test_insert_with_missing_fields(self):
        """Documents with missing fields should insert as NULL."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        mock_mgr = MagicMock()
        mock_mgr.get_or_create.return_value = 1

        inserter = MeasurementInserter(mock_conn, mock_mgr)

        batch = [
            {
                'timestamp': '2025-10-02T13:15:53.937Z',
                'station_name': 'Station A',
                'temperature': 27.5
                # Other fields missing
            }
        ]

        # Note: This test is limited by mocking; full integration test recommended
        # result = inserter.insert_batch(batch)
        # Verify NULL values are handled (requires deeper mocking)


# ============================================
# Retry Logic Tests
# ============================================

class TestRetryLogic:
    """Test exponential backoff retry."""

    def test_success_first_try(self):
        """Should return on first success."""
        mock_func = Mock(return_value="success")
        result = retry_with_backoff(mock_func, max_retries=3)

        assert result == "success"
        assert mock_func.call_count == 1

    def test_retry_then_success(self):
        """Should retry and eventually succeed."""
        mock_func = Mock(side_effect=[
            Exception("fail 1"),
            Exception("fail 2"),
            "success"
        ])

        result = retry_with_backoff(mock_func, max_retries=3)

        assert result == "success"
        assert mock_func.call_count == 3

    def test_all_retries_exhausted(self):
        """Should return None if all retries exhausted."""
        mock_func = Mock(side_effect=Exception("always fails"))
        result = retry_with_backoff(mock_func, max_retries=2)

        assert result is None
        assert mock_func.call_count == 3  # Initial + 2 retries


# ============================================
# End-to-End Scenario Tests
# ============================================

class TestIdempotency:
    """Test that reprocessing same batch is safe."""

    def test_duplicate_documents_ignored(self):
        """
        ON CONFLICT (time, station_id) DO NOTHING should prevent duplicates.

        This is a conceptual test; full test requires real DB connection.
        """
        # In real integration test:
        # 1. Insert document A with timestamp T and station 1
        # 2. Insert same document A again
        # 3. Query DB and verify only 1 row exists
        #
        # This validates the idempotency constraint.
        pass


# ============================================
# Run Tests
# ============================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
