"""Registers the run report (tests/hippo_report.py): a timestamped folder under tests/reports/ for every pytest run."""
import os

import pytest
from hippo_report import Reporter


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    if os.environ.get("HIPPO_REPORT") == "0" or hasattr(config, "workerinput"):
        return
    reporter = Reporter(config)
    reporter.configure()  # before pytest's own junitxml plugin reads --junitxml
    config.pluginmanager.register(reporter, "hippo-report")
