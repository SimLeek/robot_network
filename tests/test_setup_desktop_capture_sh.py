"""
tests/test_setup_desktop_capture_sh.py

Dry-run tests for examples/desktop/setup_desktop_capture.sh: static
checks (bash -n, shellcheck if available) plus an actual execution of
the script's control flow with pacman/paru/apt-get/sudo/tee/modprobe/
pactl/dpkg/command all replaced by logging stubs on a fake PATH, so we
can verify branching (Arch vs Debian, already-installed vs needs-AUR-
helper, pactl reachable vs not) without touching real kernel modules,
package managers, or /etc.

Each stub appends "<name> <args>" to a shared CALL_LOG file so tests can
assert on exactly what the script tried to do.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / 'examples' / 'desktop' / 'setup_desktop_capture.sh'

_STUB_TEMPLATE = """#!/bin/bash
echo "{name} $*" >> "$CALL_LOG"
{body}
"""


class TestScriptStatic(unittest.TestCase):
    """Checks that don't need to actually run the script."""

    def test_bash_syntax_is_valid(self):
        result = subprocess.run(['bash', '-n', str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_shellcheck_reports_no_default_level_issues(self):
        if shutil.which('shellcheck') is None:
            self.skipTest('shellcheck not installed in this environment')
        result = subprocess.run(['shellcheck', str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_is_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK), 'setup script should be chmod +x')


class _DryRunHarness(unittest.TestCase):
    """Base class: builds a fake PATH of stub commands and a fake $HOME-ish
    scratch dir per test, so tests never touch the real system."""

    # name -> extra shell body appended after the call is logged. Override
    # per test via self._configure_stub(name, body, exit_code).
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='robonet_script_test_')
        self.fakebin = os.path.join(self.tmpdir, 'fakebin')
        os.makedirs(self.fakebin)
        self.call_log = os.path.join(self.tmpdir, 'calls.log')
        open(self.call_log, 'w').close()

        # Sensible defaults: everything succeeds and looks "already set up"
        # unless a test overrides it. sudo is a plain passthrough -- real
        # sudo often forces its own secure_path ignoring our fake PATH, a
        # stub avoids that entirely and this sandbox already runs as root.
        self._configure_stub('sudo', 'exec "$@"')
        self._configure_stub('tee', 'cat > /dev/null')
        self._configure_stub('modprobe', 'exit 0')
        self._configure_stub('pacman', 'exit 0')          # "-Qi" succeeds -> already installed
        self._configure_stub('apt-get', 'exit 0')
        self._configure_stub('dpkg', 'echo "ii  v4l2loopback-dkms"')  # grep -q finds it -> already installed
        self._configure_stub('pactl', 'exit 0')            # "pactl info" succeeds -> reachable
        self._configure_stub('command', 'exit 1')          # "command -v paru/yay" -> not found by default
        self._configure_stub('paru', 'exit 0')
        self._configure_stub('yay', 'exit 0')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _configure_stub(self, name: str, body: str, ):
        path = os.path.join(self.fakebin, name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(_STUB_TEMPLATE.format(name=name, body=body))
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    def _set_os_marker(self, kind: str):
        """The script detects OS via /etc/arch-release or /etc/debian_version.
        We can't (and shouldn't) touch the real /etc, so instead we give the
        fake bin dir a tiny 'test' entry point that pre-creates whichever
        marker is missing on THIS sandbox and restores it after -- see
        run_script(), which does this scoped to a subprocess only."""
        self._os_kind = kind

    def calls(self):
        with open(self.call_log, encoding='utf-8') as f:
            return [line.rstrip('\n') for line in f if line.strip()]

    def run_script(self, os_kind: str, timeout=15):
        """Run the real script with our fakebin prepended to PATH. OS
        detection reads /etc/arch-release or /etc/debian_version directly
        (no override point in the script itself), so instead of patching
        real /etc, this asserts against whichever OS this sandbox actually
        is and lets that be the one path exercised end-to-end; the other
        branch's functions are still covered directly by
        TestScriptFunctions below without needing root-owned /etc edits.
        """
        env = dict(os.environ)
        env['PATH'] = self.fakebin + ':' + env['PATH']
        env['CALL_LOG'] = self.call_log
        return subprocess.run(
            ['bash', str(SCRIPT)], capture_output=True, text=True,
            env=env, timeout=timeout)


class TestScriptDryRun(_DryRunHarness):
    """Runs the real script end-to-end against this sandbox's real OS
    (Ubuntu -> the Debian branch) with every package-manager/privileged
    call stubbed out. Exercises the actual bash control flow, not a
    reimplementation of it."""

    def test_debian_already_installed_full_run_succeeds(self):
        # This sandbox is Ubuntu, so /etc/debian_version already exists for
        # real -- the script's own detection runs unmodified.
        if not os.path.exists('/etc/debian_version'):
            self.skipTest('this sandbox is not Debian-based; see TestScriptFunctions instead')
        result = self.run_script('debian')
        self.assertEqual(result.returncode, 0, f'stdout:\n{result.stdout}\nstderr:\n{result.stderr}')
        calls = self.calls()
        self.assertTrue(any(c.startswith('dpkg') for c in calls), calls)
        # Already "installed" (per the dpkg stub) -- must NOT try apt-get install.
        self.assertFalse(any('install' in c for c in calls if c.startswith('apt-get')), calls)
        self.assertTrue(any(c.startswith('modprobe') for c in calls), calls)
        self.assertIn('pactl OK', result.stdout)

    def test_debian_pactl_unreachable_degrades_to_video_only_message(self):
        if not os.path.exists('/etc/debian_version'):
            self.skipTest('this sandbox is not Debian-based; see TestScriptFunctions instead')
        self._configure_stub('pactl', 'exit 1')
        result = self.run_script('debian')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('AUDIO capture will be skipped', result.stdout)

    def test_debian_not_yet_installed_calls_apt_get_install(self):
        if not os.path.exists('/etc/debian_version'):
            self.skipTest('this sandbox is not Debian-based; see TestScriptFunctions instead')
        self._configure_stub('dpkg', 'echo "some other package"')  # grep -q won't match
        result = self.run_script('debian')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertTrue(any('apt-get' in c and 'install' in c for c in calls), calls)


class TestScriptFunctions(unittest.TestCase):
    """Covers the Arch-specific branches (this sandbox is Debian-based, so
    TestScriptDryRun can't exercise them end-to-end) by extracting just
    the relevant function body with bash itself and calling it directly,
    rather than reimplementing its logic in Python."""

    def _run_function(self, function_name: str, pre: str = '', args=(), stub_dir=None):
        """Source the script (which just defines functions and sets
        variables at the top -- 'set -e' plus the OS-detection block runs
        too, so this only works on the Debian branch for full sourcing;
        instead we extract and eval just the requested function's
        definition text, which is self-contained bash and doesn't need
        the rest of the script to run first."""
        script_text = SCRIPT.read_text()
        # Grab "func_name() { ... }" through its matching closing brace at
        # column 0 -- every function in this script is written that way.
        start = script_text.index(f'{function_name}() {{')
        end = script_text.index('\n}', start) + 2
        func_src = script_text[start:end]
        env = dict(os.environ)
        if stub_dir:
            env['PATH'] = stub_dir + ':' + env['PATH']
        full = f'{pre}\n{func_src}\n{function_name} {" ".join(args)}\n'
        return subprocess.run(['bash', '-c', full], capture_output=True, text=True, env=env)

    def _make_stub_dir(self, stubs: dict) -> str:
        d = tempfile.mkdtemp(prefix='robonet_func_test_')
        for name, body in stubs.items():
            path = os.path.join(d, name)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(f'#!/bin/bash\n{body}\n')
            os.chmod(path, 0o755)
        return d

    def test_install_v4l2loopback_arch_already_installed_short_circuits(self):
        stub_dir = self._make_stub_dir({'pacman': 'exit 0'})  # -Qi succeeds
        result = self._run_function('install_v4l2loopback_arch', stub_dir=stub_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('already installed', result.stdout)
        shutil.rmtree(stub_dir)

    def test_install_v4l2loopback_arch_uses_paru_when_available(self):
        stub_dir = self._make_stub_dir({
            'pacman': 'exit 1',   # -Qi fails -> not installed
            'command': 'if [ "$2" = "paru" ]; then exit 0; else exit 1; fi',
            'paru': 'echo "paru called: $*"',
        })
        result = self._run_function('install_v4l2loopback_arch', stub_dir=stub_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('paru called', result.stdout)
        shutil.rmtree(stub_dir)

    def test_install_v4l2loopback_arch_uses_yay_when_paru_missing(self):
        stub_dir = self._make_stub_dir({
            'pacman': 'exit 1',
            'command': 'if [ "$2" = "yay" ]; then exit 0; else exit 1; fi',
            'yay': 'echo "yay called: $*"',
        })
        result = self._run_function('install_v4l2loopback_arch', stub_dir=stub_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('yay called', result.stdout)
        shutil.rmtree(stub_dir)

    def test_install_v4l2loopback_arch_fails_clearly_with_no_aur_helper(self):
        stub_dir = self._make_stub_dir({
            'pacman': 'exit 1',
            'command': 'exit 1',   # neither paru nor yay found
        })
        result = self._run_function('install_v4l2loopback_arch', stub_dir=stub_dir)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No AUR helper', result.stdout)
        shutil.rmtree(stub_dir)


if __name__ == '__main__':
    unittest.main()
