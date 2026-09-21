"""Compatibility imports for callers moving to :mod:`scripts.environment`.

Runtime detection is owned by ``scripts.environment``; verification only
consumes that public interface.
"""
from scripts.environment import check_for as check_required_environment
from scripts.environment import detect_java, detect_python, detect_tool
from scripts.environment import diagnose as diagnose_environment

__all__ = ["check_required_environment", "detect_java", "detect_python", "detect_tool", "diagnose_environment"]
