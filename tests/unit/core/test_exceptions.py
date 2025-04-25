# tests/unit/core/test_exceptions.py
import pytest

from sa.core import exceptions

def test_raise_core_logic_error():
    """Test raising and catching CoreLogicError."""
    with pytest.raises(exceptions.CoreLogicError):
        raise exceptions.CoreLogicError("Core logic issue")

def test_raise_nlp_error():
    """Test raising and catching NLPError."""
    with pytest.raises(exceptions.NLPError):
        raise exceptions.NLPError("NLP processing issue")

def test_raise_llm_error():
    """Test raising and catching LLMError."""
    with pytest.raises(exceptions.LLMError):
        raise exceptions.LLMError("LLM provider issue")

def test_raise_database_error():
    """Test raising and catching DatabaseError."""
    with pytest.raises(exceptions.DatabaseError):
        raise exceptions.DatabaseError("Database issue")

def test_inheritance_core_logic_error():
    """Test catching subclasses via CoreLogicError."""
    with pytest.raises(exceptions.CoreLogicError):
        raise exceptions.NLPError("Caught by CoreLogicError")
    with pytest.raises(exceptions.CoreLogicError):
        raise exceptions.LLMError("Also caught by CoreLogicError")

def test_inheritance_nlp_error():
    """Test catching LLMError via NLPError."""
    with pytest.raises(exceptions.NLPError):
        raise exceptions.LLMError("Caught by NLPError")

def test_database_error_inheritance():
    """Test DatabaseError inheritance (separate from CoreLogicError)."""
    with pytest.raises(exceptions.DatabaseError):
        raise exceptions.DatabaseError("DB error")
    with pytest.raises(Exception): # Should be caught by base Exception
        raise exceptions.DatabaseError("Caught by Exception")

    # Verify it's NOT caught by CoreLogicError
    did_not_catch = False
    try:
        raise exceptions.DatabaseError("Not a CoreLogicError")
    except exceptions.CoreLogicError:
        pass # Should not happen
    except exceptions.DatabaseError:
        did_not_catch = True # It was caught by the correct type
    except Exception:
        pass # Or general exception

    assert did_not_catch, "DatabaseError should not be caught by CoreLogicError" 