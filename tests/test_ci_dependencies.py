"""Static CI snapshot contracts; no installation, network or machine secrets."""
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CIDependencyTests(unittest.TestCase):
    def test_constraints_are_unique_exact_public_versions(self):
        names = set()
        for line in (ROOT / 'constraints-windows-py312.txt').read_text().splitlines():
            if not line.strip() or line.startswith('#'):
                continue
            match = re.fullmatch(r'([a-z0-9-]+)==([0-9][a-zA-Z0-9.]+)', line)
            self.assertIsNotNone(match, line)
            self.assertNotIn(match[1], names)
            names.add(match[1])
        for line in (ROOT / 'requirements.txt').read_text().splitlines():
            if line.strip() and not line.startswith('#'):
                name = re.match(r'[\w-]+', line)[0].lower().replace('_', '-')
                self.assertIn(name, names)
        self.assertIn('ruff', names)
        # ddgs requests httpx extras; following only unconditional metadata misses them.
        for extra_dependency in ('brotli', 'h2', 'hpack', 'hyperframe', 'socksio'):
            self.assertIn(extra_dependency, names)

    def test_ci_consumes_snapshot_and_checks_consistency(self):
        requirements = (ROOT / 'requirements-ci.txt').read_text()
        self.assertIn('-c constraints-windows-py312.txt', requirements)
        self.assertIn('-r requirements.txt', requirements)
        workflow = (ROOT / '.github/workflows/ci.yml').read_text()
        for command in ('pip install -r requirements-ci.txt', 'pip check',
                        'python tester.py --all --verbose', 'python -m ruff check .'):
            self.assertIn(command, workflow)
        self.assertIn('constraints-windows-py312.txt', workflow)
        self.assertNotIn('pip install --upgrade pip', workflow)
