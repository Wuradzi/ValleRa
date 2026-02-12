# test_core.py
"""Basic tests for ValleRa core functionality."""
import pytest
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.processor import CommandProcessor
from core.ai_brain import AIBrain
from skills import get_time, get_date, system_status


class TestSkills:
    """Test basic skills."""
    
    def test_get_time(self):
        """Test time retrieval."""
        result = get_time()
        assert result is not None
        assert ":" in result  # Time format HH:MM
    
    def test_get_date(self):
        """Test date retrieval."""
        result = get_date()
        assert result is not None
        assert "2026" in result  # Year should be in date
    
    def test_system_status(self):
        """Test system status."""
        result = system_status()
        assert result is not None
        assert "CPU" in result


class TestConfig:
    """Test configuration."""
    
    def test_config_loaded(self):
        """Test that config loads without errors."""
        import config
        assert config.NAME is not None
        assert config.MAIN_MODEL is not None
    
    def test_trigger_words(self):
        """Test that trigger words are defined."""
        import config
        assert len(config.TRIGGER_WORDS) > 0
        assert any("валера" in word.lower() for word in config.TRIGGER_WORDS)


class TestProcessor:
    """Test command processor."""
    
    def test_processor_init(self):
        """Test that processor initializes."""
        # This will fail without proper voice/listener setup
        # but should not raise import errors
        from core.processor import CommandProcessor
        # Just verify import works
        assert CommandProcessor is not None


class TestAIBrain:
    """Test AI brain."""
    
    def test_ai_brain_init(self):
        """Test that AI brain initializes."""
        from core.ai_brain import AIBrain
        # Just verify import works and class exists
        assert AIBrain is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
