import unittest
import pytest
import sys
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

# Add the project root to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.crud import automation as automation_crud
from src.db.models import UserAutomationSettings

class TestAutomationCRUD:
    """Tests for the Automation CRUD operations."""
    
    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        db = AsyncMock()
        db.execute = AsyncMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        return db
    
    @pytest.fixture
    def mock_user_automation_settings(self):
        """Create a mock UserAutomationSettings object."""
        settings = MagicMock(spec=UserAutomationSettings)
        settings.id = 1
        settings.user_id = 123
        settings.is_enabled = True
        settings.session_duration = 7200
        return settings
    
    @pytest.mark.asyncio
    async def test_create_automation_settings(self, mock_db, mock_user_automation_settings):
        """Test creating automation settings."""
        # Set up the mock db to return our mock settings
        mock_db.refresh.side_effect = lambda x: None
        
        # Mock add method
        mock_db.add = MagicMock()
        
        # The test
        result = await automation_crud.create_automation_settings(
            db=mock_db,
            user_id=123,
            is_enabled=True,
            session_duration=7200
        )
        
        # Verify
        assert mock_db.add.called
        assert mock_db.commit.called
        assert mock_db.refresh.called
    
    @pytest.mark.asyncio
    async def test_get_automation_settings(self, mock_db, mock_user_automation_settings):
        """Test getting automation settings."""
        # Set up the mock db to return our mock settings
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_user_automation_settings
        mock_db.execute.return_value = mock_result
        
        # The test
        result = await automation_crud.get_automation_settings(
            db=mock_db,
            user_id=123
        )
        
        # Verify
        assert mock_db.execute.called
        assert result == mock_user_automation_settings
    
    @pytest.mark.asyncio
    async def test_update_automation_settings_existing(self, mock_db, mock_user_automation_settings):
        """Test updating existing automation settings."""
        # Set up the mock db to return our mock settings for the first get call
        mock_result1 = MagicMock()
        mock_result1.scalars.return_value.first.return_value = mock_user_automation_settings
        
        # Set up the mock db to return our mock settings for the second get call (after update)
        mock_result2 = MagicMock()
        mock_result2.scalars.return_value.first.return_value = mock_user_automation_settings
        
        # Make the execute method return different values on consecutive calls
        mock_db.execute.side_effect = [mock_result1, AsyncMock(), mock_result2]
        
        # The test
        result = await automation_crud.update_automation_settings(
            db=mock_db,
            user_id=123,
            is_enabled=False,
            session_duration=3600
        )
        
        # Verify
        assert mock_db.execute.call_count >= 2
        assert mock_db.commit.called
        assert result == mock_user_automation_settings
    
    @pytest.mark.asyncio
    async def test_update_automation_settings_nonexistent(self, mock_db, mock_user_automation_settings):
        """Test updating nonexistent automation settings (should create them)."""
        # Set up the mock db to return None for the first get call
        mock_result1 = MagicMock()
        mock_result1.scalars.return_value.first.return_value = None
        mock_db.execute.return_value = mock_result1
        
        # Patch the create_automation_settings function
        with patch('src.crud.automation.create_automation_settings') as mock_create:
            mock_create.return_value = mock_user_automation_settings
            
            # The test
            result = await automation_crud.update_automation_settings(
                db=mock_db,
                user_id=123,
                is_enabled=True,
                session_duration=3600
            )
            
            # Verify
            assert mock_db.execute.called
            assert mock_create.called
            assert result == mock_user_automation_settings
    
    @pytest.mark.asyncio
    async def test_delete_automation_settings(self, mock_db):
        """Test deleting automation settings."""
        # Set up the mock db to return a rowcount
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_db.execute.return_value = mock_result
        
        # The test
        result = await automation_crud.delete_automation_settings(
            db=mock_db,
            user_id=123
        )
        
        # Verify
        assert mock_db.execute.called
        assert mock_db.commit.called
        assert result is True 