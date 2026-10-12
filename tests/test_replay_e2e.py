"""P5: the full cycle with real DAMON: record -> profile -> replay -> record ->
compare. Needs root."""
import json
import subprocess
import sys

import pytest

from conftest import (VERIFY, HOT_REGION, MKPROFILE, REPLAY, has_real_damon,
                      run_record)

pytestmark = pytest.mark.skipif(not has_real_damon(),
                                reason='needs root, DAMON sysfs and damo')


def test_hot_region_cycle(tmp_path):
    orig, again = tmp_path / 'orig', tmp_path / 'again'
    profile = tmp_path / 'profile.txt'

    p = run_record(['-o', orig, '-i', '500', '-w', '2000', '--',
                    HOT_REGION, '256', '16', '8'], timeout=120)
    assert p.returncode == 0, p.stderr
    p = subprocess.run([sys.executable, str(MKPROFILE), str(orig), '-o', str(profile)],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    p = run_record(['-o', again, '-i', '500', '-w', '2000', '--',
                    REPLAY, profile], timeout=120)
    assert p.returncode == 0, p.stderr

    p = subprocess.run([sys.executable, str(VERIFY), str(orig), str(again)],
                       capture_output=True, text=True, timeout=60)
    print(p.stdout)
    p = subprocess.run([sys.executable, str(VERIFY), str(orig), str(again),
                        '--json'], capture_output=True, text=True, timeout=60)
    m = json.loads(p.stdout)['metrics']
    for name in ('rss_peak_mb', 'hot_mb_mean', 'hot50_mb_mean', 'app_minflt'):
        assert abs(m[name]['diff']) <= 0.2, (name, m[name])
