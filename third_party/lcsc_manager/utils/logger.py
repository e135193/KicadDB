"""Brensan host adapter: reuse application logging without KiCad side effects."""
import logging

def get_logger(name="lcsc_manager"):
    return logging.getLogger("brensan.lcsc_manager." + name)
